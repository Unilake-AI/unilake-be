"""Feed sự kiện dùng cùng phạm vi với yêu cầu sửa, kiểm tra lại khi đánh dấu đọc."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    MetaData,
    Table,
    and_,
    false,
    func,
    or_,
    select,
    union,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.roles import DATA_WRITE_ROLES, MANAGER_SCOPES, UPLOAD_STAFF_DOMAINS
from app.modules.notifications.schemas import NotificationPage, NotificationRead
from app.modules.pipeline.models import DATA_QUALITY_ISSUE_EVENTS, DATA_QUALITY_ISSUES
from app.modules.users.models import Department
from app.repositories.quality_issue_repo import visible_issue_query

if TYPE_CHECKING:
    from app.modules.staff.contracts import quality_issues

READS = Table(
    "notification_read",
    MetaData(),
    Column("app_user_id", BigInteger, primary_key=True),
    Column("event_id", BigInteger, primary_key=True),
    Column("read_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    schema="app",
)


def notification_query(actor: quality_issues.IssueActor, roles: set[str]) -> Any:
    is_admin = actor.is_admin and not DATA_WRITE_ROLES.isdisjoint(roles)
    scope: dict[str, Any] = {
        "is_admin": is_admin,
        "departments": actor.departments,
        "domains": actor.domains,
        "user_id": actor.user_id,
    }
    if is_admin:
        visible = visible_issue_query(**scope).subquery()
    else:
        staff_domains = (
            frozenset(domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in roles)
            & actor.domains
        )
        staff = visible_issue_query(**{**scope, "domains": staff_domains})
        c = DATA_QUALITY_ISSUES.c
        pairs = [
            and_(c.domain == domain, Department.department_code == code)
            for role, (domain, code) in MANAGER_SCOPES.items()
            if role in roles and domain in actor.domains
        ]
        manager = (
            select(DATA_QUALITY_ISSUES, Department.department_name)
            .join(Department, Department.department_id == c.department_id)
            .where(c.department_id.in_(actor.departments), or_(*pairs) if pairs else false())
        )
        visible = union(staff, manager).subquery()
    e, r = DATA_QUALITY_ISSUE_EVENTS.c, READS.c
    return (
        select(
            e.event_id,
            visible.c.issue_id,
            visible.c.title,
            visible.c.domain,
            visible.c.department_name,
            e.event_type,
            e.to_status,
            e.actor_id,
            e.created_at,
            r.read_at,
        )
        .select_from(visible)
        .join(DATA_QUALITY_ISSUE_EVENTS, e.issue_id == visible.c.issue_id)
        .outerjoin(READS, and_(r.event_id == e.event_id, r.app_user_id == actor.user_id))
    )


async def list_notifications(
    db: AsyncSession,
    actor: quality_issues.IssueActor,
    roles: set[str],
    *,
    unread_only: bool,
    limit: int,
    offset: int,
) -> NotificationPage:
    base = notification_query(actor, roles).subquery()
    unread = (
        await db.execute(select(func.count()).select_from(base).where(base.c.read_at.is_(None)))
    ).scalar_one()
    query = select(base)
    if unread_only:
        query = query.where(base.c.read_at.is_(None))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(query.order_by(base.c.event_id.desc()).limit(limit).offset(offset))
    ).mappings()
    return NotificationPage(
        items=[NotificationRead.model_validate(row) for row in rows],
        total=total,
        unread_count=unread,
        limit=limit,
        offset=offset,
    )


async def mark_read(
    db: AsyncSession, actor: quality_issues.IssueActor, roles: set[str], ids: set[int]
) -> bool:
    base = notification_query(actor, roles).subquery()
    found = set(
        (await db.execute(select(base.c.event_id).where(base.c.event_id.in_(ids)))).scalars()
    )
    if found != ids:
        return False
    await db.execute(
        insert(READS)
        .values([{"app_user_id": actor.user_id, "event_id": event_id} for event_id in sorted(ids)])
        .on_conflict_do_nothing()
    )
    return True
