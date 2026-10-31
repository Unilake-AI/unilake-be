-- Seed nguồn thử lỗi đọc: KHÔNG tạo hoặc upload missing.csv.
-- Chạy lại không thêm nguồn trùng hoặc sửa nguồn HR đang hoạt động.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM app.dataset d
        JOIN app.source_system s ON s.source_system_id = d.source_system_id
        JOIN app.data_layer l ON l.data_layer_id = d.data_layer_id
        WHERE s.source_code = 'HR_SYS' AND s.is_active
          AND d.dataset_code = 'hr_bronze' AND d.status = 'ACTIVE'
          AND l.layer_code = 'BRONZE'
    ) THEN
        RAISE EXCEPTION 'Active HR_SYS and hr_bronze dataset are required';
    END IF;
END $$;

LOCK TABLE app.data_source IN SHARE ROW EXCLUSIVE MODE;

INSERT INTO app.data_source
    (source_system_id, source_name, source_type, connection_type, location,
     configuration, is_active, ingestion_count, description)
SELECT s.source_system_id, 'HR Missing CSV Test', 'CSV', 'FILE',
       's3://unilake-raw/demo/hr/missing.csv',
       '{"delimiter":",","encoding":"utf-8","has_header":true,"skip_rows":0}'::jsonb,
       true, 0, 'Intentional missing object for INGESTION_READING_FAILED test'
FROM app.source_system s
WHERE s.source_code = 'HR_SYS'
  AND NOT EXISTS (
      SELECT 1 FROM app.data_source ds
      WHERE ds.source_system_id = s.source_system_id
        AND ds.source_name = 'HR Missing CSV Test'
  );

COMMIT;

SELECT ds.data_source_id, d.dataset_id, ds.location, ds.is_active,
       json_build_object(
           'data_source_id', ds.data_source_id,
           'dataset_id', d.dataset_id,
           'source_table', 'missing.csv'
       ) AS ingestion_payload
FROM app.data_source ds
JOIN app.source_system s ON s.source_system_id = ds.source_system_id
JOIN app.dataset d ON d.source_system_id = s.source_system_id
WHERE s.source_code = 'HR_SYS'
  AND ds.source_name = 'HR Missing CSV Test'
  AND d.dataset_code = 'hr_bronze';
