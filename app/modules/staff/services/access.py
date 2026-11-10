"""Giới hạn lĩnh vực nhân viên được tải file và xem kết quả nạp."""

from __future__ import annotations

from typing import Any

from app.core.exceptions import AppException
from app.core.roles import DATA_WRITE_ROLES, UPLOAD_STAFF_DOMAINS, UPLOAD_WRITE_ROLES


def upload_domains(user: dict[str, Any]) -> frozenset[str] | None:
    """Trả về miền được tải file; ``None`` nghĩa là được tải mọi miền."""
    roles = user.get("roles", [])
    roles = [roles] if isinstance(roles, str) else roles
    if not isinstance(roles, list | tuple | set):
        raise AppException("Không có quyền tải file", code="FORBIDDEN", status_code=403)
    role_set = set(roles)
    if not UPLOAD_WRITE_ROLES.isdisjoint(role_set):
        if not DATA_WRITE_ROLES.isdisjoint(role_set):
            return None
        return frozenset(
            domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in role_set
        )
    raise AppException("Không có quyền tải file", code="FORBIDDEN", status_code=403)


def ingestion_read_domains(user: dict[str, Any]) -> frozenset[str] | None:
    """Trả về miền job có thể xem; quản trị dữ liệu được xem toàn bộ."""
    roles = user.get("roles", [])
    roles = [roles] if isinstance(roles, str) else roles
    if not isinstance(roles, list | tuple | set):
        raise AppException("Không có quyền xem tác vụ ingestion", code="FORBIDDEN", status_code=403)
    role_set = set(roles)
    if not DATA_WRITE_ROLES.isdisjoint(role_set):
        return None
    domains = frozenset(domain for role, domain in UPLOAD_STAFF_DOMAINS.items() if role in role_set)
    if domains:
        return domains
    raise AppException("Không có quyền xem tác vụ ingestion", code="FORBIDDEN", status_code=403)
