-- Ba phòng phụ trách dữ liệu trong phạm vi hiện tại của UniLake AI.
-- Dùng mã cố định để chạy lại không sinh phòng trùng; giữ nguyên phòng đã được quản trị sửa.
INSERT INTO app.department (
    department_code, department_name, department_type, status
) VALUES
    ('PTS', 'Phòng Tuyển sinh', 'Office', 'ACTIVE'),
    ('PDT', 'Phòng Đào tạo', 'Office', 'ACTIVE'),
    ('PNS', 'Phòng Nhân sự', 'Office', 'ACTIVE')
ON CONFLICT (department_code) DO NOTHING;
