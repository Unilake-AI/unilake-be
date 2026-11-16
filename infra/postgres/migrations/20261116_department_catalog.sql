-- Khởi tạo ba phòng phụ trách; giữ mã và tên đã được quản trị cấu hình trước đó.
BEGIN;

INSERT INTO app.department (
    department_code, department_name, department_type, status
) VALUES
    ('PTS', 'Phòng Tuyển sinh', 'Office', 'ACTIVE'),
    ('PDT', 'Phòng Đào tạo', 'Office', 'ACTIVE'),
    ('PNS', 'Phòng Nhân sự', 'Office', 'ACTIVE')
ON CONFLICT (department_code) DO NOTHING;

-- Chỉ ngừng sử dụng đúng các phòng thử nghiệm đã nhận diện trong dữ liệu cũ.
-- Giữ nguyên hồ sơ và các liên kết để tra cứu lịch sử; không xóa dữ liệu người dùng.
UPDATE app.department
SET status = 'INACTIVE'
WHERE (
    department_code ~ '^DQI-academic-[0-9a-f]{8}$'
    AND department_name = 'Demo academic'
) OR (
    department_code ~ '^DQI-outside-[0-9a-f]{8}$'
    AND department_name = 'Demo outside'
);

COMMIT;
