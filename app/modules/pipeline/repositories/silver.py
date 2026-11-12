"""Quản lý trạng thái công bố dữ liệu riêng với việc lưu tệp."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Table, insert, select, update
from sqlalchemy.exc import IntegrityError

from app.modules.pipeline.models.silver import SilverHead, SilverRun, SilverSnapshot
from app.modules.pipeline.repositories.silver_storage import ObjectRef, SilverError
from app.repositories.silver_governance_repo import approval_required

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Connection, Engine

RUNS = cast("Table", SilverRun.__table__)
SNAPSHOTS = cast("Table", SilverSnapshot.__table__)
HEADS = cast("Table", SilverHead.__table__)
SILVER_TABLES = (RUNS, SNAPSHOTS, HEADS)


class SilverRepository:
    def __init__(
        self, engine: Engine, *, on_published: Callable[[Connection, str], None] | None = None
    ) -> None:
        self.engine = engine
        self.on_published = on_published

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

    def publish(
        self, run_id: str, *, authorize: Callable[[Connection, dict[str, Any]], None] | None = None
    ) -> dict[str, Any]:
        # Thông tin trong PostgreSQL quyết định bộ dữ liệu nào đang được sử dụng.
        # Tệp có trên MinIO chưa có nghĩa là dữ liệu đã được công bố thành công.
        if approval_required() and authorize is None:
            raise SilverError("SILVER_PROFILE_APPROVAL_REQUIRED")
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
                    if authorize is not None:
                        authorize(conn, dict(existing))
                    if existing["snapshot_id"] != run_id:
                        conn.execute(
                            update(RUNS).where(RUNS.c.run_id == run_id).values(status="REUSED")
                        )
                    if self.on_published is not None:
                        self.on_published(conn, existing["snapshot_id"])
                    return dict(existing)
                if run["status"] not in {"STAGED", "FAILED"} or not run["manifest_ref"]:
                    raise SilverError("RUN_NOT_STAGED")
                if authorize is not None:
                    authorize(conn, {**dict(run), "snapshot_id": run_id})
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
                # Chỉ công bố khi bộ dữ liệu hiện hành chưa thay đổi kể từ lúc bắt đầu.
                # Nếu lần chạy khác đã công bố dữ liệu mới, hủy toàn bộ thay đổi của lượt này.
                if changed.rowcount != 1:
                    raise SilverError("ACTIVE_SNAPSHOT_CHANGED")
                conn.execute(
                    update(RUNS)
                    .where(RUNS.c.run_id == run_id)
                    .values(status="PUBLISHED", issues="[]")
                )
                # Lưu kết quả làm sạch cùng lúc với việc công bố bộ dữ liệu mới.
                if self.on_published is not None:
                    self.on_published(conn, run_id)
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
                if authorize is not None:
                    authorize(conn, retry_existing)
                conn.execute(update(RUNS).where(RUNS.c.run_id == run_id).values(status="REUSED"))
                if self.on_published is not None:
                    self.on_published(conn, retry_existing["snapshot_id"])
            return retry_existing
