"""Các mã vai trò được hỗ trợ bởi UniLake."""

from __future__ import annotations

from enum import StrEnum


class RoleCode(StrEnum):
    ADMIN = "ADMIN"
    DATA_ADMIN = "DATA_ADMIN"
    VIEWER = "VIEWER"
    ADMISSIONS_MANAGER = "ADMISSIONS_MANAGER"
    ADMISSIONS_STAFF = "ADMISSIONS_STAFF"
    ACADEMIC_MANAGER = "ACADEMIC_MANAGER"
    ACADEMIC_STAFF = "ACADEMIC_STAFF"
    HR_MANAGER = "HR_MANAGER"
    HR_STAFF = "HR_STAFF"


DATA_WRITE_ROLES = frozenset({RoleCode.ADMIN, RoleCode.DATA_ADMIN})
DATA_READ_ROLES = DATA_WRITE_ROLES | {RoleCode.VIEWER}
