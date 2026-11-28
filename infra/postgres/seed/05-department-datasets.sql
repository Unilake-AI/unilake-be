-- Bộ tiếp nhận tệp Bronze theo phòng ban. Nguồn CSV/Excel cụ thể được tạo khi tải tệp lần đầu.
WITH department_sources (department_code, domain, source_code, source_name) AS (
    VALUES
        ('PTS', 'admissions', 'UPLOAD_ADMISSIONS', 'Tệp tải lên - Tuyển sinh'),
        ('PDT', 'academic', 'UPLOAD_ACADEMIC', 'Tệp tải lên - Đào tạo'),
        ('PNS', 'hr', 'UPLOAD_HR', 'Tệp tải lên - Nhân sự')
), active_departments AS (
    SELECT config.*, department.department_id
    FROM department_sources config
    JOIN app.department department USING (department_code)
    WHERE department.status = 'ACTIVE'
), added_layer AS (
    -- Docker chạy seed trước migration danh mục tầng dữ liệu.
    INSERT INTO app.data_layer (layer_code, layer_name, storage_type, description)
    VALUES ('BRONZE', 'Bronze', 'OBJECT_STORAGE', 'Dữ liệu nguồn đã được nạp và xác nhận')
    ON CONFLICT (layer_code) DO NOTHING
    RETURNING data_layer_id
), bronze AS (
    SELECT data_layer_id FROM added_layer
    UNION ALL
    SELECT data_layer_id FROM app.data_layer WHERE layer_code = 'BRONZE'
), added_sources AS (
    INSERT INTO app.source_system (source_code, source_name, source_type, owner_department_id)
    SELECT source_code, source_name, 'UPLOAD', department_id
    FROM active_departments
    ON CONFLICT (source_code) DO NOTHING
    RETURNING source_code, source_system_id
), upload_sources AS (
    SELECT source_code, source_system_id FROM added_sources
    UNION ALL
    SELECT source_code, source_system_id
    FROM app.source_system
    WHERE source_type = 'UPLOAD' AND is_active
), department_datasets (domain, dataset_code, dataset_name, description) AS (
    VALUES
        ('admissions', 'PTS_DANG_KY_XET_TUYEN', 'Hồ sơ đăng ký xét tuyển',
         'Hồ sơ thí sinh và đăng ký theo chương trình, năm và phương thức xét tuyển.'),
        ('academic', 'PDT_KHUNG_CHUONG_TRINH', 'Khung chương trình và học phần',
         'Phiên bản khung chương trình, danh sách học phần và điều kiện tiên quyết.'),
        ('hr', 'HR_DEPARTMENT', 'Danh mục phòng ban Nhân sự',
         'Tệp danh mục phòng ban phục vụ đối soát dữ liệu Nhân sự.'),
        ('hr', 'HR_POSITION', 'Danh mục chức vụ',
         'Tệp danh mục mã và tên chức vụ.'),
        ('hr', 'HR_QUALIFICATION', 'Danh mục bằng cấp',
         'Tệp danh mục tên và loại bằng cấp.'),
        ('hr', 'HR_EMPLOYEE', 'Hồ sơ nhân viên',
         'Thông tin và trạng thái công tác của nhân viên.'),
        ('hr', 'HR_LECTURER', 'Hồ sơ giảng viên',
         'Thông tin giảng viên, học vị, học hàm và lĩnh vực nghiên cứu.'),
        ('hr', 'HR_EMPLOYEE_QUALIFICATION', 'Bằng cấp nhân viên',
         'Bằng cấp của nhân viên, loại bằng, nơi cấp và năm cấp.'),
        ('hr', 'HR_ASSIGNMENT', 'Phân công công tác',
         'Phân công nhân viên theo phòng, chức vụ và thời gian hiệu lực.')
), legacy_hr_codes (old_code, new_code) AS (
    VALUES
        ('PNS_HO_SO_NHAN_VIEN', 'HR_EMPLOYEE'),
        ('PNS_PHAN_CONG_CONG_TAC', 'HR_ASSIGNMENT'),
        ('PNS_BANG_CAP', 'HR_EMPLOYEE_QUALIFICATION')
), renamed_datasets AS (
    -- Giữ ID và cấu hình đã chỉnh sửa của các bộ từ lần seed trước.
    UPDATE app.dataset AS existing
    SET dataset_code = config.dataset_code,
        dataset_name = CASE
            WHEN existing.dataset_name = 'Hồ sơ nhân viên và giảng viên'
                AND legacy.new_code = 'HR_EMPLOYEE' THEN config.dataset_name
            ELSE existing.dataset_name
        END,
        description = CASE
            WHEN legacy.new_code = 'HR_EMPLOYEE' AND existing.description =
                'Thông tin nhân viên, trạng thái công tác và thông tin giảng viên.'
                THEN config.description
            ELSE existing.description
        END
    FROM legacy_hr_codes legacy
    JOIN department_datasets config ON config.dataset_code = legacy.new_code
    WHERE existing.dataset_code = legacy.old_code AND existing.domain = config.domain
        AND NOT EXISTS (
            SELECT 1 FROM app.dataset current_dataset
            WHERE current_dataset.dataset_code = legacy.new_code
        )
    RETURNING existing.dataset_code
)
INSERT INTO app.dataset (
    dataset_code, dataset_name, domain, source_system_id, data_layer_id,
    owner_department_id, status, description
)
SELECT
    dataset.dataset_code, dataset.dataset_name, dataset.domain,
    source.source_system_id, bronze.data_layer_id, department.department_id,
    'ACTIVE', dataset.description
FROM department_datasets dataset
JOIN active_departments department USING (domain)
JOIN upload_sources source USING (source_code)
CROSS JOIN bronze
WHERE NOT EXISTS (
    SELECT 1 FROM renamed_datasets renamed WHERE renamed.dataset_code = dataset.dataset_code
)
ON CONFLICT (dataset_code) DO NOTHING;
