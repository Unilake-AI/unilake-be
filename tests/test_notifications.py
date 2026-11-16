"""Thông báo đọc đúng sự kiện, đúng người, đúng phòng và lưu trạng thái đã đọc."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Table, create_engine, event, insert
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.dependencies import get_db
from app.modules.ingestion.models import IngestionJob
from app.modules.notifications.router import current_actor, router
from app.modules.pipeline.models import DATA_QUALITY_ISSUE_EVENTS, DATA_QUALITY_ISSUES
from app.modules.staff.services.quality_issues import IssueActor
from app.modules.users.models import Department
from app.repositories.notification_repo import READS


@pytest.fixture
def environment():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def attach(connection, _record):
        connection.execute("ATTACH DATABASE ':memory:' AS app")

    for table in (
        Department.__table__,
        IngestionJob.__table__,
        DATA_QUALITY_ISSUES,
        DATA_QUALITY_ISSUE_EVENTS,
        READS,
    ):
        cast("Table", table).create(engine)
    ids = [str(uuid4()) for _ in range(3)]
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(Department),
            [
                {
                    "department_id": 7,
                    "department_code": "PNS",
                    "department_name": "Nhân sự",
                    "department_type": "Office",
                },
                {
                    "department_id": 8,
                    "department_code": "PDT",
                    "department_name": "Đào tạo",
                    "department_type": "Office",
                },
            ],
        )
        conn.execute(
            insert(DATA_QUALITY_ISSUES),
            [
                {
                    "issue_id": issue_id,
                    "dataset_id": 31,
                    "department_id": department,
                    "domain": domain,
                    "title": f"Yêu cầu {index}",
                    "description": "Không đưa mô tả vào thông báo",
                    "status": "ASSIGNED",
                    "created_by": 1,
                    "assignment_mode": "DEPARTMENT",
                    "created_at": now,
                    "updated_at": now,
                }
                for index, (issue_id, department, domain) in enumerate(
                    zip(ids, [7, 8, 8], ["hr", "hr", "academic"], strict=True), 1
                )
            ],
        )
        conn.execute(
            insert(DATA_QUALITY_ISSUE_EVENTS),
            [
                {
                    "event_id": index,
                    "issue_id": issue_id,
                    "actor_id": 1,
                    "event_type": "ASSIGNED",
                    "to_status": "ASSIGNED",
                    "details": {},
                    "created_at": now,
                }
                for index, issue_id in enumerate(ids, 1)
            ],
        )
    app = FastAPI()
    app.add_exception_handler(AppException, app_exception_handler)
    app.include_router(router)
    user = {"sub": "11", "roles": ["HR_MANAGER"]}
    state = {"actor": IssueActor(11, False, frozenset({7}), frozenset({"hr"}))}

    async def database():
        with Session(engine) as session:
            yield type("Database", (), {"execute": AsyncMock(side_effect=session.execute)})()
            session.commit()

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[current_actor] = lambda: state["actor"]
    yield TestClient(app), user, state, ids
    engine.dispose()


def test_manager_sees_own_department_and_can_mark_read_idempotently(environment):
    client, _, _, ids = environment
    response = client.get("/notifications?workspace=MANAGER")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["total"] == data["unread_count"] == 1
    assert data["items"][0]["issue_id"] == ids[0]
    assert "description" not in data["items"][0]
    for _ in range(2):
        assert client.post("/notifications/read", json={"event_ids": [1]}).status_code == 204
    assert client.get("/notifications").json()["unread_count"] == 0
    assert client.get("/notifications?unread_only=true").json()["items"] == []


def test_batch_rejects_inaccessible_event_without_marking_anything(environment):
    client, *_ = environment
    assert client.post("/notifications/read", json={"event_ids": [1, 2]}).status_code == 404
    assert client.get("/notifications").json()["unread_count"] == 1
    assert client.get("/notifications?workspace=DATA_ADMIN").status_code == 403


@pytest.mark.parametrize("role", ["ADMIN", "DATA_ADMIN"])
def test_administrators_see_all_events_and_read_state_is_per_user(environment, role):
    client, user, state, _ = environment
    user.update(sub="20", roles=[role])
    state["actor"] = IssueActor(20, True, frozenset(), frozenset())
    data = client.get(f"/notifications?workspace={role}&limit=1&offset=1").json()
    assert data["total"] == data["unread_count"] == 3
    assert data["items"][0]["event_id"] == 2
    assert client.post("/notifications/read", json={"event_ids": [1]}).status_code == 204
    user.update(sub="11", roles=["HR_MANAGER"])
    state["actor"] = IssueActor(11, False, frozenset({7}), frozenset({"hr"}))
    assert client.get("/notifications").json()["unread_count"] == 1


def test_staff_scope_and_multi_role_workspace_do_not_mix_departments(environment):
    client, user, state, _ = environment
    user["roles"] = ["HR_MANAGER", "ACADEMIC_STAFF"]
    state["actor"] = IssueActor(11, False, frozenset({7, 8}), frozenset({"hr", "academic"}))
    assert [
        item["event_id"] for item in client.get("/notifications?workspace=MANAGER").json()["items"]
    ] == [1]
    assert [
        item["event_id"] for item in client.get("/notifications?workspace=STAFF").json()["items"]
    ] == [3]
    assert client.get("/notifications").json()["total"] == 2
    state["actor"] = IssueActor(11, False, frozenset(), frozenset({"hr", "academic"}))
    assert client.get("/notifications").json()["items"] == []
    assert client.post("/notifications/read", json={"event_ids": [1]}).status_code == 404


@pytest.mark.parametrize(
    "payload", [{"event_ids": []}, {"event_ids": [0]}, {"event_ids": [1], "user_id": 20}]
)
def test_invalid_read_requests(environment, payload):
    client, *_ = environment
    assert client.post("/notifications/read", json=payload).status_code == 422
