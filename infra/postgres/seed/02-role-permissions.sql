-- 9 role chuẩn của UniLake. Chạy trong một transaction.

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

DELETE FROM app.role_permission
WHERE role_id IN (SELECT role_id FROM app.role WHERE role_code IN ('ADMIN', 'DATA_ADMIN', 'VIEWER', 'ADMISSIONS_MANAGER', 'ADMISSIONS_STAFF', 'ACADEMIC_MANAGER', 'ACADEMIC_STAFF', 'HR_MANAGER', 'HR_STAFF'));

INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
CROSS JOIN app.permission p
WHERE r.role_code IN ('ADMIN')
ON CONFLICT (role_id, permission_id) DO NOTHING;

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

INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
JOIN app.permission p ON p.permission_code IN (
    'auth.login',
    'dashboard.read'
)
WHERE r.role_code IN ('VIEWER')
ON CONFLICT (role_id, permission_id) DO NOTHING;

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
