"""Phạm vi toàn trường cho vai trò VIEWER đã được xác thực."""

from __future__ import annotations

from typing import Any

from app.modules.viewer.schemas import ViewerContext, ViewerDomain
from app.shared.reporting import DOMAIN_NAMES


def viewer_access(user: dict[str, Any]) -> ViewerContext:
    return ViewerContext(
        user_id=int(user["sub"]),
        roles=user["roles"],
        domains=[ViewerDomain(domain=domain, name=name) for domain, name in DOMAIN_NAMES.items()],
    )
