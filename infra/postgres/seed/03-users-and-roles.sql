-- =============================================================================
-- UniLake AI - Dữ liệu khởi tạo người dùng ứng dụng và vai trò người dùng
-- Mật khẩu mặc định cho mọi người dùng được tạo ban đầu: "Password@123"
-- Thuật toán băm mật khẩu: PBKDF2-HMAC-SHA256 (tương thích với app.core.security)
-- =============================================================================

-- 1. Thêm người dùng ứng dụng
INSERT INTO app.app_user (username, password_hash, employee_id, is_active, created_at) VALUES
('admin',       'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),  -- vai trò ADMIN
('dataadmin',   'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),  -- vai trò DATA_ADMIN
('analyst',     'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),  -- vai trò ANALYST
('manager',     'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW()),  -- vai trò MANAGER
('viewer',      'pbkdf2_sha256$54j20uXDDrH8EjRhaS54Cw$cNs1S4SC2nylW_QW3oZ0SZZovVdy2YhSULe_y3IJdvI', NULL, TRUE, NOW())   -- vai trò VIEWER
ON CONFLICT (username)
DO UPDATE SET
    password_hash = EXCLUDED.password_hash,
    is_active = EXCLUDED.is_active;

-- 2. Ánh xạ người dùng vào vai trò (bảng user_role)

-- admin -> ADMIN & SUPER_ADMIN
INSERT INTO app.user_role (app_user_id, role_id)
SELECT u.app_user_id, r.role_id
FROM app.app_user u, app.role r
WHERE u.username = 'admin' AND r.role_code IN ('ADMIN', 'SUPER_ADMIN')
ON CONFLICT (app_user_id, role_id) DO NOTHING;

-- dataadmin -> DATA_ADMIN & DATA_ENGINEER
INSERT INTO app.user_role (app_user_id, role_id)
SELECT u.app_user_id, r.role_id
FROM app.app_user u, app.role r
WHERE u.username = 'dataadmin' AND r.role_code IN ('DATA_ADMIN', 'DATA_ENGINEER', 'DATA_GOVERNANCE')
ON CONFLICT (app_user_id, role_id) DO NOTHING;

-- analyst -> ANALYST & DATA_ANALYST
INSERT INTO app.user_role (app_user_id, role_id)
SELECT u.app_user_id, r.role_id
FROM app.app_user u, app.role r
WHERE u.username = 'analyst' AND r.role_code IN ('ANALYST', 'DATA_ANALYST')
ON CONFLICT (app_user_id, role_id) DO NOTHING;

-- manager -> MANAGER & ACADEMIC_ADMIN
INSERT INTO app.user_role (app_user_id, role_id)
SELECT u.app_user_id, r.role_id
FROM app.app_user u, app.role r
WHERE u.username = 'manager' AND r.role_code IN ('MANAGER', 'ACADEMIC_ADMIN')
ON CONFLICT (app_user_id, role_id) DO NOTHING;

-- viewer -> VIEWER & USER
INSERT INTO app.user_role (app_user_id, role_id)
SELECT u.app_user_id, r.role_id
FROM app.app_user u, app.role r
WHERE u.username = 'viewer' AND r.role_code IN ('VIEWER', 'USER')
ON CONFLICT (app_user_id, role_id) DO NOTHING;
