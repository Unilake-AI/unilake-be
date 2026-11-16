"""Sao chép và kiểm chứng metadata Silver trước khi cho phép bỏ bảng PostgreSQL."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from app.modules.pipeline.repositories.object_metadata import ObjectMetadata, key_for, metadata_lock
from app.modules.pipeline.repositories.silver import object_name

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

LEGACY_TABLES = (
    "silver_source_profile",
    "silver_profile_revision",
    "silver_domain_owner",
    "silver_profile_approval",
    "silver_run",
    "silver_snapshot",
    "silver_head",
    "silver_activation",
    "silver_worker_heartbeat",
)
CONFIG_TABLES = {
    "silver_source_profile": "profiles",
    "silver_profile_revision": "revisions",
    "silver_domain_owner": "grants",
    "silver_profile_approval": "approvals",
}


def merge_verified(values: dict[str, Any], key: str, row: dict[str, Any]) -> None:
    """Không cấp checkpoint DROP khi bản đích cùng khóa đã khác dữ liệu nguồn."""

    def normalized(value: Any) -> Any:
        if isinstance(value, dict):
            return {name: normalized(item) for name, item in value.items()}
        if isinstance(value, list):
            return [normalized(item) for item in value]
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                if parsed.tzinfo is not None:
                    return parsed.astimezone(UTC).isoformat()
            except ValueError:
                pass
        return value

    if key in values and normalized(values[key]) != normalized(row):
        raise ValueError("SILVER_METADATA_EXPORT_CONFLICT")
    values[key] = row


def export_metadata(engine: Engine) -> dict[str, int]:
    store = ObjectMetadata(engine)
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        existing = [
            name
            for name in LEGACY_TABLES
            if connection.execute(
                text("SELECT to_regclass(:table)"), {"table": "app." + name}
            ).scalar_one()
        ]
        for name in existing:
            connection.execute(text("LOCK TABLE app." + name + " IN SHARE MODE"))
        rows = {
            name: [
                dict(r) for r in connection.execute(text("SELECT * FROM app." + name)).mappings()
            ]
            for name in existing
        }
        archive = {
            name: [
                {
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key, value in row.items()
                }
                for row in values
            ]
            for name, values in rows.items()
        }
        archive_name = "legacy-export/" + datetime.now().strftime("%Y%m%dT%H%M%S%f")
        store.write(archive_name, {"tables": archive})
        metadata_lock(connection, "configuration")
        config = store.read("configuration") or {kind: {} for kind in CONFIG_TABLES.values()}
        for table, kind in CONFIG_TABLES.items():
            for row in archive.get(table, []):
                key = key_for(row, kind)
                merge_verified(config[kind], key, row)
        store.write("configuration", config)
        scopes = {
            r["scope"]
            for table in ("silver_run", "silver_snapshot", "silver_head", "silver_activation")
            for r in archive.get(table, [])
        }
        for scope in scopes:
            metadata_lock(connection, scope)
            state = store.read(object_name("scopes", scope)) or {
                "scope": scope,
                "head": None,
                "runs": {},
                "snapshots": {},
                "requests": {},
                "activations": [],
            }
            for row in archive.get("silver_run", []):
                if row["scope"] == scope:
                    merge_verified(state["runs"], row["run_id"], row)
                    store.write(object_name("versions", row["run_id"]), {"scope": scope})
            for row in archive.get("silver_snapshot", []):
                if row["scope"] == scope:
                    merge_verified(
                        state["snapshots"], row["snapshot_id"], {**row, "status": "PUBLISHED"}
                    )
                    if (
                        state["requests"].get(row["request_hash"], row["snapshot_id"])
                        != row["snapshot_id"]
                    ):
                        raise ValueError("SILVER_METADATA_EXPORT_CONFLICT")
                    state["requests"][row["request_hash"]] = row["snapshot_id"]
                    store.write(object_name("versions", row["snapshot_id"]), {"scope": scope})
            if state["head"] is None:
                state["head"] = next(
                    (
                        r["snapshot_id"]
                        for r in archive.get("silver_head", [])
                        if r["scope"] == scope
                    ),
                    None,
                )
            seen = {r.get("activation_id") for r in state["activations"]}
            state["activations"].extend(
                r
                for r in archive.get("silver_activation", [])
                if r["scope"] == scope and r["activation_id"] not in seen
            )
            store.write(object_name("scopes", scope), state)
        for row in archive.get("silver_worker_heartbeat", []):
            key = hashlib.sha256(row["worker_id"].encode()).hexdigest()
            if store.read("workers/" + key) is None:
                store.write("workers/" + key, row)
        # Checkpoint chứa fingerprint PostgreSQL của từng bảng; migration DROP phải
        # tính lại và so khớp, không dựa vào việc "đã từng export" để bỏ dữ liệu mới.
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS app.storage_migration_checkpoint(table_name text PRIMARY KEY,row_count bigint NOT NULL,fingerprint text NOT NULL,archive_key text NOT NULL,verified_at timestamptz NOT NULL DEFAULT now())"
            )
        )
        for name in existing:
            count, fingerprint = connection.execute(
                text(
                    "SELECT count(*),md5(coalesce(string_agg(row_to_json(t)::text,E'\\n' ORDER BY row_to_json(t)::text),'')) FROM app."
                    + name
                    + " t"
                )
            ).one()
            connection.execute(
                text(
                    "INSERT INTO app.storage_migration_checkpoint(table_name,row_count,fingerprint,archive_key) VALUES(:name,:count,:fingerprint,:archive) ON CONFLICT(table_name) DO UPDATE SET row_count=EXCLUDED.row_count,fingerprint=EXCLUDED.fingerprint,archive_key=EXCLUDED.archive_key,verified_at=now()"
                ),
                {
                    "name": name,
                    "count": count,
                    "fingerprint": fingerprint,
                    "archive": store.prefix + archive_name + ".json",
                },
            )
    return {name: len(values) for name, values in rows.items()}
