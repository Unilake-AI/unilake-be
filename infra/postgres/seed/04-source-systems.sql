-- =============================================================================
-- UniLake AI - Dữ liệu khởi tạo hệ thống nguồn
-- =============================================================================

INSERT INTO app.source_system (
    source_code,
    source_name,
    source_type,
    description,
    owner_department_id,
    is_active,
    created_at
) VALUES
-- 1. Tuyển sinh
(
    'ADM_SYS',
    'Admissions System',
    'FILE_STORAGE',
    'Hệ thống quản lý tuyển sinh - cung cấp dữ liệu thí sinh, hồ sơ, phương thức tuyển sinh',
    NULL,
    TRUE,
    NOW()
),
-- 2. Học vụ / Đào tạo
(
    'ACA_SYS',
    'Academic System',
    'RDBMS',
    'Hệ thống quản lý đào tạo - cung cấp dữ liệu sinh viên, chương trình, môn học, điểm số',
    NULL,
    TRUE,
    NOW()
),
-- 3. Nhân sự
(
    'HR_SYS',
    'Human Resources System',
    'RDBMS',
    'Hệ thống quản lý nhân sự - cung cấp dữ liệu nhân viên, giảng viên, đơn vị, chức vụ',
    NULL,
    TRUE,
    NOW()
),
-- 4. Nguồn dạng tệp dùng chung
(
    'FILE_SHARE',
    'Shared File Storage',
    'FILE_STORAGE',
    'Kho lưu trữ file dùng chung (CSV, Excel) phục vụ ingestion thủ công',
    NULL,
    TRUE,
    NOW()
)
ON CONFLICT (source_code)
DO UPDATE SET
    source_name = EXCLUDED.source_name,
    source_type = EXCLUDED.source_type,
    description = EXCLUDED.description,
    owner_department_id = EXCLUDED.owner_department_id,
    is_active = EXCLUDED.is_active;
