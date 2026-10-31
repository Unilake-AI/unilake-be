-- Chạy sau khi upload data/ingestion-demo/hr/03-hr.csv lên MinIO.
-- Chỉ thêm nguồn HR giả lập; giữ nguyên các nguồn và cấu hình đã có.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM app.source_system WHERE source_code = 'HR_SYS' AND is_active
    ) THEN
        RAISE EXCEPTION 'HR_SYS must exist and be active';
    END IF;
END $$;

INSERT INTO app.data_layer (layer_code, layer_name, storage_type)
VALUES ('BRONZE', 'Bronze', 'MINIO')
ON CONFLICT (layer_code) DO NOTHING;

INSERT INTO app.dataset
    (dataset_code, dataset_name, domain, source_system_id, data_layer_id, status)
SELECT 'hr_bronze', 'Human Resources Bronze', 'hr', s.source_system_id,
       l.data_layer_id, 'ACTIVE'
FROM app.source_system s CROSS JOIN app.data_layer l
WHERE s.source_code = 'HR_SYS' AND l.layer_code = 'BRONZE'
ON CONFLICT (dataset_code) DO NOTHING;

INSERT INTO app.data_source
    (source_system_id, source_name, source_type, connection_type, location,
     configuration, is_active, ingestion_count, description)
SELECT s.source_system_id, 'HR Positions CSV Demo', 'CSV', 'FILE',
       's3://unilake-raw/demo/hr/03-hr.csv',
       '{"delimiter":",","encoding":"utf-8","has_header":true,"skip_rows":0}'::jsonb,
       true, 0, 'Synthetic position records for Source-to-Bronze ingestion testing'
FROM app.source_system s
WHERE s.source_code = 'HR_SYS'
  AND NOT EXISTS (
      SELECT 1 FROM app.data_source ds
      WHERE ds.source_system_id = s.source_system_id
        AND ds.source_name = 'HR Positions CSV Demo'
  );

COMMIT;

SELECT ds.data_source_id, d.dataset_id, ds.source_name, ds.source_type,
       d.domain, ds.location
FROM app.data_source ds
JOIN app.dataset d ON d.source_system_id = ds.source_system_id
WHERE ds.source_name = 'HR Positions CSV Demo'
  AND d.dataset_code = 'hr_bronze';
