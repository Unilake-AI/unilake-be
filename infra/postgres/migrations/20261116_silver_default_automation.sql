-- Mặc định nạp Bronze thành công sẽ có việc Silver; cấu hình riêng chỉ là override.
BEGIN;
CREATE OR REPLACE FUNCTION app.enqueue_default_silver(p_ingestion_id bigint)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE j app.ingestion_job%ROWTYPE; p app.auto_pipeline_policy%ROWTYPE; frozen jsonb;
BEGIN
    SELECT * INTO j FROM app.ingestion_job WHERE ingestion_job_id=p_ingestion_id;
    IF NOT FOUND OR j.status<>'SUCCESS' OR j.output IS NULL THEN RETURN; END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.dataset d JOIN app.data_layer l USING(data_layer_id)
        JOIN app.source_system s USING(source_system_id)
        JOIN app.data_source ds ON ds.data_source_id=j.data_source_id
        WHERE d.dataset_id=j.dataset_id AND d.status='ACTIVE' AND l.layer_code='BRONZE'
        AND s.is_active AND ds.is_active AND d.domain IN ('hr','academic','admissions')
    ) THEN RETURN; END IF;
    SELECT * INTO p FROM app.auto_pipeline_policy WHERE dataset_id=j.dataset_id FOR SHARE;
    -- Một lệnh tạm dừng rõ ràng vẫn có hiệu lực; không tự bật lại cấu hình đã tắt.
    IF FOUND AND NOT p.enabled THEN RETURN; END IF;
    IF p.enabled AND p.definition->'profile_definition'->>'source_table'=j.source_table
        AND (p.definition->'profile_definition'->>'data_source_id')::bigint=j.data_source_id THEN
        frozen=p.definition || jsonb_build_object('revision',p.revision,'requested_by',p.updated_by);
    ELSE
        frozen=jsonb_build_object('mode','automatic','recipe_version','auto-source-v1',
            'revision',0,'requested_by',coalesce(j.requested_by,'system'),
            'allow_partial_publication',false,'max_attempts',3,'retry_delay_seconds',30);
    END IF;
    INSERT INTO app.auto_pipeline_job(ingestion_job_id,dataset_id,policy,bronze_input)
    VALUES(j.ingestion_job_id,j.dataset_id,frozen,j.output)
    ON CONFLICT(ingestion_job_id) DO NOTHING;
END $$;

CREATE OR REPLACE FUNCTION app.enqueue_silver_after_ingestion()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status='SUCCESS' AND (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'SUCCESS') THEN
        PERFORM app.enqueue_default_silver(NEW.ingestion_job_id);
    END IF;
    RETURN NEW;
END $$;

-- Chỉ tiếp nhận bản Bronze thành công mới nhất của mỗi file, không chạy lại toàn bộ lịch sử.
-- Các lần đã có Silver hoặc job tự chạy được giữ nguyên.
DO $$ DECLARE item record;
BEGIN
    FOR item IN
        SELECT DISTINCT ON (dataset_id,data_source_id,source_table) ingestion_job_id
        FROM app.ingestion_job WHERE status='SUCCESS' AND output IS NOT NULL
        ORDER BY dataset_id,data_source_id,source_table,created_at DESC,ingestion_job_id DESC
    LOOP
        IF NOT EXISTS (SELECT 1 FROM app.cleaning_run c WHERE c.ingestion_job_id=item.ingestion_job_id
                       AND c.publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')) THEN
            PERFORM app.enqueue_default_silver(item.ingestion_job_id);
        END IF;
    END LOOP;
END $$;
COMMIT;
