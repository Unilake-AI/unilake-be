-- Chạy sau khi upload data/ingestion-demo/academic/02-academic.csv lên MinIO.
-- Chỉ thêm nguồn CSV demo; không thay đổi nguồn REST API hoặc cấu hình đã có.
BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM app.source_system WHERE source_code = 'ACA_SYS' AND is_active
    ) THEN
        RAISE EXCEPTION 'ACA_SYS must exist and be active';
    END IF;
END $$;

INSERT INTO app.data_layer (layer_code, layer_name, storage_type)
VALUES ('BRONZE', 'Bronze', 'MINIO')
ON CONFLICT (layer_code) DO NOTHING;

INSERT INTO app.dataset
    (dataset_code, dataset_name, domain, source_system_id, data_layer_id, status)
SELECT 'academic_bronze', 'Academic Bronze', 'academic', s.source_system_id,
       l.data_layer_id, 'ACTIVE'
FROM app.source_system s CROSS JOIN app.data_layer l
WHERE s.source_code = 'ACA_SYS' AND l.layer_code = 'BRONZE'
ON CONFLICT (dataset_code) DO NOTHING;

INSERT INTO app.data_source
    (source_system_id, source_name, source_type, connection_type, location,
     configuration, is_active, ingestion_count, description)
SELECT s.source_system_id, 'Academic Semesters CSV Demo', 'CSV', 'FILE',
       's3://unilake-raw/demo/academic/02-academic.csv',
       '{"delimiter":",","encoding":"utf-8","has_header":true,"skip_rows":0}'::jsonb,
       true, 0, 'Synthetic semester records for Source-to-Bronze ingestion testing'
FROM app.source_system s
WHERE s.source_code = 'ACA_SYS'
  AND NOT EXISTS (
      SELECT 1 FROM app.data_source ds
      WHERE ds.source_system_id = s.source_system_id
        AND ds.source_name = 'Academic Semesters CSV Demo'
  );

COMMIT;

SELECT ds.data_source_id, d.dataset_id, ds.source_name, ds.source_type,
       d.domain, ds.location
FROM app.data_source ds
JOIN app.dataset d ON d.source_system_id = ds.source_system_id
WHERE ds.source_name = 'Academic Semesters CSV Demo'
  AND d.dataset_code = 'academic_bronze';
