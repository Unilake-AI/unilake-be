"""Đề xuất cách xử lý từ file thật; chỉ lưu lựa chọn sau khi quản trị viên xem trước."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.pipeline.cleaning.pipeline import CleaningPipeline
from app.modules.pipeline.contracts.automation import AUTOMATIC_RECIPE_VERSION
from app.modules.pipeline.contracts.identity import digest
from app.modules.pipeline.models.identity import CrosswalkRelease
from app.modules.pipeline.models.profiles import SilverSourceProfile
from app.modules.pipeline.repositories.identity import CrosswalkRepository
from app.modules.pipeline.services.profile_storage import save_profile
from app.modules.pipeline.services.source_cleaning import (
    _job_context,
    build_cleaning_task,
    read_bronze_snapshot,
)
from app.modules.pipeline.services.source_profiles import SourceProfile
from app.modules.pipeline.services.source_schema_builder import (
    column_names,
    generic_schema,
    source_contract,
)
from app.modules.pipeline.standardization.duckdb_engine import DuckDBStandardizationEngine
from app.repositories.silver_profile_repo import active_reference_sources

if TYPE_CHECKING:
    from app.modules.ingestion.contracts.readers import ReadResult

ROOT = Path(__file__).resolve().parents[4]
DATE_FORMATS = {"YMD": "%Y-%m-%d", "DMY": "%d/%m/%Y", "MDY": "%m/%d/%Y"}


class CleaningChoices(BaseModel):
    """Giao diện chỉ gửi lựa chọn dễ hiểu; thông tin nguồn được lấy từ lần nạp đã chọn."""

    model_config = ConfigDict(extra="forbid")
    analysis_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    entity: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    identity_mode: Literal["source", "canonical"] = "source"
    field_mapping: dict[str, str]
    ignored_columns: list[str] = Field(default_factory=list)
    date_formats: dict[str, Literal["YMD", "DMY", "MDY"]] = Field(default_factory=dict)
    null_tokens: list[str] = Field(default_factory=lambda: [""], max_length=30)
    trim_text: bool = False
    collapse_exact_duplicates: bool = False
    crosswalk_release: str | None = Field(default=None, min_length=1, max_length=100)
    reference_namespaces: dict[str, str] = Field(default_factory=dict)
    field_types: dict[
        str, Literal["string", "integer", "decimal", "date", "timestamp", "boolean"]
    ] = Field(default_factory=dict)
    identifier_fields: list[str] = Field(default_factory=list)
    required_fields: list[str] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)


class ConfirmCleaningChoices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choices: CleaningChoices
    confirmation_token: str = Field(min_length=1, max_length=4096)


def _reject(message: str, code: str = "CLEANING_SETUP_INVALID", status: int = 422) -> NoReturn:
    raise AppException(message, code=code, status_code=status)


def _name(value: str) -> str:
    # Chỉ gợi ý từ tên cột tương ứng; không suy đoán ý nghĩa từ vài giá trị trong file.
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", normalized)


def _templates(domain: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if domain not in {"hr", "academic", "admissions"}:
        _reject("Lĩnh vực này chưa có quy định cấu trúc Silver để đối chiếu")
    source = json.loads((ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text())
    contracts = {
        name: json.loads((ROOT / f"docs/standardization/silver-schemas/{name}.json").read_text())
        for name in ("hr", "academic", "admissions")
    }
    # Cấu trúc Silver đã quy định chỉ lấy năm sinh và bỏ một số thông tin cá nhân.
    # Chuẩn bị quy tắc cho các trường sẽ lưu; cột bị bỏ vẫn cần được người dùng xác nhận.
    processing = deepcopy(source)
    for entity, schema in processing["entities"].items():
        if entity not in contracts[domain]["$defs"]:
            continue
        source_columns = {column["name"]: column for column in schema["columns"]}
        schema["columns"] = [
            {**source_columns[spec.get("x-derived-from", name)], "name": name}
            for name, spec in contracts[domain]["$defs"][entity]["properties"].items()
            if not name.startswith("_")
        ]
    return processing, contracts


def _capture(engine: Engine, job_id: int) -> tuple[dict[str, Any], ReadResult]:
    context = _job_context(engine, job_id)
    output = context["output"]
    if output.get("source_type") != context["source_type"] or not isinstance(
        output.get("parser_options"), dict
    ):
        raise ValueError("BRONZE_PARSER_EVIDENCE_REQUIRED")
    context["parser_options"] = output["parser_options"]
    return context, read_bronze_snapshot(context)


def _analysis_fingerprint(
    context: dict[str, Any], parsed: ReadResult, source: dict[str, Any], contracts: dict[str, Any]
) -> str:
    return digest(
        {
            "job": context["ingestion_job_id"],
            "dataset": context["dataset_id"],
            "source": context["data_source_id"],
            "system": context["source_system_id"],
            "domain": context["domain"],
            "table": context["source_table"],
            "bronze": context["output"],
            "columns": parsed.columns,
            "source_schema": source,
            "contracts": contracts,
        }
    )


def _date_options(values: list[Any]) -> list[str]:
    nonempty = [value for value in values if value is not None and value != ""]
    if nonempty and all(isinstance(value, date) for value in nonempty):
        return ["YMD"]
    possible = []
    for name, fmt in DATE_FORMATS.items():
        try:
            for value in nonempty:
                if isinstance(value, date):
                    continue
                if (
                    not isinstance(value, str)
                    or datetime.strptime(value, fmt).strftime(fmt) != value
                ):
                    raise ValueError
        except ValueError:
            continue
        possible.append(name)
    # Cột rỗng chưa chứng minh định dạng nào; dùng cách viết ngày chuẩn làm gợi ý ban đầu.
    return possible if nonempty else ["YMD"]


def _entity_proposals(
    context: dict[str, Any], parsed: ReadResult, source: dict[str, Any], contracts: dict[str, Any]
) -> list[dict[str, Any]]:
    proposals = []
    for entity, schema in source["entities"].items():
        if entity not in contracts[context["domain"]]["$defs"]:
            continue
        fields = []
        mapping = {}
        for column in schema["columns"]:
            target = column["name"]
            target_spec = contracts[context["domain"]]["$defs"][entity]["properties"][target]
            input_name = target_spec.get("x-derived-from", target)
            matches = [name for name in parsed.columns if _name(name) == _name(input_name)]
            suggested = matches[0] if len(matches) == 1 else None
            if suggested is not None:
                mapping[target] = suggested
            values = (
                [row[parsed.columns.index(suggested)] for row in parsed.rows]
                if suggested is not None
                else []
            )
            fields.append(
                {
                    "name": target,
                    "label": f"Lấy năm từ cột {input_name}"
                    if input_name != target
                    else column.get("description", target),
                    "kind": column["db_type"],
                    "nullable": column["nullable"],
                    "identifier": target in schema["primary_key"] or "references" in column,
                    "suggested_source": suggested,
                    "date_formats": _date_options(values) if column["db_type"] == "date" else [],
                    "allowed_values": column.get("enum", []),
                    "reference_entity": target_spec.get("x-references", {}).get("entity"),
                }
            )
        proposals.append(
            {
                "entity": entity,
                "label": schema.get("description", entity),
                "fields": fields,
                "field_mapping": mapping,
                "matched_columns": len(mapping),
                "total_columns": len(fields),
            }
        )
    return sorted(proposals, key=lambda item: (-item["matched_columns"], item["entity"]))


def _profile_id(context: dict[str, Any], identity_mode: str = "canonical") -> str:
    suffix = hashlib.sha256(context["source_table"].encode()).hexdigest()[:12]
    name = f"dataset-{context['dataset_id']}-source-{context['data_source_id']}-{suffix}"
    return f"{name}-source" if identity_mode == "source" else name


def _current(engine: Engine, profile_id: str) -> dict[str, Any] | None:
    with engine.connect() as connection:
        row = (
            connection.execute(
                select(SilverSourceProfile).where(SilverSourceProfile.profile_id == profile_id)
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


def _file_proposal(
    context: dict[str, Any], parsed: ReadResult, source: dict[str, Any]
) -> dict[str, Any]:
    # Các trường cá nhân đã được quy định loại khỏi Silver vẫn cần được bảo vệ khi dùng cấu trúc file.
    protected = {
        _name(name)
        for schema in source["entities"].values()
        for name in schema.get("pii_columns", [])
    }
    mapping = column_names([name for name in parsed.columns if _name(name) not in protected])
    fields = []
    for name, raw in mapping.items():
        values = [
            row[parsed.columns.index(raw)]
            for row in parsed.rows
            if row[parsed.columns.index(raw)] is not None
        ]
        kind = "string"
        if values and all(type(value) is bool for value in values):
            kind = "boolean"
        elif values and all(type(value) is int for value in values):
            kind = "integer"
        elif values and all(
            isinstance(value, int | float | Decimal) and not isinstance(value, bool)
            for value in values
        ):
            kind = "decimal"
        elif values and all(type(value) is date for value in values):
            kind = "date"
        identifier = (
            raw.casefold().endswith(("_id", "_code"))
            or raw.casefold() in {"id", "code"}
            or any(isinstance(value, str) and re.fullmatch(r"0[0-9]+", value) for value in values)
        )
        if identifier:
            kind = "string"
        fields.append(
            {
                "name": name,
                "label": raw,
                "kind": kind,
                "nullable": True,
                "identifier": identifier,
                "suggested_source": raw,
                "date_formats": _date_options(values),
                "allowed_values": [],
                "reference_entity": None,
            }
        )
    return {
        "entity": f"file_{context['dataset_id']}",
        "label": "Theo các cột trong file, giữ mã nguồn",
        "fields": fields,
        "field_mapping": mapping,
        "matched_columns": len(fields),
        "total_columns": len(fields),
        "custom": True,
    }


def _source_definition(
    context: dict[str, Any],
    parsed: ReadResult,
    choices: CleaningChoices,
    source: dict[str, Any],
    contracts: dict[str, Any],
) -> SourceProfile:
    if choices.crosswalk_release is not None or choices.reference_namespaces:
        _reject(
            "Chế độ giữ mã nguồn không dùng bản đối chiếu. Hãy chọn hợp nhất mã nếu cần quy đổi"
        )
    file_proposal = _file_proposal(context, parsed, source)
    is_file = choices.entity == file_proposal["entity"]
    primary = (
        choices.primary_key
        if is_file
        else source["entities"].get(choices.entity, {}).get("primary_key", [])
    )
    expected = (
        set(file_proposal["field_mapping"])
        if is_file
        else {
            column["name"]
            for column in source["entities"].get(choices.entity, {}).get("columns", [])
        }
    )
    if not expected or set(choices.field_mapping) != expected:
        _reject("Hãy chọn mẫu phù hợp và ghép đủ các cột cần lưu")
    mapped = set(choices.field_mapping.values())
    ignored = set(choices.ignored_columns)
    if (
        len(mapped) != len(choices.field_mapping)
        or len(ignored) != len(choices.ignored_columns)
        or mapped & ignored
        or mapped | ignored != set(parsed.columns)
    ):
        _reject("Hãy ghép đủ cột cần dùng và xác nhận rõ các cột được bỏ qua")
    if is_file and any(
        _name(value)
        in {
            _name(name)
            for schema in source["entities"].values()
            for name in schema.get("pii_columns", [])
        }
        for value in mapped
    ):
        _reject("Cột thông tin cá nhân này không thuộc cấu trúc Silver được phép lưu")
    if (
        not set(choices.field_types) <= expected
        or not set(choices.required_fields) <= expected
        or not set(choices.identifier_fields) <= expected
        or not set(primary) <= expected
        or len(set(primary)) != len(primary)
    ):
        _reject("Lựa chọn kiểu dữ liệu hoặc cột mã không khớp các cột cần lưu")
    identifiers = set(choices.identifier_fields) | set(primary)
    if not is_file:
        identifiers |= {
            column["name"]
            for column in source["entities"][choices.entity]["columns"]
            if "references" in column
        }
    if is_file:
        if any(choices.field_types.get(name, "string") != "string" for name in identifiers):
            _reject("Cột mã nhận diện cần giữ dạng chữ để không mất số 0 ở đầu")
        audit = {
            name: value
            for name, value in contracts["hr"]["$defs"]["position"]["properties"].items()
            if name.startswith("_")
        }
        source, contract = generic_schema(
            context["domain"],
            choices.entity,
            choices.field_mapping,
            choices.field_types,
            set(choices.required_fields),
            identifiers,
            primary,
            audit,
        )
    else:
        if choices.field_types or choices.required_fields or choices.primary_key:
            _reject("Muốn tự chọn kiểu hoặc khóa, hãy dùng lựa chọn Theo các cột trong file")
        source = deepcopy(source)
        contract = source_contract(
            context["domain"],
            choices.entity,
            source,
            contracts[context["domain"]]["$defs"][choices.entity],
            identifiers,
            primary,
        )
    schema = source["entities"][choices.entity]
    date_fields = {column["name"] for column in schema["columns"] if column["db_type"] == "date"}
    if set(choices.date_formats) != date_fields:
        _reject("Hãy chọn rõ cách đọc cho từng cột ngày tháng")
    if len(set(choices.null_tokens)) != len(choices.null_tokens):
        _reject("Danh sách giá trị được coi là trống không được lặp lại")
    options: dict[str, dict[str, Any]] = {}
    for column in schema["columns"]:
        name = column["name"]
        if choices.trim_text and column["db_type"] == "string" and name not in identifiers:
            options[name] = {"trim": True}
        if name in date_fields:
            options[name] = {"date_formats": [DATE_FORMATS[choices.date_formats[name]]]}
        if column["db_type"] == "timestamp":
            options[name] = {"timestamp_formats": ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"]}
    # Phạm vi riêng theo bộ dữ liệu, nguồn và cấu trúc; mã giống nhau ở nguồn khác không bị trộn.
    scope = (
        "source-"
        + digest(
            {
                "dataset": context["dataset_id"],
                "source": context["data_source_id"],
                "contract": contract,
            }
        )[:28]
    )
    return SourceProfile(
        data_source_id=context["data_source_id"],
        dataset_id=context["dataset_id"],
        source_system_id=context["source_system_id"],
        source_type=context["source_type"],
        source_table=context["source_table"],
        domain=context["domain"],
        entity=choices.entity,
        version="source-setup-1",
        scope=scope,
        source_schema_version=source["version"],
        source_schema=source,
        contracts={context["domain"]: contract},
        field_mapping=choices.field_mapping,
        ignored_columns=choices.ignored_columns,
        format_options=options,
        null_tokens={
            name: [""] if name in identifiers else choices.null_tokens for name in expected
        },
        dictionaries={
            name: {}
            for name, spec in contract["$defs"][choices.entity]["properties"].items()
            if "enum" in spec
        },
        identity_mode="source",
        crosswalk_release=None,
        effective_at=context["created_at"].replace(tzinfo=UTC),
        collapse_exact_duplicates=choices.collapse_exact_duplicates,
    )


def analyze_setup(engine: Engine, job_id: int) -> dict[str, Any]:
    context, parsed = _capture(engine, job_id)
    source, contracts = _templates(context["domain"])
    entities = _entity_proposals(context, parsed, source, contracts)
    complete = [item for item in entities if item["matched_columns"] == item["total_columns"]]
    file_proposal = _file_proposal(context, parsed, source)
    entities.append(file_proposal)
    columns = []
    for index, name in enumerate(parsed.columns):
        values = [row[index] for row in parsed.rows]
        columns.append(
            {
                "name": name,
                "empty_count": sum(value is None or value == "" for value in values),
                "leading_zero_count": sum(
                    isinstance(value, str) and re.fullmatch(r"0[0-9]+", value) is not None
                    for value in values
                ),
            }
        )
    with engine.connect() as connection:
        releases = (
            connection.execute(select(CrosswalkRelease.version).order_by(CrosswalkRelease.version))
            .scalars()
            .all()
        )
    return {
        "ingestion_job_id": job_id,
        "dataset_id": context["dataset_id"],
        "source_table": context["source_table"],
        "records_read": parsed.rows_read,
        "analysis_fingerprint": _analysis_fingerprint(context, parsed, source, contracts),
        "columns": columns,
        "entities": entities,
        "suggested_entity": complete[0]["entity"]
        if len(complete) == 1
        else file_proposal["entity"],
        "crosswalk_releases": list(releases),
        "reference_sources": active_reference_sources(engine),
        "notes": [
            "Gợi ý cần được xem trước và xác nhận trước khi lưu.",
            "Giữ nguyên mã nguồn, kể cả số 0 ở đầu. Không tự điền giá trị còn thiếu.",
            "Các cột có giờ cần ghi rõ múi giờ; dữ liệu chưa rõ sẽ được báo lỗi để kiểm tra.",
        ],
    }


def _definition(
    engine: Engine, job_id: int, choices: CleaningChoices
) -> tuple[dict[str, Any], ReadResult, SourceProfile, str | None]:
    context, parsed = _capture(engine, job_id)
    source, contracts = _templates(context["domain"])
    if choices.analysis_fingerprint != _analysis_fingerprint(context, parsed, source, contracts):
        _reject(
            "File hoặc quy định xử lý đã thay đổi. Hãy đọc lại gợi ý", "CLEANING_SETUP_STALE", 409
        )
    if choices.identity_mode == "source":
        return (
            context,
            parsed,
            _source_definition(context, parsed, choices, source, contracts),
            None,
        )
    if (
        choices.field_types
        or choices.identifier_fields
        or choices.required_fields
        or choices.primary_key
    ):
        _reject("Các lựa chọn cột riêng chỉ áp dụng khi giữ mã nguồn theo cấu trúc file")
    entity = source["entities"].get(choices.entity)
    if entity is None:
        _reject("Loại dữ liệu được chọn không thuộc lĩnh vực của bộ dữ liệu")
    expected = {column["name"] for column in entity["columns"]}
    mapped = set(choices.field_mapping.values())
    ignored = set(choices.ignored_columns)
    if (
        set(choices.field_mapping) != expected
        or len(mapped) != len(choices.field_mapping)
        or len(ignored) != len(choices.ignored_columns)
        or mapped & ignored
        or mapped | ignored != set(parsed.columns)
    ):
        _reject("Hãy ghép đủ cột cần dùng và xác nhận rõ các cột được bỏ qua")
    if len(set(choices.null_tokens)) != len(choices.null_tokens):
        _reject("Danh sách giá trị được coi là trống không được lặp lại")
    date_fields = {column["name"] for column in entity["columns"] if column["db_type"] == "date"}
    if set(choices.date_formats) != date_fields:
        _reject("Hãy chọn cách đọc ngày cho từng cột ngày tháng; hệ thống không tự đoán")
    format_options: dict[str, dict[str, Any]] = {}
    type_options: dict[str, dict[str, Any]] = {}
    for column in entity["columns"]:
        name = column["name"]
        identifier = name in entity["primary_key"] or "references" in column
        # Không áp dụng việc bỏ khoảng trắng hay thay giá trị trống cho mã nhận diện.
        # Mã nguồn chỉ được đổi qua bản đối chiếu có bằng chứng, không ép "007" thành "7".
        if choices.trim_text and column["db_type"] == "string" and not identifier:
            format_options[name] = {"trim": True}
        if name in date_fields:
            format_options[name] = {"date_formats": [DATE_FORMATS[choices.date_formats[name]]]}
        if column["db_type"] == "timestamp":
            format_options[name] = {
                "timestamp_formats": ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"]
            }
        # Bước làm sạch đã đưa ngày về dạng chuẩn trước khi chuyển sang kiểu ngày.
        type_options[name] = {"date_format": "%Y-%m-%d"}
    properties = contracts[context["domain"]]["$defs"][choices.entity]["properties"]
    references = {name for name, spec in properties.items() if "x-references" in spec}
    if set(choices.reference_namespaces) != references:
        _reject("Hãy chọn nguồn chứa mã được liên kết cho từng cột liên quan")
    if references:
        namespaces = {source["namespace"] for source in active_reference_sources(engine)}
        if not set(choices.reference_namespaces.values()) <= namespaces:
            _reject("Nguồn đối chiếu được chọn không tồn tại hoặc đang ngừng hoạt động")
    profile = SourceProfile(
        data_source_id=context["data_source_id"],
        dataset_id=context["dataset_id"],
        source_system_id=context["source_system_id"],
        source_type=context["source_type"],
        source_table=context["source_table"],
        domain=context["domain"],
        entity=choices.entity,
        version="setup-1",
        scope=f"source-{context['source_system_id']}",
        source_schema_version=source["version"],
        source_schema=source,
        contracts=contracts,
        field_mapping=choices.field_mapping,
        ignored_columns=choices.ignored_columns,
        format_options=format_options,
        type_options=type_options,
        # Chỉ chấp nhận sẵn các giá trị đúng quy định, chưa tự dịch hay thay tên giá trị nguồn.
        dictionaries={name: {} for name, spec in properties.items() if "enum" in spec},
        # Người phụ trách chọn rõ nguồn liên kết; không mặc định mã ở mọi nguồn đều giống nhau.
        reference_namespaces=choices.reference_namespaces,
        null_tokens={
            column["name"]: [""]
            if column["name"] in entity["primary_key"] or "references" in column
            else choices.null_tokens
            for column in entity["columns"]
        },
        # Giá trị tạm này chỉ dùng để xem phần làm sạch, tuyệt đối không được lưu hay công bố.
        crosswalk_release=choices.crosswalk_release or "unresolved-preview-only",
        effective_at=context["created_at"].replace(tzinfo=UTC)
        if context["created_at"].tzinfo is None
        else context["created_at"],
        collapse_exact_duplicates=choices.collapse_exact_duplicates,
        source_preview_columns=[],
    )
    crosswalk_fingerprint = None
    if choices.crosswalk_release:
        with engine.begin() as connection:
            snapshot = CrosswalkRepository(connection).load(choices.crosswalk_release)
        crosswalk_fingerprint = snapshot.fingerprint
    return context, parsed, profile, crosswalk_fingerprint


def _seal(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    signature = hmac.new(
        settings.jwt_secret_key.encode(), f"cleaning-setup:{encoded}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{encoded}.{signature}"


def _unseal(token: str) -> dict[str, Any]:
    try:
        encoded, _signature = token.split(".", 1)
        payload = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
        if not isinstance(payload, dict) or not hmac.compare_digest(_seal(payload), token):
            raise ValueError
        if type(payload.get("expires")) is not int or payload["expires"] <= time.time():
            raise ValueError
        return payload
    except (ValueError, TypeError, UnicodeError):
        _reject(
            "Bản xem trước đã hết hạn hoặc không hợp lệ. Hãy xem trước lại",
            "CLEANING_SETUP_PREVIEW_REQUIRED",
            409,
        )
    raise AssertionError


def _evidence(
    engine: Engine,
    context: dict[str, Any],
    choices: CleaningChoices,
    profile: SourceProfile,
    crosswalk_fingerprint: str | None,
    actor: str,
) -> dict[str, Any]:
    current = _current(engine, _profile_id(context, profile.identity_mode))
    return {
        "actor": actor,
        "job": context["ingestion_job_id"],
        "analysis": choices.analysis_fingerprint,
        "profile": profile.fingerprint,
        "crosswalk": crosswalk_fingerprint,
        "revision": current["revision"] if current else None,
    }


def preview_setup(
    engine: Engine, job_id: int, choices: CleaningChoices, actor: str, limit: int = 20
) -> dict[str, Any]:
    context, parsed, profile, crosswalk_fingerprint = _definition(engine, job_id, choices)
    cleaned = CleaningPipeline().clean(build_cleaning_task(context, profile, parsed))
    report = cleaned.report
    normalized = None
    blockers = []
    if profile.identity_mode == "source":
        mapping, identity = profile.services()
        normalized = DuckDBStandardizationEngine().standardize(
            cleaned.batch, mapping, identity, **profile.derivation_options(cleaned.batch)
        )
    elif choices.crosswalk_release:
        with engine.begin() as connection:
            snapshot = CrosswalkRepository(connection).load(choices.crosswalk_release)
        mapping, identity = profile.services(snapshot)
        normalized = DuckDBStandardizationEngine().standardize(
            cleaned.batch, mapping, identity, **profile.derivation_options(cleaned.batch)
        )
    else:
        blockers.append(
            "Chưa có bản đối chiếu mã được chọn. Bạn vẫn xem được phần làm sạch; "
            "cần đối chiếu mã trước khi lưu cấu hình và đưa dữ liệu vào Silver."
        )
    evidence = _evidence(engine, context, choices, profile, crosswalk_fingerprint, actor)
    token = _seal({**evidence, "expires": int(time.time()) + 1800}) if not blockers else None
    return {
        "preview": True,
        "ingestion_job_id": job_id,
        "profile_id": _profile_id(context, profile.identity_mode),
        "profile_fingerprint": profile.fingerprint,
        "records_read": report.records_read,
        "records_passed_cleaning": report.records_passed,
        "records_quarantined": report.records_quarantined,
        "records_collapsed": report.records_collapsed,
        "records_normalized": len(normalized.accepted) if normalized else 0,
        "cleaning_issues": [asdict(issue) for issue in report.issues],
        "normalization_issues": [
            *[asdict(issue) for issue in normalized.mapping_issues],
            *[asdict(issue) for issue in normalized.type_issues],
            *[asdict(issue) for issue in normalized.issues],
        ]
        if normalized
        else [],
        "cleaned_sample": list(cleaned.batch.records[:limit]),
        "normalized_sample": [row.values for row in normalized.accepted[:limit]]
        if normalized
        else [],
        "publication_checked": False,
        "blocking_reasons": blockers,
        "confirmation_token": token,
        "note": "Chưa lưu lựa chọn và chưa ghi dữ liệu vào Silver. Bước lưu Silver sẽ kiểm tra tiếp.",
    }


def confirm_setup(
    engine: Engine, job_id: int, request: ConfirmCleaningChoices, actor: str
) -> dict[str, Any]:
    payload = _unseal(request.confirmation_token)
    context, _parsed, profile, crosswalk_fingerprint = _definition(engine, job_id, request.choices)
    evidence = _evidence(engine, context, request.choices, profile, crosswalk_fingerprint, actor)
    if (profile.identity_mode == "canonical" and crosswalk_fingerprint is None) or any(
        payload.get(key) != value for key, value in evidence.items()
    ):
        _reject(
            "Lựa chọn hoặc cấu hình đã thay đổi. Hãy xem trước lại", "CLEANING_SETUP_STALE", 409
        )
    # Lưu lựa chọn để dùng lại, chưa bật chạy tự động và chưa thay cho quyết định duyệt công bố.
    return save_profile(
        engine, _profile_id(context, profile.identity_mode), profile, actor, evidence["revision"]
    )


def automatic_source_profile(engine: Engine, job_id: int) -> SourceProfile:
    """Quy tắc hệ thống theo file thật; không suy đoán khóa, mã hoặc ngày mơ hồ."""
    context, parsed = _capture(engine, job_id)
    source, contracts = _templates(context["domain"])
    proposals = _entity_proposals(context, parsed, source, contracts)
    protected = {
        _name(name)
        for schema in source["entities"].values()
        for name in schema.get("pii_columns", [])
    }
    complete = [
        item
        for item in proposals
        if item["matched_columns"] == item["total_columns"]
        and all(
            _name(name) in protected
            for name in parsed.columns
            if name not in item["field_mapping"].values()
        )
    ]
    file = _file_proposal(context, parsed, source)
    # Chỉ nhận diện nghiệp vụ khi khớp duy nhất toàn bộ cấu trúc Silver đã quy định.
    selected = complete[0] if len(complete) == 1 else file
    mapping = selected["field_mapping"]
    ignored = [name for name in parsed.columns if name not in mapping.values()]
    fields = selected["fields"]
    dates = {}
    for field in fields:
        if field["kind"] == "date":
            formats = field.get("date_formats", [])
            # Mơ hồ hoặc có giá trị sai: dùng định dạng ISO đã quy định, không đoán ngày/tháng.
            dates[field["name"]] = formats[0] if len(formats) == 1 else "YMD"
    choices = CleaningChoices(
        analysis_fingerprint="0" * 64,
        entity=selected["entity"],
        identity_mode="source",
        field_mapping=mapping,
        ignored_columns=ignored,
        date_formats=dates,
        field_types={field["name"]: field["kind"] for field in fields} if selected is file else {},
        identifier_fields=[field["name"] for field in fields if field["identifier"]],
    )
    profile = _source_definition(context, parsed, choices, source, contracts)
    # Một file có một head ổn định, các file khác không ghi đè nhau dù thuộc cùng dataset.
    scope = (
        "auto-"
        + digest(
            {
                "dataset": profile.dataset_id,
                "source": profile.data_source_id,
                "table": profile.source_table,
            }
        )[:28]
    )
    return profile.model_copy(update={"version": AUTOMATIC_RECIPE_VERSION, "scope": scope})
