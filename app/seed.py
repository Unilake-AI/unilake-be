"""Script nạp dữ liệu khởi tạo cho UniLake AI."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from app.infra.db.session import async_session_factory

PERMISSIONS_SEED_SQL = """
INSERT INTO app.permission (permission_code, permission_name, description) VALUES
('auth.login',                'Đăng nhập',                      'Cho phép đăng nhập hệ thống'),
('user.read',                 'Xem thông tin user',             'Xem danh sách và chi tiết user'),
('user.manage',               'Quản lý user',                   'Tạo, cập nhật, khóa user'),
('data_source.read',          'Xem Data Source',                'Xem danh sách và chi tiết nguồn dữ liệu'),
('data_source.write',         'Quản lý Data Source',            'Tạo và cập nhật cấu hình nguồn dữ liệu'),
('dataset.read',              'Xem Dataset',                    'Xem danh sách, schema và preview dataset'),
('dataset.manage',            'Quản lý Dataset',                'Cập nhật thông tin dataset'),
('catalog.read',              'Xem Data Catalog',               'Xem metadata dataset và column'),
('catalog.write',             'Cập nhật Data Catalog',          'Chỉnh sửa metadata, mô tả, tag'),
('lineage.read',              'Xem Data Lineage',               'Xem nguồn gốc và quá trình biến đổi dữ liệu'),
('quality.rule.read',         'Xem Quality Rules',              'Xem danh sách quy tắc chất lượng'),
('quality.rule.write',        'Quản lý Quality Rules',          'Tạo, sửa, xóa quy tắc chất lượng'),
('quality.report.read',       'Xem Quality Report',             'Xem báo cáo kết quả kiểm tra chất lượng'),
('quality.check.run',         'Chạy kiểm tra chất lượng',       'Trigger chạy Data Quality check'),
('query.execute',             'Thực thi truy vấn NL',           'Gửi câu hỏi ngôn ngữ tự nhiên và nhận kết quả'),
('query.history.read',        'Xem lịch sử truy vấn',           'Xem Query History'),
('dashboard.read',            'Xem Dashboard',                  'Xem Dashboard và KPI'),
('dashboard.manage',          'Quản lý Dashboard',              'Tạo và chỉnh sửa Dashboard'),
('system.config',             'Cấu hình hệ thống',              'Thay đổi cấu hình hệ thống'),
('system.log.read',           'Xem system log',                 'Xem log hoạt động hệ thống')
ON CONFLICT (permission_code)
DO UPDATE SET
    permission_name = EXCLUDED.permission_name,
    description = EXCLUDED.description;
"""

ROLES_SEED_SQL = """
INSERT INTO app.role (role_code, role_name, description) VALUES
('ADMIN', 'Quản trị viên hệ thống', 'Quản trị hệ thống, tài khoản và phân quyền'),
('DATA_ADMIN', 'Quản trị dữ liệu', 'Quản lý nguồn, pipeline, chất lượng dữ liệu; truy vấn AI và phân tích xuyên phòng ban'),
('VIEWER', 'Ban Giám hiệu', 'Xem dashboard và báo cáo tổng hợp'),
('ADMISSIONS_MANAGER', 'Trưởng phòng Tuyển sinh', 'Trưởng phòng Tuyển sinh'),
('ADMISSIONS_STAFF', 'Nhân viên Tuyển sinh', 'Nhân viên phòng Tuyển sinh'),
('ACADEMIC_MANAGER', 'Trưởng phòng Đào tạo', 'Trưởng phòng Đào tạo'),
('ACADEMIC_STAFF', 'Nhân viên Đào tạo', 'Nhân viên phòng Đào tạo'),
('HR_MANAGER', 'Trưởng phòng Nhân sự', 'Trưởng phòng Nhân sự'),
('HR_STAFF', 'Nhân viên Nhân sự', 'Nhân viên phòng Nhân sự')
ON CONFLICT (role_code) DO UPDATE SET
    role_name = EXCLUDED.role_name,
    description = EXCLUDED.description;
"""

ROLE_PERMISSIONS_SQL_LIST = [
    """
DELETE FROM app.role_permission
WHERE role_id IN (SELECT role_id FROM app.role WHERE role_code IN ('ADMIN', 'DATA_ADMIN', 'VIEWER', 'ADMISSIONS_MANAGER', 'ADMISSIONS_STAFF', 'ACADEMIC_MANAGER', 'ACADEMIC_STAFF', 'HR_MANAGER', 'HR_STAFF'));
    """,
    """
INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
CROSS JOIN app.permission p
WHERE r.role_code IN ('ADMIN')
ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
JOIN app.permission p ON p.permission_code IN (
    'auth.login',
    'data_source.read',
    'data_source.write',
    'dataset.read',
    'dataset.manage',
    'catalog.read',
    'catalog.write',
    'lineage.read',
    'quality.rule.read',
    'quality.rule.write',
    'quality.report.read',
    'quality.check.run',
    'query.execute',
    'query.history.read',
    'dashboard.read'
)
WHERE r.role_code IN ('DATA_ADMIN')
ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
JOIN app.permission p ON p.permission_code IN (
    'auth.login',
    'dashboard.read'
)
WHERE r.role_code IN ('VIEWER')
ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
JOIN app.permission p ON p.permission_code IN (
    'auth.login',
    'dataset.read',
    'catalog.read',
    'query.execute',
    'query.history.read',
    'dashboard.read'
)
WHERE r.role_code IN ('ADMISSIONS_MANAGER', 'ADMISSIONS_STAFF', 'ACADEMIC_MANAGER', 'ACADEMIC_STAFF', 'HR_MANAGER', 'HR_STAFF')
ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
]

USERS_SEED_SQL = """
INSERT INTO app.app_user (username, password_hash, employee_id, is_active, created_at) VALUES
('admin', 'pbkdf2_sha256$DpYe0yDL1Z-7SLOzUTTFdA$ng82oqN5XItDwvT3kxcVrpN2HWnpaE94hOnQBYTNQJ0', NULL, TRUE, NOW()),
('dataadmin', 'pbkdf2_sha256$9ty-J4bxZHkrWTdSiH4DPQ$0plarWnu6o4YFXxDLVR3SHuP4lMmpv3SzMBEE5IhcgY', NULL, TRUE, NOW()),
('viewer', 'pbkdf2_sha256$nT3BFxQn5i1ivPp4Zr2xhA$WcajWOPnsZLDM0j_t55YgjTxu-6H4kqEAkzVJuPsuB8', NULL, TRUE, NOW()),
('admissions_manager', 'pbkdf2_sha256$3Yh5tlUkIqoN0UmzgxOOKw$GGWd9HvIG_cyrvVQZPYjSX0jhPcnl0cU-61E9n-LUmw', NULL, TRUE, NOW()),
('admissions_staff', 'pbkdf2_sha256$Wx-zjMFd6-5bLVRgRAyDRA$8zhcbL-9lPFXwybLUktd6ekpfDdHJbx8rDNT4rtB-To', NULL, TRUE, NOW()),
('academic_manager', 'pbkdf2_sha256$9u6OxPZC0I1OipGMLjtmCg$xk9p_6HYpVbxgZoJnf5K6jNNAWqyt26nqbxb80Fuf2E', NULL, TRUE, NOW()),
('academic_staff', 'pbkdf2_sha256$IQynN0m0aUp5ELnIWhIufg$IZ9-tRTW78JF2soIuJGugaj4R3Txu25GQ6cX7od07XM', NULL, TRUE, NOW()),
('hr_manager', 'pbkdf2_sha256$3D0noMkkELrun02q3FHSpw$Fq8WCiEVTOJRFBskwS5x90Zm6W9zQchh3vL5FKAwzqs', NULL, TRUE, NOW()),
('hr_staff', 'pbkdf2_sha256$fNI_xP1Sjfb_o-bJs6xJ6A$06lxrdtoz34mvaDNuLvz1Y_zy_kFA8ctykJsVxABB98', NULL, TRUE, NOW())
ON CONFLICT (username) DO NOTHING;
"""

USER_ROLES_SQL_LIST = [
    """
DELETE FROM app.user_role
WHERE app_user_id IN (
    SELECT app_user_id FROM app.app_user WHERE username IN ('admin', 'dataadmin', 'viewer', 'admissions_manager', 'admissions_staff', 'academic_manager', 'academic_staff', 'hr_manager', 'hr_staff', 'analyst', 'manager')
);
    """,
    """
UPDATE app.app_user SET is_active = FALSE WHERE username IN ('analyst', 'manager');
    """,
    """
INSERT INTO app.user_role (app_user_id, role_id)
SELECT u.app_user_id, r.role_id
FROM (VALUES
    ('admin', 'ADMIN'),
    ('dataadmin', 'DATA_ADMIN'),
    ('viewer', 'VIEWER'),
    ('admissions_manager', 'ADMISSIONS_MANAGER'),
    ('admissions_staff', 'ADMISSIONS_STAFF'),
    ('academic_manager', 'ACADEMIC_MANAGER'),
    ('academic_staff', 'ACADEMIC_STAFF'),
    ('hr_manager', 'HR_MANAGER'),
    ('hr_staff', 'HR_STAFF')
) AS mapping(username, role_code)
JOIN app.app_user u ON u.username = mapping.username
JOIN app.role r ON r.role_code = mapping.role_code
ON CONFLICT (app_user_id, role_id) DO NOTHING;
    """,
    """
-- Chỉ xóa role cũ khi không còn tài khoản nào sử dụng.
DELETE FROM app.role_permission rp USING app.role r
WHERE rp.role_id = r.role_id
  AND r.role_code IN ('SUPER_ADMIN', 'ANALYST', 'MANAGER')
  AND NOT EXISTS (SELECT 1 FROM app.user_role ur WHERE ur.role_id = r.role_id);

    """,
    """
DELETE FROM app.role r
WHERE r.role_code IN ('SUPER_ADMIN', 'ANALYST', 'MANAGER')
  AND NOT EXISTS (SELECT 1 FROM app.user_role ur WHERE ur.role_id = r.role_id);
    """,
]


async def seed_data() -> None:
    """Thực thi các script nạp dữ liệu ban đầu."""
    print("Seeding database roles, permissions, and users...")
    async with async_session_factory() as db:
        await db.execute(text(PERMISSIONS_SEED_SQL))
        await db.execute(text(ROLES_SEED_SQL))
        for query in ROLE_PERMISSIONS_SQL_LIST:
            await db.execute(text(query))
        await db.execute(text(USERS_SEED_SQL))
        for query in USER_ROLES_SQL_LIST:
            await db.execute(text(query))
        await db.commit()
    print("Seed roles, permissions, users, and user_roles successfully completed!")


if __name__ == "__main__":
    asyncio.run(seed_data())
