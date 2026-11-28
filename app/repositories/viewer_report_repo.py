"""Dữ liệu báo cáo toàn trường trong ba domain của VIEWER."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, func, select, text

from app.modules.datasources.models import Dataset
from app.modules.pipeline.models import DATA_QUALITY_ISSUES
from app.modules.staff.schemas import IssueStats
from app.modules.viewer.schemas import ViewerIssue, ViewerIssuePage
from app.shared.reporting import DOMAIN_NAMES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.viewer.schemas import ViewerIssueFilters
    from app.repositories.report_read_repo import DatasetScope
    from app.shared.reporting import Domain


def dataset_scope(domain: Domain | None = None, dataset_id: int | None = None) -> DatasetScope:
    return (
        "d.status='ACTIVE' AND d.domain=ANY(CAST(:viewer_domains AS text[]))"
        " AND (CAST(:dataset AS bigint) IS NULL OR d.dataset_id=:dataset)",
        {"viewer_domains": [domain] if domain else list(DOMAIN_NAMES), "dataset": dataset_id},
    )


async def publication(db: AsyncSession, domain: Domain | None = None) -> list[dict[str, Any]]:
    clause, params = dataset_scope(domain)
    rows = await db.execute(
        text(
            """
            WITH datasets AS (
                SELECT d.dataset_id, d.domain FROM app.dataset d WHERE """
            + clause
            + """
            ), sources AS (
                SELECT d.dataset_id, d.domain, s.scope, j.finished_at,
                       coalesce(j.status='SUCCEEDED' AND j.job_id=s.active_job_id, false) AS is_current,
                       j.status='FAILED' AS gold_failed,
                       q.status IN ('FAILED', 'BLOCKED') AS sync_failed
                FROM datasets d
                JOIN app.gold_scope s ON s.dataset_id=d.dataset_id AND s.domain=d.domain
                LEFT JOIN app.gold_job j ON j.scope=s.scope AND j.generation=s.generation
                    AND j.silver_snapshot_id=s.silver_snapshot_id
                    AND j.dataset_id=s.dataset_id AND j.domain=s.domain
                LEFT JOIN app.gold_sync_job q ON q.gold_job_id=j.job_id
                    AND j.status='SUCCEEDED' AND j.job_id=s.active_job_id
            ), summary AS (
                SELECT d.domain,
                       count(DISTINCT d.dataset_id) AS datasets,
                       count(DISTINCT d.dataset_id) FILTER (WHERE s.scope IS NULL) AS datasets_without_scope,
                       count(s.scope) AS total,
                       count(*) FILTER (WHERE s.is_current) AS current,
                       count(*) FILTER (WHERE s.gold_failed) AS gold_job_failed,
                       count(*) FILTER (WHERE s.sync_failed) AS sync_failed,
                       max(s.finished_at) FILTER (WHERE s.is_current) AS last_published_at
                FROM datasets d
                LEFT JOIN sources s ON s.dataset_id=d.dataset_id AND s.domain=d.domain
                GROUP BY d.domain
            ), highlights AS (
                SELECT m.domain, m.dataset_id, m.scope, m.entity, m.value,
                       count(*) OVER (PARTITION BY m.domain) AS metric_count
                FROM summary p
                JOIN gold.current_metrics m ON m.domain=p.domain
                JOIN sources s ON s.scope=m.scope AND s.dataset_id=m.dataset_id AND s.domain=m.domain
                WHERE p.current=p.total AND p.datasets_without_scope=0 AND s.is_current
                      AND m.metric='record_count' AND m.dimensions='{}'::jsonb
            )
            SELECT p.*, h.dataset_id, h.scope, h.entity, h.value
            FROM summary p LEFT JOIN highlights h ON h.domain=p.domain AND h.metric_count=1
            """
        ),
        params,
    )
    return [dict(row) for row in rows.mappings()]


def _issue_scope(
    query: Select[Any], domain: Domain | None = None, dataset_id: int | None = None
) -> Select[Any]:
    issue = DATA_QUALITY_ISSUES.c
    query = query.join(Dataset, Dataset.dataset_id == issue.dataset_id).where(
        Dataset.domain == issue.domain,
        Dataset.status == "ACTIVE",
        issue.domain == domain if domain else issue.domain.in_(DOMAIN_NAMES),
    )
    if dataset_id is not None:
        query = query.where(issue.dataset_id == dataset_id)
    return query


async def issue_stats(db: AsyncSession) -> IssueStats:
    issue = DATA_QUALITY_ISSUES.c
    query = _issue_scope(select(issue.status, func.count())).group_by(issue.status)
    counts = dict((await db.execute(query)).all())
    return IssueStats(
        total=sum(counts.values()),
        assigned=counts.get("ASSIGNED", 0),
        pending_verification=counts.get("PENDING_VERIFICATION", 0),
        verified=counts.get("VERIFIED", 0),
    )


async def issues(db: AsyncSession, filters: ViewerIssueFilters) -> ViewerIssuePage:
    issue = DATA_QUALITY_ISSUES.c
    query = _issue_scope(
        select(
            issue.issue_id,
            issue.dataset_id,
            issue.department_id,
            issue.domain,
            issue.status,
            issue.created_at,
            issue.updated_at,
        ),
        filters.domain,
        filters.dataset_id,
    )
    if filters.status is not None:
        query = query.where(issue.status == filters.status)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    page = (
        query.order_by(issue.updated_at.desc(), issue.issue_id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    rows = (await db.execute(page)).mappings()
    return ViewerIssuePage(
        items=[ViewerIssue.model_validate(row) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )
