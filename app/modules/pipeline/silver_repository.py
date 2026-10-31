"""Giao dịch metadata quyết định dữ liệu nào được nhìn thấy. Kho đối tượng không thuộc giao dịch này."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.modules.pipeline.silver_models import SilverHead, SilverRun, SilverSnapshot
from app.modules.pipeline.silver_storage import ObjectRef, SilverError

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

RUNS = cast("Table", SilverRun.__table__)
SNAPSHOTS = cast("Table", SilverSnapshot.__table__)
HEADS = cast("Table", SilverHead.__table__)
SILVER_TABLES = (RUNS, SNAPSHOTS, HEADS)


class SilverRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def head(self, scope: str) -> str | None:
        with self.engine.begin() as conn:
            return conn.execute(
                select(HEADS.c.snapshot_id).where(HEADS.c.scope == scope)
            ).scalar_one_or_none()

    def find(self, scope: str, request_hash: str) -> dict[str, Any] | None:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(SNAPSHOTS).where(
                        SNAPSHOTS.c.scope == scope, SNAPSHOTS.c.request_hash == request_hash
                    )
                )
                .mappings()
                .first()
            )
            return dict(row) if row else None

    def snapshot(self, snapshot_id: str, scope: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(SNAPSHOTS).where(
                        SNAPSHOTS.c.snapshot_id == snapshot_id, SNAPSHOTS.c.scope == scope
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise SilverError("SNAPSHOT_NOT_PUBLISHED")
            return dict(row)

    def start(self, run_id: str, scope: str, request_hash: str, base: str | None) -> None:
        with self.engine.begin() as conn:
            try:
                with conn.begin_nested():
                    conn.execute(insert(HEADS).values(scope=scope, snapshot_id=None))
            except IntegrityError:
                pass
            conn.execute(
                insert(RUNS).values(
                    run_id=run_id,
                    scope=scope,
                    request_hash=request_hash,
                    base_snapshot=base,
                    status="PREPARING",
                    issues="[]",
                )
            )

    def stage(self, run_id: str, manifest: ObjectRef) -> None:
        with self.engine.begin() as conn:
            changed = conn.execute(
                update(RUNS)
                .where(RUNS.c.run_id == run_id, RUNS.c.status == "PREPARING")
                .values(status="STAGED", manifest_ref=json.dumps(asdict(manifest)))
            )
            if changed.rowcount != 1:
                raise SilverError("INVALID_RUN_STATE")

    def fail(self, run_id: str, issues: list[dict[str, Any]]) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                update(RUNS)
                .where(
                    RUNS.c.run_id == run_id, RUNS.c.status.in_(("PREPARING", "STAGED", "FAILED"))
                )
                .values(status="FAILED", issues=json.dumps(issues))
            )

    def run(self, run_id: str) -> dict[str, Any]:
        with self.engine.begin() as conn:
            row = conn.execute(select(RUNS).where(RUNS.c.run_id == run_id)).mappings().one()
            return dict(row)

    def publish(self, run_id: str) -> dict[str, Any]:
        # PostgreSQL quyết định snapshot nào được nhìn thấy qua active head.
        # File trên MinIO có thể đã tồn tại, nhưng chưa được xem là công bố thành công.
        try:
            with self.engine.begin() as conn:
                run = conn.execute(select(RUNS).where(RUNS.c.run_id == run_id)).mappings().one()
                existing = (
                    conn.execute(
                        select(SNAPSHOTS).where(
                            SNAPSHOTS.c.scope == run["scope"],
                            SNAPSHOTS.c.request_hash == run["request_hash"],
                        )
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["snapshot_id"] != run_id:
                        conn.execute(
                            update(RUNS).where(RUNS.c.run_id == run_id).values(status="REUSED")
                        )
                    return dict(existing)
                if run["status"] not in {"STAGED", "FAILED"} or not run["manifest_ref"]:
                    raise SilverError("RUN_NOT_STAGED")
                conn.execute(
                    insert(SNAPSHOTS).values(
                        snapshot_id=run_id,
                        scope=run["scope"],
                        request_hash=run["request_hash"],
                        manifest_ref=run["manifest_ref"],
                    )
                )
                condition = (
                    HEADS.c.snapshot_id.is_(None)
                    if run["base_snapshot"] is None
                    else HEADS.c.snapshot_id == run["base_snapshot"]
                )
                changed = conn.execute(
                    update(HEADS)
                    .where(HEADS.c.scope == run["scope"], condition)
                    .values(snapshot_id=run_id)
                )
                # Chỉ đổi head khi snapshot cha vẫn đúng như lúc bắt đầu (CAS).
                # Nếu lần chạy khác đã đổi head, rollback cả transaction để tránh
                # công bố đè lên dữ liệu mới từ một snapshot cha đã lỗi thời.
                if changed.rowcount != 1:
                    raise SilverError("ACTIVE_SNAPSHOT_CHANGED")
                conn.execute(
                    update(RUNS)
                    .where(RUNS.c.run_id == run_id)
                    .values(status="PUBLISHED", issues="[]")
                )
                return {
                    "snapshot_id": run_id,
                    "scope": run["scope"],
                    "request_hash": run["request_hash"],
                    "manifest_ref": run["manifest_ref"],
                }
        except IntegrityError:
            retry_run = self.run(run_id)
            retry_existing = self.find(retry_run["scope"], retry_run["request_hash"])
            if retry_existing is None:
                raise
            with self.engine.begin() as conn:
                conn.execute(update(RUNS).where(RUNS.c.run_id == run_id).values(status="REUSED"))
            return retry_existing
