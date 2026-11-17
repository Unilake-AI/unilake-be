"""Manager V1: boundaries, metadata reads and SQL execution without an external AI call."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.exceptions import AppException
from app.modules.manager import ai, exploration, history
from app.modules.manager.access import ManagerAccess
from app.modules.manager.schemas import ManagerContext, ReportFilters
from tests.test_manager_api import environment  # noqa: F401


def manager():
    return ManagerAccess(
        ManagerContext(
            user_id=11,
            roles=["HR_MANAGER"],
            domains=[],
            departments=[],
            default_domain="hr",
            note="",
        ),
        (("hr", 7),),
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE manager_metrics",
        "SELECT * FROM manager_metrics; SELECT 1",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT read_blob('/etc/passwd') FROM manager_metrics",
        "SELECT * FROM app.app_user",
        "SELECT * FROM other.manager_metrics",
        "SELECT * FROM manager_metrics JOIN manager_metrics m ON TRUE",
        "SELECT (SELECT 1) FROM manager_metrics",
        "SELECT * FROM manager_metrics UNION SELECT * FROM manager_metrics",
        "SELECT sum(value) FROM manager_metrics",
        "SELECT * INTO copy FROM manager_metrics",
        "SELECT * FROM manager_metrics WHERE pg_sleep(5)=1",
    ],
)
def test_reject_unsafe_or_unscoped_sql(sql):
    with pytest.raises(AppException):
        ai.validate_sql(sql)


def test_executes_real_scoped_aggregates_without_inventing_answers():
    sql = ai.validate_sql(
        "SELECT dataset_id,scope,entity,value FROM manager_metrics WHERE entity='employee' AND metric='record_count'"
    )
    rows = [
        dict(
            dataset_id=31,
            dataset_name="HR",
            domain="hr",
            scope="source-a",
            entity="employee",
            metric="record_count",
            group_field=None,
            group_value=None,
            value=12,
            finished_at="2026-11-17",
        )
    ]
    result, truncated = ai.execute(sql, rows)
    assert result == [dict(dataset_id=31, scope="source-a", entity="employee", value=12)]
    assert not truncated
    assert ai.execute(sql, [])[0] == []


def test_result_bound_is_enforced_even_if_model_omits_limit():
    row = dict(
        dataset_id=31,
        dataset_name="HR",
        domain="hr",
        scope="s",
        entity="employee",
        metric="record_count",
        group_field=None,
        group_value=None,
        value=0,
        finished_at="2026-11-17",
    )
    result, truncated = ai.execute(
        ai.validate_sql("SELECT * FROM manager_metrics LIMIT 999999"), [row] * 202
    )
    assert len(result) == 200
    assert truncated


def test_history_rechecks_current_dataset_ownership_and_user():
    clause, params = history.history_scope(manager(), ReportFilters(domain="hr"))
    assert "h.app_user_id=:user" in clause
    assert "jsonb_array_elements_text(h.dataset_ids)" in clause
    assert "d.status='ACTIVE'" in clause
    assert "owner_department_id" in clause
    assert params["user"] == 11
    assert params["department_0"] == 7
    with pytest.raises(AppException):
        history.history_scope(manager(), ReportFilters(domain="academic"))


@pytest.mark.parametrize(
    "path",
    [
        "catalog?domain=academic",
        "query-history?domain=academic",
        "analysis-options?domain=academic",
        "publication-trend?domain=academic",
    ],
)
def test_metadata_cannot_expand_domain(environment, path):
    client, *_, db = environment
    assert client.get("/api/v1/manager/" + path).status_code == 403
    db.execute.assert_not_called()


@pytest.mark.parametrize(
    "path",
    ["catalog", "catalog/1", "catalog/1/lineage", "query-history", "query-history/" + str(uuid4())],
)
def test_new_manager_routes_require_manager_role(environment, path):
    client, user, *_ = environment
    user["roles"] = ["HR_STAFF"]
    assert client.get("/api/v1/manager/" + path).status_code == 403


@pytest.mark.asyncio
async def test_unknown_dataset_cannot_leak_lineage(monkeypatch):
    db = AsyncMock()
    monkeypatch.setattr(
        exploration,
        "catalog",
        AsyncMock(return_value=exploration.ExplorerPage(items=[], total=0, limit=1, offset=0)),
    )
    with pytest.raises(AppException) as exc:
        await exploration.lineage(db, manager(), 999)
    assert exc.value.status_code == 404
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_query_scopes_metadata_before_reading_fields():
    db = AsyncMock()
    result = Mock()
    result.scalar_one.return_value = 0
    result.mappings.return_value.all.return_value = []
    db.execute.return_value = result
    page = await exploration.catalog(db, manager(), ReportFilters())
    assert page.items == []
    for call in db.execute.await_args_list:
        assert call.args[1]["department_0"] == 7
        assert "d.status='ACTIVE'" in str(call.args[0])


@pytest.mark.asyncio
async def test_ai_missing_configuration_is_explicit(monkeypatch):
    monkeypatch.setattr(ai.settings, "llm_provider", "mock")
    with pytest.raises(AppException) as exc:
        await ai.generate_sql("Số nhân viên?", ["hr"])
    assert exc.value.code == "NLQ_UNAVAILABLE"
