"""Đọc Gold hiện hành, luôn giới hạn theo lĩnh vực và chủ sở hữu dataset."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.manager.schemas import (
    AnalyticsFilters,
    GoldMetric,
    ManagerIssueFilters,
    MetricPage,
    ReportFilters,
    SourceStats,
)
from app.modules.pipeline.models import DATA_QUALITY_ISSUES
from app.modules.staff.schemas import (
    IssueDetail,
    IssueEventRead,
    IssueQuarantineRecord,
    IssueRead,
    IssueStats,
)
from app.modules.users.models import Department
from app.repositories import quality_issue_repo

if TYPE_CHECKING:
    from app.modules.manager.access import ManagerAccess

# Bao gồm Silver chưa tạo Gold, không trả số liệu Gold cũ khi Silver đã đổi.
SOURCES = """
WITH sources AS (
    SELECT h.scope,h.silver_snapshot_id,cr.dataset_id,cr.domain,
           j.job_id,j.finished_at,j.error_code,
           CASE WHEN j.status='SUCCEEDED' AND s.active_job_id=j.job_id THEN 'CURRENT'
                WHEN j.job_id IS NULL THEN 'NOT_STARTED' ELSE j.status END AS sync_status
    FROM app.gold_scope h
    JOIN LATERAL (
        SELECT c.dataset_id,c.domain FROM app.cleaning_run c
        WHERE c.silver_snapshot_id=h.silver_snapshot_id
          AND c.publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
        ORDER BY c.finished_at DESC NULLS LAST,c.run_id LIMIT 1
    ) cr ON TRUE
    LEFT JOIN app.gold_scope s ON s.scope=h.scope AND s.silver_snapshot_id=h.silver_snapshot_id
    LEFT JOIN app.gold_job j ON j.scope=s.scope AND j.generation=s.generation
)
"""


def dataset_scope(access: ManagerAccess, filters: ReportFilters) -> tuple[str, dict[str, Any]]:
    access.require_scope(filters.domain)
    params: dict[str, Any] = {"dataset": filters.dataset_id, "domain": filters.domain}
    clauses = []
    for index, (domain, department) in enumerate(access.scopes):
        clauses.append(f"(d.domain=:domain_{index} AND d.owner_department_id=:department_{index})")
        params[f"domain_{index}"] = domain
        params[f"department_{index}"] = department
    return (
        "d.status='ACTIVE' AND (" + " OR ".join(clauses) + ")"
        " AND (CAST(:dataset AS bigint) IS NULL OR d.dataset_id=:dataset)"
        " AND (CAST(:domain AS text) IS NULL OR d.domain=:domain)",
        params,
    )


async def metrics(db: AsyncSession, access: ManagerAccess, filters: AnalyticsFilters) -> MetricPage:
    clause, params = dataset_scope(access, filters)
    if filters.dimension_value is not None and not filters.metric.startswith("record_count_by_"):
        raise AppException(
            "Bộ lọc nhóm cần phép thống kê theo chiều tương ứng",
            code="INVALID_ANALYTICS_FILTER",
            status_code=422,
        )
    params.update(
        entity=filters.entity,
        metric=filters.metric,
        limit=filters.limit,
        offset=filters.offset,
        dimension=filters.metric.removeprefix("record_count_by_"),
        dimension_value=filters.dimension_value,
    )
    query = (
        " FROM gold.current_metrics m JOIN app.dataset d ON d.dataset_id=m.dataset_id AND d.domain=m.domain WHERE "
        + clause
        + " AND (CAST(:entity AS text) IS NULL OR m.entity=:entity) AND m.metric=:metric"
        " AND (CAST(:dimension_value AS text) IS NULL OR m.dimensions->>:dimension=:dimension_value)"
    )
    total = (await db.execute(text("SELECT count(*)" + query), params)).scalar_one()
    rows = (
        await db.execute(
            text(
                "SELECT m.*,d.dataset_name"
                + query
                + " ORDER BY m.scope,m.entity,m.metric,m.dimensions LIMIT :limit OFFSET :offset"
            ),
            params,
        )
    ).mappings()
    return MetricPage(
        items=[GoldMetric.model_validate(row) for row in rows],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


async def dataset_count(db: AsyncSession, access: ManagerAccess, filters: ReportFilters) -> int:
    clause, params = dataset_scope(access, filters)
    return int(
        (
            await db.execute(text("SELECT count(*) FROM app.dataset d WHERE " + clause), params)
        ).scalar_one()
    )


async def source_stats(
    db: AsyncSession, access: ManagerAccess, filters: ReportFilters
) -> SourceStats:
    clause, params = dataset_scope(access, filters)
    rows = (
        await db.execute(
            text(
                SOURCES + "SELECT s.sync_status,count(*) AS count FROM sources s"
                " JOIN app.dataset d ON d.dataset_id=s.dataset_id AND d.domain=s.domain WHERE "
                + clause
                + " GROUP BY s.sync_status"
            ),
            params,
        )
    ).mappings()
    counts = {row["sync_status"].lower(): row["count"] for row in rows}
    return SourceStats(total=sum(counts.values()), **counts)


async def last_updated(
    db: AsyncSession, access: ManagerAccess, filters: ReportFilters
) -> datetime | None:
    clause, params = dataset_scope(access, filters)
    return cast(
        "datetime | None",
        (
            await db.execute(
                text(
                    "SELECT max(m.finished_at) FROM gold.current_metrics m JOIN app.dataset d"
                    " ON d.dataset_id=m.dataset_id AND d.domain=m.domain WHERE " + clause
                ),
                params,
            )
        ).scalar_one(),
    )


def issue_query(access: ManagerAccess, filters: ManagerIssueFilters) -> Any:
    access.require_scope(filters.domain)
    c = DATA_QUALITY_ISSUES.c
    query = (
        select(DATA_QUALITY_ISSUES, Department.department_name)
        .join(Department, Department.department_id == c.department_id)
        .where(
            or_(
                *(
                    and_(c.domain == domain, c.department_id == department)
                    for domain, department in access.scopes
                )
            )
        )
    )
    for field in ("domain", "dataset_id", "department_id", "assignee_user_id", "status"):
        value = getattr(filters, field)
        if value is not None:
            query = query.where(c[field] == value)
    if filters.mine_only:
        query = query.where(c.assignee_user_id == access.context.user_id)
    return query


async def issues(
    db: AsyncSession, access: ManagerAccess, filters: ManagerIssueFilters
) -> list[IssueRead]:
    query = (
        issue_query(access, filters)
        .order_by(DATA_QUALITY_ISSUES.c.updated_at.desc(), DATA_QUALITY_ISSUES.c.issue_id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return [IssueRead.model_validate(row) for row in (await db.execute(query)).mappings()]


async def issue_stats(
    db: AsyncSession, access: ManagerAccess, filters: ManagerIssueFilters
) -> IssueStats:
    query = issue_query(access, filters).subquery()
    rows = (await db.execute(select(query.c.status, func.count()).group_by(query.c.status))).all()
    counts = {status.lower(): count for status, count in rows}
    return IssueStats(
        total=sum(counts.values()),
        assigned=counts.get("assigned", 0),
        pending_verification=counts.get("pending_verification", 0),
        verified=counts.get("verified", 0),
    )


async def issue_detail(
    db: AsyncSession, access: ManagerAccess, issue_id: UUID
) -> IssueDetail | None:
    query = issue_query(access, ManagerIssueFilters()).where(
        DATA_QUALITY_ISSUES.c.issue_id == str(issue_id)
    )
    row = (await db.execute(query)).mappings().first()
    if row is None:
        return None
    records = await quality_issue_repo.issue_records(db, str(issue_id))
    return IssueDetail(
        **row,
        quarantine_record_ids=[record["record_id"] for record in records],
        quarantine_records=[IssueQuarantineRecord.model_validate(record) for record in records],
        history=[
            IssueEventRead.model_validate(event)
            for event in await quality_issue_repo.issue_history(db, str(issue_id))
        ],
    )


async def analysis_options(
    db: AsyncSession, access: ManagerAccess, filters: ReportFilters
) -> list[dict[str, Any]]:
    clause, params = dataset_scope(access, filters)
    return [
        dict(row)
        for row in (
            await db.execute(
                text(
                    "SELECT DISTINCT m.entity,m.metric FROM gold.current_metrics m JOIN app.dataset d ON d.dataset_id=m.dataset_id AND d.domain=m.domain WHERE "
                    + clause
                    + " ORDER BY m.entity,m.metric"
                ),
                params,
            )
        ).mappings()
    ]


async def publication_trend(
    db: AsyncSession, access: ManagerAccess, filters: ReportFilters
) -> list[dict[str, Any]]:
    clause, params = dataset_scope(access, filters)
    return [
        dict(row)
        for row in (
            await db.execute(
                text(
                    "SELECT j.scope,j.finished_at,a.entity,a.value,d.dataset_name FROM app.gold_job j"
                    " JOIN gold.aggregate a ON a.job_id=j.job_id JOIN app.dataset d ON d.dataset_id=j.dataset_id AND d.domain=j.domain"
                    " WHERE " + clause + " AND j.status='SUCCEEDED' AND a.metric='record_count'"
                    " ORDER BY j.finished_at DESC,j.scope,a.entity LIMIT 200"
                ),
                params,
            )
        ).mappings()
    ]
