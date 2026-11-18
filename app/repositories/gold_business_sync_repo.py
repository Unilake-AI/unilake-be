"""Đồng bộ bản ghi Gold vào các bảng nghiệp vụ bằng khóa nguồn có namespace.

Không dùng ID nguồn làm PK PostgreSQL, không tạo FK/tên người giả, không xóa
hồ sơ vắng mặt trong một file. Bên gọi quản lý transaction và fence Gold head.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Table,
    func,
    insert,
    select,
    text,
    update,
)

from app.core.roles import SYSTEM_DEPARTMENT_CODES
from app.repositories.business_identity import (
    IDENTITIES as IDENTITIES,
)
from app.repositories.business_identity import (
    MODELS as MODELS,
)
from app.repositories.business_identity import (
    BusinessSyncBlockedError as BusinessSyncBlockedError,
)
from app.repositories.business_identity import (
    _save_identity as _save_identity,
)
from app.repositories.business_identity import (
    canonical as canonical,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

NATURAL = {
    "department": ("department_code",),
    "position": ("position_code",),
    "employee": ("employee_code",),
    "major": ("major_code",),
    "program": ("program_code",),
    "course": ("course_code",),
    "semester": ("semester_code",),
    "curriculum": ("program_id", "version"),
    "admission_year": ("year_label",),
    "admission_method": ("method_code",),
    "student": ("student_code",),
    "enrollment": ("student_id", "class_id"),
    "class_lecturer": ("class_id", "lecturer_id"),
    "curriculum_course": ("curriculum_id", "course_id"),
    "admission": ("applicant_id", "program_id", "admission_year_id", "admission_method_id"),
    "enrollment_grade": ("enrollment_id",),
}


def source_key(table: Table, row: dict[str, Any], entity: str) -> str:
    keys = [column.name for column in table.primary_key]
    if all(row.get(key) is not None for key in keys):
        key = canonical([str(row[name]) for name in keys])
    elif entity in NATURAL and all(row.get(key) is not None for key in NATURAL[entity]):
        key = canonical({"natural": [str(row[name]) for name in NATURAL[entity]]})
    else:
        raise BusinessSyncBlockedError("GOLD_SYNC_SOURCE_ID_REQUIRED", entity)
    if len(key) > 1024:
        raise BusinessSyncBlockedError("GOLD_SYNC_SOURCE_ID_INVALID", entity)
    return key


def project_records(entity: str, rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    if entity == "admission":
        raise BusinessSyncBlockedError("GOLD_SYNC_ADMISSION_CONTRACT_REQUIRED", entity)
    if entity in MODELS:
        return entity, rows
    raise BusinessSyncBlockedError("GOLD_SYNC_ENTITY_MAPPING_REQUIRED", entity)


def sync_records(
    connection: Connection,
    payload: dict[str, list[dict[str, Any]]],
    gold_job_id: str,
    *,
    domain: str | None = None,
    actor: Any = None,
) -> dict[str, int]:
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('gold.business.sync',0))")
        )
    if any(name.startswith("import_") for name in payload):
        from app.repositories.business_import_repo import ImportBlockedError, sync_import_records

        if len(payload) != 1:
            raise ImportBlockedError([{"code": "IMPORT_MIXED_CONTRACTS_NOT_ALLOWED"}])
        entity, records = next(iter(payload.items()))
        if domain is not None:
            from app.modules.ingestion.business_imports import CONTRACTS

            if not any(c.entity == entity and c.domain == domain for c in CONTRACTS.values()):
                raise ImportBlockedError([{"code": "IMPORT_TYPE_OR_DOMAIN_INVALID"}])
        return sync_import_records(connection, entity, records, gold_job_id, actor=actor)
    pending: list[tuple[str, dict[str, Any]]] = []
    for entity, rows in payload.items():
        target, projected = project_records(entity, rows)
        pending.extend((target, row) for row in projected)
    counts: dict[str, int] = {}
    last_error = None
    while pending:
        remaining = []
        progress = False
        for entity, row in pending:
            try:
                _upsert(connection, entity, row, gold_job_id)
                counts[entity] = counts.get(entity, 0) + 1
                progress = True
            except BusinessSyncBlockedError as exc:
                if exc.code != "GOLD_SYNC_REFERENCE_MISSING":
                    raise
                last_error = exc
                remaining.append((entity, row))
        if remaining and not progress:
            assert last_error is not None
            raise last_error
        pending = remaining
    return counts


def _upsert(connection: Connection, entity: str, row: dict[str, Any], job_id: str) -> None:
    table = MODELS[entity]
    namespace = row.get("_source_system_id")
    if not namespace:
        raise BusinessSyncBlockedError("GOLD_SYNC_NAMESPACE_REQUIRED", entity)
    namespace = str(namespace)
    key = source_key(table, row, entity)
    identity_condition = (
        IDENTITIES.c.namespace == namespace,
        IDENTITIES.c.entity == entity,
        IDENTITIES.c.source_key == key,
    )
    identity = connection.execute(select(IDENTITIES).where(*identity_condition)).mappings().first()
    if entity == "department":
        # app.department là danh mục RBAC cố định, không phải bản sao cây tổ chức nguồn.
        # Chỉ tạo ánh xạ nguồn -> phòng đã có; tuyệt đối không INSERT/UPDATE phòng.
        code = row.get("department_code")
        if not isinstance(code, str) or code.strip().upper() not in SYSTEM_DEPARTMENT_CODES:
            raise BusinessSyncBlockedError(
                "GOLD_SYNC_SYSTEM_DEPARTMENT_ONLY", entity, "department_code"
            )
        target_id = connection.execute(
            select(table.c.department_id)
            .where(table.c.department_code == code.strip().upper(), table.c.status == "ACTIVE")
            .with_for_update(read=True)
        ).scalar_one_or_none()
        if target_id is None:
            raise BusinessSyncBlockedError(
                "GOLD_SYNC_SYSTEM_DEPARTMENT_MISSING", entity, "department_code"
            )
        target_key = {"department_id": target_id}
        if identity is not None and identity["target_key"] != target_key:
            raise BusinessSyncBlockedError(
                "GOLD_SYNC_DEPARTMENT_MAPPING_CONFLICT", entity, "department_code"
            )
        _save_identity(connection, namespace, entity, key, target_key, job_id, identity is not None)
        return
    values = {name: value for name, value in row.items() if name in table.c}
    # Ứng dụng dùng mã trạng thái viết hoa; Silver giữ enum theo hợp đồng nguồn.
    for field, value in values.items():
        if isinstance(value, str) and (field == "status" or field.endswith("_status")):
            values[field] = value.upper()
    pk_names = [column.name for column in table.primary_key]
    for column in table.c:
        foreign = next(iter(column.foreign_keys), None)
        if foreign is None or column.name not in values or values[column.name] is None:
            continue
        reference = connection.execute(
            select(IDENTITIES.c.target_key).where(
                IDENTITIES.c.namespace == namespace,
                IDENTITIES.c.entity == foreign.column.table.name,
                IDENTITIES.c.source_key == canonical([str(values[column.name])]),
            )
        ).scalar_one_or_none()
        if reference is None:
            raise BusinessSyncBlockedError("GOLD_SYNC_REFERENCE_MISSING", entity, column.name)
        values[column.name] = reference[foreign.column.name]
    natural = NATURAL.get(entity)
    target = None
    if identity is not None:
        target = (
            connection.execute(
                select(table)
                .where(*(table.c[name] == value for name, value in identity["target_key"].items()))
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if target is None:
            raise BusinessSyncBlockedError("GOLD_SYNC_TARGET_MISSING", entity)
    elif natural and all(values.get(name) is not None for name in natural):
        target = (
            connection.execute(
                select(table)
                .where(*(table.c[name] == values[name] for name in natural))
                .with_for_update()
            )
            .mappings()
            .first()
        )
    elif all(name in values for name in pk_names) and all(
        table.c[name].foreign_keys for name in pk_names
    ):
        target = (
            connection.execute(
                select(table).where(*(table.c[name] == values[name] for name in pk_names))
            )
            .mappings()
            .first()
        )
    # Khóa chính tự sinh thuộc DB; chỉ PK đồng thời là FK mới được mang từ mapping.
    for name in pk_names:
        if not table.c[name].foreign_keys:
            values.pop(name, None)
    for column in table.c:
        if column.name not in values:
            if (
                target is None
                and not column.nullable
                and not column.primary_key
                and column.server_default is None
                and column.default is None
            ):
                raise BusinessSyncBlockedError("GOLD_SYNC_REQUIRED_FIELD", entity, column.name)
            continue
        value = values[column.name]
        if value is None:
            if not column.nullable:
                raise BusinessSyncBlockedError("GOLD_SYNC_REQUIRED_FIELD", entity, column.name)
            continue
        python_type = column.type.python_type
        if python_type is str:
            maximum_length = getattr(column.type, "length", None)
            if (
                not isinstance(value, str)
                or not value.strip()
                or (maximum_length and len(value) > maximum_length)
            ):
                raise BusinessSyncBlockedError("GOLD_SYNC_FIELD_INVALID", entity, column.name)
        elif (
            (python_type is int and (isinstance(value, bool) or not isinstance(value, int)))
            or (python_type is bool and not isinstance(value, bool))
            or (
                python_type is date and (not isinstance(value, date) or isinstance(value, datetime))
            )
            or (python_type is datetime and not isinstance(value, datetime))
            or (python_type is Decimal and not isinstance(value, (Decimal, int)))
        ):
            raise BusinessSyncBlockedError("GOLD_SYNC_FIELD_INVALID", entity, column.name)
    if target is None:
        if (
            connection.dialect.name == "sqlite"
            and len(pk_names) == 1
            and not table.c[pk_names[0]].foreign_keys
        ):
            values[pk_names[0]] = (
                connection.execute(select(func.max(table.c[pk_names[0]]))).scalar_one() or 0
            ) + 1
        target_key = dict(
            connection.execute(insert(table).values(**values).returning(*table.primary_key))
            .mappings()
            .one()
        )
    else:
        target_key = {name: target[name] for name in pk_names}
        fingerprint = hashlib.sha256(canonical(target_key).encode()).hexdigest()
        other_source = connection.execute(
            select(IDENTITIES.c.namespace).where(
                IDENTITIES.c.entity == entity,
                IDENTITIES.c.target_fingerprint == fingerprint,
                IDENTITIES.c.namespace != namespace,
            )
        ).first()
        if other_source is not None:
            raise BusinessSyncBlockedError("GOLD_SYNC_SOURCE_CONFLICT", entity)
        connection.execute(
            update(table)
            .where(*(table.c[name] == value for name, value in target_key.items()))
            .values(**values)
        )
    _save_identity(connection, namespace, entity, key, target_key, job_id, identity is not None)
