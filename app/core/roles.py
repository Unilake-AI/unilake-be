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
# Quyền đọc này vẫn được các phân hệ danh mục dùng. Ingestion dùng phạm vi hẹp hơn
# vì trạng thái job có thể cho biết dữ liệu của phòng ban đang được xử lý.
DATA_READ_ROLES = DATA_WRITE_ROLES | {RoleCode.VIEWER}

# Nhân viên chỉ được gửi dữ liệu thuộc đúng lĩnh vực phòng ban của mình.
UPLOAD_STAFF_DOMAINS = {
    RoleCode.ADMISSIONS_STAFF: "admissions",
    RoleCode.ACADEMIC_STAFF: "academic",
    RoleCode.HR_STAFF: "hr",
}
UPLOAD_WRITE_ROLES = DATA_WRITE_ROLES | frozenset(UPLOAD_STAFF_DOMAINS)
