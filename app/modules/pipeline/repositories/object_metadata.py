"""Metadata Silver trên kho đối tượng; PostgreSQL chỉ cung cấp mutex giao dịch.

Không tạo bảng hay lưu bản ghi Silver trong PostgreSQL. SQLite là adapter cho
các unit test hiện có; môi trường PostgreSQL luôn dùng MinIO.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

from minio.error import S3Error
from sqlalchemy import Table, insert, select, text, update

from app.core.config import settings
from app.modules.pipeline.models.profiles import (
    SilverDomainOwner,
    SilverProfileApproval,
    SilverProfileRevision,
    SilverSourceProfile,
)
from app.providers import silver_writer_client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

CONFIG_MODELS = {
    "profiles": SilverSourceProfile,
    "revisions": SilverProfileRevision,
    "grants": SilverDomainOwner,
    "approvals": SilverProfileApproval,
}


def key_for(row: dict[str, Any], kind: str) -> str:
    if kind in {"revisions", "approvals"}:
        return f"{row['profile_id']}:{row['revision']}"
    return str(row["grant_id"] if kind == "grants" else row["profile_id"])


class ObjectMetadata:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        namespace = hashlib.sha256(str(engine.url.database).encode()).hexdigest()
        self.prefix = f"silver/control/v1/{namespace}/"
        self.client = silver_writer_client()

    def read(self, name: str) -> dict[str, Any] | None:
        response = None
        try:
            response = self.client.get_object(
                settings.minio_bucket_silver, self.prefix + name + ".json"
            )
            payload = response.read()
            if len(payload) > 32 * 1024 * 1024:
                raise ValueError("SILVER_METADATA_LIMIT_EXCEEDED")
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("SILVER_METADATA_INVALID")
            return value
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return None
            raise
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def write(self, name: str, value: dict[str, Any]) -> None:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, default=self._encode
        ).encode()
        if len(payload) > 32 * 1024 * 1024:
            raise ValueError("SILVER_METADATA_LIMIT_EXCEEDED")
        self.client.put_object(
            settings.minio_bucket_silver,
            self.prefix + name + ".json",
            BytesIO(payload),
            len(payload),
            content_type="application/json",
        )
        if self.read(name) != json.loads(payload):
            raise ValueError("SILVER_METADATA_WRITE_NOT_VERIFIED")

    @staticmethod
    def _encode(value: Any) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        raise TypeError(type(value).__name__)

    def documents(self, directory: str) -> list[dict[str, Any]]:
        prefix = self.prefix + directory + "/"
        result = []
        for item in self.client.list_objects(
            settings.minio_bucket_silver, prefix=prefix, recursive=True
        ):
            if item.object_name and item.object_name.endswith(".json"):
                value = self.read(item.object_name[len(self.prefix) : -5])
                if value is not None:
                    result.append(value)
        return result


def metadata_lock(connection: Connection, name: str) -> None:
    if connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "silver-metadata:" + name},
        )


def configuration(connection: Connection) -> dict[str, dict[str, Any]]:
    if connection.dialect.name == "sqlite":
        return {
            kind: {
                key_for(dict(r), kind): dict(r)
                for r in connection.execute(select(model)).mappings()
            }
            for kind, model in CONFIG_MODELS.items()
        }
    state = ObjectMetadata(connection.engine).read("configuration") or {}
    result = {kind: state.get(kind, {}) for kind in CONFIG_MODELS}
    for values in result.values():
        for row in values.values():
            for name in ("updated_at", "created_at", "granted_at", "approved_at", "revoked_at"):
                if isinstance(row.get(name), str):
                    row[name] = datetime.fromisoformat(row[name])
    return result


@contextmanager
def edit_configuration(connection: Connection) -> Iterator[dict[str, dict[str, Any]]]:
    metadata_lock(connection, "configuration")
    state = configuration(connection)
    previous = deepcopy(state)
    yield state
    if state == previous:
        return
    if connection.dialect.name != "sqlite":
        ObjectMetadata(connection.engine).write("configuration", state)
        return
    for kind, model in CONFIG_MODELS.items():
        table = cast("Table", model.__table__)
        for key, row in state[kind].items():
            if key not in previous[kind]:
                connection.execute(insert(table).values(**row))
            elif row != previous[kind][key]:
                if kind == "revisions":
                    raise ValueError("SILVER_PROFILE_REVISION_IMMUTABLE")
                conditions = [column == row[column.name] for column in table.primary_key]
                connection.execute(update(table).where(*conditions).values(**row))


def worker_heartbeats(engine: Engine) -> list[dict[str, Any]]:
    rows = ObjectMetadata(engine).documents("workers")
    for row in rows:
        row["last_heartbeat_at"] = datetime.fromisoformat(row["last_heartbeat_at"])
        row["healthy"] = (
            datetime.now(UTC) - row["last_heartbeat_at"].astimezone(UTC)
        ).total_seconds() < 45
    return sorted(rows, key=lambda r: r["last_heartbeat_at"], reverse=True)


def record_heartbeat(engine: Engine, worker_id: str, job_id: str | None) -> None:
    store = ObjectMetadata(engine)
    name = hashlib.sha256(worker_id.encode()).hexdigest()
    store.write(
        "workers/" + name,
        {
            "worker_id": worker_id,
            "current_job_id": job_id,
            "last_heartbeat_at": datetime.now(UTC).isoformat(),
        },
    )
