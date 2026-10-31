"""Cấp phát đối chiếu định danh qua giao dịch và phát hành bất biến bằng SQLAlchemy Core.

Bên gọi quản lý giao dịch (engine.begin / AsyncConnection.run_sync). Không commit,
DDL hay cấp phát trong bản xem trước S4. SQLite chỉ dùng cho kiểm thử đơn vị.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import Table, insert, select
from sqlalchemy.exc import IntegrityError

from app.modules.pipeline.identity_contracts import (
    CrosswalkEntry,
    CrosswalkSnapshot,
    IdentityConfigurationError,
    source_key,
    utc,
)
from app.modules.pipeline.identity_models import (
    CanonicalIdentity,
    CrosswalkRelease,
    IdentityCrosswalk,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, RowMapping

IDENTITIES = cast("Table", CanonicalIdentity.__table__)
CROSSWALK = cast("Table", IdentityCrosswalk.__table__)
RELEASES = cast("Table", CrosswalkRelease.__table__)
IDENTITY_TABLES = (IDENTITIES, CROSSWALK, RELEASES)


def _entry(row: RowMapping) -> CrosswalkEntry:
    return CrosswalkEntry(
        row["namespace"],
        row["entity"],
        tuple(json.loads(row["source_key"])),
        row["canonical_id"],
        row["evidence"],
        datetime.fromisoformat(row["valid_from"]),
        datetime.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
    )


class CrosswalkRepository:
    def __init__(self, connection: Connection) -> None:
        if not connection.in_transaction():
            raise IdentityConfigurationError("CROSSWALK_TRANSACTION_REQUIRED")
        self.connection = connection

    def register(
        self,
        *,
        namespace: str,
        entity: str,
        source: tuple[object, ...],
        evidence: str,
        valid_from: datetime,
        valid_to: datetime | None = None,
        canonical_id: int | None = None,
    ) -> int:
        key = source_key(source)
        # Khóa ghép employee_qualification được phân giải từ hai thành phần khóa ngoại.
        if entity == "employee_qualification" or len(key) != 1:
            raise IdentityConfigurationError("COMPOSITE_IDENTITY_REQUIRES_COMPONENTS")
        encoded = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode()) > 512 or len(namespace) > 100 or len(entity) > 80:
            raise IdentityConfigurationError("SOURCE_ID_TOO_LONG")
        prototype = CrosswalkEntry(
            namespace,
            entity,
            key,
            canonical_id if canonical_id is not None else 1,
            evidence,
            valid_from,
            valid_to,
        )
        identity_entity = "employee" if entity == "lecturer" else entity
        if entity == "lecturer" and canonical_id is None:
            raise IdentityConfigurationError("SHARED_IDENTITY_REQUIRED")
        query = select(CROSSWALK).where(
            CROSSWALK.c.namespace == namespace,
            CROSSWALK.c.entity == entity,
            CROSSWALK.c.source_key == encoded,
        )

        def existing_id(row: RowMapping) -> int:
            existing = _entry(row)
            if (
                (canonical_id is not None and existing.canonical_id != canonical_id)
                or existing.evidence != prototype.evidence
                or utc(existing.valid_from) != utc(prototype.valid_from)
                or existing.valid_to != prototype.valid_to
            ):
                raise IdentityConfigurationError("CROSSWALK_IMMUTABLE_CONFLICT")
            return existing.canonical_id

        existing = self.connection.execute(query).mappings().first()
        if existing is not None:
            return existing_id(existing)
        try:
            with self.connection.begin_nested():
                allocated: int
                if canonical_id is None:
                    allocated = self.connection.execute(
                        insert(IDENTITIES)
                        .values(entity=identity_entity)
                        .returning(IDENTITIES.c.canonical_id)
                    ).scalar_one()
                else:
                    allocated = canonical_id
                    found = self.connection.execute(
                        select(IDENTITIES.c.entity).where(IDENTITIES.c.canonical_id == allocated)
                    ).scalar_one_or_none()
                    if found != identity_entity:
                        raise IdentityConfigurationError("CANONICAL_ENTITY_MISMATCH")
                self.connection.execute(
                    insert(CROSSWALK).values(
                        namespace=namespace,
                        entity=entity,
                        source_key=encoded,
                        canonical_id=allocated,
                        evidence=evidence,
                        valid_from=utc(valid_from).isoformat(),
                        valid_to=utc(valid_to).isoformat() if valid_to else None,
                    )
                )
                return int(allocated)
        except IntegrityError:
            # Một đăng ký đồng thời khác đã thành công. Savepoint đã hoàn tác phần cấp phát của ta.
            existing = self.connection.execute(query).mappings().first()
            if existing is None:
                raise
            return existing_id(existing)

    def freeze(self, version: str) -> CrosswalkSnapshot:
        rows = self.connection.execute(select(CROSSWALK)).mappings().all()
        snapshot = CrosswalkSnapshot(version, tuple(_entry(row) for row in rows))
        if len(version) > 100:
            raise IdentityConfigurationError("CROSSWALK_VERSION_INVALID")
        try:
            with self.connection.begin_nested():
                self.connection.execute(
                    insert(RELEASES).values(
                        version=version,
                        fingerprint=snapshot.fingerprint,
                        payload=json.dumps(snapshot.payload(), ensure_ascii=False),
                    )
                )
        except IntegrityError:
            existing = self.load(version)
            if existing.fingerprint != snapshot.fingerprint:
                raise IdentityConfigurationError("CROSSWALK_VERSION_CONFLICT") from None
            return existing
        return snapshot

    def load(self, version: str) -> CrosswalkSnapshot:
        row = (
            self.connection.execute(select(RELEASES).where(RELEASES.c.version == version))
            .mappings()
            .first()
        )
        if row is None:
            raise IdentityConfigurationError("CROSSWALK_VERSION_NOT_FOUND")
        entries = tuple(
            CrosswalkEntry(
                e["namespace"],
                e["entity"],
                tuple(e["source"]),
                e["canonical_id"],
                e["evidence"],
                datetime.fromisoformat(e["valid_from"]),
                datetime.fromisoformat(e["valid_to"]) if e["valid_to"] else None,
            )
            for e in json.loads(row["payload"])
        )
        snapshot = CrosswalkSnapshot(version, entries)
        if snapshot.fingerprint != row["fingerprint"]:
            raise IdentityConfigurationError("CROSSWALK_FINGERPRINT_MISMATCH")
        return snapshot
