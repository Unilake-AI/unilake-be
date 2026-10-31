-- =============================================================================
-- UniLake AI - Dữ liệu khởi tạo quyền
-- =============================================================================

INSERT INTO app.permission (permission_code, permission_name, description) VALUES
-- ========== XÁC THỰC / NGƯỜI DÙNG ==========
('auth.login',                'Đăng nhập',                      'Cho phép đăng nhập hệ thống'),
('user.read',                 'Xem thông tin user',             'Xem danh sách và chi tiết user'),
('user.manage',               'Quản lý user',                   'Tạo, cập nhật, khóa user'),

-- ========== NGUỒN DỮ LIỆU ==========
('data_source.read',          'Xem Data Source',                'Xem danh sách và chi tiết nguồn dữ liệu'),
('data_source.write',         'Quản lý Data Source',            'Tạo và cập nhật cấu hình nguồn dữ liệu'),

-- ========== DATASET ==========
('dataset.read',              'Xem Dataset',                    'Xem danh sách, schema và preview dataset'),
('dataset.manage',            'Quản lý Dataset',                'Cập nhật thông tin dataset'),

-- ========== CATALOG DỮ LIỆU ==========
('catalog.read',              'Xem Data Catalog',               'Xem metadata dataset và column'),
('catalog.write',             'Cập nhật Data Catalog',          'Chỉnh sửa metadata, mô tả, tag'),

-- ========== NGUỒN GỐC DỮ LIỆU ==========
('lineage.read',              'Xem Data Lineage',               'Xem nguồn gốc và quá trình biến đổi dữ liệu'),

-- ========== CHẤT LƯỢNG DỮ LIỆU ==========
('quality.rule.read',         'Xem Quality Rules',              'Xem danh sách quy tắc chất lượng'),
('quality.rule.write',        'Quản lý Quality Rules',          'Tạo, sửa, xóa quy tắc chất lượng'),
('quality.report.read',       'Xem Quality Report',             'Xem báo cáo kết quả kiểm tra chất lượng'),
('quality.check.run',         'Chạy kiểm tra chất lượng',       'Trigger chạy Data Quality check'),

-- ========== AI / TRUY VẤN ==========
('query.execute',             'Thực thi truy vấn NL',           'Gửi câu hỏi ngôn ngữ tự nhiên và nhận kết quả'),
('query.history.read',        'Xem lịch sử truy vấn',           'Xem Query History'),

-- ========== BẢNG ĐIỀU KHIỂN ==========
('dashboard.read',            'Xem Dashboard',                  'Xem Dashboard và KPI'),
('dashboard.manage',          'Quản lý Dashboard',              'Tạo và chỉnh sửa Dashboard'),

-- ========== HỆ THỐNG ==========
('system.config',             'Cấu hình hệ thống',              'Thay đổi cấu hình hệ thống'),
('system.log.read',           'Xem system log',                 'Xem log hoạt động hệ thống')
ON CONFLICT (permission_code)
DO UPDATE SET
    permission_name = EXCLUDED.permission_name,
    description = EXCLUDED.description;
