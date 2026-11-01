-- Tài khoản DEMO/LOCAL: mật khẩu khởi tạo Password@123.
-- Giữ nguyên mật khẩu và trạng thái của tài khoản đã tồn tại.
-- Tài khoản mẫu analyst/manager cũ được khóa, giữ lại lịch sử.
-- Chạy trong một transaction sau 02-role-permissions.sql.

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

DELETE FROM app.user_role
WHERE app_user_id IN (
    SELECT app_user_id FROM app.app_user WHERE username IN ('admin', 'dataadmin', 'viewer', 'admissions_manager', 'admissions_staff', 'academic_manager', 'academic_staff', 'hr_manager', 'hr_staff', 'analyst', 'manager')
);

UPDATE app.app_user SET is_active = FALSE WHERE username IN ('analyst', 'manager');

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

-- Chỉ xóa role cũ khi không còn tài khoản nào sử dụng.
DELETE FROM app.role_permission rp USING app.role r
WHERE rp.role_id = r.role_id
  AND r.role_code IN ('SUPER_ADMIN', 'ANALYST', 'MANAGER')
  AND NOT EXISTS (SELECT 1 FROM app.user_role ur WHERE ur.role_id = r.role_id);


DELETE FROM app.role r
WHERE r.role_code IN ('SUPER_ADMIN', 'ANALYST', 'MANAGER')
  AND NOT EXISTS (SELECT 1 FROM app.user_role ur WHERE ur.role_id = r.role_id);
