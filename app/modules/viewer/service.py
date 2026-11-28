"""Tổng hợp độ sẵn sàng và chất lượng dữ liệu cho VIEWER."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.modules.viewer.schemas import (
    DomainOverview,
    GoldStatus,
    HighlightMetric,
    PublicationStats,
    QualityOverview,
    ViewerOverview,
    ViewerSummary,
)
from app.repositories import viewer_report_repo
from app.shared.reporting import DOMAIN_NAMES

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.shared.reporting import Domain


def _domain_overview(domain: Domain, row: dict[str, Any]) -> DomainOverview:
    sources = PublicationStats(**row, not_current=row.get("total", 0) - row.get("current", 0))
    datasets = row.get("datasets", 0)
    status: GoldStatus
    message = None
    if not datasets:
        status = "NO_DATA"
        message = "Chưa có dữ liệu Gold hiện hành"
    elif not sources.current:
        status = "PENDING"
        message = "Chưa có dữ liệu Gold hiện hành"
    elif sources.not_current or sources.datasets_without_scope:
        status = "PARTIAL"
        message = "Dữ liệu Gold hiện hành chưa đầy đủ"
    else:
        status = "READY"

    highlight = None
    if status == "READY" and row.get("scope") is not None:
        highlight = HighlightMetric.model_validate(row)
    return DomainOverview(
        domain=domain,
        name=DOMAIN_NAMES[domain],
        status=status,
        datasets=datasets,
        sources=sources,
        last_published_at=row.get("last_published_at"),
        highlight_metric=highlight,
        message=message,
    )


async def domain_overview(db: AsyncSession, domain: Domain) -> DomainOverview:
    rows = await viewer_report_repo.publication(db, domain)
    return _domain_overview(domain, rows[0] if rows else {})


async def _domains(db: AsyncSession) -> list[DomainOverview]:
    rows = {row["domain"]: row for row in await viewer_report_repo.publication(db)}
    return [_domain_overview(domain, rows.get(domain, {})) for domain in DOMAIN_NAMES]


async def overview(db: AsyncSession) -> ViewerOverview:
    domains = await _domains(db)
    return ViewerOverview(
        domains=domains,
        summary=ViewerSummary(
            datasets=sum(item.datasets for item in domains),
            sources=sum(item.sources.total for item in domains),
            ready_domains=sum(item.status == "READY" for item in domains),
        ),
    )


async def quality_overview(db: AsyncSession) -> QualityOverview:
    domains = await _domains(db)
    publication = PublicationStats(
        **{
            field: sum(getattr(item.sources, field) for item in domains)
            for field in PublicationStats.model_fields
        }
    )
    return QualityOverview(
        quality=await viewer_report_repo.issue_stats(db),
        publication=publication,
        freshness={item.domain: item.last_published_at for item in domains},
    )
