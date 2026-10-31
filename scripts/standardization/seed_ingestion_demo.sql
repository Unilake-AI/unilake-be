-- Catalog giả lập riêng cho worker Docker. Không cập nhật nguồn hiện có.
-- Có thể chạy lại: mã/tên demo cố định giúp các lệnh thêm không tạo dữ liệu trùng.
BEGIN;
INSERT INTO app.data_layer (layer_code,layer_name,storage_type)
VALUES ('BRONZE','Bronze','MINIO') ON CONFLICT (layer_code) DO NOTHING;

INSERT INTO app.source_system (source_code,source_name,source_type,is_active)
SELECT 'DEMO_FLOW_' || domain, 'Synthetic flow ' || domain, 'EXCEL', true
FROM (VALUES ('admissions'),('academic'),('hr')) AS domains(domain)
ON CONFLICT (source_code) DO NOTHING;

INSERT INTO app.data_source
    (source_system_id,source_name,source_type,connection_type,location,configuration,is_active,ingestion_count)
SELECT s.source_system_id, s.source_code || '_FILES', 'EXCEL', 'FILE',
       '/data/standardization/live-demo',
       '{"sheet_name":"Data","header_row":1,"skip_rows":0}'::jsonb, true, 0
FROM app.source_system s
WHERE s.source_code IN ('DEMO_FLOW_admissions','DEMO_FLOW_academic','DEMO_FLOW_hr')
AND NOT EXISTS (SELECT 1 FROM app.data_source ds
                WHERE ds.source_system_id=s.source_system_id
                  AND ds.source_name=s.source_code || '_FILES');

INSERT INTO app.dataset
    (dataset_code,dataset_name,domain,source_system_id,data_layer_id,storage_format,status)
SELECT s.source_code || '_BRONZE', s.source_name || ' Bronze',
       substring(s.source_code FROM 11), s.source_system_id, l.data_layer_id, 'EXCEL', 'ACTIVE'
FROM app.source_system s CROSS JOIN app.data_layer l
WHERE s.source_code IN ('DEMO_FLOW_admissions','DEMO_FLOW_academic','DEMO_FLOW_hr')
  AND l.layer_code='BRONZE'
ON CONFLICT (dataset_code) DO NOTHING;
COMMIT;

SELECT d.domain, ds.data_source_id, d.dataset_id, ds.location, ds.configuration
FROM app.dataset d JOIN app.source_system s ON s.source_system_id=d.source_system_id
JOIN app.data_source ds ON ds.source_system_id=s.source_system_id
WHERE d.dataset_code IN ('DEMO_FLOW_admissions_BRONZE','DEMO_FLOW_academic_BRONZE','DEMO_FLOW_hr_BRONZE')
  AND ds.source_name=s.source_code || '_FILES'
ORDER BY d.domain;
