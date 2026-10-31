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
('ADMIN',       'Quản trị viên hệ thống', 'Toàn quyền quản trị hệ thống và phân quyền'),
('SUPER_ADMIN', 'Super Admin',            'Toàn quyền hệ thống tối cao'),
('DATA_ADMIN',  'Quản trị dữ liệu',       'Quản lý Data Sources, Datasets, Catalog và Data Quality'),
('ANALYST',     'Chuyên viên phân tích',   'Xem dữ liệu, thực thi câu hỏi NLQ, xem báo cáo'),
('MANAGER',     'Quản lý',                'Xem Datasets, Catalog và Dashboard tổng quan'),
('VIEWER',      'Người xem',              'Quyền xem Dashboard cơ bản')
ON CONFLICT (role_code)
DO UPDATE SET
    role_name = EXCLUDED.role_name,
    description = EXCLUDED.description;
"""

ROLE_PERMISSIONS_SQL_LIST = [
    """
    INSERT INTO app.role_permission (role_id, permission_id)
    SELECT r.role_id, p.permission_id
    FROM app.role r, app.permission p
    WHERE r.role_code IN ('ADMIN', 'SUPER_ADMIN')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
    INSERT INTO app.role_permission (role_id, permission_id)
    SELECT r.role_id, p.permission_id
    FROM app.role r
    JOIN app.permission p ON p.permission_code IN (
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
        'query.history.read',
        'dashboard.read'
    )
    WHERE r.role_code IN ('DATA_ADMIN', 'DATA_ENGINEER', 'DATA_GOVERNANCE')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
    INSERT INTO app.role_permission (role_id, permission_id)
    SELECT r.role_id, p.permission_id
    FROM app.role r
    JOIN app.permission p ON p.permission_code IN (
        'data_source.read',
        'dataset.read',
        'catalog.read',
        'lineage.read',
        'quality.rule.read',
        'quality.report.read',
        'query.execute',
        'query.history.read',
        'dashboard.read'
    )
    WHERE r.role_code IN ('ANALYST', 'DATA_ANALYST')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
    INSERT INTO app.role_permission (role_id, permission_id)
    SELECT r.role_id, p.permission_id
    FROM app.role r
    JOIN app.permission p ON p.permission_code IN (
        'dataset.read',
        'catalog.read',
        'query.execute',
        'query.history.read',
        'dashboard.read'
    )
    WHERE r.role_code IN ('MANAGER', 'ACADEMIC_ADMIN')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
    """
    INSERT INTO app.role_permission (role_id, permission_id)
    SELECT r.role_id, p.permission_id
    FROM app.role r
    JOIN app.permission p ON p.permission_code IN (
        'dashboard.read'
    )
    WHERE r.role_code IN ('VIEWER', 'USER')
    ON CONFLICT (role_id, permission_id) DO NOTHING;
    """,
]

USERS_SEED_SQL = """
INSERT INTO app.app_user (username, password_hash, employee_id, is_active, created_at) VALUES
('admin',       'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),
('dataadmin',   'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),
('analyst',     'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),
('manager',     'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),
('viewer',      'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW())
ON CONFLICT (username)
DO UPDATE SET
    password_hash = EXCLUDED.password_hash,
    is_active = EXCLUDED.is_active;
"""

USER_ROLES_SQL_LIST = [
    """
    INSERT INTO app.user_role (app_user_id, role_id)
    SELECT u.app_user_id, r.role_id
    FROM app.app_user u, app.role r
    WHERE u.username = 'admin' AND r.role_code IN ('ADMIN', 'SUPER_ADMIN')
    ON CONFLICT (app_user_id, role_id) DO NOTHING;
    """,
    """
    INSERT INTO app.user_role (app_user_id, role_id)
    SELECT u.app_user_id, r.role_id
    FROM app.app_user u, app.role r
    WHERE u.username = 'dataadmin' AND r.role_code IN ('DATA_ADMIN', 'DATA_ENGINEER', 'DATA_GOVERNANCE')
    ON CONFLICT (app_user_id, role_id) DO NOTHING;
    """,
    """
    INSERT INTO app.user_role (app_user_id, role_id)
    SELECT u.app_user_id, r.role_id
    FROM app.app_user u, app.role r
    WHERE u.username = 'analyst' AND r.role_code IN ('ANALYST', 'DATA_ANALYST')
    ON CONFLICT (app_user_id, role_id) DO NOTHING;
    """,
    """
    INSERT INTO app.user_role (app_user_id, role_id)
    SELECT u.app_user_id, r.role_id
    FROM app.app_user u, app.role r
    WHERE u.username = 'manager' AND r.role_code IN ('MANAGER', 'ACADEMIC_ADMIN')
    ON CONFLICT (app_user_id, role_id) DO NOTHING;
    """,
    """
    INSERT INTO app.user_role (app_user_id, role_id)
    SELECT u.app_user_id, r.role_id
    FROM app.app_user u, app.role r
    WHERE u.username = 'viewer' AND r.role_code IN ('VIEWER', 'USER')
    ON CONFLICT (app_user_id, role_id) DO NOTHING;
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
