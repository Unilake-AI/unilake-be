-- =============================================================================
-- UniLake AI - Dữ liệu khởi tạo vai trò và phân quyền
-- =============================================================================

-- 1. Bảo đảm các vai trò tồn tại trong app.role
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

-- 2. Nạp các ánh xạ vai trò và quyền

-- ====================== ADMIN & SUPER_ADMIN (Full quyền) ======================
INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r, app.permission p
WHERE r.role_code IN ('ADMIN', 'SUPER_ADMIN')
ON CONFLICT (role_id, permission_id) DO NOTHING;

-- ====================== DATA_ADMIN ======================
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

-- ====================== ANALYST ======================
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

-- ====================== MANAGER ======================
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

-- ====================== VIEWER ======================
INSERT INTO app.role_permission (role_id, permission_id)
SELECT r.role_id, p.permission_id
FROM app.role r
JOIN app.permission p ON p.permission_code IN (
    'dashboard.read'
)
WHERE r.role_code IN ('VIEWER', 'USER')
ON CONFLICT (role_id, permission_id) DO NOTHING;
