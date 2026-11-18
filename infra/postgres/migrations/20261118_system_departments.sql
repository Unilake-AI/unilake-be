-- app.department là danh mục phân quyền hệ thống, không phải cây tổ chức từ file.
-- Không xóa dữ liệu cũ hoặc tự gán khoa/bộ môn vào một phòng bất kỳ.
BEGIN;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM app.department WHERE department_code NOT IN ('PDT', 'PNS', 'PTS')) THEN
        RAISE EXCEPTION 'SYSTEM_DEPARTMENT_MIGRATION_BLOCKED: unexpected departments require explicit data migration';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid='app.department'::regclass AND conname='ck_department_system_code'
    ) THEN
        ALTER TABLE app.department ADD CONSTRAINT ck_department_system_code
            CHECK (department_code IN ('PDT', 'PNS', 'PTS'));
    END IF;
END $$;
COMMIT;
