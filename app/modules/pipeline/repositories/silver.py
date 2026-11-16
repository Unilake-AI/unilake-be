"""Phiên bản, head và lịch sử Silver nằm trên MinIO; Gold giữ input đã xác nhận."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import text

from app.modules.pipeline.repositories.object_metadata import ObjectMetadata, metadata_lock
from app.modules.pipeline.repositories.silver_sqlite import HEADS as HEADS
from app.modules.pipeline.repositories.silver_sqlite import RUNS as RUNS
from app.modules.pipeline.repositories.silver_sqlite import SILVER_TABLES as SILVER_TABLES
from app.modules.pipeline.repositories.silver_sqlite import SNAPSHOTS as SNAPSHOTS
from app.modules.pipeline.repositories.silver_sqlite import SqliteSilverRepository
from app.modules.pipeline.repositories.silver_storage import ObjectRef, SilverError
from app.repositories.silver_governance_repo import approval_required

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)


def object_name(kind: str, value: str) -> str:
    return kind + "/" + hashlib.sha256(value.encode()).hexdigest()


class SilverRepository:
    def __new__(cls, engine: Engine, **kwargs: Any) -> Any:
        if engine.dialect.name == "sqlite":
            return SqliteSilverRepository(engine, **kwargs)
        return super().__new__(cls)

    def __init__(
        self, engine: Engine, *, on_published: Callable[[Connection, str], None] | None = None
    ) -> None:
        self.engine, self.on_published = engine, on_published
        self.store = ObjectMetadata(engine)

    def _state(self, scope: str) -> dict[str, Any]:
        return self.store.read(object_name("scopes", scope)) or {
            "scope": scope,
            "head": None,
            "runs": {},
            "snapshots": {},
            "requests": {},
            "activations": [],
        }

    def _write(self, state: dict[str, Any]) -> None:
        self.store.write(object_name("scopes", state["scope"]), state)

    def _scope(self, run_id: str) -> str:
        locator = self.store.read(object_name("versions", run_id))
        if locator is None:
            raise SilverError("SNAPSHOT_NOT_PUBLISHED")
        return str(locator["scope"])

    @staticmethod
    def _db_head(connection: Connection, scope: str) -> str | None:
        return connection.execute(
            text("SELECT silver_snapshot_id FROM app.gold_scope WHERE scope=:scope"),
            {"scope": scope},
        ).scalar_one_or_none()

    def head(self, scope: str) -> str | None:
        with self.engine.begin() as connection:
            return self._db_head(connection, scope) or self._state(scope)["head"]

    def _is_published(self, connection: Connection, snapshot: dict[str, Any]) -> bool:
        if snapshot.get("status") == "PUBLISHED":
            return True
        return (
            connection.execute(
                text(
                    "SELECT 1 FROM app.gold_job WHERE scope=:scope AND silver_snapshot_id=:id LIMIT 1"
                ),
                {"scope": snapshot["scope"], "id": snapshot["snapshot_id"]},
            ).first()
            is not None
        )

    def find(self, scope: str, request_hash: str) -> dict[str, Any] | None:
        state = self._state(scope)
        snapshot_id = state["requests"].get(request_hash)
        snapshot = state["snapshots"].get(snapshot_id)
        if snapshot is None:
            return None
        with self.engine.begin() as connection:
            return dict(snapshot) if self._is_published(connection, snapshot) else None

    def snapshot(self, snapshot_id: str, scope: str) -> dict[str, Any]:
        snapshot = self._state(scope)["snapshots"].get(snapshot_id)
        with self.engine.begin() as connection:
            if snapshot is None or not self._is_published(connection, snapshot):
                raise SilverError("SNAPSHOT_NOT_PUBLISHED")
        return dict(snapshot)

    def snapshots(self, scope: str) -> list[dict[str, Any]]:
        with self.engine.begin() as connection:
            return [
                dict(row)
                for row in self._state(scope)["snapshots"].values()
                if self._is_published(connection, row)
            ]

    def snapshot_by_id(self, snapshot_id: str) -> dict[str, Any]:
        return self.snapshot(snapshot_id, self._scope(snapshot_id))

    def start(self, run_id: str, scope: str, request_hash: str, base: str | None) -> None:
        with self.engine.begin() as connection:
            metadata_lock(connection, scope)
            state = self._state(scope)
            if run_id in state["runs"]:
                raise SilverError("RUN_ALREADY_EXISTS")
            state["runs"][run_id] = {
                "run_id": run_id,
                "scope": scope,
                "request_hash": request_hash,
                "base_snapshot": base,
                "status": "PREPARING",
                "issues": "[]",
                "manifest_ref": None,
            }
            self.store.write(object_name("versions", run_id), {"scope": scope})
            self._write(state)

    def stage(self, run_id: str, manifest: ObjectRef) -> None:
        scope = self._scope(run_id)
        with self.engine.begin() as connection:
            metadata_lock(connection, scope)
            state = self._state(scope)
            run = state["runs"][run_id]
            if run["status"] != "PREPARING":
                raise SilverError("INVALID_RUN_STATE")
            run.update(status="STAGED", manifest_ref=json.dumps(asdict(manifest)))
            self._write(state)

    def fail(self, run_id: str, issues: list[dict[str, Any]]) -> None:
        scope = self._scope(run_id)
        with self.engine.begin() as connection:
            metadata_lock(connection, scope)
            state = self._state(scope)
            run = state["runs"][run_id]
            if run["status"] in {"PREPARING", "STAGED", "FAILED"}:
                run.update(status="FAILED", issues=json.dumps(issues))
                self._write(state)

    def run(self, run_id: str) -> dict[str, Any]:
        row = dict(self._state(self._scope(run_id))["runs"][run_id])
        with self.engine.begin() as connection:
            if row["status"] in {"STAGED", "FAILED"} and self._is_published(
                connection, {**row, "snapshot_id": run_id}
            ):
                row["status"] = "PUBLISHED"
        return row

    @staticmethod
    def _queue_gold(connection: Connection, snapshot: dict[str, Any]) -> None:
        if not snapshot["scope"].startswith("production:"):
            return
        connection.execute(
            text("SELECT app.enqueue_gold_object(:scope,:snapshot,CAST(:manifest AS jsonb))"),
            {
                "scope": snapshot["scope"],
                "snapshot": snapshot["snapshot_id"],
                "manifest": snapshot["manifest_ref"]
                if isinstance(snapshot["manifest_ref"], str)
                else json.dumps(snapshot["manifest_ref"]),
            },
        )

    def publish(
        self, run_id: str, *, authorize: Callable[[Connection, dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        if approval_required() and authorize is None:
            raise SilverError("SILVER_PROFILE_APPROVAL_REQUIRED")
        scope = self._scope(run_id)
        with self.engine.begin() as connection:
            metadata_lock(connection, scope)
            state = self._state(scope)
            run = state["runs"][run_id]
            previous = state["snapshots"].get(state["requests"].get(run["request_hash"]))
            if previous is not None and self._is_published(connection, previous):
                snapshot = previous
                reused = True
            else:
                reused = False
                if run["status"] not in {"STAGED", "FAILED"} or not run["manifest_ref"]:
                    raise SilverError("RUN_NOT_STAGED")
                current = self._db_head(connection, scope) or state["head"]
                if current != run["base_snapshot"]:
                    raise SilverError("ACTIVE_SNAPSHOT_CHANGED")
                snapshot = {
                    "snapshot_id": run_id,
                    "scope": scope,
                    "request_hash": run["request_hash"],
                    "manifest_ref": run["manifest_ref"],
                    "status": "STAGED",
                }
            if authorize is not None:
                authorize(connection, snapshot)
            state["snapshots"][snapshot["snapshot_id"]] = snapshot
            state["requests"][run["request_hash"]] = snapshot["snapshot_id"]
            # Durable intent trước khi commit SQL. STAGED không được coi là đã công bố.
            self._write(state)
            if self.on_published is not None:
                self.on_published(connection, snapshot["snapshot_id"])
            if not reused:
                self._queue_gold(connection, snapshot)
        # Gold input pointer và callback đã commit. Nếu ghi cache cuối lỗi, các API
        # xác minh lại bằng Gold input đã commit; không báo thất bại giả hoặc công bố lại.
        try:
            with self.engine.begin() as connection:
                metadata_lock(connection, scope)
                state = self._state(scope)
                state["snapshots"][snapshot["snapshot_id"]]["status"] = "PUBLISHED"
                state["runs"][run_id]["status"] = "REUSED" if reused else "PUBLISHED"
                if not reused:
                    state["head"] = self._db_head(connection, scope) or snapshot["snapshot_id"]
                self._write(state)
        except Exception as exc:
            logger.warning("Metadata cache cần hoàn tất (%s)", type(exc).__name__)
        return {
            key: snapshot[key] for key in ("snapshot_id", "scope", "request_hash", "manifest_ref")
        }

    def activate(
        self,
        snapshot_id: str,
        scope: str,
        expected: str | None,
        actor: str,
        authorize: Callable[[Connection, dict[str, Any]], None] | None = None,
    ) -> None:
        snapshot = self.snapshot(snapshot_id, scope)
        with self.engine.begin() as connection:
            metadata_lock(connection, scope)
            state = self._state(scope)
            if (self._db_head(connection, scope) or state["head"]) != expected:
                raise SilverError("ACTIVE_SNAPSHOT_CHANGED")
            if authorize is not None:
                authorize(connection, snapshot)
            self._queue_gold(connection, snapshot)
        with self.engine.begin() as connection:
            metadata_lock(connection, scope)
            state = self._state(scope)
            state["head"] = self._db_head(connection, scope) or snapshot_id
            state["activations"].append(
                {
                    "activation_id": str(uuid4()),
                    "scope": scope,
                    "activated_at": datetime.now(UTC).isoformat(),
                    "snapshot_id": snapshot_id,
                    "previous_snapshot_id": expected,
                    "activated_by": actor,
                }
            )
            self._write(state)
