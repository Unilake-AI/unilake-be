-- Danh mục bắt buộc để API/worker tìm tầng lưu trữ trên DB mới hoặc đã tồn tại.
-- Chỉ thêm mã còn thiếu, giữ nguyên ID/tên/cấu hình đã được quản trị sửa.
BEGIN;
INSERT INTO app.data_layer (layer_code, layer_name, storage_type, description) VALUES
    ('BRONZE', 'Bronze', 'OBJECT_STORAGE', 'Dữ liệu nguồn đã được nạp và xác nhận'),
    ('SILVER', 'Silver', 'OBJECT_STORAGE', 'Dữ liệu đã làm sạch, chuẩn hóa và kiểm tra'),
    ('GOLD', 'Gold', 'OBJECT_STORAGE', 'Dữ liệu tổng hợp phục vụ báo cáo')
ON CONFLICT (layer_code) DO NOTHING;
COMMIT;
