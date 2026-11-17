--
-- PostgreSQL database dump
--

\restrict CMT2VPuOAVcGzCe6tbXNZkqUXZ6I3Vgz9cDOdUvFCVBilrR6Bk95rDg31dO6VZd

-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: app; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA app;


--
-- Name: gold; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA gold;


--
-- Name: enqueue_default_silver(bigint); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.enqueue_default_silver(p_ingestion_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
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


--
-- Name: enqueue_gold_business_sync(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.enqueue_gold_business_sync() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
 IF NEW.status='SUCCEEDED' THEN
  INSERT INTO app.gold_sync_job(gold_job_id) VALUES(NEW.job_id) ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END $$;


--
-- Name: enqueue_gold_object(text, text, jsonb); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.enqueue_gold_object(p_scope text, p_snapshot text, p_manifest jsonb) RETURNS character varying
    LANGUAGE plpgsql
    AS $$
DECLARE c record; s record; v_job varchar(36);
BEGIN
    IF p_manifest IS NULL OR p_manifest->>'key' IS NULL THEN RAISE EXCEPTION 'GOLD_SOURCE_EVIDENCE_REQUIRED'; END IF;
    SELECT dataset_id,domain INTO c FROM app.cleaning_run
      WHERE silver_snapshot_id=p_snapshot AND publication_status IN ('PUBLISHED','PUBLISHED_WITH_QUARANTINE')
      ORDER BY finished_at DESC NULLS LAST,run_id LIMIT 1;
    IF c IS NULL OR c.domain NOT IN ('hr','academic','admissions') THEN RETURN NULL; END IF;
    INSERT INTO app.gold_scope(scope,dataset_id,domain,silver_snapshot_id)
      VALUES(p_scope,c.dataset_id,c.domain,p_snapshot)
      ON CONFLICT(scope) DO UPDATE SET dataset_id=EXCLUDED.dataset_id,domain=EXCLUDED.domain,
      silver_snapshot_id=EXCLUDED.silver_snapshot_id,generation=app.gold_scope.generation+1,updated_at=now()
      WHERE app.gold_scope.silver_snapshot_id IS DISTINCT FROM EXCLUDED.silver_snapshot_id;
    SELECT * INTO s FROM app.gold_scope WHERE scope=p_scope FOR UPDATE;
    v_job=gen_random_uuid()::text;
    INSERT INTO app.gold_job(job_id,scope,dataset_id,domain,generation,silver_snapshot_id,silver_manifest_ref)
      VALUES(v_job,p_scope,s.dataset_id,s.domain,s.generation,p_snapshot,p_manifest)
      ON CONFLICT(scope,generation) DO UPDATE SET silver_manifest_ref=EXCLUDED.silver_manifest_ref
      WHERE app.gold_job.silver_manifest_ref IS NULL;
    SELECT job_id INTO v_job FROM app.gold_job WHERE scope=p_scope AND generation=s.generation;
    RETURN v_job;
END $$;


--
-- Name: enqueue_silver_after_ingestion(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.enqueue_silver_after_ingestion() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.status='SUCCESS' AND (TG_OP='INSERT' OR OLD.status IS DISTINCT FROM 'SUCCESS') THEN
        PERFORM app.enqueue_default_silver(NEW.ingestion_job_id);
    END IF;
    RETURN NEW;
END $$;


--
-- Name: reject_silver_profile_revision_mutation(); Type: FUNCTION; Schema: app; Owner: -
--

CREATE FUNCTION app.reject_silver_profile_revision_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'SILVER_PROFILE_REVISION_IMMUTABLE';
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admission; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.admission (
    admission_id bigint NOT NULL,
    applicant_id bigint NOT NULL,
    program_id bigint NOT NULL,
    preference_rank integer NOT NULL,
    admission_method_id bigint NOT NULL,
    admission_year_id bigint NOT NULL,
    application_date date,
    admission_score numeric(6,2),
    admission_status character varying(30) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: admission_admission_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.admission ALTER COLUMN admission_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.admission_admission_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: admission_method; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.admission_method (
    admission_method_id bigint NOT NULL,
    method_code character varying(30) NOT NULL,
    method_name character varying(150) NOT NULL,
    max_score numeric(6,2) NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: admission_method_admission_method_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.admission_method ALTER COLUMN admission_method_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.admission_method_admission_method_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: admission_year; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.admission_year (
    admission_year_id bigint NOT NULL,
    year_label character varying(20) NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    status character varying(20) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE admission_year; Type: COMMENT; Schema: app; Owner: -
--

COMMENT ON TABLE app.admission_year IS 'Phong Tuyen sinh - Quan ly dot tuyen sinh';


--
-- Name: admission_year_admission_year_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.admission_year ALTER COLUMN admission_year_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.admission_year_admission_year_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_model; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.ai_model (
    ai_model_id bigint NOT NULL,
    provider character varying(100) NOT NULL,
    model_name character varying(150) NOT NULL,
    model_version character varying(100),
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_model_ai_model_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.ai_model ALTER COLUMN ai_model_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.ai_model_ai_model_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: app_user; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.app_user (
    app_user_id bigint NOT NULL,
    username character varying(100) NOT NULL,
    password_hash text NOT NULL,
    employee_id bigint,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: app_user_app_user_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.app_user ALTER COLUMN app_user_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.app_user_app_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: applicant; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.applicant (
    applicant_id bigint NOT NULL,
    national_id character varying(30),
    full_name character varying(200) NOT NULL,
    date_of_birth date,
    gender character varying(20),
    phone character varying(30),
    email character varying(150),
    high_school_name character varying(250),
    province character varying(100),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE applicant; Type: COMMENT; Schema: app; Owner: -
--

COMMENT ON TABLE app.applicant IS 'Thong tin goc cua thi sinh dang ky tuyen sinh';


--
-- Name: applicant_applicant_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.applicant ALTER COLUMN applicant_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.applicant_applicant_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: assignment; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.assignment (
    assignment_id bigint NOT NULL,
    employee_id bigint NOT NULL,
    department_id bigint NOT NULL,
    position_id bigint NOT NULL,
    start_date date NOT NULL,
    end_date date,
    is_primary boolean DEFAULT true NOT NULL,
    assignment_status character varying(30) NOT NULL
);


--
-- Name: assignment_assignment_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.assignment ALTER COLUMN assignment_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.assignment_assignment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: auto_pipeline_attempt; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.auto_pipeline_attempt (
    job_id character varying(36) NOT NULL,
    attempt_number integer NOT NULL,
    lease_token character varying(36) NOT NULL,
    worker_id character varying(100) NOT NULL,
    status character varying(30) NOT NULL,
    error_code character varying(100),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: auto_pipeline_job; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.auto_pipeline_job (
    job_id character varying(36) DEFAULT (gen_random_uuid())::text NOT NULL,
    ingestion_job_id bigint NOT NULL,
    dataset_id bigint NOT NULL,
    policy jsonb NOT NULL,
    bronze_input jsonb NOT NULL,
    status character varying(30) DEFAULT 'QUEUED'::character varying NOT NULL,
    attempt_number integer DEFAULT 0 NOT NULL,
    lease_token character varying(36),
    lease_expires_at timestamp with time zone,
    worker_id character varying(100),
    next_attempt_at timestamp with time zone,
    error_code character varying(100),
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT auto_pipeline_job_status_check CHECK (((status)::text = ANY ((ARRAY['QUEUED'::character varying, 'RUNNING'::character varying, 'RETRY_WAIT'::character varying, 'SUCCESS'::character varying, 'QUARANTINED'::character varying, 'FAILED'::character varying, 'CANCELLED'::character varying])::text[])))
);


--
-- Name: auto_pipeline_policy; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.auto_pipeline_policy (
    dataset_id bigint NOT NULL,
    revision integer NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    definition jsonb NOT NULL,
    updated_by character varying(100) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT auto_pipeline_policy_revision_check CHECK ((revision > 0))
);


--
-- Name: canonical_identity; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.canonical_identity (
    canonical_id bigint NOT NULL,
    entity character varying(80) NOT NULL,
    CONSTRAINT canonical_identity_canonical_id_check CHECK ((canonical_id > 0))
);


--
-- Name: canonical_identity_canonical_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.canonical_identity ALTER COLUMN canonical_id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME app.canonical_identity_canonical_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: catalog_column; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.catalog_column (
    catalog_column_id bigint NOT NULL,
    catalog_table_id bigint NOT NULL,
    column_name character varying(150) NOT NULL,
    data_type character varying(50) NOT NULL,
    description text,
    is_nullable boolean,
    is_primary_key boolean DEFAULT false NOT NULL,
    is_foreign_key boolean DEFAULT false NOT NULL
);


--
-- Name: catalog_column_catalog_column_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.catalog_column ALTER COLUMN catalog_column_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.catalog_column_catalog_column_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: catalog_metadata; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.catalog_metadata (
    domain_id character varying(50) NOT NULL,
    dataset character varying(150) NOT NULL,
    metadata_payload jsonb NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: catalog_table; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.catalog_table (
    catalog_table_id bigint NOT NULL,
    dataset_id bigint NOT NULL,
    table_name character varying(150) NOT NULL,
    source_table_name character varying(150),
    description text,
    owner character varying(150),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: catalog_table_catalog_table_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.catalog_table ALTER COLUMN catalog_table_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.catalog_table_catalog_table_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: chart_widget; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.chart_widget (
    chart_widget_id bigint NOT NULL,
    dashboard_id bigint NOT NULL,
    widget_type character varying(50) NOT NULL,
    title character varying(200) NOT NULL,
    query_request_id bigint,
    kpi_id bigint,
    config_json text,
    position_x integer,
    position_y integer,
    width integer,
    height integer
);


--
-- Name: chart_widget_chart_widget_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.chart_widget ALTER COLUMN chart_widget_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.chart_widget_chart_widget_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: class; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.class (
    class_id bigint NOT NULL,
    class_code character varying(50) NOT NULL,
    course_id bigint NOT NULL,
    semester_id bigint NOT NULL,
    max_capacity integer,
    room character varying(50),
    class_status character varying(30) NOT NULL
);


--
-- Name: class_class_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.class ALTER COLUMN class_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.class_class_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: class_lecturer; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.class_lecturer (
    class_lecturer_id bigint NOT NULL,
    class_id bigint NOT NULL,
    lecturer_id bigint NOT NULL,
    teaching_role character varying(50) DEFAULT 'PRIMARY_LECTURER'::character varying,
    is_primary boolean DEFAULT true NOT NULL,
    assigned_from date,
    assigned_to date
);


--
-- Name: class_lecturer_class_lecturer_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.class_lecturer ALTER COLUMN class_lecturer_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.class_lecturer_class_lecturer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cleaning_run; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.cleaning_run (
    run_id character varying(36) NOT NULL,
    ingestion_job_id bigint NOT NULL,
    dataset_id bigint NOT NULL,
    domain character varying(50) NOT NULL,
    entity character varying(100) NOT NULL,
    requested_by character varying(100) NOT NULL,
    session character varying(40) NOT NULL,
    bronze_version text NOT NULL,
    input_version text,
    cleaning_status character varying(20) NOT NULL,
    publication_status character varying(30) NOT NULL,
    records_read bigint,
    records_passed bigint,
    records_quarantined bigint,
    records_collapsed bigint,
    records_published bigint NOT NULL,
    silver_snapshot_id character varying(36),
    report_ref json,
    quarantine_source json,
    error_code character varying(80),
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    profile_evidence json,
    auto_job_id character varying(36)
);


--
-- Name: course; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.course (
    course_id bigint NOT NULL,
    course_code character varying(30) NOT NULL,
    course_name character varying(200) NOT NULL,
    credit_hours integer NOT NULL,
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL
);


--
-- Name: course_course_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.course ALTER COLUMN course_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.course_course_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: crosswalk_release; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.crosswalk_release (
    version character varying(100) NOT NULL,
    fingerprint character varying(64) NOT NULL,
    payload text NOT NULL
);


--
-- Name: curriculum; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.curriculum (
    curriculum_id bigint NOT NULL,
    program_id bigint NOT NULL,
    curriculum_code character varying(50) NOT NULL,
    curriculum_name character varying(250) NOT NULL,
    version character varying(30) NOT NULL,
    effective_from date,
    effective_to date,
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL
);


--
-- Name: curriculum_course; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.curriculum_course (
    curriculum_course_id bigint NOT NULL,
    curriculum_id bigint NOT NULL,
    course_id bigint NOT NULL,
    semester_no integer,
    course_type character varying(30),
    is_required boolean DEFAULT true NOT NULL,
    prerequisite_course_id bigint
);


--
-- Name: curriculum_course_curriculum_course_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.curriculum_course ALTER COLUMN curriculum_course_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.curriculum_course_curriculum_course_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: curriculum_curriculum_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.curriculum ALTER COLUMN curriculum_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.curriculum_curriculum_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dashboard; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.dashboard (
    dashboard_id bigint NOT NULL,
    dashboard_name character varying(150) NOT NULL,
    owner_app_user_id bigint NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: dashboard_dashboard_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.dashboard ALTER COLUMN dashboard_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.dashboard_dashboard_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_layer; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_layer (
    data_layer_id bigint NOT NULL,
    layer_code character varying(20) NOT NULL,
    layer_name character varying(50) NOT NULL,
    storage_type character varying(50),
    description text
);


--
-- Name: data_layer_data_layer_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_layer ALTER COLUMN data_layer_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_layer_data_layer_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_lineage; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_lineage (
    data_lineage_id bigint NOT NULL,
    transformation_step_id bigint NOT NULL,
    source_column_id bigint NOT NULL,
    target_column_id bigint NOT NULL,
    lineage_type character varying(50) NOT NULL,
    transformation_expression text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: data_lineage_data_lineage_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_lineage ALTER COLUMN data_lineage_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_lineage_data_lineage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_processing_run; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_processing_run (
    run_id bigint NOT NULL,
    run_type character varying(50) NOT NULL,
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    status character varying(30) NOT NULL,
    triggered_by bigint,
    error_message text
);


--
-- Name: data_processing_run_run_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_processing_run ALTER COLUMN run_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_processing_run_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_quality_issue; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_quality_issue (
    issue_id character varying(36) NOT NULL,
    dataset_id bigint NOT NULL,
    department_id bigint NOT NULL,
    domain character varying(50) NOT NULL,
    title character varying(200) NOT NULL,
    description text NOT NULL,
    status character varying(30) NOT NULL,
    created_by bigint NOT NULL,
    assignee_user_id bigint,
    assignment_mode character varying(30) DEFAULT 'DEPARTMENT'::character varying NOT NULL,
    origin_upload_job_id bigint,
    resolution_job_id bigint,
    verification_run_id character varying(36),
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT data_quality_issue_status_check CHECK (((status)::text = ANY ((ARRAY['ASSIGNED'::character varying, 'PENDING_VERIFICATION'::character varying, 'VERIFIED'::character varying])::text[])))
);


--
-- Name: data_quality_issue_event; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_quality_issue_event (
    event_id bigint NOT NULL,
    issue_id character varying(36) NOT NULL,
    actor_id bigint NOT NULL,
    event_type character varying(30) NOT NULL,
    from_status character varying(30),
    to_status character varying(30) NOT NULL,
    details json NOT NULL,
    created_at timestamp with time zone NOT NULL
);


--
-- Name: data_quality_issue_event_event_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_quality_issue_event ALTER COLUMN event_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_quality_issue_event_event_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_quality_issue_record; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_quality_issue_record (
    issue_id character varying(36) NOT NULL,
    record_id character varying(36) NOT NULL
);


--
-- Name: data_quality_rule; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_quality_rule (
    data_quality_rule_id bigint NOT NULL,
    catalog_column_id bigint NOT NULL,
    rule_code character varying(50) NOT NULL,
    rule_name character varying(150) NOT NULL,
    rule_type character varying(50) NOT NULL,
    rule_expression text NOT NULL,
    severity character varying(20) DEFAULT 'HIGH'::character varying NOT NULL,
    is_active boolean DEFAULT true NOT NULL
);


--
-- Name: data_quality_rule_data_quality_rule_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_quality_rule ALTER COLUMN data_quality_rule_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_quality_rule_data_quality_rule_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_quality_run; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_quality_run (
    data_quality_run_id bigint NOT NULL,
    data_quality_rule_id bigint NOT NULL,
    ingestion_job_id bigint,
    run_at timestamp without time zone DEFAULT now() NOT NULL,
    total_records bigint NOT NULL,
    passed_records bigint NOT NULL,
    failed_records bigint NOT NULL,
    pass_rate numeric(5,2) NOT NULL,
    execution_status character varying(30) NOT NULL,
    failure_sample_ref text
);


--
-- Name: data_quality_run_data_quality_run_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_quality_run ALTER COLUMN data_quality_run_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_quality_run_data_quality_run_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_source; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.data_source (
    data_source_id bigint NOT NULL,
    source_system_id bigint NOT NULL,
    source_name character varying(200) NOT NULL,
    source_type character varying(50) NOT NULL,
    connection_type character varying(50),
    location text,
    configuration jsonb DEFAULT '{}'::jsonb NOT NULL,
    description text,
    is_active boolean DEFAULT true NOT NULL,
    last_ingested_at timestamp without time zone,
    last_ingestion_status character varying(30),
    ingestion_count integer DEFAULT 0 NOT NULL,
    created_by bigint,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: data_source_data_source_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.data_source ALTER COLUMN data_source_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.data_source_data_source_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: dataset; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.dataset (
    dataset_id bigint NOT NULL,
    dataset_code character varying(100) NOT NULL,
    dataset_name character varying(200) NOT NULL,
    domain character varying(50) NOT NULL,
    source_system_id bigint,
    data_layer_id bigint NOT NULL,
    owner_department_id bigint,
    storage_format character varying(50),
    location text,
    version character varying(30),
    status character varying(30) DEFAULT 'ACTIVE'::character varying NOT NULL,
    description text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone
);


--
-- Name: dataset_dataset_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.dataset ALTER COLUMN dataset_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.dataset_dataset_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: department; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.department (
    department_id bigint NOT NULL,
    department_code character varying(30) NOT NULL,
    department_name character varying(200) NOT NULL,
    department_type character varying(50) NOT NULL,
    parent_department_id bigint,
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE department; Type: COMMENT; Schema: app; Owner: -
--

COMMENT ON TABLE app.department IS 'Khoa, Vien, Phong ban dung chung cho Academic va HR';


--
-- Name: department_department_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.department ALTER COLUMN department_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.department_department_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.employee (
    employee_id bigint NOT NULL,
    employee_code character varying(30) NOT NULL,
    full_name character varying(200) NOT NULL,
    date_of_birth date,
    gender character varying(20),
    email character varying(150),
    phone character varying(30),
    hire_date date,
    employee_status character varying(30) NOT NULL
);


--
-- Name: employee_employee_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.employee ALTER COLUMN employee_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.employee_employee_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_qualification; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.employee_qualification (
    employee_id bigint NOT NULL,
    qualification_id bigint NOT NULL,
    institution character varying(250),
    year_obtained integer
);


--
-- Name: enrollment; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.enrollment (
    enrollment_id bigint NOT NULL,
    student_id bigint NOT NULL,
    class_id bigint NOT NULL,
    enrollment_date date NOT NULL,
    enrollment_status character varying(30) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: enrollment_enrollment_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.enrollment ALTER COLUMN enrollment_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.enrollment_enrollment_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: enrollment_grade; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.enrollment_grade (
    enrollment_grade_id bigint NOT NULL,
    enrollment_id bigint NOT NULL,
    midterm_score numeric(5,2),
    final_score numeric(5,2),
    total_score numeric(5,2),
    grade_value numeric(4,2),
    letter_grade character varying(5),
    registration_date date
);


--
-- Name: enrollment_grade_enrollment_grade_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.enrollment_grade ALTER COLUMN enrollment_grade_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.enrollment_grade_enrollment_grade_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: generated_sql; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.generated_sql (
    generated_sql_id bigint NOT NULL,
    query_request_id bigint NOT NULL,
    ai_model_id bigint NOT NULL,
    prompt_template_id bigint NOT NULL,
    sql_text text NOT NULL,
    generated_at timestamp without time zone DEFAULT now() NOT NULL,
    validation_status character varying(30) NOT NULL,
    validation_error text
);


--
-- Name: generated_sql_generated_sql_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.generated_sql ALTER COLUMN generated_sql_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.generated_sql_generated_sql_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: gold_attempt; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.gold_attempt (
    lease_token character varying(36) NOT NULL,
    job_id character varying(36) NOT NULL,
    attempt_number integer NOT NULL,
    status character varying(20) DEFAULT 'RUNNING'::character varying NOT NULL,
    worker_id character varying(200) NOT NULL,
    error_code character varying(100),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone
);


--
-- Name: gold_job; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.gold_job (
    job_id character varying(36) NOT NULL,
    scope character varying(120) NOT NULL,
    dataset_id bigint NOT NULL,
    domain character varying(50) NOT NULL,
    generation bigint NOT NULL,
    silver_snapshot_id character varying(36) NOT NULL,
    recipe_version character varying(50) DEFAULT 'group-counts-v1'::character varying NOT NULL,
    recipe_fingerprint character varying(64) DEFAULT '9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa'::character varying NOT NULL,
    status character varying(20) DEFAULT 'QUEUED'::character varying NOT NULL,
    attempt_number integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 3 NOT NULL,
    lease_token character varying(36),
    lease_until timestamp with time zone,
    worker_id character varying(200),
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    error_code character varying(100),
    manifest_ref jsonb,
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    silver_manifest_ref jsonb,
    CONSTRAINT gold_job_domain_check CHECK (((domain)::text = ANY ((ARRAY['hr'::character varying, 'academic'::character varying, 'admissions'::character varying])::text[]))),
    CONSTRAINT gold_job_status_check CHECK (((status)::text = ANY ((ARRAY['QUEUED'::character varying, 'RUNNING'::character varying, 'RETRY_WAIT'::character varying, 'SUCCEEDED'::character varying, 'FAILED'::character varying, 'SUPERSEDED'::character varying])::text[])))
);


--
-- Name: gold_record_identity; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.gold_record_identity (
    namespace character varying(100) NOT NULL,
    entity character varying(100) NOT NULL,
    source_key character varying(1024) NOT NULL,
    target_key jsonb NOT NULL,
    target_fingerprint character varying(64) NOT NULL,
    last_gold_job_id character varying(36) NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: gold_scope; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.gold_scope (
    scope character varying(120) NOT NULL,
    dataset_id bigint NOT NULL,
    domain character varying(50) NOT NULL,
    silver_snapshot_id character varying(36) NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    active_job_id character varying(36),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT gold_scope_domain_check CHECK (((domain)::text = ANY ((ARRAY['hr'::character varying, 'academic'::character varying, 'admissions'::character varying])::text[])))
);


--
-- Name: gold_sync_job; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.gold_sync_job (
    gold_job_id character varying(36) NOT NULL,
    recipe_version character varying(50) DEFAULT 'gold-business-records-v1'::character varying NOT NULL,
    status character varying(20) DEFAULT 'QUEUED'::character varying NOT NULL,
    source_manifest_ref jsonb,
    attempt_number integer DEFAULT 0 NOT NULL,
    lease_token character varying(36),
    lease_until timestamp with time zone,
    next_attempt_at timestamp with time zone DEFAULT now() NOT NULL,
    error_code character varying(100),
    error_report jsonb,
    result jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    CONSTRAINT gold_sync_job_status_check CHECK (((status)::text = ANY ((ARRAY['QUEUED'::character varying, 'RUNNING'::character varying, 'SYNCED'::character varying, 'BLOCKED'::character varying, 'FAILED'::character varying, 'SUPERSEDED'::character varying])::text[])))
);


--
-- Name: gold_worker; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.gold_worker (
    worker_id character varying(200) NOT NULL,
    heartbeat_at timestamp with time zone NOT NULL
);


--
-- Name: identity_crosswalk; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.identity_crosswalk (
    namespace character varying(100) NOT NULL,
    entity character varying(80) NOT NULL,
    source_key character varying(1024) NOT NULL,
    canonical_id bigint NOT NULL,
    evidence text NOT NULL,
    valid_from character varying(40) NOT NULL,
    valid_to character varying(40)
);


--
-- Name: ingestion_job; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.ingestion_job (
    ingestion_job_id bigint NOT NULL,
    data_source_id bigint NOT NULL,
    dataset_id bigint NOT NULL,
    target_layer_id bigint NOT NULL,
    job_type character varying(50),
    started_at timestamp without time zone,
    finished_at timestamp without time zone,
    status character varying(30) NOT NULL,
    rows_read bigint,
    rows_written bigint,
    rows_rejected bigint,
    error_message text,
    source_table character varying(200),
    created_at timestamp without time zone,
    requested_by character varying(100),
    stage character varying(30),
    lease_expires_at timestamp without time zone,
    output jsonb,
    validation_input jsonb,
    validation_report jsonb,
    upload_input jsonb,
    bronze_output jsonb,
    root_job_id bigint,
    attempt_number integer DEFAULT 1 NOT NULL,
    next_attempt_at timestamp without time zone,
    retryable boolean DEFAULT false NOT NULL
);


--
-- Name: ingestion_job_ingestion_job_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.ingestion_job ALTER COLUMN ingestion_job_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.ingestion_job_ingestion_job_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ingestion_worker_heartbeat; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.ingestion_worker_heartbeat (
    worker_id character varying(100) NOT NULL,
    current_job_id bigint,
    last_heartbeat_at timestamp without time zone NOT NULL
);


--
-- Name: kpi; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.kpi (
    kpi_id bigint NOT NULL,
    kpi_code character varying(50) NOT NULL,
    kpi_name character varying(150) NOT NULL,
    domain character varying(50) NOT NULL,
    unit character varying(50),
    description text
);


--
-- Name: kpi_kpi_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.kpi ALTER COLUMN kpi_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.kpi_kpi_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: kpi_value; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.kpi_value (
    kpi_value_id bigint NOT NULL,
    kpi_id bigint NOT NULL,
    period_date date NOT NULL,
    kpi_value numeric(20,4) NOT NULL,
    dimension_json text,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: kpi_value_kpi_value_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.kpi_value ALTER COLUMN kpi_value_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.kpi_value_kpi_value_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lecturer; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.lecturer (
    employee_id bigint NOT NULL,
    academic_degree character varying(100),
    academic_title character varying(250),
    research_field character varying(250)
);


--
-- Name: login_session; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.login_session (
    login_session_id bigint NOT NULL,
    app_user_id bigint NOT NULL,
    login_at timestamp without time zone DEFAULT now() NOT NULL,
    logout_at timestamp without time zone,
    ip_address character varying(45),
    user_agent text,
    refresh_token_hash text,
    session_status character varying(30) DEFAULT 'ACTIVE'::character varying NOT NULL
);


--
-- Name: login_session_login_session_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.login_session ALTER COLUMN login_session_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.login_session_login_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: major; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.major (
    major_id bigint NOT NULL,
    major_code character varying(30) NOT NULL,
    major_name character varying(200) NOT NULL,
    department_id bigint NOT NULL,
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL
);


--
-- Name: major_major_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.major ALTER COLUMN major_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.major_major_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: notification_read; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.notification_read (
    app_user_id bigint NOT NULL,
    event_id bigint NOT NULL,
    read_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: permission; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.permission (
    permission_id bigint NOT NULL,
    permission_code character varying(100) NOT NULL,
    permission_name character varying(150) NOT NULL,
    description text
);


--
-- Name: permission_permission_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.permission ALTER COLUMN permission_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.permission_permission_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: pipeline_lineage; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.pipeline_lineage (
    lineage_id bigint NOT NULL,
    source_layer character varying(50) NOT NULL,
    source_name text NOT NULL,
    target_layer character varying(50) NOT NULL,
    target_name text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: pipeline_lineage_lineage_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.pipeline_lineage ALTER COLUMN lineage_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.pipeline_lineage_lineage_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: position; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app."position" (
    position_id bigint NOT NULL,
    position_code character varying(30) NOT NULL,
    position_name character varying(150) NOT NULL,
    position_level character varying(50)
);


--
-- Name: position_position_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app."position" ALTER COLUMN position_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.position_position_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: program; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.program (
    program_id bigint NOT NULL,
    program_code character varying(30) NOT NULL,
    program_name character varying(200) NOT NULL,
    major_id bigint NOT NULL,
    degree_level character varying(50) NOT NULL,
    duration_years numeric(4,1),
    tuition_fee_per_credit numeric(15,2),
    status character varying(20) DEFAULT 'ACTIVE'::character varying NOT NULL
);


--
-- Name: program_program_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.program ALTER COLUMN program_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.program_program_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: prompt_template; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.prompt_template (
    prompt_template_id bigint NOT NULL,
    template_name character varying(150) NOT NULL,
    version character varying(30) NOT NULL,
    template_text text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: prompt_template_prompt_template_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.prompt_template ALTER COLUMN prompt_template_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.prompt_template_prompt_template_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: qualification; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.qualification (
    qualification_id bigint NOT NULL,
    qualification_name character varying(150) NOT NULL,
    qualification_type character varying(100)
);


--
-- Name: qualification_qualification_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.qualification ALTER COLUMN qualification_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.qualification_qualification_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quarantine_record; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.quarantine_record (
    record_id character varying(36) NOT NULL,
    cleaning_run_id character varying(36) NOT NULL,
    locator text NOT NULL,
    record_position bigint NOT NULL,
    issues json NOT NULL,
    error_codes jsonb NOT NULL,
    CONSTRAINT quarantine_record_record_position_check CHECK ((record_position > 0))
);


--
-- Name: query_execution; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.query_execution (
    query_execution_id bigint NOT NULL,
    generated_sql_id bigint NOT NULL,
    executed_at timestamp without time zone DEFAULT now() NOT NULL,
    execution_status character varying(30) NOT NULL,
    row_count bigint,
    execution_time_ms integer,
    result_ref text,
    error_message text
);


--
-- Name: query_execution_query_execution_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.query_execution ALTER COLUMN query_execution_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.query_execution_query_execution_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: query_request; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.query_request (
    query_request_id bigint NOT NULL,
    app_user_id bigint NOT NULL,
    question_text text NOT NULL,
    language character varying(20) DEFAULT 'vi'::character varying,
    detected_intent character varying(150),
    submitted_at timestamp without time zone DEFAULT now() NOT NULL,
    status character varying(30) NOT NULL
);


--
-- Name: query_request_query_request_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.query_request ALTER COLUMN query_request_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.query_request_query_request_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: role; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.role (
    role_id bigint NOT NULL,
    role_code character varying(50) NOT NULL,
    role_name character varying(100) NOT NULL,
    description text
);


--
-- Name: role_permission; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.role_permission (
    role_id bigint NOT NULL,
    permission_id bigint NOT NULL
);


--
-- Name: role_role_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.role ALTER COLUMN role_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.role_role_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: schema_migration; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.schema_migration (
    migration_name character varying(200) NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: schema_retrieval; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.schema_retrieval (
    schema_retrieval_id bigint NOT NULL,
    query_request_id bigint NOT NULL,
    catalog_table_id bigint NOT NULL,
    retrieval_rank integer NOT NULL,
    relevance_score numeric(6,4)
);


--
-- Name: schema_retrieval_schema_retrieval_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.schema_retrieval ALTER COLUMN schema_retrieval_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.schema_retrieval_schema_retrieval_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: semester; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.semester (
    semester_id bigint NOT NULL,
    semester_code character varying(30) NOT NULL,
    academic_year character varying(20) NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    status character varying(20) DEFAULT 'UPCOMING'::character varying NOT NULL
);


--
-- Name: semester_semester_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.semester ALTER COLUMN semester_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.semester_semester_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: source_system; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.source_system (
    source_system_id bigint NOT NULL,
    source_code character varying(50) NOT NULL,
    source_name character varying(200) NOT NULL,
    source_type character varying(50) NOT NULL,
    description text,
    owner_department_id bigint,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: source_system_source_system_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.source_system ALTER COLUMN source_system_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.source_system_source_system_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: storage_migration_checkpoint; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.storage_migration_checkpoint (
    table_name text NOT NULL,
    row_count bigint NOT NULL,
    fingerprint text NOT NULL,
    archive_key text NOT NULL,
    verified_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: student; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.student (
    student_id bigint NOT NULL,
    student_code character varying(30) NOT NULL,
    applicant_id bigint,
    admission_id bigint,
    full_name character varying(200) NOT NULL,
    date_of_birth date,
    gender character varying(20),
    program_id bigint NOT NULL,
    admission_year_id bigint NOT NULL,
    student_status character varying(30) NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE student; Type: COMMENT; Schema: app; Owner: -
--

COMMENT ON TABLE app.student IS 'Ho so sinh vien chinh thuc';


--
-- Name: student_student_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.student ALTER COLUMN student_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.student_student_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: transformation_step; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.transformation_step (
    transformation_step_id bigint NOT NULL,
    ingestion_job_id bigint,
    source_dataset_id bigint NOT NULL,
    target_dataset_id bigint NOT NULL,
    step_name character varying(150) NOT NULL,
    step_order integer NOT NULL,
    transformation_type character varying(50) NOT NULL,
    transformation_expression text,
    rows_input bigint,
    rows_output bigint,
    status character varying(30) NOT NULL,
    executed_at timestamp without time zone
);


--
-- Name: transformation_step_transformation_step_id_seq; Type: SEQUENCE; Schema: app; Owner: -
--

ALTER TABLE app.transformation_step ALTER COLUMN transformation_step_id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME app.transformation_step_transformation_step_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_role; Type: TABLE; Schema: app; Owner: -
--

CREATE TABLE app.user_role (
    app_user_id bigint NOT NULL,
    role_id bigint NOT NULL
);


--
-- Name: aggregate; Type: TABLE; Schema: gold; Owner: -
--

CREATE TABLE gold.aggregate (
    job_id character varying(36) NOT NULL,
    entity character varying(150) NOT NULL,
    metric character varying(100) NOT NULL,
    dimensions jsonb NOT NULL,
    value bigint NOT NULL,
    CONSTRAINT aggregate_value_check CHECK ((value >= 0))
);


--
-- Name: current_metrics; Type: VIEW; Schema: gold; Owner: -
--

CREATE VIEW gold.current_metrics AS
 SELECT j.dataset_id,
    j.domain,
    s.scope,
    j.job_id AS gold_snapshot_id,
    j.silver_snapshot_id,
    j.recipe_version,
    j.finished_at,
    a.entity,
    a.metric,
    a.dimensions,
    a.value
   FROM ((app.gold_scope s
     JOIN app.gold_job j ON (((j.job_id)::text = (s.active_job_id)::text)))
     JOIN gold.aggregate a ON (((a.job_id)::text = (j.job_id)::text)))
  WHERE (((j.status)::text = 'SUCCEEDED'::text) AND (j.generation = s.generation) AND ((j.silver_snapshot_id)::text = (s.silver_snapshot_id)::text));


--
-- Name: academic_summary; Type: VIEW; Schema: gold; Owner: -
--

CREATE VIEW gold.academic_summary AS
 SELECT dataset_id,
    domain,
    scope,
    gold_snapshot_id,
    silver_snapshot_id,
    recipe_version,
    finished_at,
    entity,
    metric,
    dimensions,
    value
   FROM gold.current_metrics
  WHERE ((domain)::text = 'academic'::text);


--
-- Name: admissions_summary; Type: VIEW; Schema: gold; Owner: -
--

CREATE VIEW gold.admissions_summary AS
 SELECT dataset_id,
    domain,
    scope,
    gold_snapshot_id,
    silver_snapshot_id,
    recipe_version,
    finished_at,
    entity,
    metric,
    dimensions,
    value
   FROM gold.current_metrics
  WHERE ((domain)::text = 'admissions'::text);


--
-- Name: hr_summary; Type: VIEW; Schema: gold; Owner: -
--

CREATE VIEW gold.hr_summary AS
 SELECT dataset_id,
    domain,
    scope,
    gold_snapshot_id,
    silver_snapshot_id,
    recipe_version,
    finished_at,
    entity,
    metric,
    dimensions,
    value
   FROM gold.current_metrics
  WHERE ((domain)::text = 'hr'::text);


--
-- Data for Name: admission; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.admission (admission_id, applicant_id, program_id, preference_rank, admission_method_id, admission_year_id, application_date, admission_score, admission_status, created_at) FROM stdin;
\.


--
-- Data for Name: admission_method; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.admission_method (admission_method_id, method_code, method_name, max_score, description, is_active) FROM stdin;
\.


--
-- Data for Name: admission_year; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.admission_year (admission_year_id, year_label, start_date, end_date, status, created_at) FROM stdin;
\.


--
-- Data for Name: ai_model; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.ai_model (ai_model_id, provider, model_name, model_version, is_active, created_at) FROM stdin;
\.


--
-- Data for Name: app_user; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.app_user (app_user_id, username, password_hash, employee_id, is_active, created_at) FROM stdin;
1	admin	pbkdf2_sha256$DpYe0yDL1Z-7SLOzUTTFdA$ng82oqN5XItDwvT3kxcVrpN2HWnpaE94hOnQBYTNQJ0	\N	t	2026-11-16 20:22:05.751813
2	dataadmin	pbkdf2_sha256$9ty-J4bxZHkrWTdSiH4DPQ$0plarWnu6o4YFXxDLVR3SHuP4lMmpv3SzMBEE5IhcgY	\N	t	2026-11-16 20:22:05.751813
3	viewer	pbkdf2_sha256$nT3BFxQn5i1ivPp4Zr2xhA$WcajWOPnsZLDM0j_t55YgjTxu-6H4kqEAkzVJuPsuB8	\N	t	2026-11-16 20:22:05.751813
4	admissions_manager	pbkdf2_sha256$3Yh5tlUkIqoN0UmzgxOOKw$GGWd9HvIG_cyrvVQZPYjSX0jhPcnl0cU-61E9n-LUmw	\N	t	2026-11-16 20:22:05.751813
5	admissions_staff	pbkdf2_sha256$Wx-zjMFd6-5bLVRgRAyDRA$8zhcbL-9lPFXwybLUktd6ekpfDdHJbx8rDNT4rtB-To	\N	t	2026-11-16 20:22:05.751813
6	academic_manager	pbkdf2_sha256$9u6OxPZC0I1OipGMLjtmCg$xk9p_6HYpVbxgZoJnf5K6jNNAWqyt26nqbxb80Fuf2E	\N	t	2026-11-16 20:22:05.751813
7	academic_staff	pbkdf2_sha256$IQynN0m0aUp5ELnIWhIufg$IZ9-tRTW78JF2soIuJGugaj4R3Txu25GQ6cX7od07XM	\N	t	2026-11-16 20:22:05.751813
8	hr_manager	pbkdf2_sha256$3D0noMkkELrun02q3FHSpw$Fq8WCiEVTOJRFBskwS5x90Zm6W9zQchh3vL5FKAwzqs	\N	t	2026-11-16 20:22:05.751813
9	hr_staff	pbkdf2_sha256$fNI_xP1Sjfb_o-bJs6xJ6A$06lxrdtoz34mvaDNuLvz1Y_zy_kFA8ctykJsVxABB98	\N	t	2026-11-16 20:22:05.751813
\.


--
-- Data for Name: applicant; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.applicant (applicant_id, national_id, full_name, date_of_birth, gender, phone, email, high_school_name, province, created_at) FROM stdin;
\.


--
-- Data for Name: assignment; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.assignment (assignment_id, employee_id, department_id, position_id, start_date, end_date, is_primary, assignment_status) FROM stdin;
\.


--
-- Data for Name: auto_pipeline_attempt; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.auto_pipeline_attempt (job_id, attempt_number, lease_token, worker_id, status, error_code, started_at, finished_at) FROM stdin;
22c2e420-b06a-45d9-ad48-290366ea6af2	1	0d51c3f8-aa4e-4525-b780-c936ed77f7fa	bae0fdc7bb26	SUCCESS	\N	2026-11-16 21:06:29.147033+07	2026-11-16 21:06:31.214306+07
2cfe4d5c-255b-48b6-95c8-218009d675cf	1	889ba554-8c6e-474b-9cc6-fadafc56a302	bae0fdc7bb26	SUCCESS	\N	2026-11-16 21:07:17.494538+07	2026-11-16 21:07:17.960015+07
c6b682d5-1d7e-4531-a632-b9f9b3337743	1	2d14b3ef-57df-44e6-b0cc-a4aeacd38543	bae0fdc7bb26	QUARANTINED	\N	2026-11-16 21:07:36.071717+07	2026-11-16 21:07:36.263562+07
6c3be928-5856-4ca9-9ec6-5425dd3c6a2d	1	1e7e562f-3071-47a2-ae0b-eb593c2af7f3	bae0fdc7bb26	SUCCESS	\N	2026-11-16 21:15:57.324037+07	2026-11-16 21:15:57.710363+07
c45fc8e5-5029-45e9-a41a-92663268ca4e	1	1d7cb2c1-52d6-4cf0-95fd-86e9549da7c7	bae0fdc7bb26	SUCCESS	\N	2026-11-16 21:22:57.863363+07	2026-11-16 21:22:58.193257+07
d4a7c552-74d9-45be-a5c6-2f74612d578c	1	ab82f778-51b7-4e7c-a75e-e50c9d6017cf	bae0fdc7bb26	SUCCESS	\N	2026-11-16 21:26:07.399012+07	2026-11-16 21:26:07.693458+07
9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4	1	8c22c4e9-7732-4ddf-8156-2d3c119cb587	bae0fdc7bb26	SUCCESS	\N	2026-11-16 21:26:29.795087+07	2026-11-16 21:26:30.127713+07
63b18667-eb2a-49e1-8770-97d623d0a2e6	1	ba28814c-447a-4efd-8f77-7eddd37ce953	bae0fdc7bb26	SUCCESS	\N	2026-11-16 23:04:03.194162+07	2026-11-16 23:04:04.054225+07
\.


--
-- Data for Name: auto_pipeline_job; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.auto_pipeline_job (job_id, ingestion_job_id, dataset_id, policy, bronze_input, status, attempt_number, lease_token, lease_expires_at, worker_id, next_attempt_at, error_code, result, created_at, finished_at) FROM stdin;
22c2e420-b06a-45d9-ad48-290366ea6af2	1	1	{"mode": "automatic", "revision": 0, "profile_id": "automatic-22c2e420-b06a-45d9-ad48-290366ea6af2", "max_attempts": 3, "requested_by": "9", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-899188c733e26f94ba29ead9cf0e", "domain": "hr", "entity": "assignment", "version": "auto-source-v1", "contracts": {"hr": {"$defs": {"assignment": {"type": "object", "x-grain": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "x-owner": "hr", "required": ["assignment_id", "employee_id", "department_id", "position_id", "start_date", "end_date", "is_primary", "assignment_status", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "end_date": {"type": ["string", "null"], "format": "date", "x-physical-type": "date32"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "is_primary": {"type": "boolean", "x-physical-type": "boolean"}, "start_date": {"type": "string", "format": "date", "x-physical-type": "date32"}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "position_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "assignment_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "department_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "assignment_status": {"enum": ["Active", "Ended"], "type": "string", "maxLength": 30, "description": "Active khi end_date null hoặc sau ngày chốt", "x-physical-type": "varchar(30)"}}, "x-primary-key": ["assignment_id"], "x-identity-mode": "source", "x-unique-together": [], "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 1, "null_tokens": {"end_date": [""], "is_primary": [""], "start_date": [""], "employee_id": [""], "position_id": [""], "assignment_id": [""], "department_id": [""], "assignment_status": [""]}, "source_type": "CSV", "dictionaries": {"assignment_status": {}}, "effective_at": "2026-11-16T14:06:27.145583Z", "source_table": "assignment.csv", "type_options": {}, "field_mapping": {"end_date": "end_date", "is_primary": "is_primary", "start_date": "start_date", "employee_id": "employee_id", "position_id": "position_id", "assignment_id": "assignment_id", "department_id": "department_id", "assignment_status": "assignment_status"}, "identity_mode": "source", "source_schema": {"dataset": "hr", "version": "1.2.0", "entities": {"employee": {"file": "hr/employee.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^NV[0-9]{4}$", "nullable": false}, {"enum": ["Male", "Female"], "name": "gender", "type": "varchar(20)", "db_type": "string", "nullable": true}, {"name": "hire_date", "type": "date", "db_type": "date", "nullable": true, "description": "Khi có thì bằng start_date sớm nhất trong assignment"}, {"enum": ["Active", "Resigned", "Retired"], "name": "employee_status", "type": "varchar(30)", "db_type": "string", "nullable": false}, {"name": "birth_year", "type": "date", "db_type": "date", "nullable": true}], "description": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "primary_key": ["employee_id"], "owned_by_dataset": true}, "lecturer": {"file": "hr/lecturer.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"enum": ["B.Sc.", "M.Sc.", "Ph.D."], "name": "academic_degree", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học vị cao nhất"}, {"enum": ["Assoc. Prof.", "Prof."], "name": "academic_title", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học hàm; null nếu chưa có"}, {"name": "research_field", "type": "varchar(250)", "db_type": "string", "nullable": true}], "description": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "primary_key": ["employee_id"], "owned_by_dataset": true}, "position": {"file": "hr/position.csv", "domain": "hr", "columns": [{"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "position_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^[A-Z]{2,5}$", "nullable": false}, {"name": "position_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Leadership", "Management", "Staff"], "name": "position_level", "type": "varchar(50)", "db_type": "string", "nullable": true}], "description": "Chức danh/chức vụ.", "primary_key": ["position_id"], "owned_by_dataset": true}, "assignment": {"file": "hr/assignment.csv", "domain": "hr", "columns": [{"name": "assignment_id", "type": "text", "db_type": "string", "nullable": false, "source_identifier": true}, {"name": "employee_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}, "source_identifier": true}, {"name": "department_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "department_id", "entity": "department"}, "source_identifier": true}, {"name": "position_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "position_id", "entity": "position"}, "source_identifier": true}, {"name": "start_date", "type": "date", "db_type": "date", "nullable": false}, {"name": "end_date", "type": "date", "db_type": "date", "nullable": true}, {"name": "is_primary", "type": "boolean", "db_type": "boolean", "nullable": false}, {"enum": ["Active", "Ended"], "name": "assignment_status", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Active khi end_date null hoặc sau ngày chốt"}], "description": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "primary_key": ["assignment_id"], "owned_by_dataset": true}, "department": {"file": "hr/department.csv", "domain": "hr", "columns": [{"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "department_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "nullable": false, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)"}, {"name": "department_name", "type": "varchar(200)", "db_type": "string", "nullable": false}, {"enum": ["University", "Board", "Office", "Faculty", "Division"], "name": "department_type", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Trường, Ban, Phòng, Khoa, Bộ môn"}, {"name": "parent_department_id", "type": "bigint", "db_type": "bigint", "nullable": true, "references": {"column": "department_id", "entity": "department"}, "description": "Null = đơn vị gốc"}, {"enum": ["Active", "Inactive"], "name": "status", "type": "varchar(20)", "db_type": "string", "nullable": false}, {"name": "created_at", "type": "timestamp", "db_type": "timestamp", "nullable": false}], "description": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "primary_key": ["department_id"], "owned_by_dataset": true}, "qualification": {"file": "hr/qualification.csv", "domain": "hr", "columns": [{"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "qualification_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Degree", "Certificate"], "name": "qualification_type", "type": "varchar(100)", "db_type": "string", "nullable": true}], "description": "Danh mục văn bằng và chứng chỉ. DB không còn UNIQUE trên qualification_name; HR-DQ-13 kiểm tra trùng.", "primary_key": ["qualification_id"], "owned_by_dataset": true}, "employee_qualification": {"file": "hr/employee_qualification.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "qualification_id", "entity": "qualification"}}, {"name": "institution", "type": "varchar(250)", "db_type": "string", "nullable": true}, {"max": 2100, "min": 1960, "name": "year_obtained", "type": "int", "db_type": "int", "nullable": true}], "description": "Văn bằng/chứng chỉ của nhân sự (N-N giữa employee và qualification).", "primary_key": ["employee_id", "qualification_id"], "owned_by_dataset": true}}, "source_db": "docs/data-schema/unilake-db-architecture.txt", "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. Bám đúng DB nguồn docs/data-schema/unilake-db-architecture.txt (tên cột, thứ tự, db_type, nullable, PK, UNIQUE, FK). type là kiểu SQL đề xuất cho DDL vì DB chỉ khai báo kiểu chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "file_format": {"type": "csv", "header": true, "encoding": "utf-8", "delimiter": ",", "null_value": "", "date_format": "YYYY-MM-DD", "boolean_values": ["true", "false"], "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": "."}, "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "Hệ thống nguồn"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID lô ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Thời điểm ghi vào Bronze (UTC)"}], "quality_rules": [{"id": "HR-DQ-01", "rule": "PK (kể cả PK ghép) và cột unique không null, không trùng", "entity": "*", "severity": "error"}, {"id": "HR-DQ-02", "rule": "FK trỏ tới bản ghi có thật", "entity": "*", "severity": "error"}, {"id": "HR-DQ-03", "rule": "Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max", "entity": "*", "severity": "error"}, {"id": "HR-DQ-04", "rule": "Một gốc, không chu trình; department_type khớp tiền tố mã; bộ môn (Division) thuộc khoa (Faculty)", "entity": "department", "severity": "error"}, {"id": "HR-DQ-05", "rule": "Khi có ngày sinh và hire_date: tuổi khi tuyển 20 - 60; hire_date = start_date sớm nhất trong assignment", "entity": "employee", "severity": "error"}, {"id": "HR-DQ-06", "rule": "start_date >= hire_date (nếu có); end_date >= start_date; vị trí chính không chồng lấn", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-07", "rule": "Active có đúng một vị trí chính đang hiệu lực; Resigned/Retired không còn assignment mở", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-08", "rule": "HT, TK, TP, TBM: một người/đơn vị/thời điểm; chức vụ đúng loại đơn vị", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-09", "rule": "Chức vụ giảng dạy chỉ cho giảng viên; CV, NV, TP chỉ cho cán bộ hành chính", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-10", "rule": "Năm cấp sau 18 tuổi, không sau ngày chốt; Cử nhân/Kỹ sư < Thạc sĩ < Tiến sĩ", "entity": "employee_qualification", "severity": "error"}, {"id": "HR-DQ-11", "rule": "academic_degree khớp văn bằng cao nhất; academic_title (Assoc. Prof., Prof.) chỉ khi academic_degree = Ph.D.", "entity": "lecturer", "severity": "warning"}, {"id": "HR-DQ-12", "rule": "assignment_status khớp end_date (Active khi end_date null hoặc sau ngày chốt)", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-13", "rule": "qualification_name không trùng", "entity": "qualification", "severity": "error"}, {"id": "HR-DQ-14", "rule": "Giảng viên có assignment hiệu lực suốt khoảng phụ trách lớp (assigned_from..assigned_to, mặc định là học kỳ của lớp)", "entity": "class_lecturer (Academic)", "severity": "error"}], "position_rules": {"staff_only": ["CV", "NV", "TP"], "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "allowed_department_types": {"CV": ["Office"], "GV": ["Faculty", "Division"], "HT": ["Board"], "NV": ["Office"], "TG": ["Faculty", "Division"], "TK": ["Faculty"], "TP": ["Office"], "GVC": ["Faculty", "Division"], "PHT": ["Board"], "PTK": ["Faculty"], "TBM": ["Division"]}, "unique_holder_per_department": ["HT", "TK", "TP", "TBM"]}, "department_type_prefix": {"Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM", "University": "UNI"}, "silver_derived_columns": {"employee": [{"name": "birth_year", "rule": "Thay date_of_birth (PII)", "type": "int"}, {"name": "is_lecturer", "rule": "Có dòng trong lecturer", "type": "boolean"}, {"name": "primary_department_id", "rule": "department_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "faculty_id", "rule": "Đơn vị con trực tiếp của đơn vị gốc chứa primary_department_id", "type": "bigint"}, {"name": "current_position_id", "rule": "position_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "seniority_years", "rule": "Từ hire_date (hoặc assignment sớm nhất) tới ngày chốt hoặc ngày nghỉ", "type": "decimal(4,1)"}, {"name": "highest_degree", "rule": "Tiến sĩ > Thạc sĩ > Kỹ sư/Cử nhân theo employee_qualification", "type": "varchar(30)"}, {"name": "leave_date", "rule": "end_date lớn nhất của assignment khi Resigned/Retired", "type": "date"}]}}, "data_source_id": 1, "format_options": {"end_date": {"date_formats": ["%Y-%m-%d"]}, "start_date": {"date_formats": ["%Y-%m-%d"]}}, "ignored_columns": [], "source_system_id": 34, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "1.2.0", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/hr/2026/11/16/job-1/assignment.csv", "domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "lineage": {"mode": "ingestion", "run_ref": "ingestion:1", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/26226c35a4d84d5d8add2bba721b05ec/assignment.csv", "layer": "SOURCE", "domain": "hr", "version": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22", "asset_id": "source:1:assignment.csv", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-1/assignment.csv", "layer": "BRONZE", "domain": "hr", "version": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "asset_id": "dataset:1", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": "ingestion:1"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "batch-id": "job-1", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:06:27.571071+00:00"}, "size_bytes": 7286, "version_id": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22"}	SUCCESS	1	0d51c3f8-aa4e-4525-b780-c936ed77f7fa	\N	bae0fdc7bb26	\N	\N	{"domain": "hr", "entity": "assignment", "issues": [{"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:1", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:2", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:3", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:4", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:5", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:6", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:7", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:8", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:9", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:10", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:11", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:12", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:13", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:14", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:15", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:16", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:17", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:18", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:19", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:20", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:21", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:22", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:23", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:24", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:25", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:26", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:27", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:28", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:29", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:30", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:31", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:32", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:33", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:34", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:35", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:36", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:37", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:38", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:39", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:40", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:41", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:42", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:43", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:44", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:45", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:46", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:47", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:48", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:49", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:50", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:51", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:52", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:53", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:54", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:55", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:56", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:57", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:58", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:59", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:60", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:61", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:62", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:63", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:64", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:65", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:66", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:67", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:68", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:69", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:70", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:71", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:72", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:73", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:74", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:75", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:76", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:77", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:78", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:79", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:80", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:81", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:82", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:83", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:84", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:85", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:86", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:87", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:88", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:89", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:90", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:91", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:92", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:93", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:94", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:95", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:96", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:97", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:98", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:99", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:100", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:101", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:102", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:103", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:104", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:105", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:106", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:107", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:108", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:109", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:110", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:111", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:112", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:113", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:114", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:115", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:116", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:117", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:118", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:119", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:120", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:121", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:122", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:123", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:124", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:125", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:126", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:127", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:128", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:129", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:130", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:131", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:132", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:133", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:134", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:135", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:136", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:137", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:138", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:139", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:140", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:141", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:142", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:143", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:144", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:145", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:146", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:147", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:148", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:149", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:150", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:151", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:152", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:153", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:154", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:155", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:156", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:157", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:158", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:159", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:160", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:161", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:162", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:163", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:164", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:165", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:166", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:167", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:168", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:169", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:170", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:171", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:172", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:173", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:174", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:175", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:176", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:177", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:178", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:179", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:180", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:181", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:182", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:183", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:184", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:185", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:186", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:187", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:188", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:189", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:190", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:191", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:192", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:193", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:194", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:195", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:196", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:197", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:198", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:199", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:200", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}], "run_id": "dc17ddcb-0eed-4f83-af4d-87dbd38ab94c", "status": "PUBLISHED", "profile_id": "automatic-22c2e420-b06a-45d9-ad48-290366ea6af2", "publication": {"mode": "production", "scope": "production:auto-899188c733e26f94ba29ead9cf0e", "entity": "assignment", "snapshot_id": "52df8123-672a-425d-93a1-04f53de4a774", "records_published": 200}, "records_read": 200, "requested_by": "9", "input_version": "sha256:3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "records_passed": 200, "ingestion_job_id": 1, "profile_approval": {"job_id": "22c2e420-b06a-45d9-ad48-290366ea6af2", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004", "records_quarantined": 0}	2026-11-16 21:06:27.70821+07	2026-11-16 21:06:31.214306+07
c6b682d5-1d7e-4531-a632-b9f9b3337743	3	3	{"mode": "automatic", "revision": 0, "profile_id": "automatic-c6b682d5-1d7e-4531-a632-b9f9b3337743", "max_attempts": 3, "requested_by": "7", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-91a9e7cbf19dfbcbc68cea2d878b", "domain": "academic", "entity": "file_3", "version": "auto-source-v1", "contracts": {"academic": {"$defs": {"file_3": {"type": "object", "x-owner": "academic", "required": ["enrollment_id", "student_id", "student_name", "course_code", "course_name", "semester", "academic_year", "credits", "score", "status", "enrollment_date", "note", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"note": {"type": ["string", "null"], "x-physical-type": "utf8"}, "score": {"type": ["string", "null"], "x-physical-type": "utf8"}, "status": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "credits": {"type": ["integer", "null"], "x-physical-type": "int64"}, "semester": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "student_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "course_code": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "course_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "student_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "academic_year": {"type": ["string", "null"], "x-physical-type": "utf8"}, "enrollment_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "enrollment_date": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-primary-key": ["_source_record_id"], "x-identity-mode": "source", "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 3, "null_tokens": {"note": [""], "score": [""], "status": [""], "credits": [""], "semester": [""], "student_id": [""], "course_code": [""], "course_name": [""], "student_name": [""], "academic_year": [""], "enrollment_id": [""], "enrollment_date": [""]}, "source_type": "EXCEL", "dictionaries": {}, "effective_at": "2026-11-16T14:07:33.418484Z", "source_table": "academic_du_lieu_loi.xlsx", "type_options": {}, "field_mapping": {"note": "note", "score": "score", "status": "status", "credits": "credits", "semester": "semester", "student_id": "student_id", "course_code": "course_code", "course_name": "course_name", "student_name": "student_name", "academic_year": "academic_year", "enrollment_id": "enrollment_id", "enrollment_date": "enrollment_date"}, "identity_mode": "source", "source_schema": {"version": "source-1", "entities": {"file_3": {"columns": [{"name": "enrollment_id", "type": "text", "db_type": "string", "nullable": true, "source_identifier": true}, {"name": "student_id", "type": "text", "db_type": "string", "nullable": true, "source_identifier": true}, {"name": "student_name", "type": "text", "db_type": "string", "nullable": true}, {"name": "course_code", "type": "text", "db_type": "string", "nullable": true, "source_identifier": true}, {"name": "course_name", "type": "text", "db_type": "string", "nullable": true}, {"name": "semester", "type": "text", "db_type": "string", "nullable": true}, {"name": "academic_year", "type": "text", "db_type": "string", "nullable": true}, {"name": "credits", "type": "bigint", "db_type": "bigint", "nullable": true}, {"name": "score", "type": "text", "db_type": "string", "nullable": true}, {"name": "status", "type": "text", "db_type": "string", "nullable": true}, {"name": "enrollment_date", "type": "text", "db_type": "string", "nullable": true}, {"name": "note", "type": "text", "db_type": "string", "nullable": true}], "primary_key": []}}, "file_format": {"date_format": "YYYY-MM-DD"}}, "data_source_id": 3, "format_options": {}, "ignored_columns": [], "source_system_id": 35, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "source-1", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "domain": "academic", "sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "lineage": {"mode": "ingestion", "run_ref": "ingestion:3", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/3/e8822a0e9da646d9ad11fcf64295fccd/academic_du_lieu_loi.xlsx", "layer": "SOURCE", "domain": "academic", "version": "69b84f2d-51a0-431c-bd4e-f1e28a7de582", "asset_id": "source:3:academic_du_lieu_loi.xlsx", "checksum": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "producer_run_ref": null}], "output": {"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "layer": "BRONZE", "domain": "academic", "version": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "asset_id": "dataset:3", "checksum": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "producer_run_ref": "ingestion:3"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "batch-id": "job-3", "source-system-id": "35", "ingestion-timestamp": "2026-11-16T14:07:34.643533+00:00"}, "size_bytes": 9522, "version_id": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "source_type": "EXCEL", "records_read": 100, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": "Academic"}, "source_version_id": "69b84f2d-51a0-431c-bd4e-f1e28a7de582"}	QUARANTINED	1	2d14b3ef-57df-44e6-b0cc-a4aeacd38543	\N	bae0fdc7bb26	\N	\N	{"domain": "academic", "entity": "file_3", "issues": [{"code": "NULL_VALUE_PRESERVED", "field": "student_id", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:16", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "NULL_VALUE_PRESERVED", "field": "course_code", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:61", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:2", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:3", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:4", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:5", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:6", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:7", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:8", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:9", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:10", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:11", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:12", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:13", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:14", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:15", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:16", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:17", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:18", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:19", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:20", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:21", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:22", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:23", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:24", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:25", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:26", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:27", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:28", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:29", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:30", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:31", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:32", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:33", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:34", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:35", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:36", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:37", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:38", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:39", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:40", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:41", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:42", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:43", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:44", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:45", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:46", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:47", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:48", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:49", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:50", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:51", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:52", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:53", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:54", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:55", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:56", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:57", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:58", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:59", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:60", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:61", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:62", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:63", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:64", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:65", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:66", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:67", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:68", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:69", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:70", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:71", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:72", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:73", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:74", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:75", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:77", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:78", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:79", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:80", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:81", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:82", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:83", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:84", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:85", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:86", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:87", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:88", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:89", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:90", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:91", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:92", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:93", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:94", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:95", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:96", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:97", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:98", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:99", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:100", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, {"code": "INVALID_STRING_TYPE", "field": "score", "domain": "academic", "entity": "file_3", "locator": "sheet:Academic:row:101", "rule_id": "CLN-VAL-01", "severity": "ERROR", "disposition": "QUARANTINED", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}], "run_id": "dee1dae0-81cd-47ce-affd-5ac9e8af266b", "status": "QUARANTINED", "profile_id": "automatic-c6b682d5-1d7e-4531-a632-b9f9b3337743", "publication": null, "records_read": 100, "requested_by": "7", "input_version": "sha256:bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "records_passed": 1, "ingestion_job_id": 3, "profile_approval": {"job_id": "c6b682d5-1d7e-4531-a632-b9f9b3337743", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5"}, "partial_key_check": {"reason": "SOURCE_PRIMARY_KEY_REQUIRED_FOR_PARTIAL_PUBLICATION", "status": "BLOCKED"}, "quarantine_report": {"key": "silver/reports/dee1dae0-81cd-47ce-affd-5ac9e8af266b/report-b7a7619f5c035c36f662306abd05076b3a8ee8f40bb50ff0d69b3e10a22856e6.json", "size": 31608, "sha256": "b7a7619f5c035c36f662306abd05076b3a8ee8f40bb50ff0d69b3e10a22856e6", "version_id": null}, "records_collapsed": 0, "profile_fingerprint": "74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "records_quarantined": 99}	2026-11-16 21:07:34.699024+07	2026-11-16 21:07:36.263562+07
2cfe4d5c-255b-48b6-95c8-218009d675cf	2	2	{"mode": "automatic", "revision": 0, "profile_id": "automatic-2cfe4d5c-255b-48b6-95c8-218009d675cf", "max_attempts": 3, "requested_by": "7", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-5cb5b4e2548a0f888863ffd97381", "domain": "academic", "entity": "file_2", "version": "auto-source-v1", "contracts": {"academic": {"$defs": {"file_2": {"type": "object", "x-owner": "academic", "required": ["enrollment_id", "student_id", "student_name", "course_code", "course_name", "semester", "academic_year", "credits", "score", "status", "enrollment_date", "note", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"note": {"type": ["string", "null"], "x-physical-type": "utf8"}, "score": {"type": ["string", "null"], "x-scale": 10, "x-precision": 38, "x-physical-type": "decimal(38,10)"}, "status": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "credits": {"type": ["integer", "null"], "x-physical-type": "int64"}, "semester": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "student_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "course_code": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "course_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "student_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "academic_year": {"type": ["string", "null"], "x-physical-type": "utf8"}, "enrollment_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "enrollment_date": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-primary-key": ["_source_record_id"], "x-identity-mode": "source", "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 2, "null_tokens": {"note": [""], "score": [""], "status": [""], "credits": [""], "semester": [""], "student_id": [""], "course_code": [""], "course_name": [""], "student_name": [""], "academic_year": [""], "enrollment_id": [""], "enrollment_date": [""]}, "source_type": "EXCEL", "dictionaries": {}, "effective_at": "2026-11-16T14:07:16.083365Z", "source_table": "academic_du_lieu_chuan.xlsx", "type_options": {}, "field_mapping": {"note": "note", "score": "score", "status": "status", "credits": "credits", "semester": "semester", "student_id": "student_id", "course_code": "course_code", "course_name": "course_name", "student_name": "student_name", "academic_year": "academic_year", "enrollment_id": "enrollment_id", "enrollment_date": "enrollment_date"}, "identity_mode": "source", "source_schema": {"version": "source-1", "entities": {"file_2": {"columns": [{"name": "enrollment_id", "type": "text", "db_type": "string", "nullable": true, "source_identifier": true}, {"name": "student_id", "type": "text", "db_type": "string", "nullable": true, "source_identifier": true}, {"name": "student_name", "type": "text", "db_type": "string", "nullable": true}, {"name": "course_code", "type": "text", "db_type": "string", "nullable": true, "source_identifier": true}, {"name": "course_name", "type": "text", "db_type": "string", "nullable": true}, {"name": "semester", "type": "text", "db_type": "string", "nullable": true}, {"name": "academic_year", "type": "text", "db_type": "string", "nullable": true}, {"name": "credits", "type": "bigint", "db_type": "bigint", "nullable": true}, {"name": "score", "type": "decimal(38,10)", "db_type": "decimal", "nullable": true}, {"name": "status", "type": "text", "db_type": "string", "nullable": true}, {"name": "enrollment_date", "type": "text", "db_type": "string", "nullable": true}, {"name": "note", "type": "text", "db_type": "string", "nullable": true}], "primary_key": []}}, "file_format": {"date_format": "YYYY-MM-DD"}}, "data_source_id": 2, "format_options": {}, "ignored_columns": [], "source_system_id": 35, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "source-1", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx", "domain": "academic", "sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "lineage": {"mode": "ingestion", "run_ref": "ingestion:2", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/2/15fed72597154ac5b9e059c815d41f31/academic_du_lieu_chuan.xlsx", "layer": "SOURCE", "domain": "academic", "version": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6", "asset_id": "source:2:academic_du_lieu_chuan.xlsx", "checksum": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "producer_run_ref": null}], "output": {"uri": "s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx", "layer": "BRONZE", "domain": "academic", "version": "4cde87fd-70f8-4818-8d64-89dd065b3795", "asset_id": "dataset:2", "checksum": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "producer_run_ref": "ingestion:2"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "batch-id": "job-2", "source-system-id": "35", "ingestion-timestamp": "2026-11-16T14:07:16.260404+00:00"}, "size_bytes": 9391, "version_id": "4cde87fd-70f8-4818-8d64-89dd065b3795", "source_type": "EXCEL", "records_read": 100, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": "Academic"}, "source_version_id": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6"}	SUCCESS	1	889ba554-8c6e-474b-9cc6-fadafc56a302	\N	bae0fdc7bb26	\N	\N	{"domain": "academic", "entity": "file_2", "issues": [{"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:2", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:3", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:4", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:5", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:6", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:7", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:8", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:9", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:10", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:11", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:12", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:13", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:14", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:15", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:16", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:17", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:18", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:19", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:20", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:21", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:22", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:23", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:24", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:25", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:26", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:27", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:28", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:29", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:30", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:31", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:32", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:33", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:34", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:35", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:36", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:37", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:38", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:39", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:40", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:41", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:42", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:43", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:44", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:45", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:46", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:47", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:48", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:49", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:50", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:51", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:52", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:53", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:54", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:55", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:56", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:57", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:58", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:59", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:60", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:61", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:62", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:63", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:64", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:65", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:66", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:67", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:68", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:69", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:70", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:71", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:72", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:73", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:74", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:75", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:76", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:77", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:78", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:79", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:80", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:81", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:82", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:83", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:84", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:85", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:86", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:87", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:88", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:89", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:90", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:91", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:92", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:93", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:94", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:95", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:96", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:97", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:98", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:99", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:100", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, {"code": "DECIMAL_FORMAT_CANONICALIZED", "field": "score", "domain": "academic", "entity": "file_2", "locator": "sheet:Academic:row:101", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}], "run_id": "c57c99da-0ae6-41e4-bc7a-59755f0627ed", "status": "PUBLISHED", "profile_id": "automatic-2cfe4d5c-255b-48b6-95c8-218009d675cf", "publication": {"mode": "production", "scope": "production:auto-5cb5b4e2548a0f888863ffd97381", "entity": "file_2", "snapshot_id": "539ae500-2f13-451b-be83-286f62b636f9", "records_published": 100}, "records_read": 100, "requested_by": "7", "input_version": "sha256:e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "records_passed": 100, "ingestion_job_id": 2, "profile_approval": {"job_id": "2cfe4d5c-255b-48b6-95c8-218009d675cf", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35", "records_quarantined": 0}	2026-11-16 21:07:16.345735+07	2026-11-16 21:07:17.960015+07
6c3be928-5856-4ca9-9ec6-5425dd3c6a2d	4	1	{"mode": "automatic", "revision": 0, "profile_id": "automatic-6c3be928-5856-4ca9-9ec6-5425dd3c6a2d", "max_attempts": 3, "requested_by": "9", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-7858eb8f709a3e0a1583c6d93948", "domain": "hr", "entity": "department", "version": "auto-source-v1", "contracts": {"hr": {"$defs": {"department": {"type": "object", "x-grain": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "x-owner": "hr", "required": ["department_id", "department_code", "department_name", "department_type", "parent_department_id", "status", "created_at", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"status": {"enum": ["Active", "Inactive"], "type": "string", "maxLength": 20, "x-physical-type": "varchar(20)"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "created_at": {"type": "string", "format": "date-time", "pattern": "Z$", "x-physical-type": "timestamp[us,UTC]"}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "department_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "department_code": {"type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "maxLength": 30, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)", "x-physical-type": "varchar(30)", "x-unique-non-null": true}, "department_name": {"type": "string", "maxLength": 200, "x-physical-type": "varchar(200)"}, "department_type": {"enum": ["University", "Board", "Office", "Faculty", "Division"], "type": "string", "maxLength": 30, "description": "Trường, Ban, Phòng, Khoa, Bộ môn", "x-physical-type": "varchar(30)"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "parent_department_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}}, "x-primary-key": ["department_id"], "x-identity-mode": "source", "x-unique-together": [], "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 1, "null_tokens": {"status": [""], "created_at": [""], "department_id": [""], "department_code": [""], "department_name": [""], "department_type": [""], "parent_department_id": [""]}, "source_type": "CSV", "dictionaries": {"status": {}, "department_type": {}}, "effective_at": "2026-11-16T14:15:55.456478Z", "source_table": "department.csv", "type_options": {}, "field_mapping": {"status": "status", "created_at": "created_at", "department_id": "department_id", "department_code": "department_code", "department_name": "department_name", "department_type": "department_type", "parent_department_id": "parent_department_id"}, "identity_mode": "source", "source_schema": {"dataset": "hr", "version": "1.2.0", "entities": {"employee": {"file": "hr/employee.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^NV[0-9]{4}$", "nullable": false}, {"enum": ["Male", "Female"], "name": "gender", "type": "varchar(20)", "db_type": "string", "nullable": true}, {"name": "hire_date", "type": "date", "db_type": "date", "nullable": true, "description": "Khi có thì bằng start_date sớm nhất trong assignment"}, {"enum": ["Active", "Resigned", "Retired"], "name": "employee_status", "type": "varchar(30)", "db_type": "string", "nullable": false}, {"name": "birth_year", "type": "date", "db_type": "date", "nullable": true}], "description": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "primary_key": ["employee_id"], "owned_by_dataset": true}, "lecturer": {"file": "hr/lecturer.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"enum": ["B.Sc.", "M.Sc.", "Ph.D."], "name": "academic_degree", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học vị cao nhất"}, {"enum": ["Assoc. Prof.", "Prof."], "name": "academic_title", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học hàm; null nếu chưa có"}, {"name": "research_field", "type": "varchar(250)", "db_type": "string", "nullable": true}], "description": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "primary_key": ["employee_id"], "owned_by_dataset": true}, "position": {"file": "hr/position.csv", "domain": "hr", "columns": [{"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "position_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^[A-Z]{2,5}$", "nullable": false}, {"name": "position_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Leadership", "Management", "Staff"], "name": "position_level", "type": "varchar(50)", "db_type": "string", "nullable": true}], "description": "Chức danh/chức vụ.", "primary_key": ["position_id"], "owned_by_dataset": true}, "assignment": {"file": "hr/assignment.csv", "domain": "hr", "columns": [{"name": "assignment_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "department_id", "entity": "department"}}, {"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "position_id", "entity": "position"}}, {"name": "start_date", "type": "date", "db_type": "date", "nullable": false}, {"name": "end_date", "type": "date", "db_type": "date", "nullable": true}, {"name": "is_primary", "type": "boolean", "db_type": "boolean", "nullable": false}, {"enum": ["Active", "Ended"], "name": "assignment_status", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Active khi end_date null hoặc sau ngày chốt"}], "description": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "primary_key": ["assignment_id"], "owned_by_dataset": true}, "department": {"file": "hr/department.csv", "domain": "hr", "columns": [{"name": "department_id", "type": "text", "db_type": "string", "nullable": false, "source_identifier": true}, {"name": "department_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "nullable": false, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)"}, {"name": "department_name", "type": "varchar(200)", "db_type": "string", "nullable": false}, {"enum": ["University", "Board", "Office", "Faculty", "Division"], "name": "department_type", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Trường, Ban, Phòng, Khoa, Bộ môn"}, {"name": "parent_department_id", "type": "text", "db_type": "string", "nullable": true, "references": {"column": "department_id", "entity": "department"}, "description": "Null = đơn vị gốc", "source_identifier": true}, {"enum": ["Active", "Inactive"], "name": "status", "type": "varchar(20)", "db_type": "string", "nullable": false}, {"name": "created_at", "type": "timestamp", "db_type": "timestamp", "nullable": false}], "description": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "primary_key": ["department_id"], "owned_by_dataset": true}, "qualification": {"file": "hr/qualification.csv", "domain": "hr", "columns": [{"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "qualification_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Degree", "Certificate"], "name": "qualification_type", "type": "varchar(100)", "db_type": "string", "nullable": true}], "description": "Danh mục văn bằng và chứng chỉ. DB không còn UNIQUE trên qualification_name; HR-DQ-13 kiểm tra trùng.", "primary_key": ["qualification_id"], "owned_by_dataset": true}, "employee_qualification": {"file": "hr/employee_qualification.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "qualification_id", "entity": "qualification"}}, {"name": "institution", "type": "varchar(250)", "db_type": "string", "nullable": true}, {"max": 2100, "min": 1960, "name": "year_obtained", "type": "int", "db_type": "int", "nullable": true}], "description": "Văn bằng/chứng chỉ của nhân sự (N-N giữa employee và qualification).", "primary_key": ["employee_id", "qualification_id"], "owned_by_dataset": true}}, "source_db": "docs/data-schema/unilake-db-architecture.txt", "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. Bám đúng DB nguồn docs/data-schema/unilake-db-architecture.txt (tên cột, thứ tự, db_type, nullable, PK, UNIQUE, FK). type là kiểu SQL đề xuất cho DDL vì DB chỉ khai báo kiểu chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "file_format": {"type": "csv", "header": true, "encoding": "utf-8", "delimiter": ",", "null_value": "", "date_format": "YYYY-MM-DD", "boolean_values": ["true", "false"], "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": "."}, "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "Hệ thống nguồn"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID lô ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Thời điểm ghi vào Bronze (UTC)"}], "quality_rules": [{"id": "HR-DQ-01", "rule": "PK (kể cả PK ghép) và cột unique không null, không trùng", "entity": "*", "severity": "error"}, {"id": "HR-DQ-02", "rule": "FK trỏ tới bản ghi có thật", "entity": "*", "severity": "error"}, {"id": "HR-DQ-03", "rule": "Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max", "entity": "*", "severity": "error"}, {"id": "HR-DQ-04", "rule": "Một gốc, không chu trình; department_type khớp tiền tố mã; bộ môn (Division) thuộc khoa (Faculty)", "entity": "department", "severity": "error"}, {"id": "HR-DQ-05", "rule": "Khi có ngày sinh và hire_date: tuổi khi tuyển 20 - 60; hire_date = start_date sớm nhất trong assignment", "entity": "employee", "severity": "error"}, {"id": "HR-DQ-06", "rule": "start_date >= hire_date (nếu có); end_date >= start_date; vị trí chính không chồng lấn", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-07", "rule": "Active có đúng một vị trí chính đang hiệu lực; Resigned/Retired không còn assignment mở", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-08", "rule": "HT, TK, TP, TBM: một người/đơn vị/thời điểm; chức vụ đúng loại đơn vị", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-09", "rule": "Chức vụ giảng dạy chỉ cho giảng viên; CV, NV, TP chỉ cho cán bộ hành chính", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-10", "rule": "Năm cấp sau 18 tuổi, không sau ngày chốt; Cử nhân/Kỹ sư < Thạc sĩ < Tiến sĩ", "entity": "employee_qualification", "severity": "error"}, {"id": "HR-DQ-11", "rule": "academic_degree khớp văn bằng cao nhất; academic_title (Assoc. Prof., Prof.) chỉ khi academic_degree = Ph.D.", "entity": "lecturer", "severity": "warning"}, {"id": "HR-DQ-12", "rule": "assignment_status khớp end_date (Active khi end_date null hoặc sau ngày chốt)", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-13", "rule": "qualification_name không trùng", "entity": "qualification", "severity": "error"}, {"id": "HR-DQ-14", "rule": "Giảng viên có assignment hiệu lực suốt khoảng phụ trách lớp (assigned_from..assigned_to, mặc định là học kỳ của lớp)", "entity": "class_lecturer (Academic)", "severity": "error"}], "position_rules": {"staff_only": ["CV", "NV", "TP"], "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "allowed_department_types": {"CV": ["Office"], "GV": ["Faculty", "Division"], "HT": ["Board"], "NV": ["Office"], "TG": ["Faculty", "Division"], "TK": ["Faculty"], "TP": ["Office"], "GVC": ["Faculty", "Division"], "PHT": ["Board"], "PTK": ["Faculty"], "TBM": ["Division"]}, "unique_holder_per_department": ["HT", "TK", "TP", "TBM"]}, "department_type_prefix": {"Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM", "University": "UNI"}, "silver_derived_columns": {"employee": [{"name": "birth_year", "rule": "Thay date_of_birth (PII)", "type": "int"}, {"name": "is_lecturer", "rule": "Có dòng trong lecturer", "type": "boolean"}, {"name": "primary_department_id", "rule": "department_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "faculty_id", "rule": "Đơn vị con trực tiếp của đơn vị gốc chứa primary_department_id", "type": "bigint"}, {"name": "current_position_id", "rule": "position_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "seniority_years", "rule": "Từ hire_date (hoặc assignment sớm nhất) tới ngày chốt hoặc ngày nghỉ", "type": "decimal(4,1)"}, {"name": "highest_degree", "rule": "Tiến sĩ > Thạc sĩ > Kỹ sư/Cử nhân theo employee_qualification", "type": "varchar(30)"}, {"name": "leave_date", "rule": "end_date lớn nhất của assignment khi Resigned/Retired", "type": "date"}]}}, "data_source_id": 1, "format_options": {"created_at": {"timestamp_formats": ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"]}}, "ignored_columns": [], "source_system_id": 34, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "1.2.0", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/hr/2026/11/16/job-4/department.csv", "domain": "hr", "sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "lineage": {"mode": "ingestion", "run_ref": "ingestion:4", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/0a96804285494784b2b30a16bf8f46e5/department.csv", "layer": "SOURCE", "domain": "hr", "version": "686a2ae2-80af-4809-b431-c713393aa9ba", "asset_id": "source:1:department.csv", "checksum": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-4/department.csv", "layer": "BRONZE", "domain": "hr", "version": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "asset_id": "dataset:1", "checksum": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "producer_run_ref": "ingestion:4"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "batch-id": "job-4", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:15:56.029943+00:00"}, "size_bytes": 419, "version_id": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "source_type": "CSV", "records_read": 4, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "686a2ae2-80af-4809-b431-c713393aa9ba"}	SUCCESS	1	1e7e562f-3071-47a2-ae0b-eb593c2af7f3	\N	bae0fdc7bb26	\N	\N	{"domain": "hr", "entity": "department", "issues": [{"code": "NULL_VALUE_PRESERVED", "field": "parent_department_id", "domain": "hr", "entity": "department", "locator": "record:1", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547"}, {"code": "TIMESTAMP_FORMAT_CANONICALIZED", "field": "created_at", "domain": "hr", "entity": "department", "locator": "record:1", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547"}, {"code": "TIMESTAMP_FORMAT_CANONICALIZED", "field": "created_at", "domain": "hr", "entity": "department", "locator": "record:2", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547"}, {"code": "TIMESTAMP_FORMAT_CANONICALIZED", "field": "created_at", "domain": "hr", "entity": "department", "locator": "record:3", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547"}, {"code": "TIMESTAMP_FORMAT_CANONICALIZED", "field": "created_at", "domain": "hr", "entity": "department", "locator": "record:4", "rule_id": "CLN-FMT-01", "severity": "INFO", "disposition": "NORMALIZED", "rule_version": "auto-source-v1:5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547"}], "run_id": "cccba0f5-32ff-466a-b1bd-4692a1830a46", "status": "PUBLISHED", "profile_id": "automatic-6c3be928-5856-4ca9-9ec6-5425dd3c6a2d", "publication": {"mode": "production", "scope": "production:auto-7858eb8f709a3e0a1583c6d93948", "entity": "department", "snapshot_id": "1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf", "records_published": 4}, "records_read": 4, "requested_by": "9", "input_version": "sha256:efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "records_passed": 4, "ingestion_job_id": 4, "profile_approval": {"job_id": "6c3be928-5856-4ca9-9ec6-5425dd3c6a2d", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547", "records_quarantined": 0}	2026-11-16 21:15:56.078445+07	2026-11-16 21:15:57.710363+07
c45fc8e5-5029-45e9-a41a-92663268ca4e	5	1	{"mode": "automatic", "revision": 0, "profile_id": "automatic-c45fc8e5-5029-45e9-a41a-92663268ca4e", "max_attempts": 3, "requested_by": "9", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-e270bc9994f1530b4a540c0daa6c", "domain": "hr", "entity": "employee", "version": "auto-source-v1", "contracts": {"hr": {"$defs": {"employee": {"type": "object", "x-grain": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "x-owner": "hr", "required": ["employee_id", "employee_code", "gender", "hire_date", "employee_status", "birth_year", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"gender": {"enum": ["Male", "Female", null], "type": ["string", "null"], "maxLength": 20, "x-physical-type": "varchar(20)"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "hire_date": {"type": ["string", "null"], "format": "date", "description": "Khi có thì bằng start_date sớm nhất trong assignment", "x-physical-type": "date32"}, "birth_year": {"type": ["integer", "null"], "x-rule": "PII-02", "maximum": 9999, "minimum": 1, "x-derived-from": "date_of_birth", "x-physical-type": "int32"}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "employee_code": {"type": "string", "pattern": "^NV[0-9]{4}$", "maxLength": 30, "x-physical-type": "varchar(30)", "x-unique-non-null": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "employee_status": {"enum": ["Active", "Resigned", "Retired"], "type": "string", "maxLength": 30, "x-physical-type": "varchar(30)"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-primary-key": ["employee_id"], "x-identity-mode": "source", "x-unique-together": [], "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 1, "null_tokens": {"gender": [""], "hire_date": [""], "birth_year": [""], "employee_id": [""], "employee_code": [""], "employee_status": [""]}, "source_type": "CSV", "dictionaries": {"gender": {}, "employee_status": {}}, "effective_at": "2026-11-16T14:22:55.388069Z", "source_table": "employee.csv", "type_options": {}, "field_mapping": {"gender": "gender", "hire_date": "hire_date", "birth_year": "date_of_birth", "employee_id": "employee_id", "employee_code": "employee_code", "employee_status": "employee_status"}, "identity_mode": "source", "source_schema": {"dataset": "hr", "version": "1.2.0", "entities": {"employee": {"file": "hr/employee.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "text", "db_type": "string", "nullable": false, "source_identifier": true}, {"name": "employee_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^NV[0-9]{4}$", "nullable": false}, {"enum": ["Male", "Female"], "name": "gender", "type": "varchar(20)", "db_type": "string", "nullable": true}, {"name": "hire_date", "type": "date", "db_type": "date", "nullable": true, "description": "Khi có thì bằng start_date sớm nhất trong assignment"}, {"enum": ["Active", "Resigned", "Retired"], "name": "employee_status", "type": "varchar(30)", "db_type": "string", "nullable": false}, {"name": "birth_year", "type": "date", "db_type": "date", "nullable": true}], "description": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "primary_key": ["employee_id"], "owned_by_dataset": true}, "lecturer": {"file": "hr/lecturer.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"enum": ["B.Sc.", "M.Sc.", "Ph.D."], "name": "academic_degree", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học vị cao nhất"}, {"enum": ["Assoc. Prof.", "Prof."], "name": "academic_title", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học hàm; null nếu chưa có"}, {"name": "research_field", "type": "varchar(250)", "db_type": "string", "nullable": true}], "description": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "primary_key": ["employee_id"], "owned_by_dataset": true}, "position": {"file": "hr/position.csv", "domain": "hr", "columns": [{"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "position_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^[A-Z]{2,5}$", "nullable": false}, {"name": "position_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Leadership", "Management", "Staff"], "name": "position_level", "type": "varchar(50)", "db_type": "string", "nullable": true}], "description": "Chức danh/chức vụ.", "primary_key": ["position_id"], "owned_by_dataset": true}, "assignment": {"file": "hr/assignment.csv", "domain": "hr", "columns": [{"name": "assignment_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "department_id", "entity": "department"}}, {"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "position_id", "entity": "position"}}, {"name": "start_date", "type": "date", "db_type": "date", "nullable": false}, {"name": "end_date", "type": "date", "db_type": "date", "nullable": true}, {"name": "is_primary", "type": "boolean", "db_type": "boolean", "nullable": false}, {"enum": ["Active", "Ended"], "name": "assignment_status", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Active khi end_date null hoặc sau ngày chốt"}], "description": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "primary_key": ["assignment_id"], "owned_by_dataset": true}, "department": {"file": "hr/department.csv", "domain": "hr", "columns": [{"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "department_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "nullable": false, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)"}, {"name": "department_name", "type": "varchar(200)", "db_type": "string", "nullable": false}, {"enum": ["University", "Board", "Office", "Faculty", "Division"], "name": "department_type", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Trường, Ban, Phòng, Khoa, Bộ môn"}, {"name": "parent_department_id", "type": "bigint", "db_type": "bigint", "nullable": true, "references": {"column": "department_id", "entity": "department"}, "description": "Null = đơn vị gốc"}, {"enum": ["Active", "Inactive"], "name": "status", "type": "varchar(20)", "db_type": "string", "nullable": false}, {"name": "created_at", "type": "timestamp", "db_type": "timestamp", "nullable": false}], "description": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "primary_key": ["department_id"], "owned_by_dataset": true}, "qualification": {"file": "hr/qualification.csv", "domain": "hr", "columns": [{"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "qualification_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Degree", "Certificate"], "name": "qualification_type", "type": "varchar(100)", "db_type": "string", "nullable": true}], "description": "Danh mục văn bằng và chứng chỉ. DB không còn UNIQUE trên qualification_name; HR-DQ-13 kiểm tra trùng.", "primary_key": ["qualification_id"], "owned_by_dataset": true}, "employee_qualification": {"file": "hr/employee_qualification.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "qualification_id", "entity": "qualification"}}, {"name": "institution", "type": "varchar(250)", "db_type": "string", "nullable": true}, {"max": 2100, "min": 1960, "name": "year_obtained", "type": "int", "db_type": "int", "nullable": true}], "description": "Văn bằng/chứng chỉ của nhân sự (N-N giữa employee và qualification).", "primary_key": ["employee_id", "qualification_id"], "owned_by_dataset": true}}, "source_db": "docs/data-schema/unilake-db-architecture.txt", "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. Bám đúng DB nguồn docs/data-schema/unilake-db-architecture.txt (tên cột, thứ tự, db_type, nullable, PK, UNIQUE, FK). type là kiểu SQL đề xuất cho DDL vì DB chỉ khai báo kiểu chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "file_format": {"type": "csv", "header": true, "encoding": "utf-8", "delimiter": ",", "null_value": "", "date_format": "YYYY-MM-DD", "boolean_values": ["true", "false"], "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": "."}, "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "Hệ thống nguồn"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID lô ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Thời điểm ghi vào Bronze (UTC)"}], "quality_rules": [{"id": "HR-DQ-01", "rule": "PK (kể cả PK ghép) và cột unique không null, không trùng", "entity": "*", "severity": "error"}, {"id": "HR-DQ-02", "rule": "FK trỏ tới bản ghi có thật", "entity": "*", "severity": "error"}, {"id": "HR-DQ-03", "rule": "Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max", "entity": "*", "severity": "error"}, {"id": "HR-DQ-04", "rule": "Một gốc, không chu trình; department_type khớp tiền tố mã; bộ môn (Division) thuộc khoa (Faculty)", "entity": "department", "severity": "error"}, {"id": "HR-DQ-05", "rule": "Khi có ngày sinh và hire_date: tuổi khi tuyển 20 - 60; hire_date = start_date sớm nhất trong assignment", "entity": "employee", "severity": "error"}, {"id": "HR-DQ-06", "rule": "start_date >= hire_date (nếu có); end_date >= start_date; vị trí chính không chồng lấn", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-07", "rule": "Active có đúng một vị trí chính đang hiệu lực; Resigned/Retired không còn assignment mở", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-08", "rule": "HT, TK, TP, TBM: một người/đơn vị/thời điểm; chức vụ đúng loại đơn vị", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-09", "rule": "Chức vụ giảng dạy chỉ cho giảng viên; CV, NV, TP chỉ cho cán bộ hành chính", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-10", "rule": "Năm cấp sau 18 tuổi, không sau ngày chốt; Cử nhân/Kỹ sư < Thạc sĩ < Tiến sĩ", "entity": "employee_qualification", "severity": "error"}, {"id": "HR-DQ-11", "rule": "academic_degree khớp văn bằng cao nhất; academic_title (Assoc. Prof., Prof.) chỉ khi academic_degree = Ph.D.", "entity": "lecturer", "severity": "warning"}, {"id": "HR-DQ-12", "rule": "assignment_status khớp end_date (Active khi end_date null hoặc sau ngày chốt)", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-13", "rule": "qualification_name không trùng", "entity": "qualification", "severity": "error"}, {"id": "HR-DQ-14", "rule": "Giảng viên có assignment hiệu lực suốt khoảng phụ trách lớp (assigned_from..assigned_to, mặc định là học kỳ của lớp)", "entity": "class_lecturer (Academic)", "severity": "error"}], "position_rules": {"staff_only": ["CV", "NV", "TP"], "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "allowed_department_types": {"CV": ["Office"], "GV": ["Faculty", "Division"], "HT": ["Board"], "NV": ["Office"], "TG": ["Faculty", "Division"], "TK": ["Faculty"], "TP": ["Office"], "GVC": ["Faculty", "Division"], "PHT": ["Board"], "PTK": ["Faculty"], "TBM": ["Division"]}, "unique_holder_per_department": ["HT", "TK", "TP", "TBM"]}, "department_type_prefix": {"Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM", "University": "UNI"}, "silver_derived_columns": {"employee": [{"name": "birth_year", "rule": "Thay date_of_birth (PII)", "type": "int"}, {"name": "is_lecturer", "rule": "Có dòng trong lecturer", "type": "boolean"}, {"name": "primary_department_id", "rule": "department_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "faculty_id", "rule": "Đơn vị con trực tiếp của đơn vị gốc chứa primary_department_id", "type": "bigint"}, {"name": "current_position_id", "rule": "position_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "seniority_years", "rule": "Từ hire_date (hoặc assignment sớm nhất) tới ngày chốt hoặc ngày nghỉ", "type": "decimal(4,1)"}, {"name": "highest_degree", "rule": "Tiến sĩ > Thạc sĩ > Kỹ sư/Cử nhân theo employee_qualification", "type": "varchar(30)"}, {"name": "leave_date", "rule": "end_date lớn nhất của assignment khi Resigned/Retired", "type": "date"}]}}, "data_source_id": 1, "format_options": {"hire_date": {"date_formats": ["%Y-%m-%d"]}, "birth_year": {"date_formats": ["%Y-%m-%d"]}}, "ignored_columns": ["full_name", "email", "phone"], "source_system_id": 34, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "1.2.0", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "42c85bd54dc10c420a2fc2e4da477c8b5a18bedaf4b9cac1ae787a8326c4c47f", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/hr/2026/11/16/job-5/employee.csv", "domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "lineage": {"mode": "ingestion", "run_ref": "ingestion:5", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/9a49f95055024b449dd1d5ec6c8b6588/employee.csv", "layer": "SOURCE", "domain": "hr", "version": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785", "asset_id": "source:1:employee.csv", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-5/employee.csv", "layer": "BRONZE", "domain": "hr", "version": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "asset_id": "dataset:1", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": "ingestion:5"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "batch-id": "job-5", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:22:55.928460+00:00"}, "size_bytes": 16230, "version_id": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785"}	SUCCESS	1	1d7cb2c1-52d6-4cf0-95fd-86e9549da7c7	\N	bae0fdc7bb26	\N	\N	{"domain": "hr", "entity": "employee", "issues": [], "run_id": "f139324d-e632-4dd3-8ce2-8da48012acf2", "status": "PUBLISHED", "profile_id": "automatic-c45fc8e5-5029-45e9-a41a-92663268ca4e", "publication": {"mode": "production", "scope": "production:auto-e270bc9994f1530b4a540c0daa6c", "entity": "employee", "snapshot_id": "8079b128-c5b0-4625-917b-e01e5386342d", "records_published": 200}, "records_read": 200, "requested_by": "9", "input_version": "sha256:0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "records_passed": 200, "ingestion_job_id": 5, "profile_approval": {"job_id": "c45fc8e5-5029-45e9-a41a-92663268ca4e", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "42c85bd54dc10c420a2fc2e4da477c8b5a18bedaf4b9cac1ae787a8326c4c47f"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "42c85bd54dc10c420a2fc2e4da477c8b5a18bedaf4b9cac1ae787a8326c4c47f", "records_quarantined": 0}	2026-11-16 21:22:55.964293+07	2026-11-16 21:22:58.193257+07
d4a7c552-74d9-45be-a5c6-2f74612d578c	6	1	{"mode": "automatic", "revision": 0, "profile_id": "automatic-d4a7c552-74d9-45be-a5c6-2f74612d578c", "max_attempts": 3, "requested_by": "9", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-714bd628f3e833afe92a14aa6053", "domain": "hr", "entity": "lecturer", "version": "auto-source-v1", "contracts": {"hr": {"$defs": {"lecturer": {"type": "object", "x-grain": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "x-owner": "hr", "required": ["employee_id", "academic_degree", "academic_title", "research_field", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "academic_title": {"enum": ["Assoc. Prof.", "Prof.", null], "type": ["string", "null"], "maxLength": 50, "description": "Học hàm; null nếu chưa có", "x-physical-type": "varchar(50)"}, "research_field": {"type": ["string", "null"], "maxLength": 250, "x-physical-type": "varchar(250)"}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "academic_degree": {"enum": ["B.Sc.", "M.Sc.", "Ph.D.", null], "type": ["string", "null"], "maxLength": 50, "description": "Học vị cao nhất", "x-physical-type": "varchar(50)"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-primary-key": ["employee_id"], "x-identity-mode": "source", "x-unique-together": [], "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 1, "null_tokens": {"employee_id": [""], "academic_title": [""], "research_field": [""], "academic_degree": [""]}, "source_type": "CSV", "dictionaries": {"academic_title": {}, "academic_degree": {}}, "effective_at": "2026-11-16T14:26:04.807503Z", "source_table": "lecturer.csv", "type_options": {}, "field_mapping": {"employee_id": "employee_id", "academic_title": "academic_title", "research_field": "research_field", "academic_degree": "academic_degree"}, "identity_mode": "source", "source_schema": {"dataset": "hr", "version": "1.2.0", "entities": {"employee": {"file": "hr/employee.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^NV[0-9]{4}$", "nullable": false}, {"enum": ["Male", "Female"], "name": "gender", "type": "varchar(20)", "db_type": "string", "nullable": true}, {"name": "hire_date", "type": "date", "db_type": "date", "nullable": true, "description": "Khi có thì bằng start_date sớm nhất trong assignment"}, {"enum": ["Active", "Resigned", "Retired"], "name": "employee_status", "type": "varchar(30)", "db_type": "string", "nullable": false}, {"name": "birth_year", "type": "date", "db_type": "date", "nullable": true}], "description": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "primary_key": ["employee_id"], "owned_by_dataset": true}, "lecturer": {"file": "hr/lecturer.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}, "source_identifier": true}, {"enum": ["B.Sc.", "M.Sc.", "Ph.D."], "name": "academic_degree", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học vị cao nhất"}, {"enum": ["Assoc. Prof.", "Prof."], "name": "academic_title", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học hàm; null nếu chưa có"}, {"name": "research_field", "type": "varchar(250)", "db_type": "string", "nullable": true}], "description": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "primary_key": ["employee_id"], "owned_by_dataset": true}, "position": {"file": "hr/position.csv", "domain": "hr", "columns": [{"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "position_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^[A-Z]{2,5}$", "nullable": false}, {"name": "position_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Leadership", "Management", "Staff"], "name": "position_level", "type": "varchar(50)", "db_type": "string", "nullable": true}], "description": "Chức danh/chức vụ.", "primary_key": ["position_id"], "owned_by_dataset": true}, "assignment": {"file": "hr/assignment.csv", "domain": "hr", "columns": [{"name": "assignment_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "department_id", "entity": "department"}}, {"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "position_id", "entity": "position"}}, {"name": "start_date", "type": "date", "db_type": "date", "nullable": false}, {"name": "end_date", "type": "date", "db_type": "date", "nullable": true}, {"name": "is_primary", "type": "boolean", "db_type": "boolean", "nullable": false}, {"enum": ["Active", "Ended"], "name": "assignment_status", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Active khi end_date null hoặc sau ngày chốt"}], "description": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "primary_key": ["assignment_id"], "owned_by_dataset": true}, "department": {"file": "hr/department.csv", "domain": "hr", "columns": [{"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "department_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "nullable": false, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)"}, {"name": "department_name", "type": "varchar(200)", "db_type": "string", "nullable": false}, {"enum": ["University", "Board", "Office", "Faculty", "Division"], "name": "department_type", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Trường, Ban, Phòng, Khoa, Bộ môn"}, {"name": "parent_department_id", "type": "bigint", "db_type": "bigint", "nullable": true, "references": {"column": "department_id", "entity": "department"}, "description": "Null = đơn vị gốc"}, {"enum": ["Active", "Inactive"], "name": "status", "type": "varchar(20)", "db_type": "string", "nullable": false}, {"name": "created_at", "type": "timestamp", "db_type": "timestamp", "nullable": false}], "description": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "primary_key": ["department_id"], "owned_by_dataset": true}, "qualification": {"file": "hr/qualification.csv", "domain": "hr", "columns": [{"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "qualification_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Degree", "Certificate"], "name": "qualification_type", "type": "varchar(100)", "db_type": "string", "nullable": true}], "description": "Danh mục văn bằng và chứng chỉ. DB không còn UNIQUE trên qualification_name; HR-DQ-13 kiểm tra trùng.", "primary_key": ["qualification_id"], "owned_by_dataset": true}, "employee_qualification": {"file": "hr/employee_qualification.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "qualification_id", "entity": "qualification"}}, {"name": "institution", "type": "varchar(250)", "db_type": "string", "nullable": true}, {"max": 2100, "min": 1960, "name": "year_obtained", "type": "int", "db_type": "int", "nullable": true}], "description": "Văn bằng/chứng chỉ của nhân sự (N-N giữa employee và qualification).", "primary_key": ["employee_id", "qualification_id"], "owned_by_dataset": true}}, "source_db": "docs/data-schema/unilake-db-architecture.txt", "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. Bám đúng DB nguồn docs/data-schema/unilake-db-architecture.txt (tên cột, thứ tự, db_type, nullable, PK, UNIQUE, FK). type là kiểu SQL đề xuất cho DDL vì DB chỉ khai báo kiểu chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "file_format": {"type": "csv", "header": true, "encoding": "utf-8", "delimiter": ",", "null_value": "", "date_format": "YYYY-MM-DD", "boolean_values": ["true", "false"], "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": "."}, "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "Hệ thống nguồn"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID lô ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Thời điểm ghi vào Bronze (UTC)"}], "quality_rules": [{"id": "HR-DQ-01", "rule": "PK (kể cả PK ghép) và cột unique không null, không trùng", "entity": "*", "severity": "error"}, {"id": "HR-DQ-02", "rule": "FK trỏ tới bản ghi có thật", "entity": "*", "severity": "error"}, {"id": "HR-DQ-03", "rule": "Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max", "entity": "*", "severity": "error"}, {"id": "HR-DQ-04", "rule": "Một gốc, không chu trình; department_type khớp tiền tố mã; bộ môn (Division) thuộc khoa (Faculty)", "entity": "department", "severity": "error"}, {"id": "HR-DQ-05", "rule": "Khi có ngày sinh và hire_date: tuổi khi tuyển 20 - 60; hire_date = start_date sớm nhất trong assignment", "entity": "employee", "severity": "error"}, {"id": "HR-DQ-06", "rule": "start_date >= hire_date (nếu có); end_date >= start_date; vị trí chính không chồng lấn", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-07", "rule": "Active có đúng một vị trí chính đang hiệu lực; Resigned/Retired không còn assignment mở", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-08", "rule": "HT, TK, TP, TBM: một người/đơn vị/thời điểm; chức vụ đúng loại đơn vị", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-09", "rule": "Chức vụ giảng dạy chỉ cho giảng viên; CV, NV, TP chỉ cho cán bộ hành chính", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-10", "rule": "Năm cấp sau 18 tuổi, không sau ngày chốt; Cử nhân/Kỹ sư < Thạc sĩ < Tiến sĩ", "entity": "employee_qualification", "severity": "error"}, {"id": "HR-DQ-11", "rule": "academic_degree khớp văn bằng cao nhất; academic_title (Assoc. Prof., Prof.) chỉ khi academic_degree = Ph.D.", "entity": "lecturer", "severity": "warning"}, {"id": "HR-DQ-12", "rule": "assignment_status khớp end_date (Active khi end_date null hoặc sau ngày chốt)", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-13", "rule": "qualification_name không trùng", "entity": "qualification", "severity": "error"}, {"id": "HR-DQ-14", "rule": "Giảng viên có assignment hiệu lực suốt khoảng phụ trách lớp (assigned_from..assigned_to, mặc định là học kỳ của lớp)", "entity": "class_lecturer (Academic)", "severity": "error"}], "position_rules": {"staff_only": ["CV", "NV", "TP"], "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "allowed_department_types": {"CV": ["Office"], "GV": ["Faculty", "Division"], "HT": ["Board"], "NV": ["Office"], "TG": ["Faculty", "Division"], "TK": ["Faculty"], "TP": ["Office"], "GVC": ["Faculty", "Division"], "PHT": ["Board"], "PTK": ["Faculty"], "TBM": ["Division"]}, "unique_holder_per_department": ["HT", "TK", "TP", "TBM"]}, "department_type_prefix": {"Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM", "University": "UNI"}, "silver_derived_columns": {"employee": [{"name": "birth_year", "rule": "Thay date_of_birth (PII)", "type": "int"}, {"name": "is_lecturer", "rule": "Có dòng trong lecturer", "type": "boolean"}, {"name": "primary_department_id", "rule": "department_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "faculty_id", "rule": "Đơn vị con trực tiếp của đơn vị gốc chứa primary_department_id", "type": "bigint"}, {"name": "current_position_id", "rule": "position_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "seniority_years", "rule": "Từ hire_date (hoặc assignment sớm nhất) tới ngày chốt hoặc ngày nghỉ", "type": "decimal(4,1)"}, {"name": "highest_degree", "rule": "Tiến sĩ > Thạc sĩ > Kỹ sư/Cử nhân theo employee_qualification", "type": "varchar(30)"}, {"name": "leave_date", "rule": "end_date lớn nhất của assignment khi Resigned/Retired", "type": "date"}]}}, "data_source_id": 1, "format_options": {}, "ignored_columns": [], "source_system_id": 34, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "1.2.0", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/hr/2026/11/16/job-6/lecturer.csv", "domain": "hr", "sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "lineage": {"mode": "ingestion", "run_ref": "ingestion:6", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/c8f9c761b5ef45ef9a437618a950c546/lecturer.csv", "layer": "SOURCE", "domain": "hr", "version": "9f890512-7708-4d68-be23-ff718489aa6e", "asset_id": "source:1:lecturer.csv", "checksum": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-6/lecturer.csv", "layer": "BRONZE", "domain": "hr", "version": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "asset_id": "dataset:1", "checksum": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "producer_run_ref": "ingestion:6"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "batch-id": "job-6", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:26:05.651450+00:00"}, "size_bytes": 3606, "version_id": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "source_type": "CSV", "records_read": 100, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "9f890512-7708-4d68-be23-ff718489aa6e"}	SUCCESS	1	ab82f778-51b7-4e7c-a75e-e50c9d6017cf	\N	bae0fdc7bb26	\N	\N	{"domain": "hr", "entity": "lecturer", "issues": [{"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:1", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:2", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:3", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:4", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:5", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:6", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:7", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:8", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:9", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:10", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:11", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:12", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:13", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:14", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:15", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:16", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:17", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:18", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:19", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:20", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:21", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:22", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:23", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:24", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:25", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:26", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:27", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:28", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:29", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:30", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:31", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:32", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:33", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:34", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:35", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:36", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:37", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:38", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:39", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:40", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:41", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:42", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:43", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:44", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:45", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:46", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:47", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:48", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:49", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:50", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:51", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:52", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:53", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:54", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:55", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:56", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:57", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:58", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:59", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:60", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:61", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:62", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:63", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:64", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:65", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:66", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:67", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:68", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:69", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:70", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:71", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:72", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:73", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:74", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:75", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:76", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:77", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:78", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:79", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:80", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:81", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:82", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:83", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:84", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:85", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:86", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:87", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:88", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:89", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:90", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:91", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:92", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:93", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:94", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:95", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:96", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:97", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:98", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:99", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, {"code": "NULL_VALUE_PRESERVED", "field": "academic_title", "domain": "hr", "entity": "lecturer", "locator": "record:100", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}], "run_id": "c97d27b1-e712-4f14-bccd-0c4e3694d441", "status": "PUBLISHED", "profile_id": "automatic-d4a7c552-74d9-45be-a5c6-2f74612d578c", "publication": {"mode": "production", "scope": "production:auto-714bd628f3e833afe92a14aa6053", "entity": "lecturer", "snapshot_id": "a1efba66-0aa4-4215-a9fc-758af2a59a0d", "records_published": 100}, "records_read": 100, "requested_by": "9", "input_version": "sha256:91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "records_passed": 100, "ingestion_job_id": 6, "profile_approval": {"job_id": "d4a7c552-74d9-45be-a5c6-2f74612d578c", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03", "records_quarantined": 0}	2026-11-16 21:26:05.700297+07	2026-11-16 21:26:07.693458+07
9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4	7	1	{"mode": "automatic", "revision": 0, "profile_id": "automatic-9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4", "max_attempts": 3, "requested_by": "9", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-899188c733e26f94ba29ead9cf0e", "domain": "hr", "entity": "assignment", "version": "auto-source-v1", "contracts": {"hr": {"$defs": {"assignment": {"type": "object", "x-grain": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "x-owner": "hr", "required": ["assignment_id", "employee_id", "department_id", "position_id", "start_date", "end_date", "is_primary", "assignment_status", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "end_date": {"type": ["string", "null"], "format": "date", "x-physical-type": "date32"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "is_primary": {"type": "boolean", "x-physical-type": "boolean"}, "start_date": {"type": "string", "format": "date", "x-physical-type": "date32"}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "position_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "assignment_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "department_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "assignment_status": {"enum": ["Active", "Ended"], "type": "string", "maxLength": 30, "description": "Active khi end_date null hoặc sau ngày chốt", "x-physical-type": "varchar(30)"}}, "x-primary-key": ["assignment_id"], "x-identity-mode": "source", "x-unique-together": [], "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 1, "null_tokens": {"end_date": [""], "is_primary": [""], "start_date": [""], "employee_id": [""], "position_id": [""], "assignment_id": [""], "department_id": [""], "assignment_status": [""]}, "source_type": "CSV", "dictionaries": {"assignment_status": {}}, "effective_at": "2026-11-16T14:26:26.626762Z", "source_table": "assignment.csv", "type_options": {}, "field_mapping": {"end_date": "end_date", "is_primary": "is_primary", "start_date": "start_date", "employee_id": "employee_id", "position_id": "position_id", "assignment_id": "assignment_id", "department_id": "department_id", "assignment_status": "assignment_status"}, "identity_mode": "source", "source_schema": {"dataset": "hr", "version": "1.2.0", "entities": {"employee": {"file": "hr/employee.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^NV[0-9]{4}$", "nullable": false}, {"enum": ["Male", "Female"], "name": "gender", "type": "varchar(20)", "db_type": "string", "nullable": true}, {"name": "hire_date", "type": "date", "db_type": "date", "nullable": true, "description": "Khi có thì bằng start_date sớm nhất trong assignment"}, {"enum": ["Active", "Resigned", "Retired"], "name": "employee_status", "type": "varchar(30)", "db_type": "string", "nullable": false}, {"name": "birth_year", "type": "date", "db_type": "date", "nullable": true}], "description": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "primary_key": ["employee_id"], "owned_by_dataset": true}, "lecturer": {"file": "hr/lecturer.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"enum": ["B.Sc.", "M.Sc.", "Ph.D."], "name": "academic_degree", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học vị cao nhất"}, {"enum": ["Assoc. Prof.", "Prof."], "name": "academic_title", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học hàm; null nếu chưa có"}, {"name": "research_field", "type": "varchar(250)", "db_type": "string", "nullable": true}], "description": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "primary_key": ["employee_id"], "owned_by_dataset": true}, "position": {"file": "hr/position.csv", "domain": "hr", "columns": [{"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "position_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^[A-Z]{2,5}$", "nullable": false}, {"name": "position_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Leadership", "Management", "Staff"], "name": "position_level", "type": "varchar(50)", "db_type": "string", "nullable": true}], "description": "Chức danh/chức vụ.", "primary_key": ["position_id"], "owned_by_dataset": true}, "assignment": {"file": "hr/assignment.csv", "domain": "hr", "columns": [{"name": "assignment_id", "type": "text", "db_type": "string", "nullable": false, "source_identifier": true}, {"name": "employee_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}, "source_identifier": true}, {"name": "department_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "department_id", "entity": "department"}, "source_identifier": true}, {"name": "position_id", "type": "text", "db_type": "string", "nullable": false, "references": {"column": "position_id", "entity": "position"}, "source_identifier": true}, {"name": "start_date", "type": "date", "db_type": "date", "nullable": false}, {"name": "end_date", "type": "date", "db_type": "date", "nullable": true}, {"name": "is_primary", "type": "boolean", "db_type": "boolean", "nullable": false}, {"enum": ["Active", "Ended"], "name": "assignment_status", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Active khi end_date null hoặc sau ngày chốt"}], "description": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "primary_key": ["assignment_id"], "owned_by_dataset": true}, "department": {"file": "hr/department.csv", "domain": "hr", "columns": [{"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "department_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "nullable": false, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)"}, {"name": "department_name", "type": "varchar(200)", "db_type": "string", "nullable": false}, {"enum": ["University", "Board", "Office", "Faculty", "Division"], "name": "department_type", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Trường, Ban, Phòng, Khoa, Bộ môn"}, {"name": "parent_department_id", "type": "bigint", "db_type": "bigint", "nullable": true, "references": {"column": "department_id", "entity": "department"}, "description": "Null = đơn vị gốc"}, {"enum": ["Active", "Inactive"], "name": "status", "type": "varchar(20)", "db_type": "string", "nullable": false}, {"name": "created_at", "type": "timestamp", "db_type": "timestamp", "nullable": false}], "description": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "primary_key": ["department_id"], "owned_by_dataset": true}, "qualification": {"file": "hr/qualification.csv", "domain": "hr", "columns": [{"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "qualification_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Degree", "Certificate"], "name": "qualification_type", "type": "varchar(100)", "db_type": "string", "nullable": true}], "description": "Danh mục văn bằng và chứng chỉ. DB không còn UNIQUE trên qualification_name; HR-DQ-13 kiểm tra trùng.", "primary_key": ["qualification_id"], "owned_by_dataset": true}, "employee_qualification": {"file": "hr/employee_qualification.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "qualification_id", "entity": "qualification"}}, {"name": "institution", "type": "varchar(250)", "db_type": "string", "nullable": true}, {"max": 2100, "min": 1960, "name": "year_obtained", "type": "int", "db_type": "int", "nullable": true}], "description": "Văn bằng/chứng chỉ của nhân sự (N-N giữa employee và qualification).", "primary_key": ["employee_id", "qualification_id"], "owned_by_dataset": true}}, "source_db": "docs/data-schema/unilake-db-architecture.txt", "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. Bám đúng DB nguồn docs/data-schema/unilake-db-architecture.txt (tên cột, thứ tự, db_type, nullable, PK, UNIQUE, FK). type là kiểu SQL đề xuất cho DDL vì DB chỉ khai báo kiểu chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "file_format": {"type": "csv", "header": true, "encoding": "utf-8", "delimiter": ",", "null_value": "", "date_format": "YYYY-MM-DD", "boolean_values": ["true", "false"], "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": "."}, "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "Hệ thống nguồn"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID lô ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Thời điểm ghi vào Bronze (UTC)"}], "quality_rules": [{"id": "HR-DQ-01", "rule": "PK (kể cả PK ghép) và cột unique không null, không trùng", "entity": "*", "severity": "error"}, {"id": "HR-DQ-02", "rule": "FK trỏ tới bản ghi có thật", "entity": "*", "severity": "error"}, {"id": "HR-DQ-03", "rule": "Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max", "entity": "*", "severity": "error"}, {"id": "HR-DQ-04", "rule": "Một gốc, không chu trình; department_type khớp tiền tố mã; bộ môn (Division) thuộc khoa (Faculty)", "entity": "department", "severity": "error"}, {"id": "HR-DQ-05", "rule": "Khi có ngày sinh và hire_date: tuổi khi tuyển 20 - 60; hire_date = start_date sớm nhất trong assignment", "entity": "employee", "severity": "error"}, {"id": "HR-DQ-06", "rule": "start_date >= hire_date (nếu có); end_date >= start_date; vị trí chính không chồng lấn", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-07", "rule": "Active có đúng một vị trí chính đang hiệu lực; Resigned/Retired không còn assignment mở", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-08", "rule": "HT, TK, TP, TBM: một người/đơn vị/thời điểm; chức vụ đúng loại đơn vị", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-09", "rule": "Chức vụ giảng dạy chỉ cho giảng viên; CV, NV, TP chỉ cho cán bộ hành chính", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-10", "rule": "Năm cấp sau 18 tuổi, không sau ngày chốt; Cử nhân/Kỹ sư < Thạc sĩ < Tiến sĩ", "entity": "employee_qualification", "severity": "error"}, {"id": "HR-DQ-11", "rule": "academic_degree khớp văn bằng cao nhất; academic_title (Assoc. Prof., Prof.) chỉ khi academic_degree = Ph.D.", "entity": "lecturer", "severity": "warning"}, {"id": "HR-DQ-12", "rule": "assignment_status khớp end_date (Active khi end_date null hoặc sau ngày chốt)", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-13", "rule": "qualification_name không trùng", "entity": "qualification", "severity": "error"}, {"id": "HR-DQ-14", "rule": "Giảng viên có assignment hiệu lực suốt khoảng phụ trách lớp (assigned_from..assigned_to, mặc định là học kỳ của lớp)", "entity": "class_lecturer (Academic)", "severity": "error"}], "position_rules": {"staff_only": ["CV", "NV", "TP"], "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "allowed_department_types": {"CV": ["Office"], "GV": ["Faculty", "Division"], "HT": ["Board"], "NV": ["Office"], "TG": ["Faculty", "Division"], "TK": ["Faculty"], "TP": ["Office"], "GVC": ["Faculty", "Division"], "PHT": ["Board"], "PTK": ["Faculty"], "TBM": ["Division"]}, "unique_holder_per_department": ["HT", "TK", "TP", "TBM"]}, "department_type_prefix": {"Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM", "University": "UNI"}, "silver_derived_columns": {"employee": [{"name": "birth_year", "rule": "Thay date_of_birth (PII)", "type": "int"}, {"name": "is_lecturer", "rule": "Có dòng trong lecturer", "type": "boolean"}, {"name": "primary_department_id", "rule": "department_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "faculty_id", "rule": "Đơn vị con trực tiếp của đơn vị gốc chứa primary_department_id", "type": "bigint"}, {"name": "current_position_id", "rule": "position_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "seniority_years", "rule": "Từ hire_date (hoặc assignment sớm nhất) tới ngày chốt hoặc ngày nghỉ", "type": "decimal(4,1)"}, {"name": "highest_degree", "rule": "Tiến sĩ > Thạc sĩ > Kỹ sư/Cử nhân theo employee_qualification", "type": "varchar(30)"}, {"name": "leave_date", "rule": "end_date lớn nhất của assignment khi Resigned/Retired", "type": "date"}]}}, "data_source_id": 1, "format_options": {"end_date": {"date_formats": ["%Y-%m-%d"]}, "start_date": {"date_formats": ["%Y-%m-%d"]}}, "ignored_columns": [], "source_system_id": 34, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "1.2.0", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/hr/2026/11/16/job-7/assignment.csv", "domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "lineage": {"mode": "ingestion", "run_ref": "ingestion:7", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/3fbcb552f825410e8f83c7653982ce2d/assignment.csv", "layer": "SOURCE", "domain": "hr", "version": "fab0a178-c122-4a0f-983c-53f8359f1446", "asset_id": "source:1:assignment.csv", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-7/assignment.csv", "layer": "BRONZE", "domain": "hr", "version": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "asset_id": "dataset:1", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": "ingestion:7"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "batch-id": "job-7", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:26:27.891741+00:00"}, "size_bytes": 7286, "version_id": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "fab0a178-c122-4a0f-983c-53f8359f1446"}	SUCCESS	1	8c22c4e9-7732-4ddf-8156-2d3c119cb587	\N	bae0fdc7bb26	\N	\N	{"domain": "hr", "entity": "assignment", "issues": [{"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:1", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:2", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:3", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:4", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:5", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:6", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:7", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:8", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:9", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:10", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:11", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:12", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:13", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:14", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:15", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:16", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:17", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:18", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:19", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:20", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:21", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:22", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:23", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:24", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:25", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:26", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:27", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:28", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:29", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:30", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:31", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:32", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:33", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:34", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:35", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:36", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:37", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:38", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:39", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:40", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:41", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:42", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:43", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:44", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:45", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:46", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:47", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:48", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:49", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:50", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:51", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:52", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:53", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:54", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:55", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:56", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:57", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:58", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:59", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:60", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:61", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:62", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:63", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:64", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:65", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:66", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:67", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:68", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:69", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:70", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:71", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:72", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:73", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:74", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:75", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:76", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:77", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:78", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:79", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:80", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:81", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:82", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:83", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:84", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:85", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:86", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:87", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:88", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:89", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:90", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:91", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:92", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:93", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:94", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:95", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:96", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:97", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:98", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:99", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:100", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:101", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:102", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:103", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:104", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:105", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:106", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:107", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:108", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:109", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:110", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:111", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:112", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:113", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:114", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:115", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:116", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:117", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:118", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:119", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:120", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:121", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:122", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:123", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:124", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:125", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:126", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:127", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:128", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:129", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:130", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:131", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:132", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:133", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:134", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:135", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:136", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:137", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:138", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:139", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:140", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:141", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:142", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:143", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:144", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:145", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:146", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:147", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:148", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:149", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:150", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:151", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:152", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:153", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:154", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:155", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:156", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:157", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:158", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:159", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:160", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:161", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:162", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:163", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:164", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:165", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:166", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:167", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:168", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:169", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:170", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:171", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:172", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:173", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:174", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:175", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:176", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:177", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:178", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:179", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:180", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:181", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:182", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:183", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:184", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:185", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:186", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:187", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:188", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:189", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:190", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:191", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:192", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:193", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:194", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:195", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:196", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:197", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:198", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:199", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, {"code": "NULL_VALUE_PRESERVED", "field": "end_date", "domain": "hr", "entity": "assignment", "locator": "record:200", "rule_id": "CLN-NULL-01", "severity": "INFO", "disposition": "PRESERVED", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}], "run_id": "bb9ae615-7497-4fa6-bf73-99e9db7c38f1", "status": "PUBLISHED", "profile_id": "automatic-9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4", "publication": {"mode": "production", "scope": "production:auto-899188c733e26f94ba29ead9cf0e", "entity": "assignment", "snapshot_id": "58804508-8b12-45f5-98cf-2f1f1e2506e2", "records_published": 200}, "records_read": 200, "requested_by": "9", "input_version": "sha256:3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "records_passed": 200, "ingestion_job_id": 7, "profile_approval": {"job_id": "9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6", "records_quarantined": 0}	2026-11-16 21:26:27.924611+07	2026-11-16 21:26:30.127713+07
63b18667-eb2a-49e1-8770-97d623d0a2e6	8	1	{"mode": "automatic", "revision": 0, "profile_id": "automatic-63b18667-eb2a-49e1-8770-97d623d0a2e6", "max_attempts": 3, "requested_by": "system:gold-storage-v2", "recipe_version": "auto-source-v1", "profile_definition": {"scope": "auto-e270bc9994f1530b4a540c0daa6c", "domain": "hr", "entity": "employee", "version": "auto-source-v1", "contracts": {"hr": {"$defs": {"employee": {"type": "object", "x-grain": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "x-owner": "hr", "required": ["employee_id", "employee_code", "gender", "hire_date", "employee_status", "birth_year", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"gender": {"enum": ["Male", "Female", null], "type": ["string", "null"], "maxLength": 20, "x-physical-type": "varchar(20)"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "hire_date": {"type": ["string", "null"], "format": "date", "description": "Khi có thì bằng start_date sớm nhất trong assignment", "x-physical-type": "date32"}, "birth_year": {"type": ["integer", "null"], "x-rule": "PII-02", "maximum": 9999, "minimum": 1, "x-derived-from": "date_of_birth", "x-physical-type": "int32"}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "_processed_at": {"type": "string", "format": "date-time", "pattern": "Z$", "minLength": 1, "x-physical-type": "timestamp[us,UTC]"}, "employee_code": {"type": "string", "pattern": "^NV[0-9]{4}$", "maxLength": 30, "x-physical-type": "varchar(30)", "x-unique-non-null": true}, "_schema_version": {"type": "string", "const": "source-1", "minLength": 1, "x-physical-type": "utf8"}, "employee_status": {"enum": ["Active", "Resigned", "Retired"], "type": "string", "maxLength": 30, "x-physical-type": "varchar(30)"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-primary-key": ["employee_id"], "x-identity-mode": "source", "x-unique-together": [], "additionalProperties": false}}, "x-version": "source-1"}}, "dataset_id": 1, "null_tokens": {"gender": [""], "hire_date": [""], "birth_year": [""], "employee_id": [""], "employee_code": [""], "employee_status": [""]}, "source_type": "CSV", "dictionaries": {"gender": {}, "employee_status": {}}, "effective_at": "2026-11-16T16:04:01.823260Z", "source_table": "employee.csv", "type_options": {}, "field_mapping": {"gender": "gender", "hire_date": "hire_date", "birth_year": "date_of_birth", "employee_id": "employee_id", "employee_code": "employee_code", "employee_status": "employee_status"}, "identity_mode": "source", "source_schema": {"dataset": "hr", "version": "1.2.0", "entities": {"employee": {"file": "hr/employee.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "text", "db_type": "string", "nullable": false, "source_identifier": true}, {"name": "employee_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^NV[0-9]{4}$", "nullable": false}, {"enum": ["Male", "Female"], "name": "gender", "type": "varchar(20)", "db_type": "string", "nullable": true}, {"name": "hire_date", "type": "date", "db_type": "date", "nullable": true, "description": "Khi có thì bằng start_date sớm nhất trong assignment"}, {"enum": ["Active", "Resigned", "Retired"], "name": "employee_status", "type": "varchar(30)", "db_type": "string", "nullable": false}, {"name": "birth_year", "type": "date", "db_type": "date", "nullable": true}], "description": "Nhân sự (giảng viên và cán bộ hành chính). Đơn vị và chức vụ lấy qua assignment.", "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "primary_key": ["employee_id"], "owned_by_dataset": true}, "lecturer": {"file": "hr/lecturer.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"enum": ["B.Sc.", "M.Sc.", "Ph.D."], "name": "academic_degree", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học vị cao nhất"}, {"enum": ["Assoc. Prof.", "Prof."], "name": "academic_title", "type": "varchar(50)", "db_type": "string", "nullable": true, "description": "Học hàm; null nếu chưa có"}, {"name": "research_field", "type": "varchar(250)", "db_type": "string", "nullable": true}], "description": "Giảng viên: kiểu con của employee, khóa chính employee_id dùng chung (quan hệ 1 - 0..1).", "primary_key": ["employee_id"], "owned_by_dataset": true}, "position": {"file": "hr/position.csv", "domain": "hr", "columns": [{"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "position_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^[A-Z]{2,5}$", "nullable": false}, {"name": "position_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Leadership", "Management", "Staff"], "name": "position_level", "type": "varchar(50)", "db_type": "string", "nullable": true}], "description": "Chức danh/chức vụ.", "primary_key": ["position_id"], "owned_by_dataset": true}, "assignment": {"file": "hr/assignment.csv", "domain": "hr", "columns": [{"name": "assignment_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "department_id", "entity": "department"}}, {"name": "position_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "position_id", "entity": "position"}}, {"name": "start_date", "type": "date", "db_type": "date", "nullable": false}, {"name": "end_date", "type": "date", "db_type": "date", "nullable": true}, {"name": "is_primary", "type": "boolean", "db_type": "boolean", "nullable": false}, {"enum": ["Active", "Ended"], "name": "assignment_status", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Active khi end_date null hoặc sau ngày chốt"}], "description": "Quá trình công tác: nhân sự giữ chức vụ tại đơn vị trong một khoảng thời gian. end_date null = đang giữ. is_primary false = kiêm nhiệm.", "primary_key": ["assignment_id"], "owned_by_dataset": true}, "department": {"file": "hr/department.csv", "domain": "hr", "columns": [{"name": "department_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "department_code", "type": "varchar(30)", "unique": true, "db_type": "string", "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "nullable": false, "description": "Tiền tố khớp department_type: UNI, BGH, P (phòng), K (khoa), BM (bộ môn)"}, {"name": "department_name", "type": "varchar(200)", "db_type": "string", "nullable": false}, {"enum": ["University", "Board", "Office", "Faculty", "Division"], "name": "department_type", "type": "varchar(30)", "db_type": "string", "nullable": false, "description": "Trường, Ban, Phòng, Khoa, Bộ môn"}, {"name": "parent_department_id", "type": "bigint", "db_type": "bigint", "nullable": true, "references": {"column": "department_id", "entity": "department"}, "description": "Null = đơn vị gốc"}, {"enum": ["Active", "Inactive"], "name": "status", "type": "varchar(20)", "db_type": "string", "nullable": false}, {"name": "created_at", "type": "timestamp", "db_type": "timestamp", "nullable": false}], "description": "Đơn vị tổ chức dạng cây: trường -> ban giám hiệu/phòng/khoa -> bộ môn. Được major (Admissions), source_system và dataset (governance) tham chiếu.", "primary_key": ["department_id"], "owned_by_dataset": true}, "qualification": {"file": "hr/qualification.csv", "domain": "hr", "columns": [{"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false}, {"name": "qualification_name", "type": "varchar(150)", "db_type": "string", "nullable": false}, {"enum": ["Degree", "Certificate"], "name": "qualification_type", "type": "varchar(100)", "db_type": "string", "nullable": true}], "description": "Danh mục văn bằng và chứng chỉ. DB không còn UNIQUE trên qualification_name; HR-DQ-13 kiểm tra trùng.", "primary_key": ["qualification_id"], "owned_by_dataset": true}, "employee_qualification": {"file": "hr/employee_qualification.csv", "domain": "hr", "columns": [{"name": "employee_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "employee_id", "entity": "employee"}}, {"name": "qualification_id", "type": "bigint", "db_type": "bigint", "nullable": false, "references": {"column": "qualification_id", "entity": "qualification"}}, {"name": "institution", "type": "varchar(250)", "db_type": "string", "nullable": true}, {"max": 2100, "min": 1960, "name": "year_obtained", "type": "int", "db_type": "int", "nullable": true}], "description": "Văn bằng/chứng chỉ của nhân sự (N-N giữa employee và qualification).", "primary_key": ["employee_id", "qualification_id"], "owned_by_dataset": true}}, "source_db": "docs/data-schema/unilake-db-architecture.txt", "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. Bám đúng DB nguồn docs/data-schema/unilake-db-architecture.txt (tên cột, thứ tự, db_type, nullable, PK, UNIQUE, FK). type là kiểu SQL đề xuất cho DDL vì DB chỉ khai báo kiểu chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "file_format": {"type": "csv", "header": true, "encoding": "utf-8", "delimiter": ",", "null_value": "", "date_format": "YYYY-MM-DD", "boolean_values": ["true", "false"], "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": "."}, "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "Hệ thống nguồn"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID lô ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Thời điểm ghi vào Bronze (UTC)"}], "quality_rules": [{"id": "HR-DQ-01", "rule": "PK (kể cả PK ghép) và cột unique không null, không trùng", "entity": "*", "severity": "error"}, {"id": "HR-DQ-02", "rule": "FK trỏ tới bản ghi có thật", "entity": "*", "severity": "error"}, {"id": "HR-DQ-03", "rule": "Đúng kiểu, độ dài, null, giá trị trạng thái, pattern, min/max", "entity": "*", "severity": "error"}, {"id": "HR-DQ-04", "rule": "Một gốc, không chu trình; department_type khớp tiền tố mã; bộ môn (Division) thuộc khoa (Faculty)", "entity": "department", "severity": "error"}, {"id": "HR-DQ-05", "rule": "Khi có ngày sinh và hire_date: tuổi khi tuyển 20 - 60; hire_date = start_date sớm nhất trong assignment", "entity": "employee", "severity": "error"}, {"id": "HR-DQ-06", "rule": "start_date >= hire_date (nếu có); end_date >= start_date; vị trí chính không chồng lấn", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-07", "rule": "Active có đúng một vị trí chính đang hiệu lực; Resigned/Retired không còn assignment mở", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-08", "rule": "HT, TK, TP, TBM: một người/đơn vị/thời điểm; chức vụ đúng loại đơn vị", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-09", "rule": "Chức vụ giảng dạy chỉ cho giảng viên; CV, NV, TP chỉ cho cán bộ hành chính", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-10", "rule": "Năm cấp sau 18 tuổi, không sau ngày chốt; Cử nhân/Kỹ sư < Thạc sĩ < Tiến sĩ", "entity": "employee_qualification", "severity": "error"}, {"id": "HR-DQ-11", "rule": "academic_degree khớp văn bằng cao nhất; academic_title (Assoc. Prof., Prof.) chỉ khi academic_degree = Ph.D.", "entity": "lecturer", "severity": "warning"}, {"id": "HR-DQ-12", "rule": "assignment_status khớp end_date (Active khi end_date null hoặc sau ngày chốt)", "entity": "assignment", "severity": "error"}, {"id": "HR-DQ-13", "rule": "qualification_name không trùng", "entity": "qualification", "severity": "error"}, {"id": "HR-DQ-14", "rule": "Giảng viên có assignment hiệu lực suốt khoảng phụ trách lớp (assigned_from..assigned_to, mặc định là học kỳ của lớp)", "entity": "class_lecturer (Academic)", "severity": "error"}], "position_rules": {"staff_only": ["CV", "NV", "TP"], "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "allowed_department_types": {"CV": ["Office"], "GV": ["Faculty", "Division"], "HT": ["Board"], "NV": ["Office"], "TG": ["Faculty", "Division"], "TK": ["Faculty"], "TP": ["Office"], "GVC": ["Faculty", "Division"], "PHT": ["Board"], "PTK": ["Faculty"], "TBM": ["Division"]}, "unique_holder_per_department": ["HT", "TK", "TP", "TBM"]}, "department_type_prefix": {"Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM", "University": "UNI"}, "silver_derived_columns": {"employee": [{"name": "birth_year", "rule": "Thay date_of_birth (PII)", "type": "int"}, {"name": "is_lecturer", "rule": "Có dòng trong lecturer", "type": "boolean"}, {"name": "primary_department_id", "rule": "department_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "faculty_id", "rule": "Đơn vị con trực tiếp của đơn vị gốc chứa primary_department_id", "type": "bigint"}, {"name": "current_position_id", "rule": "position_id của assignment is_primary đang hiệu lực", "type": "bigint"}, {"name": "seniority_years", "rule": "Từ hire_date (hoặc assignment sớm nhất) tới ngày chốt hoặc ngày nghỉ", "type": "decimal(4,1)"}, {"name": "highest_degree", "rule": "Tiến sĩ > Thạc sĩ > Kỹ sư/Cử nhân theo employee_qualification", "type": "varchar(30)"}, {"name": "leave_date", "rule": "end_date lớn nhất của assignment khi Resigned/Retired", "type": "date"}]}}, "data_source_id": 1, "format_options": {"hire_date": {"date_formats": ["%Y-%m-%d"]}, "birth_year": {"date_formats": ["%Y-%m-%d"]}}, "ignored_columns": ["full_name", "email", "phone"], "source_system_id": 34, "crosswalk_release": null, "reference_namespaces": {}, "source_schema_version": "1.2.0", "source_preview_columns": [], "collapse_exact_duplicates": false}, "profile_fingerprint": "d817b1f5ed152b9d55fd66f4239ca687d89c27b9d10fddc6f16997eaacce8f8a", "retry_delay_seconds": 30, "allow_partial_publication": false}	{"uri": "s3://bronze/hr/2026/11/16/job-8/employee.csv", "domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "lineage": {"mode": "ingestion", "run_ref": "ingestion:8", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/590ab0317dd74f5c87841f3bf1019a5f/employee.csv", "layer": "SOURCE", "domain": "hr", "version": "5dbc6d51-c0c6-450e-ad7e-d488673503e8", "asset_id": "source:1:employee.csv", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-8/employee.csv", "layer": "BRONZE", "domain": "hr", "version": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "asset_id": "dataset:1", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": "ingestion:8"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "batch-id": "job-8", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T16:04:03.122021+00:00"}, "size_bytes": 16230, "version_id": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "5dbc6d51-c0c6-450e-ad7e-d488673503e8"}	SUCCESS	1	ba28814c-447a-4efd-8f77-7eddd37ce953	\N	bae0fdc7bb26	\N	\N	{"domain": "hr", "entity": "employee", "issues": [], "run_id": "5733877e-5dbb-446b-8cb6-9a69f3d508a7", "status": "PUBLISHED", "profile_id": "automatic-63b18667-eb2a-49e1-8770-97d623d0a2e6", "publication": {"mode": "production", "scope": "production:auto-e270bc9994f1530b4a540c0daa6c", "entity": "employee", "snapshot_id": "3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c", "records_published": 200}, "records_read": 200, "requested_by": "system:gold-storage-v2", "input_version": "sha256:0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "records_passed": 200, "ingestion_job_id": 8, "profile_approval": {"job_id": "63b18667-eb2a-49e1-8770-97d623d0a2e6", "status": "SYSTEM_RECIPE_VERIFIED", "recipe_version": "auto-source-v1", "profile_fingerprint": "d817b1f5ed152b9d55fd66f4239ca687d89c27b9d10fddc6f16997eaacce8f8a"}, "quarantine_report": null, "records_collapsed": 0, "profile_fingerprint": "d817b1f5ed152b9d55fd66f4239ca687d89c27b9d10fddc6f16997eaacce8f8a", "records_quarantined": 0}	2026-11-16 23:04:03.179496+07	2026-11-16 23:04:04.054225+07
\.


--
-- Data for Name: auto_pipeline_policy; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.auto_pipeline_policy (dataset_id, revision, enabled, definition, updated_by, updated_at) FROM stdin;
\.


--
-- Data for Name: canonical_identity; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.canonical_identity (canonical_id, entity) FROM stdin;
\.


--
-- Data for Name: catalog_column; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.catalog_column (catalog_column_id, catalog_table_id, column_name, data_type, description, is_nullable, is_primary_key, is_foreign_key) FROM stdin;
\.


--
-- Data for Name: catalog_metadata; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.catalog_metadata (domain_id, dataset, metadata_payload, is_deleted, updated_at) FROM stdin;
\.


--
-- Data for Name: catalog_table; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.catalog_table (catalog_table_id, dataset_id, table_name, source_table_name, description, owner, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: chart_widget; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.chart_widget (chart_widget_id, dashboard_id, widget_type, title, query_request_id, kpi_id, config_json, position_x, position_y, width, height) FROM stdin;
\.


--
-- Data for Name: class; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.class (class_id, class_code, course_id, semester_id, max_capacity, room, class_status) FROM stdin;
\.


--
-- Data for Name: class_lecturer; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.class_lecturer (class_lecturer_id, class_id, lecturer_id, teaching_role, is_primary, assigned_from, assigned_to) FROM stdin;
\.


--
-- Data for Name: cleaning_run; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.cleaning_run (run_id, ingestion_job_id, dataset_id, domain, entity, requested_by, session, bronze_version, input_version, cleaning_status, publication_status, records_read, records_passed, records_quarantined, records_collapsed, records_published, silver_snapshot_id, report_ref, quarantine_source, error_code, started_at, finished_at, profile_evidence, auto_job_id) FROM stdin;
c57c99da-0ae6-41e4-bc7a-59755f0627ed	2	2	academic	file_2	7	auto-5cb5b4e2548a0f888863ffd97381	4cde87fd-70f8-4818-8d64-89dd065b3795	sha256:e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb	COMPLETED	PUBLISHED	100	100	0	0	100	539ae500-2f13-451b-be83-286f62b636f9	{"key": "silver/reports/c57c99da-0ae6-41e4-bc7a-59755f0627ed/report-6d5c65c6e1495494011b8cab3a698ed6a10c512d95e83ff5f246ac84c24af448.json", "sha256": "6d5c65c6e1495494011b8cab3a698ed6a10c512d95e83ff5f246ac84c24af448", "size": 31887, "version_id": null}	null	\N	2026-11-16 21:07:17.584629+07	2026-11-16 21:07:17.962393+07	{"profile_id": "automatic-2cfe4d5c-255b-48b6-95c8-218009d675cf", "fingerprint": "3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35", "rule_version": "auto-source-v1:3479d4329fa4437f4f1150efb12a6fce990c0211fdbccec71589b9fa27aeed35", "definition": {"data_source_id": 2, "dataset_id": 2, "source_system_id": 35, "source_type": "EXCEL", "source_table": "academic_du_lieu_chuan.xlsx", "domain": "academic", "entity": "file_2", "version": "auto-source-v1", "scope": "auto-5cb5b4e2548a0f888863ffd97381", "source_schema_version": "source-1", "source_schema": {"version": "source-1", "file_format": {"date_format": "YYYY-MM-DD"}, "entities": {"file_2": {"primary_key": [], "columns": [{"name": "enrollment_id", "db_type": "string", "type": "text", "nullable": true, "source_identifier": true}, {"name": "student_id", "db_type": "string", "type": "text", "nullable": true, "source_identifier": true}, {"name": "student_name", "db_type": "string", "type": "text", "nullable": true}, {"name": "course_code", "db_type": "string", "type": "text", "nullable": true, "source_identifier": true}, {"name": "course_name", "db_type": "string", "type": "text", "nullable": true}, {"name": "semester", "db_type": "string", "type": "text", "nullable": true}, {"name": "academic_year", "db_type": "string", "type": "text", "nullable": true}, {"name": "credits", "db_type": "bigint", "type": "bigint", "nullable": true}, {"name": "score", "db_type": "decimal", "type": "decimal(38,10)", "nullable": true}, {"name": "status", "db_type": "string", "type": "text", "nullable": true}, {"name": "enrollment_date", "db_type": "string", "type": "text", "nullable": true}, {"name": "note", "db_type": "string", "type": "text", "nullable": true}]}}}, "contracts": {"academic": {"x-version": "source-1", "$defs": {"file_2": {"type": "object", "additionalProperties": false, "properties": {"enrollment_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "student_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "student_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "course_code": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "course_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "semester": {"type": ["string", "null"], "x-physical-type": "utf8"}, "academic_year": {"type": ["string", "null"], "x-physical-type": "utf8"}, "credits": {"type": ["integer", "null"], "x-physical-type": "int64"}, "score": {"type": ["string", "null"], "x-physical-type": "decimal(38,10)", "x-precision": 38, "x-scale": 10}, "status": {"type": ["string", "null"], "x-physical-type": "utf8"}, "enrollment_date": {"type": ["string", "null"], "x-physical-type": "utf8"}, "note": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-identity-mode": "source", "x-owner": "academic", "x-primary-key": ["_source_record_id"], "required": ["enrollment_id", "student_id", "student_name", "course_code", "course_name", "semester", "academic_year", "credits", "score", "status", "enrollment_date", "note", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"]}}}}, "field_mapping": {"enrollment_id": "enrollment_id", "student_id": "student_id", "student_name": "student_name", "course_code": "course_code", "course_name": "course_name", "semester": "semester", "academic_year": "academic_year", "credits": "credits", "score": "score", "status": "status", "enrollment_date": "enrollment_date", "note": "note"}, "ignored_columns": [], "format_options": {}, "type_options": {}, "null_tokens": {"enrollment_date": [""], "course_code": [""], "enrollment_id": [""], "semester": [""], "status": [""], "note": [""], "credits": [""], "academic_year": [""], "course_name": [""], "student_id": [""], "score": [""], "student_name": [""]}, "dictionaries": {}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:07:16.083365Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	2cfe4d5c-255b-48b6-95c8-218009d675cf
dc17ddcb-0eed-4f83-af4d-87dbd38ab94c	1	1	hr	assignment	9	auto-899188c733e26f94ba29ead9cf0e	d0f13b5b-371d-4d97-b29c-3dc744cfdb66	sha256:3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e	COMPLETED	PUBLISHED	200	200	0	0	200	52df8123-672a-425d-93a1-04f53de4a774	{"key": "silver/reports/dc17ddcb-0eed-4f83-af4d-87dbd38ab94c/report-998af512b6c90b66ad9f6a662ef099c2dfe0bc6cc023e6bcba358c7fd80c96ac.json", "sha256": "998af512b6c90b66ad9f6a662ef099c2dfe0bc6cc023e6bcba358c7fd80c96ac", "size": 59283, "version_id": null}	null	\N	2026-11-16 21:06:30.177241+07	2026-11-16 21:06:31.217185+07	{"profile_id": "automatic-22c2e420-b06a-45d9-ad48-290366ea6af2", "fingerprint": "08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004", "rule_version": "auto-source-v1:08fb52389d14b294a1786a68190e9745a89d9988fdcac8dcf5a7f62c6b340004", "definition": {"data_source_id": 1, "dataset_id": 1, "source_system_id": 34, "source_type": "CSV", "source_table": "assignment.csv", "domain": "hr", "entity": "assignment", "version": "auto-source-v1", "scope": "auto-899188c733e26f94ba29ead9cf0e", "source_schema_version": "1.2.0", "source_schema": {"dataset": "hr", "version": "1.2.0", "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. B\\u00e1m \\u0111\\u00fang DB ngu\\u1ed3n docs/data-schema/unilake-db-architecture.txt (t\\u00ean c\\u1ed9t, th\\u1ee9 t\\u1ef1, db_type, nullable, PK, UNIQUE, FK). type l\\u00e0 ki\\u1ec3u SQL \\u0111\\u1ec1 xu\\u1ea5t cho DDL v\\u00ec DB ch\\u1ec9 khai b\\u00e1o ki\\u1ec3u chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "source_db": "docs/data-schema/unilake-db-architecture.txt", "file_format": {"type": "csv", "encoding": "utf-8", "delimiter": ",", "header": true, "null_value": "", "date_format": "YYYY-MM-DD", "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": ".", "boolean_values": ["true", "false"]}, "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "H\\u1ec7 th\\u1ed1ng ngu\\u1ed3n"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID l\\u00f4 ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Th\\u1eddi \\u0111i\\u1ec3m ghi v\\u00e0o Bronze (UTC)"}], "entities": {"department": {"domain": "hr", "owned_by_dataset": true, "description": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "file": "hr/department.csv", "primary_key": ["department_id"], "columns": [{"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "department_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)"}, {"name": "department_name", "db_type": "string", "type": "varchar(200)", "nullable": false}, {"name": "department_type", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, {"name": "parent_department_id", "db_type": "bigint", "type": "bigint", "nullable": true, "references": {"entity": "department", "column": "department_id"}, "description": "Null = \\u0111\\u01a1n v\\u1ecb g\\u1ed1c"}, {"name": "status", "db_type": "string", "type": "varchar(20)", "nullable": false, "enum": ["Active", "Inactive"]}, {"name": "created_at", "db_type": "timestamp", "type": "timestamp", "nullable": false}]}, "position": {"domain": "hr", "owned_by_dataset": true, "description": "Ch\\u1ee9c danh/ch\\u1ee9c v\\u1ee5.", "file": "hr/position.csv", "primary_key": ["position_id"], "columns": [{"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "position_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^[A-Z]{2,5}$"}, {"name": "position_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "position_level", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Leadership", "Management", "Staff"]}]}, "qualification": {"domain": "hr", "owned_by_dataset": true, "description": "Danh m\\u1ee5c v\\u0103n b\\u1eb1ng v\\u00e0 ch\\u1ee9ng ch\\u1ec9. DB kh\\u00f4ng c\\u00f2n UNIQUE tr\\u00ean qualification_name; HR-DQ-13 ki\\u1ec3m tra tr\\u00f9ng.", "file": "hr/qualification.csv", "primary_key": ["qualification_id"], "columns": [{"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "qualification_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "qualification_type", "db_type": "string", "type": "varchar(100)", "nullable": true, "enum": ["Degree", "Certificate"]}]}, "employee": {"domain": "hr", "owned_by_dataset": true, "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "description": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "file": "hr/employee.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^NV[0-9]{4}$"}, {"name": "gender", "db_type": "string", "type": "varchar(20)", "nullable": true, "enum": ["Male", "Female"]}, {"name": "hire_date", "db_type": "date", "type": "date", "nullable": true, "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, {"name": "employee_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Resigned", "Retired"]}, {"name": "birth_year", "db_type": "date", "type": "date", "nullable": true}]}, "lecturer": {"domain": "hr", "owned_by_dataset": true, "description": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "file": "hr/lecturer.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "academic_degree", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["B.Sc.", "M.Sc.", "Ph.D."], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, {"name": "academic_title", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Assoc. Prof.", "Prof."], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, {"name": "research_field", "db_type": "string", "type": "varchar(250)", "nullable": true}]}, "employee_qualification": {"domain": "hr", "owned_by_dataset": true, "description": "V\\u0103n b\\u1eb1ng/ch\\u1ee9ng ch\\u1ec9 c\\u1ee7a nh\\u00e2n s\\u1ef1 (N-N gi\\u1eefa employee v\\u00e0 qualification).", "file": "hr/employee_qualification.csv", "primary_key": ["employee_id", "qualification_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "qualification", "column": "qualification_id"}}, {"name": "institution", "db_type": "string", "type": "varchar(250)", "nullable": true}, {"name": "year_obtained", "db_type": "int", "type": "int", "nullable": true, "min": 1960, "max": 2100}]}, "assignment": {"domain": "hr", "owned_by_dataset": true, "description": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "file": "hr/assignment.csv", "primary_key": ["assignment_id"], "columns": [{"name": "assignment_id", "db_type": "string", "type": "text", "nullable": false, "source_identifier": true}, {"name": "employee_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}, "source_identifier": true}, {"name": "department_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "department", "column": "department_id"}, "source_identifier": true}, {"name": "position_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "position", "column": "position_id"}, "source_identifier": true}, {"name": "start_date", "db_type": "date", "type": "date", "nullable": false}, {"name": "end_date", "db_type": "date", "type": "date", "nullable": true}, {"name": "is_primary", "db_type": "boolean", "type": "boolean", "nullable": false}, {"name": "assignment_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}]}}, "department_type_prefix": {"University": "UNI", "Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM"}, "position_rules": {"unique_holder_per_department": ["HT", "TK", "TP", "TBM"], "allowed_department_types": {"HT": ["Board"], "PHT": ["Board"], "TK": ["Faculty"], "PTK": ["Faculty"], "TP": ["Office"], "TBM": ["Division"], "GV": ["Faculty", "Division"], "GVC": ["Faculty", "Division"], "TG": ["Faculty", "Division"], "CV": ["Office"], "NV": ["Office"]}, "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "staff_only": ["CV", "NV", "TP"]}, "silver_derived_columns": {"employee": [{"name": "birth_year", "type": "int", "rule": "Thay date_of_birth (PII)"}, {"name": "is_lecturer", "type": "boolean", "rule": "C\\u00f3 d\\u00f2ng trong lecturer"}, {"name": "primary_department_id", "type": "bigint", "rule": "department_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "faculty_id", "type": "bigint", "rule": "\\u0110\\u01a1n v\\u1ecb con tr\\u1ef1c ti\\u1ebfp c\\u1ee7a \\u0111\\u01a1n v\\u1ecb g\\u1ed1c ch\\u1ee9a primary_department_id"}, {"name": "current_position_id", "type": "bigint", "rule": "position_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "seniority_years", "type": "decimal(4,1)", "rule": "T\\u1eeb hire_date (ho\\u1eb7c assignment s\\u1edbm nh\\u1ea5t) t\\u1edbi ng\\u00e0y ch\\u1ed1t ho\\u1eb7c ng\\u00e0y ngh\\u1ec9"}, {"name": "highest_degree", "type": "varchar(30)", "rule": "Ti\\u1ebfn s\\u0129 > Th\\u1ea1c s\\u0129 > K\\u1ef9 s\\u01b0/C\\u1eed nh\\u00e2n theo employee_qualification"}, {"name": "leave_date", "type": "date", "rule": "end_date l\\u1edbn nh\\u1ea5t c\\u1ee7a assignment khi Resigned/Retired"}]}, "quality_rules": [{"id": "HR-DQ-01", "entity": "*", "rule": "PK (k\\u1ec3 c\\u1ea3 PK gh\\u00e9p) v\\u00e0 c\\u1ed9t unique kh\\u00f4ng null, kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-02", "entity": "*", "rule": "FK tr\\u1ecf t\\u1edbi b\\u1ea3n ghi c\\u00f3 th\\u1eadt", "severity": "error"}, {"id": "HR-DQ-03", "entity": "*", "rule": "\\u0110\\u00fang ki\\u1ec3u, \\u0111\\u1ed9 d\\u00e0i, null, gi\\u00e1 tr\\u1ecb tr\\u1ea1ng th\\u00e1i, pattern, min/max", "severity": "error"}, {"id": "HR-DQ-04", "entity": "department", "rule": "M\\u1ed9t g\\u1ed1c, kh\\u00f4ng chu tr\\u00ecnh; department_type kh\\u1edbp ti\\u1ec1n t\\u1ed1 m\\u00e3; b\\u1ed9 m\\u00f4n (Division) thu\\u1ed9c khoa (Faculty)", "severity": "error"}, {"id": "HR-DQ-05", "entity": "employee", "rule": "Khi c\\u00f3 ng\\u00e0y sinh v\\u00e0 hire_date: tu\\u1ed5i khi tuy\\u1ec3n 20 - 60; hire_date = start_date s\\u1edbm nh\\u1ea5t trong assignment", "severity": "error"}, {"id": "HR-DQ-06", "entity": "assignment", "rule": "start_date >= hire_date (n\\u1ebfu c\\u00f3); end_date >= start_date; v\\u1ecb tr\\u00ed ch\\u00ednh kh\\u00f4ng ch\\u1ed3ng l\\u1ea5n", "severity": "error"}, {"id": "HR-DQ-07", "entity": "assignment", "rule": "Active c\\u00f3 \\u0111\\u00fang m\\u1ed9t v\\u1ecb tr\\u00ed ch\\u00ednh \\u0111ang hi\\u1ec7u l\\u1ef1c; Resigned/Retired kh\\u00f4ng c\\u00f2n assignment m\\u1edf", "severity": "error"}, {"id": "HR-DQ-08", "entity": "assignment", "rule": "HT, TK, TP, TBM: m\\u1ed9t ng\\u01b0\\u1eddi/\\u0111\\u01a1n v\\u1ecb/th\\u1eddi \\u0111i\\u1ec3m; ch\\u1ee9c v\\u1ee5 \\u0111\\u00fang lo\\u1ea1i \\u0111\\u01a1n v\\u1ecb", "severity": "error"}, {"id": "HR-DQ-09", "entity": "assignment", "rule": "Ch\\u1ee9c v\\u1ee5 gi\\u1ea3ng d\\u1ea1y ch\\u1ec9 cho gi\\u1ea3ng vi\\u00ean; CV, NV, TP ch\\u1ec9 cho c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh", "severity": "error"}, {"id": "HR-DQ-10", "entity": "employee_qualification", "rule": "N\\u0103m c\\u1ea5p sau 18 tu\\u1ed5i, kh\\u00f4ng sau ng\\u00e0y ch\\u1ed1t; C\\u1eed nh\\u00e2n/K\\u1ef9 s\\u01b0 < Th\\u1ea1c s\\u0129 < Ti\\u1ebfn s\\u0129", "severity": "error"}, {"id": "HR-DQ-11", "entity": "lecturer", "rule": "academic_degree kh\\u1edbp v\\u0103n b\\u1eb1ng cao nh\\u1ea5t; academic_title (Assoc. Prof., Prof.) ch\\u1ec9 khi academic_degree = Ph.D.", "severity": "warning"}, {"id": "HR-DQ-12", "entity": "assignment", "rule": "assignment_status kh\\u1edbp end_date (Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t)", "severity": "error"}, {"id": "HR-DQ-13", "entity": "qualification", "rule": "qualification_name kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-14", "entity": "class_lecturer (Academic)", "rule": "Gi\\u1ea3ng vi\\u00ean c\\u00f3 assignment hi\\u1ec7u l\\u1ef1c su\\u1ed1t kho\\u1ea3ng ph\\u1ee5 tr\\u00e1ch l\\u1edbp (assigned_from..assigned_to, m\\u1eb7c \\u0111\\u1ecbnh l\\u00e0 h\\u1ecdc k\\u1ef3 c\\u1ee7a l\\u1edbp)", "severity": "error"}]}, "contracts": {"hr": {"x-version": "source-1", "$defs": {"assignment": {"type": "object", "additionalProperties": false, "required": ["assignment_id", "employee_id", "department_id", "position_id", "start_date", "end_date", "is_primary", "assignment_status", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"assignment_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "department_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "position_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "start_date": {"type": "string", "x-physical-type": "date32", "format": "date"}, "end_date": {"type": ["string", "null"], "x-physical-type": "date32", "format": "date"}, "is_primary": {"type": "boolean", "x-physical-type": "boolean"}, "assignment_status": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-grain": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "x-owner": "hr", "x-primary-key": ["assignment_id"], "x-unique-together": [], "x-identity-mode": "source"}}}}, "field_mapping": {"assignment_id": "assignment_id", "employee_id": "employee_id", "department_id": "department_id", "position_id": "position_id", "start_date": "start_date", "end_date": "end_date", "is_primary": "is_primary", "assignment_status": "assignment_status"}, "ignored_columns": [], "format_options": {"start_date": {"date_formats": ["%Y-%m-%d"]}, "end_date": {"date_formats": ["%Y-%m-%d"]}}, "type_options": {}, "null_tokens": {"end_date": [""], "employee_id": [""], "position_id": [""], "start_date": [""], "is_primary": [""], "department_id": [""], "assignment_status": [""], "assignment_id": [""]}, "dictionaries": {"assignment_status": {}}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:06:27.145583Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	22c2e420-b06a-45d9-ad48-290366ea6af2
dee1dae0-81cd-47ce-affd-5ac9e8af266b	3	3	academic	file_3	7	auto-91a9e7cbf19dfbcbc68cea2d878b	c710c36f-f6fb-401a-a9ea-0472f9116b76	sha256:bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428	COMPLETED	BLOCKED	100	1	99	0	0	\N	{"key": "silver/reports/dee1dae0-81cd-47ce-affd-5ac9e8af266b/report-b7a7619f5c035c36f662306abd05076b3a8ee8f40bb50ff0d69b3e10a22856e6.json", "sha256": "b7a7619f5c035c36f662306abd05076b3a8ee8f40bb50ff0d69b3e10a22856e6", "size": 31608, "version_id": null}	{"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "version_id": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "size_bytes": 9522, "source_type": "EXCEL", "file_name": "academic_du_lieu_loi.xlsx", "parser_options": {"sheet_name": "Academic", "header_row": 1, "skip_rows": 0}, "parser_fingerprint": "2cd36ed5e8f775ca508c9dc249f790dd2fc2cce2a6b1b5bfb4ccb0f3251fd7ba", "columns_fingerprint": "ed5891e45e0ad42a30c8fae1837bab952f2f2e4fdf089adc5f3a4bf1eac5f926", "source_system_id": "35", "data_source_id": "3", "dataset_id": "3", "allowed_columns": []}	SILVER_PARTIAL_KEY_SAFETY_BLOCKED	2026-11-16 21:07:36.137074+07	2026-11-16 21:07:36.261862+07	{"profile_id": "automatic-c6b682d5-1d7e-4531-a632-b9f9b3337743", "fingerprint": "74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "definition": {"data_source_id": 3, "dataset_id": 3, "source_system_id": 35, "source_type": "EXCEL", "source_table": "academic_du_lieu_loi.xlsx", "domain": "academic", "entity": "file_3", "version": "auto-source-v1", "scope": "auto-91a9e7cbf19dfbcbc68cea2d878b", "source_schema_version": "source-1", "source_schema": {"version": "source-1", "file_format": {"date_format": "YYYY-MM-DD"}, "entities": {"file_3": {"primary_key": [], "columns": [{"name": "enrollment_id", "db_type": "string", "type": "text", "nullable": true, "source_identifier": true}, {"name": "student_id", "db_type": "string", "type": "text", "nullable": true, "source_identifier": true}, {"name": "student_name", "db_type": "string", "type": "text", "nullable": true}, {"name": "course_code", "db_type": "string", "type": "text", "nullable": true, "source_identifier": true}, {"name": "course_name", "db_type": "string", "type": "text", "nullable": true}, {"name": "semester", "db_type": "string", "type": "text", "nullable": true}, {"name": "academic_year", "db_type": "string", "type": "text", "nullable": true}, {"name": "credits", "db_type": "bigint", "type": "bigint", "nullable": true}, {"name": "score", "db_type": "string", "type": "text", "nullable": true}, {"name": "status", "db_type": "string", "type": "text", "nullable": true}, {"name": "enrollment_date", "db_type": "string", "type": "text", "nullable": true}, {"name": "note", "db_type": "string", "type": "text", "nullable": true}]}}}, "contracts": {"academic": {"x-version": "source-1", "$defs": {"file_3": {"type": "object", "additionalProperties": false, "properties": {"enrollment_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "student_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "student_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "course_code": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "course_name": {"type": ["string", "null"], "x-physical-type": "utf8"}, "semester": {"type": ["string", "null"], "x-physical-type": "utf8"}, "academic_year": {"type": ["string", "null"], "x-physical-type": "utf8"}, "credits": {"type": ["integer", "null"], "x-physical-type": "int64"}, "score": {"type": ["string", "null"], "x-physical-type": "utf8"}, "status": {"type": ["string", "null"], "x-physical-type": "utf8"}, "enrollment_date": {"type": ["string", "null"], "x-physical-type": "utf8"}, "note": {"type": ["string", "null"], "x-physical-type": "utf8"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-identity-mode": "source", "x-owner": "academic", "x-primary-key": ["_source_record_id"], "required": ["enrollment_id", "student_id", "student_name", "course_code", "course_name", "semester", "academic_year", "credits", "score", "status", "enrollment_date", "note", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"]}}}}, "field_mapping": {"enrollment_id": "enrollment_id", "student_id": "student_id", "student_name": "student_name", "course_code": "course_code", "course_name": "course_name", "semester": "semester", "academic_year": "academic_year", "credits": "credits", "score": "score", "status": "status", "enrollment_date": "enrollment_date", "note": "note"}, "ignored_columns": [], "format_options": {}, "type_options": {}, "null_tokens": {"enrollment_date": [""], "course_code": [""], "enrollment_id": [""], "semester": [""], "status": [""], "note": [""], "credits": [""], "academic_year": [""], "course_name": [""], "student_id": [""], "score": [""], "student_name": [""]}, "dictionaries": {}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:07:33.418484Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	c6b682d5-1d7e-4531-a632-b9f9b3337743
bb9ae615-7497-4fa6-bf73-99e9db7c38f1	7	1	hr	assignment	9	auto-899188c733e26f94ba29ead9cf0e	c63ab48e-b5e9-4e29-a134-011542cbf4b0	sha256:3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e	COMPLETED	PUBLISHED	200	200	0	0	200	58804508-8b12-45f5-98cf-2f1f1e2506e2	{"key": "silver/reports/bb9ae615-7497-4fa6-bf73-99e9db7c38f1/report-42a509757d236252482975a19dbb091ecba1841e8211616d9044c62241b63a92.json", "sha256": "42a509757d236252482975a19dbb091ecba1841e8211616d9044c62241b63a92", "size": 59283, "version_id": null}	null	\N	2026-11-16 21:26:29.875206+07	2026-11-16 21:26:30.131629+07	{"profile_id": "automatic-9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4", "fingerprint": "17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6", "rule_version": "auto-source-v1:17af93f0b7a20035acbdf439ecbf619dc31e561cf3b64a4c47805cc16b50aea6", "definition": {"data_source_id": 1, "dataset_id": 1, "source_system_id": 34, "source_type": "CSV", "source_table": "assignment.csv", "domain": "hr", "entity": "assignment", "version": "auto-source-v1", "scope": "auto-899188c733e26f94ba29ead9cf0e", "source_schema_version": "1.2.0", "source_schema": {"dataset": "hr", "version": "1.2.0", "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. B\\u00e1m \\u0111\\u00fang DB ngu\\u1ed3n docs/data-schema/unilake-db-architecture.txt (t\\u00ean c\\u1ed9t, th\\u1ee9 t\\u1ef1, db_type, nullable, PK, UNIQUE, FK). type l\\u00e0 ki\\u1ec3u SQL \\u0111\\u1ec1 xu\\u1ea5t cho DDL v\\u00ec DB ch\\u1ec9 khai b\\u00e1o ki\\u1ec3u chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "source_db": "docs/data-schema/unilake-db-architecture.txt", "file_format": {"type": "csv", "encoding": "utf-8", "delimiter": ",", "header": true, "null_value": "", "date_format": "YYYY-MM-DD", "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": ".", "boolean_values": ["true", "false"]}, "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "H\\u1ec7 th\\u1ed1ng ngu\\u1ed3n"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID l\\u00f4 ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Th\\u1eddi \\u0111i\\u1ec3m ghi v\\u00e0o Bronze (UTC)"}], "entities": {"department": {"domain": "hr", "owned_by_dataset": true, "description": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "file": "hr/department.csv", "primary_key": ["department_id"], "columns": [{"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "department_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)"}, {"name": "department_name", "db_type": "string", "type": "varchar(200)", "nullable": false}, {"name": "department_type", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, {"name": "parent_department_id", "db_type": "bigint", "type": "bigint", "nullable": true, "references": {"entity": "department", "column": "department_id"}, "description": "Null = \\u0111\\u01a1n v\\u1ecb g\\u1ed1c"}, {"name": "status", "db_type": "string", "type": "varchar(20)", "nullable": false, "enum": ["Active", "Inactive"]}, {"name": "created_at", "db_type": "timestamp", "type": "timestamp", "nullable": false}]}, "position": {"domain": "hr", "owned_by_dataset": true, "description": "Ch\\u1ee9c danh/ch\\u1ee9c v\\u1ee5.", "file": "hr/position.csv", "primary_key": ["position_id"], "columns": [{"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "position_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^[A-Z]{2,5}$"}, {"name": "position_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "position_level", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Leadership", "Management", "Staff"]}]}, "qualification": {"domain": "hr", "owned_by_dataset": true, "description": "Danh m\\u1ee5c v\\u0103n b\\u1eb1ng v\\u00e0 ch\\u1ee9ng ch\\u1ec9. DB kh\\u00f4ng c\\u00f2n UNIQUE tr\\u00ean qualification_name; HR-DQ-13 ki\\u1ec3m tra tr\\u00f9ng.", "file": "hr/qualification.csv", "primary_key": ["qualification_id"], "columns": [{"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "qualification_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "qualification_type", "db_type": "string", "type": "varchar(100)", "nullable": true, "enum": ["Degree", "Certificate"]}]}, "employee": {"domain": "hr", "owned_by_dataset": true, "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "description": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "file": "hr/employee.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^NV[0-9]{4}$"}, {"name": "gender", "db_type": "string", "type": "varchar(20)", "nullable": true, "enum": ["Male", "Female"]}, {"name": "hire_date", "db_type": "date", "type": "date", "nullable": true, "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, {"name": "employee_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Resigned", "Retired"]}, {"name": "birth_year", "db_type": "date", "type": "date", "nullable": true}]}, "lecturer": {"domain": "hr", "owned_by_dataset": true, "description": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "file": "hr/lecturer.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "academic_degree", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["B.Sc.", "M.Sc.", "Ph.D."], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, {"name": "academic_title", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Assoc. Prof.", "Prof."], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, {"name": "research_field", "db_type": "string", "type": "varchar(250)", "nullable": true}]}, "employee_qualification": {"domain": "hr", "owned_by_dataset": true, "description": "V\\u0103n b\\u1eb1ng/ch\\u1ee9ng ch\\u1ec9 c\\u1ee7a nh\\u00e2n s\\u1ef1 (N-N gi\\u1eefa employee v\\u00e0 qualification).", "file": "hr/employee_qualification.csv", "primary_key": ["employee_id", "qualification_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "qualification", "column": "qualification_id"}}, {"name": "institution", "db_type": "string", "type": "varchar(250)", "nullable": true}, {"name": "year_obtained", "db_type": "int", "type": "int", "nullable": true, "min": 1960, "max": 2100}]}, "assignment": {"domain": "hr", "owned_by_dataset": true, "description": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "file": "hr/assignment.csv", "primary_key": ["assignment_id"], "columns": [{"name": "assignment_id", "db_type": "string", "type": "text", "nullable": false, "source_identifier": true}, {"name": "employee_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}, "source_identifier": true}, {"name": "department_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "department", "column": "department_id"}, "source_identifier": true}, {"name": "position_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "position", "column": "position_id"}, "source_identifier": true}, {"name": "start_date", "db_type": "date", "type": "date", "nullable": false}, {"name": "end_date", "db_type": "date", "type": "date", "nullable": true}, {"name": "is_primary", "db_type": "boolean", "type": "boolean", "nullable": false}, {"name": "assignment_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}]}}, "department_type_prefix": {"University": "UNI", "Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM"}, "position_rules": {"unique_holder_per_department": ["HT", "TK", "TP", "TBM"], "allowed_department_types": {"HT": ["Board"], "PHT": ["Board"], "TK": ["Faculty"], "PTK": ["Faculty"], "TP": ["Office"], "TBM": ["Division"], "GV": ["Faculty", "Division"], "GVC": ["Faculty", "Division"], "TG": ["Faculty", "Division"], "CV": ["Office"], "NV": ["Office"]}, "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "staff_only": ["CV", "NV", "TP"]}, "silver_derived_columns": {"employee": [{"name": "birth_year", "type": "int", "rule": "Thay date_of_birth (PII)"}, {"name": "is_lecturer", "type": "boolean", "rule": "C\\u00f3 d\\u00f2ng trong lecturer"}, {"name": "primary_department_id", "type": "bigint", "rule": "department_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "faculty_id", "type": "bigint", "rule": "\\u0110\\u01a1n v\\u1ecb con tr\\u1ef1c ti\\u1ebfp c\\u1ee7a \\u0111\\u01a1n v\\u1ecb g\\u1ed1c ch\\u1ee9a primary_department_id"}, {"name": "current_position_id", "type": "bigint", "rule": "position_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "seniority_years", "type": "decimal(4,1)", "rule": "T\\u1eeb hire_date (ho\\u1eb7c assignment s\\u1edbm nh\\u1ea5t) t\\u1edbi ng\\u00e0y ch\\u1ed1t ho\\u1eb7c ng\\u00e0y ngh\\u1ec9"}, {"name": "highest_degree", "type": "varchar(30)", "rule": "Ti\\u1ebfn s\\u0129 > Th\\u1ea1c s\\u0129 > K\\u1ef9 s\\u01b0/C\\u1eed nh\\u00e2n theo employee_qualification"}, {"name": "leave_date", "type": "date", "rule": "end_date l\\u1edbn nh\\u1ea5t c\\u1ee7a assignment khi Resigned/Retired"}]}, "quality_rules": [{"id": "HR-DQ-01", "entity": "*", "rule": "PK (k\\u1ec3 c\\u1ea3 PK gh\\u00e9p) v\\u00e0 c\\u1ed9t unique kh\\u00f4ng null, kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-02", "entity": "*", "rule": "FK tr\\u1ecf t\\u1edbi b\\u1ea3n ghi c\\u00f3 th\\u1eadt", "severity": "error"}, {"id": "HR-DQ-03", "entity": "*", "rule": "\\u0110\\u00fang ki\\u1ec3u, \\u0111\\u1ed9 d\\u00e0i, null, gi\\u00e1 tr\\u1ecb tr\\u1ea1ng th\\u00e1i, pattern, min/max", "severity": "error"}, {"id": "HR-DQ-04", "entity": "department", "rule": "M\\u1ed9t g\\u1ed1c, kh\\u00f4ng chu tr\\u00ecnh; department_type kh\\u1edbp ti\\u1ec1n t\\u1ed1 m\\u00e3; b\\u1ed9 m\\u00f4n (Division) thu\\u1ed9c khoa (Faculty)", "severity": "error"}, {"id": "HR-DQ-05", "entity": "employee", "rule": "Khi c\\u00f3 ng\\u00e0y sinh v\\u00e0 hire_date: tu\\u1ed5i khi tuy\\u1ec3n 20 - 60; hire_date = start_date s\\u1edbm nh\\u1ea5t trong assignment", "severity": "error"}, {"id": "HR-DQ-06", "entity": "assignment", "rule": "start_date >= hire_date (n\\u1ebfu c\\u00f3); end_date >= start_date; v\\u1ecb tr\\u00ed ch\\u00ednh kh\\u00f4ng ch\\u1ed3ng l\\u1ea5n", "severity": "error"}, {"id": "HR-DQ-07", "entity": "assignment", "rule": "Active c\\u00f3 \\u0111\\u00fang m\\u1ed9t v\\u1ecb tr\\u00ed ch\\u00ednh \\u0111ang hi\\u1ec7u l\\u1ef1c; Resigned/Retired kh\\u00f4ng c\\u00f2n assignment m\\u1edf", "severity": "error"}, {"id": "HR-DQ-08", "entity": "assignment", "rule": "HT, TK, TP, TBM: m\\u1ed9t ng\\u01b0\\u1eddi/\\u0111\\u01a1n v\\u1ecb/th\\u1eddi \\u0111i\\u1ec3m; ch\\u1ee9c v\\u1ee5 \\u0111\\u00fang lo\\u1ea1i \\u0111\\u01a1n v\\u1ecb", "severity": "error"}, {"id": "HR-DQ-09", "entity": "assignment", "rule": "Ch\\u1ee9c v\\u1ee5 gi\\u1ea3ng d\\u1ea1y ch\\u1ec9 cho gi\\u1ea3ng vi\\u00ean; CV, NV, TP ch\\u1ec9 cho c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh", "severity": "error"}, {"id": "HR-DQ-10", "entity": "employee_qualification", "rule": "N\\u0103m c\\u1ea5p sau 18 tu\\u1ed5i, kh\\u00f4ng sau ng\\u00e0y ch\\u1ed1t; C\\u1eed nh\\u00e2n/K\\u1ef9 s\\u01b0 < Th\\u1ea1c s\\u0129 < Ti\\u1ebfn s\\u0129", "severity": "error"}, {"id": "HR-DQ-11", "entity": "lecturer", "rule": "academic_degree kh\\u1edbp v\\u0103n b\\u1eb1ng cao nh\\u1ea5t; academic_title (Assoc. Prof., Prof.) ch\\u1ec9 khi academic_degree = Ph.D.", "severity": "warning"}, {"id": "HR-DQ-12", "entity": "assignment", "rule": "assignment_status kh\\u1edbp end_date (Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t)", "severity": "error"}, {"id": "HR-DQ-13", "entity": "qualification", "rule": "qualification_name kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-14", "entity": "class_lecturer (Academic)", "rule": "Gi\\u1ea3ng vi\\u00ean c\\u00f3 assignment hi\\u1ec7u l\\u1ef1c su\\u1ed1t kho\\u1ea3ng ph\\u1ee5 tr\\u00e1ch l\\u1edbp (assigned_from..assigned_to, m\\u1eb7c \\u0111\\u1ecbnh l\\u00e0 h\\u1ecdc k\\u1ef3 c\\u1ee7a l\\u1edbp)", "severity": "error"}]}, "contracts": {"hr": {"x-version": "source-1", "$defs": {"assignment": {"type": "object", "additionalProperties": false, "required": ["assignment_id", "employee_id", "department_id", "position_id", "start_date", "end_date", "is_primary", "assignment_status", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"assignment_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "department_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "position_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "start_date": {"type": "string", "x-physical-type": "date32", "format": "date"}, "end_date": {"type": ["string", "null"], "x-physical-type": "date32", "format": "date"}, "is_primary": {"type": "boolean", "x-physical-type": "boolean"}, "assignment_status": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-grain": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "x-owner": "hr", "x-primary-key": ["assignment_id"], "x-unique-together": [], "x-identity-mode": "source"}}}}, "field_mapping": {"assignment_id": "assignment_id", "employee_id": "employee_id", "department_id": "department_id", "position_id": "position_id", "start_date": "start_date", "end_date": "end_date", "is_primary": "is_primary", "assignment_status": "assignment_status"}, "ignored_columns": [], "format_options": {"start_date": {"date_formats": ["%Y-%m-%d"]}, "end_date": {"date_formats": ["%Y-%m-%d"]}}, "type_options": {}, "null_tokens": {"end_date": [""], "employee_id": [""], "position_id": [""], "start_date": [""], "is_primary": [""], "department_id": [""], "assignment_status": [""], "assignment_id": [""]}, "dictionaries": {"assignment_status": {}}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:26:26.626762Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	9e753c5c-8cda-4c88-aeb4-4bd3f3cde7a4
cccba0f5-32ff-466a-b1bd-4692a1830a46	4	1	hr	department	9	auto-7858eb8f709a3e0a1583c6d93948	9bcaacef-d212-4f6c-b8ba-9d21c7daaf03	sha256:efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760	COMPLETED	PUBLISHED	4	4	0	0	4	1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf	{"key": "silver/reports/cccba0f5-32ff-466a-b1bd-4692a1830a46/report-bd40839e0725767247328e89ba7946f1253bfc8bfd7a39558ee0a8381be98652.json", "sha256": "bd40839e0725767247328e89ba7946f1253bfc8bfd7a39558ee0a8381be98652", "size": 2302, "version_id": null}	null	\N	2026-11-16 21:15:57.464244+07	2026-11-16 21:15:57.711753+07	{"profile_id": "automatic-6c3be928-5856-4ca9-9ec6-5425dd3c6a2d", "fingerprint": "5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547", "rule_version": "auto-source-v1:5f37e78968da9f99f060a0fe6c0e43f5473c80263f51b8a9ee3b2c449bae9547", "definition": {"data_source_id": 1, "dataset_id": 1, "source_system_id": 34, "source_type": "CSV", "source_table": "department.csv", "domain": "hr", "entity": "department", "version": "auto-source-v1", "scope": "auto-7858eb8f709a3e0a1583c6d93948", "source_schema_version": "1.2.0", "source_schema": {"dataset": "hr", "version": "1.2.0", "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. B\\u00e1m \\u0111\\u00fang DB ngu\\u1ed3n docs/data-schema/unilake-db-architecture.txt (t\\u00ean c\\u1ed9t, th\\u1ee9 t\\u1ef1, db_type, nullable, PK, UNIQUE, FK). type l\\u00e0 ki\\u1ec3u SQL \\u0111\\u1ec1 xu\\u1ea5t cho DDL v\\u00ec DB ch\\u1ec9 khai b\\u00e1o ki\\u1ec3u chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "source_db": "docs/data-schema/unilake-db-architecture.txt", "file_format": {"type": "csv", "encoding": "utf-8", "delimiter": ",", "header": true, "null_value": "", "date_format": "YYYY-MM-DD", "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": ".", "boolean_values": ["true", "false"]}, "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "H\\u1ec7 th\\u1ed1ng ngu\\u1ed3n"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID l\\u00f4 ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Th\\u1eddi \\u0111i\\u1ec3m ghi v\\u00e0o Bronze (UTC)"}], "entities": {"department": {"domain": "hr", "owned_by_dataset": true, "description": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "file": "hr/department.csv", "primary_key": ["department_id"], "columns": [{"name": "department_id", "db_type": "string", "type": "text", "nullable": false, "source_identifier": true}, {"name": "department_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)"}, {"name": "department_name", "db_type": "string", "type": "varchar(200)", "nullable": false}, {"name": "department_type", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, {"name": "parent_department_id", "db_type": "string", "type": "text", "nullable": true, "references": {"entity": "department", "column": "department_id"}, "description": "Null = \\u0111\\u01a1n v\\u1ecb g\\u1ed1c", "source_identifier": true}, {"name": "status", "db_type": "string", "type": "varchar(20)", "nullable": false, "enum": ["Active", "Inactive"]}, {"name": "created_at", "db_type": "timestamp", "type": "timestamp", "nullable": false}]}, "position": {"domain": "hr", "owned_by_dataset": true, "description": "Ch\\u1ee9c danh/ch\\u1ee9c v\\u1ee5.", "file": "hr/position.csv", "primary_key": ["position_id"], "columns": [{"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "position_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^[A-Z]{2,5}$"}, {"name": "position_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "position_level", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Leadership", "Management", "Staff"]}]}, "qualification": {"domain": "hr", "owned_by_dataset": true, "description": "Danh m\\u1ee5c v\\u0103n b\\u1eb1ng v\\u00e0 ch\\u1ee9ng ch\\u1ec9. DB kh\\u00f4ng c\\u00f2n UNIQUE tr\\u00ean qualification_name; HR-DQ-13 ki\\u1ec3m tra tr\\u00f9ng.", "file": "hr/qualification.csv", "primary_key": ["qualification_id"], "columns": [{"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "qualification_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "qualification_type", "db_type": "string", "type": "varchar(100)", "nullable": true, "enum": ["Degree", "Certificate"]}]}, "employee": {"domain": "hr", "owned_by_dataset": true, "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "description": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "file": "hr/employee.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^NV[0-9]{4}$"}, {"name": "gender", "db_type": "string", "type": "varchar(20)", "nullable": true, "enum": ["Male", "Female"]}, {"name": "hire_date", "db_type": "date", "type": "date", "nullable": true, "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, {"name": "employee_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Resigned", "Retired"]}, {"name": "birth_year", "db_type": "date", "type": "date", "nullable": true}]}, "lecturer": {"domain": "hr", "owned_by_dataset": true, "description": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "file": "hr/lecturer.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "academic_degree", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["B.Sc.", "M.Sc.", "Ph.D."], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, {"name": "academic_title", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Assoc. Prof.", "Prof."], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, {"name": "research_field", "db_type": "string", "type": "varchar(250)", "nullable": true}]}, "employee_qualification": {"domain": "hr", "owned_by_dataset": true, "description": "V\\u0103n b\\u1eb1ng/ch\\u1ee9ng ch\\u1ec9 c\\u1ee7a nh\\u00e2n s\\u1ef1 (N-N gi\\u1eefa employee v\\u00e0 qualification).", "file": "hr/employee_qualification.csv", "primary_key": ["employee_id", "qualification_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "qualification", "column": "qualification_id"}}, {"name": "institution", "db_type": "string", "type": "varchar(250)", "nullable": true}, {"name": "year_obtained", "db_type": "int", "type": "int", "nullable": true, "min": 1960, "max": 2100}]}, "assignment": {"domain": "hr", "owned_by_dataset": true, "description": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "file": "hr/assignment.csv", "primary_key": ["assignment_id"], "columns": [{"name": "assignment_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "department", "column": "department_id"}}, {"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "position", "column": "position_id"}}, {"name": "start_date", "db_type": "date", "type": "date", "nullable": false}, {"name": "end_date", "db_type": "date", "type": "date", "nullable": true}, {"name": "is_primary", "db_type": "boolean", "type": "boolean", "nullable": false}, {"name": "assignment_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}]}}, "department_type_prefix": {"University": "UNI", "Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM"}, "position_rules": {"unique_holder_per_department": ["HT", "TK", "TP", "TBM"], "allowed_department_types": {"HT": ["Board"], "PHT": ["Board"], "TK": ["Faculty"], "PTK": ["Faculty"], "TP": ["Office"], "TBM": ["Division"], "GV": ["Faculty", "Division"], "GVC": ["Faculty", "Division"], "TG": ["Faculty", "Division"], "CV": ["Office"], "NV": ["Office"]}, "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "staff_only": ["CV", "NV", "TP"]}, "silver_derived_columns": {"employee": [{"name": "birth_year", "type": "int", "rule": "Thay date_of_birth (PII)"}, {"name": "is_lecturer", "type": "boolean", "rule": "C\\u00f3 d\\u00f2ng trong lecturer"}, {"name": "primary_department_id", "type": "bigint", "rule": "department_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "faculty_id", "type": "bigint", "rule": "\\u0110\\u01a1n v\\u1ecb con tr\\u1ef1c ti\\u1ebfp c\\u1ee7a \\u0111\\u01a1n v\\u1ecb g\\u1ed1c ch\\u1ee9a primary_department_id"}, {"name": "current_position_id", "type": "bigint", "rule": "position_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "seniority_years", "type": "decimal(4,1)", "rule": "T\\u1eeb hire_date (ho\\u1eb7c assignment s\\u1edbm nh\\u1ea5t) t\\u1edbi ng\\u00e0y ch\\u1ed1t ho\\u1eb7c ng\\u00e0y ngh\\u1ec9"}, {"name": "highest_degree", "type": "varchar(30)", "rule": "Ti\\u1ebfn s\\u0129 > Th\\u1ea1c s\\u0129 > K\\u1ef9 s\\u01b0/C\\u1eed nh\\u00e2n theo employee_qualification"}, {"name": "leave_date", "type": "date", "rule": "end_date l\\u1edbn nh\\u1ea5t c\\u1ee7a assignment khi Resigned/Retired"}]}, "quality_rules": [{"id": "HR-DQ-01", "entity": "*", "rule": "PK (k\\u1ec3 c\\u1ea3 PK gh\\u00e9p) v\\u00e0 c\\u1ed9t unique kh\\u00f4ng null, kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-02", "entity": "*", "rule": "FK tr\\u1ecf t\\u1edbi b\\u1ea3n ghi c\\u00f3 th\\u1eadt", "severity": "error"}, {"id": "HR-DQ-03", "entity": "*", "rule": "\\u0110\\u00fang ki\\u1ec3u, \\u0111\\u1ed9 d\\u00e0i, null, gi\\u00e1 tr\\u1ecb tr\\u1ea1ng th\\u00e1i, pattern, min/max", "severity": "error"}, {"id": "HR-DQ-04", "entity": "department", "rule": "M\\u1ed9t g\\u1ed1c, kh\\u00f4ng chu tr\\u00ecnh; department_type kh\\u1edbp ti\\u1ec1n t\\u1ed1 m\\u00e3; b\\u1ed9 m\\u00f4n (Division) thu\\u1ed9c khoa (Faculty)", "severity": "error"}, {"id": "HR-DQ-05", "entity": "employee", "rule": "Khi c\\u00f3 ng\\u00e0y sinh v\\u00e0 hire_date: tu\\u1ed5i khi tuy\\u1ec3n 20 - 60; hire_date = start_date s\\u1edbm nh\\u1ea5t trong assignment", "severity": "error"}, {"id": "HR-DQ-06", "entity": "assignment", "rule": "start_date >= hire_date (n\\u1ebfu c\\u00f3); end_date >= start_date; v\\u1ecb tr\\u00ed ch\\u00ednh kh\\u00f4ng ch\\u1ed3ng l\\u1ea5n", "severity": "error"}, {"id": "HR-DQ-07", "entity": "assignment", "rule": "Active c\\u00f3 \\u0111\\u00fang m\\u1ed9t v\\u1ecb tr\\u00ed ch\\u00ednh \\u0111ang hi\\u1ec7u l\\u1ef1c; Resigned/Retired kh\\u00f4ng c\\u00f2n assignment m\\u1edf", "severity": "error"}, {"id": "HR-DQ-08", "entity": "assignment", "rule": "HT, TK, TP, TBM: m\\u1ed9t ng\\u01b0\\u1eddi/\\u0111\\u01a1n v\\u1ecb/th\\u1eddi \\u0111i\\u1ec3m; ch\\u1ee9c v\\u1ee5 \\u0111\\u00fang lo\\u1ea1i \\u0111\\u01a1n v\\u1ecb", "severity": "error"}, {"id": "HR-DQ-09", "entity": "assignment", "rule": "Ch\\u1ee9c v\\u1ee5 gi\\u1ea3ng d\\u1ea1y ch\\u1ec9 cho gi\\u1ea3ng vi\\u00ean; CV, NV, TP ch\\u1ec9 cho c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh", "severity": "error"}, {"id": "HR-DQ-10", "entity": "employee_qualification", "rule": "N\\u0103m c\\u1ea5p sau 18 tu\\u1ed5i, kh\\u00f4ng sau ng\\u00e0y ch\\u1ed1t; C\\u1eed nh\\u00e2n/K\\u1ef9 s\\u01b0 < Th\\u1ea1c s\\u0129 < Ti\\u1ebfn s\\u0129", "severity": "error"}, {"id": "HR-DQ-11", "entity": "lecturer", "rule": "academic_degree kh\\u1edbp v\\u0103n b\\u1eb1ng cao nh\\u1ea5t; academic_title (Assoc. Prof., Prof.) ch\\u1ec9 khi academic_degree = Ph.D.", "severity": "warning"}, {"id": "HR-DQ-12", "entity": "assignment", "rule": "assignment_status kh\\u1edbp end_date (Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t)", "severity": "error"}, {"id": "HR-DQ-13", "entity": "qualification", "rule": "qualification_name kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-14", "entity": "class_lecturer (Academic)", "rule": "Gi\\u1ea3ng vi\\u00ean c\\u00f3 assignment hi\\u1ec7u l\\u1ef1c su\\u1ed1t kho\\u1ea3ng ph\\u1ee5 tr\\u00e1ch l\\u1edbp (assigned_from..assigned_to, m\\u1eb7c \\u0111\\u1ecbnh l\\u00e0 h\\u1ecdc k\\u1ef3 c\\u1ee7a l\\u1edbp)", "severity": "error"}]}, "contracts": {"hr": {"x-version": "source-1", "$defs": {"department": {"type": "object", "additionalProperties": false, "required": ["department_id", "department_code", "department_name", "department_type", "parent_department_id", "status", "created_at", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"department_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "department_code": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)", "x-unique-non-null": true}, "department_name": {"type": "string", "x-physical-type": "varchar(200)", "maxLength": 200}, "department_type": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, "parent_department_id": {"type": ["string", "null"], "x-physical-type": "utf8", "x-source-identifier": true}, "status": {"type": "string", "x-physical-type": "varchar(20)", "maxLength": 20, "enum": ["Active", "Inactive"]}, "created_at": {"type": "string", "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-grain": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "x-owner": "hr", "x-primary-key": ["department_id"], "x-unique-together": [], "x-identity-mode": "source"}}}}, "field_mapping": {"department_id": "department_id", "department_code": "department_code", "department_name": "department_name", "department_type": "department_type", "parent_department_id": "parent_department_id", "status": "status", "created_at": "created_at"}, "ignored_columns": [], "format_options": {"created_at": {"timestamp_formats": ["%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"]}}, "type_options": {}, "null_tokens": {"created_at": [""], "department_type": [""], "parent_department_id": [""], "status": [""], "department_id": [""], "department_code": [""], "department_name": [""]}, "dictionaries": {"department_type": {}, "status": {}}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:15:55.456478Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	6c3be928-5856-4ca9-9ec6-5425dd3c6a2d
c97d27b1-e712-4f14-bccd-0c4e3694d441	6	1	hr	lecturer	9	auto-714bd628f3e833afe92a14aa6053	b1cc721d-a063-44b0-a8e3-63b7f80084fa	sha256:91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2	COMPLETED	PUBLISHED	100	100	0	0	100	a1efba66-0aa4-4215-a9fc-758af2a59a0d	{"key": "silver/reports/c97d27b1-e712-4f14-bccd-0c4e3694d441/report-b30d39c6a87ab3197ddf262cdb236865339370960b5feae0b45b3eff727659a8.json", "sha256": "b30d39c6a87ab3197ddf262cdb236865339370960b5feae0b45b3eff727659a8", "size": 30381, "version_id": null}	null	\N	2026-11-16 21:26:07.499145+07	2026-11-16 21:26:07.694629+07	{"profile_id": "automatic-d4a7c552-74d9-45be-a5c6-2f74612d578c", "fingerprint": "0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03", "rule_version": "auto-source-v1:0a8d30ba2bdc28cfdeece0a2a32a3371a66774e153d8d51cce0d2f19f7d22f03", "definition": {"data_source_id": 1, "dataset_id": 1, "source_system_id": 34, "source_type": "CSV", "source_table": "lecturer.csv", "domain": "hr", "entity": "lecturer", "version": "auto-source-v1", "scope": "auto-714bd628f3e833afe92a14aa6053", "source_schema_version": "1.2.0", "source_schema": {"dataset": "hr", "version": "1.2.0", "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. B\\u00e1m \\u0111\\u00fang DB ngu\\u1ed3n docs/data-schema/unilake-db-architecture.txt (t\\u00ean c\\u1ed9t, th\\u1ee9 t\\u1ef1, db_type, nullable, PK, UNIQUE, FK). type l\\u00e0 ki\\u1ec3u SQL \\u0111\\u1ec1 xu\\u1ea5t cho DDL v\\u00ec DB ch\\u1ec9 khai b\\u00e1o ki\\u1ec3u chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "source_db": "docs/data-schema/unilake-db-architecture.txt", "file_format": {"type": "csv", "encoding": "utf-8", "delimiter": ",", "header": true, "null_value": "", "date_format": "YYYY-MM-DD", "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": ".", "boolean_values": ["true", "false"]}, "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "H\\u1ec7 th\\u1ed1ng ngu\\u1ed3n"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID l\\u00f4 ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Th\\u1eddi \\u0111i\\u1ec3m ghi v\\u00e0o Bronze (UTC)"}], "entities": {"department": {"domain": "hr", "owned_by_dataset": true, "description": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "file": "hr/department.csv", "primary_key": ["department_id"], "columns": [{"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "department_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)"}, {"name": "department_name", "db_type": "string", "type": "varchar(200)", "nullable": false}, {"name": "department_type", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, {"name": "parent_department_id", "db_type": "bigint", "type": "bigint", "nullable": true, "references": {"entity": "department", "column": "department_id"}, "description": "Null = \\u0111\\u01a1n v\\u1ecb g\\u1ed1c"}, {"name": "status", "db_type": "string", "type": "varchar(20)", "nullable": false, "enum": ["Active", "Inactive"]}, {"name": "created_at", "db_type": "timestamp", "type": "timestamp", "nullable": false}]}, "position": {"domain": "hr", "owned_by_dataset": true, "description": "Ch\\u1ee9c danh/ch\\u1ee9c v\\u1ee5.", "file": "hr/position.csv", "primary_key": ["position_id"], "columns": [{"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "position_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^[A-Z]{2,5}$"}, {"name": "position_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "position_level", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Leadership", "Management", "Staff"]}]}, "qualification": {"domain": "hr", "owned_by_dataset": true, "description": "Danh m\\u1ee5c v\\u0103n b\\u1eb1ng v\\u00e0 ch\\u1ee9ng ch\\u1ec9. DB kh\\u00f4ng c\\u00f2n UNIQUE tr\\u00ean qualification_name; HR-DQ-13 ki\\u1ec3m tra tr\\u00f9ng.", "file": "hr/qualification.csv", "primary_key": ["qualification_id"], "columns": [{"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "qualification_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "qualification_type", "db_type": "string", "type": "varchar(100)", "nullable": true, "enum": ["Degree", "Certificate"]}]}, "employee": {"domain": "hr", "owned_by_dataset": true, "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "description": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "file": "hr/employee.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^NV[0-9]{4}$"}, {"name": "gender", "db_type": "string", "type": "varchar(20)", "nullable": true, "enum": ["Male", "Female"]}, {"name": "hire_date", "db_type": "date", "type": "date", "nullable": true, "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, {"name": "employee_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Resigned", "Retired"]}, {"name": "birth_year", "db_type": "date", "type": "date", "nullable": true}]}, "lecturer": {"domain": "hr", "owned_by_dataset": true, "description": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "file": "hr/lecturer.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "string", "type": "text", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}, "source_identifier": true}, {"name": "academic_degree", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["B.Sc.", "M.Sc.", "Ph.D."], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, {"name": "academic_title", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Assoc. Prof.", "Prof."], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, {"name": "research_field", "db_type": "string", "type": "varchar(250)", "nullable": true}]}, "employee_qualification": {"domain": "hr", "owned_by_dataset": true, "description": "V\\u0103n b\\u1eb1ng/ch\\u1ee9ng ch\\u1ec9 c\\u1ee7a nh\\u00e2n s\\u1ef1 (N-N gi\\u1eefa employee v\\u00e0 qualification).", "file": "hr/employee_qualification.csv", "primary_key": ["employee_id", "qualification_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "qualification", "column": "qualification_id"}}, {"name": "institution", "db_type": "string", "type": "varchar(250)", "nullable": true}, {"name": "year_obtained", "db_type": "int", "type": "int", "nullable": true, "min": 1960, "max": 2100}]}, "assignment": {"domain": "hr", "owned_by_dataset": true, "description": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "file": "hr/assignment.csv", "primary_key": ["assignment_id"], "columns": [{"name": "assignment_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "department", "column": "department_id"}}, {"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "position", "column": "position_id"}}, {"name": "start_date", "db_type": "date", "type": "date", "nullable": false}, {"name": "end_date", "db_type": "date", "type": "date", "nullable": true}, {"name": "is_primary", "db_type": "boolean", "type": "boolean", "nullable": false}, {"name": "assignment_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}]}}, "department_type_prefix": {"University": "UNI", "Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM"}, "position_rules": {"unique_holder_per_department": ["HT", "TK", "TP", "TBM"], "allowed_department_types": {"HT": ["Board"], "PHT": ["Board"], "TK": ["Faculty"], "PTK": ["Faculty"], "TP": ["Office"], "TBM": ["Division"], "GV": ["Faculty", "Division"], "GVC": ["Faculty", "Division"], "TG": ["Faculty", "Division"], "CV": ["Office"], "NV": ["Office"]}, "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "staff_only": ["CV", "NV", "TP"]}, "silver_derived_columns": {"employee": [{"name": "birth_year", "type": "int", "rule": "Thay date_of_birth (PII)"}, {"name": "is_lecturer", "type": "boolean", "rule": "C\\u00f3 d\\u00f2ng trong lecturer"}, {"name": "primary_department_id", "type": "bigint", "rule": "department_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "faculty_id", "type": "bigint", "rule": "\\u0110\\u01a1n v\\u1ecb con tr\\u1ef1c ti\\u1ebfp c\\u1ee7a \\u0111\\u01a1n v\\u1ecb g\\u1ed1c ch\\u1ee9a primary_department_id"}, {"name": "current_position_id", "type": "bigint", "rule": "position_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "seniority_years", "type": "decimal(4,1)", "rule": "T\\u1eeb hire_date (ho\\u1eb7c assignment s\\u1edbm nh\\u1ea5t) t\\u1edbi ng\\u00e0y ch\\u1ed1t ho\\u1eb7c ng\\u00e0y ngh\\u1ec9"}, {"name": "highest_degree", "type": "varchar(30)", "rule": "Ti\\u1ebfn s\\u0129 > Th\\u1ea1c s\\u0129 > K\\u1ef9 s\\u01b0/C\\u1eed nh\\u00e2n theo employee_qualification"}, {"name": "leave_date", "type": "date", "rule": "end_date l\\u1edbn nh\\u1ea5t c\\u1ee7a assignment khi Resigned/Retired"}]}, "quality_rules": [{"id": "HR-DQ-01", "entity": "*", "rule": "PK (k\\u1ec3 c\\u1ea3 PK gh\\u00e9p) v\\u00e0 c\\u1ed9t unique kh\\u00f4ng null, kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-02", "entity": "*", "rule": "FK tr\\u1ecf t\\u1edbi b\\u1ea3n ghi c\\u00f3 th\\u1eadt", "severity": "error"}, {"id": "HR-DQ-03", "entity": "*", "rule": "\\u0110\\u00fang ki\\u1ec3u, \\u0111\\u1ed9 d\\u00e0i, null, gi\\u00e1 tr\\u1ecb tr\\u1ea1ng th\\u00e1i, pattern, min/max", "severity": "error"}, {"id": "HR-DQ-04", "entity": "department", "rule": "M\\u1ed9t g\\u1ed1c, kh\\u00f4ng chu tr\\u00ecnh; department_type kh\\u1edbp ti\\u1ec1n t\\u1ed1 m\\u00e3; b\\u1ed9 m\\u00f4n (Division) thu\\u1ed9c khoa (Faculty)", "severity": "error"}, {"id": "HR-DQ-05", "entity": "employee", "rule": "Khi c\\u00f3 ng\\u00e0y sinh v\\u00e0 hire_date: tu\\u1ed5i khi tuy\\u1ec3n 20 - 60; hire_date = start_date s\\u1edbm nh\\u1ea5t trong assignment", "severity": "error"}, {"id": "HR-DQ-06", "entity": "assignment", "rule": "start_date >= hire_date (n\\u1ebfu c\\u00f3); end_date >= start_date; v\\u1ecb tr\\u00ed ch\\u00ednh kh\\u00f4ng ch\\u1ed3ng l\\u1ea5n", "severity": "error"}, {"id": "HR-DQ-07", "entity": "assignment", "rule": "Active c\\u00f3 \\u0111\\u00fang m\\u1ed9t v\\u1ecb tr\\u00ed ch\\u00ednh \\u0111ang hi\\u1ec7u l\\u1ef1c; Resigned/Retired kh\\u00f4ng c\\u00f2n assignment m\\u1edf", "severity": "error"}, {"id": "HR-DQ-08", "entity": "assignment", "rule": "HT, TK, TP, TBM: m\\u1ed9t ng\\u01b0\\u1eddi/\\u0111\\u01a1n v\\u1ecb/th\\u1eddi \\u0111i\\u1ec3m; ch\\u1ee9c v\\u1ee5 \\u0111\\u00fang lo\\u1ea1i \\u0111\\u01a1n v\\u1ecb", "severity": "error"}, {"id": "HR-DQ-09", "entity": "assignment", "rule": "Ch\\u1ee9c v\\u1ee5 gi\\u1ea3ng d\\u1ea1y ch\\u1ec9 cho gi\\u1ea3ng vi\\u00ean; CV, NV, TP ch\\u1ec9 cho c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh", "severity": "error"}, {"id": "HR-DQ-10", "entity": "employee_qualification", "rule": "N\\u0103m c\\u1ea5p sau 18 tu\\u1ed5i, kh\\u00f4ng sau ng\\u00e0y ch\\u1ed1t; C\\u1eed nh\\u00e2n/K\\u1ef9 s\\u01b0 < Th\\u1ea1c s\\u0129 < Ti\\u1ebfn s\\u0129", "severity": "error"}, {"id": "HR-DQ-11", "entity": "lecturer", "rule": "academic_degree kh\\u1edbp v\\u0103n b\\u1eb1ng cao nh\\u1ea5t; academic_title (Assoc. Prof., Prof.) ch\\u1ec9 khi academic_degree = Ph.D.", "severity": "warning"}, {"id": "HR-DQ-12", "entity": "assignment", "rule": "assignment_status kh\\u1edbp end_date (Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t)", "severity": "error"}, {"id": "HR-DQ-13", "entity": "qualification", "rule": "qualification_name kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-14", "entity": "class_lecturer (Academic)", "rule": "Gi\\u1ea3ng vi\\u00ean c\\u00f3 assignment hi\\u1ec7u l\\u1ef1c su\\u1ed1t kho\\u1ea3ng ph\\u1ee5 tr\\u00e1ch l\\u1edbp (assigned_from..assigned_to, m\\u1eb7c \\u0111\\u1ecbnh l\\u00e0 h\\u1ecdc k\\u1ef3 c\\u1ee7a l\\u1edbp)", "severity": "error"}]}, "contracts": {"hr": {"x-version": "source-1", "$defs": {"lecturer": {"type": "object", "additionalProperties": false, "required": ["employee_id", "academic_degree", "academic_title", "research_field", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "academic_degree": {"type": ["string", "null"], "x-physical-type": "varchar(50)", "maxLength": 50, "enum": ["B.Sc.", "M.Sc.", "Ph.D.", null], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, "academic_title": {"type": ["string", "null"], "x-physical-type": "varchar(50)", "maxLength": 50, "enum": ["Assoc. Prof.", "Prof.", null], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, "research_field": {"type": ["string", "null"], "x-physical-type": "varchar(250)", "maxLength": 250}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-grain": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "x-owner": "hr", "x-primary-key": ["employee_id"], "x-unique-together": [], "x-identity-mode": "source"}}}}, "field_mapping": {"employee_id": "employee_id", "academic_degree": "academic_degree", "academic_title": "academic_title", "research_field": "research_field"}, "ignored_columns": [], "format_options": {}, "type_options": {}, "null_tokens": {"academic_title": [""], "academic_degree": [""], "employee_id": [""], "research_field": [""]}, "dictionaries": {"academic_degree": {}, "academic_title": {}}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:26:04.807503Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	d4a7c552-74d9-45be-a5c6-2f74612d578c
f139324d-e632-4dd3-8ce2-8da48012acf2	5	1	hr	employee	9	auto-e270bc9994f1530b4a540c0daa6c	d9fb5d0b-f453-43a7-9943-75fdd9ec0fde	sha256:0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6	COMPLETED	PUBLISHED	200	200	0	0	200	8079b128-c5b0-4625-917b-e01e5386342d	{"key": "silver/reports/f139324d-e632-4dd3-8ce2-8da48012acf2/report-baedcd58349c8facd55a011c45b313e963e4c52bf05909e85df0a52af9d66c67.json", "sha256": "baedcd58349c8facd55a011c45b313e963e4c52bf05909e85df0a52af9d66c67", "size": 791, "version_id": null}	null	\N	2026-11-16 21:22:57.967208+07	2026-11-16 21:22:58.194463+07	{"profile_id": "automatic-c45fc8e5-5029-45e9-a41a-92663268ca4e", "fingerprint": "42c85bd54dc10c420a2fc2e4da477c8b5a18bedaf4b9cac1ae787a8326c4c47f", "rule_version": "auto-source-v1:42c85bd54dc10c420a2fc2e4da477c8b5a18bedaf4b9cac1ae787a8326c4c47f", "definition": {"data_source_id": 1, "dataset_id": 1, "source_system_id": 34, "source_type": "CSV", "source_table": "employee.csv", "domain": "hr", "entity": "employee", "version": "auto-source-v1", "scope": "auto-e270bc9994f1530b4a540c0daa6c", "source_schema_version": "1.2.0", "source_schema": {"dataset": "hr", "version": "1.2.0", "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. B\\u00e1m \\u0111\\u00fang DB ngu\\u1ed3n docs/data-schema/unilake-db-architecture.txt (t\\u00ean c\\u1ed9t, th\\u1ee9 t\\u1ef1, db_type, nullable, PK, UNIQUE, FK). type l\\u00e0 ki\\u1ec3u SQL \\u0111\\u1ec1 xu\\u1ea5t cho DDL v\\u00ec DB ch\\u1ec9 khai b\\u00e1o ki\\u1ec3u chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "source_db": "docs/data-schema/unilake-db-architecture.txt", "file_format": {"type": "csv", "encoding": "utf-8", "delimiter": ",", "header": true, "null_value": "", "date_format": "YYYY-MM-DD", "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": ".", "boolean_values": ["true", "false"]}, "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "H\\u1ec7 th\\u1ed1ng ngu\\u1ed3n"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID l\\u00f4 ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Th\\u1eddi \\u0111i\\u1ec3m ghi v\\u00e0o Bronze (UTC)"}], "entities": {"department": {"domain": "hr", "owned_by_dataset": true, "description": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "file": "hr/department.csv", "primary_key": ["department_id"], "columns": [{"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "department_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)"}, {"name": "department_name", "db_type": "string", "type": "varchar(200)", "nullable": false}, {"name": "department_type", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, {"name": "parent_department_id", "db_type": "bigint", "type": "bigint", "nullable": true, "references": {"entity": "department", "column": "department_id"}, "description": "Null = \\u0111\\u01a1n v\\u1ecb g\\u1ed1c"}, {"name": "status", "db_type": "string", "type": "varchar(20)", "nullable": false, "enum": ["Active", "Inactive"]}, {"name": "created_at", "db_type": "timestamp", "type": "timestamp", "nullable": false}]}, "position": {"domain": "hr", "owned_by_dataset": true, "description": "Ch\\u1ee9c danh/ch\\u1ee9c v\\u1ee5.", "file": "hr/position.csv", "primary_key": ["position_id"], "columns": [{"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "position_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^[A-Z]{2,5}$"}, {"name": "position_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "position_level", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Leadership", "Management", "Staff"]}]}, "qualification": {"domain": "hr", "owned_by_dataset": true, "description": "Danh m\\u1ee5c v\\u0103n b\\u1eb1ng v\\u00e0 ch\\u1ee9ng ch\\u1ec9. DB kh\\u00f4ng c\\u00f2n UNIQUE tr\\u00ean qualification_name; HR-DQ-13 ki\\u1ec3m tra tr\\u00f9ng.", "file": "hr/qualification.csv", "primary_key": ["qualification_id"], "columns": [{"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "qualification_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "qualification_type", "db_type": "string", "type": "varchar(100)", "nullable": true, "enum": ["Degree", "Certificate"]}]}, "employee": {"domain": "hr", "owned_by_dataset": true, "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "description": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "file": "hr/employee.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "string", "type": "text", "nullable": false, "source_identifier": true}, {"name": "employee_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^NV[0-9]{4}$"}, {"name": "gender", "db_type": "string", "type": "varchar(20)", "nullable": true, "enum": ["Male", "Female"]}, {"name": "hire_date", "db_type": "date", "type": "date", "nullable": true, "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, {"name": "employee_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Resigned", "Retired"]}, {"name": "birth_year", "db_type": "date", "type": "date", "nullable": true}]}, "lecturer": {"domain": "hr", "owned_by_dataset": true, "description": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "file": "hr/lecturer.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "academic_degree", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["B.Sc.", "M.Sc.", "Ph.D."], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, {"name": "academic_title", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Assoc. Prof.", "Prof."], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, {"name": "research_field", "db_type": "string", "type": "varchar(250)", "nullable": true}]}, "employee_qualification": {"domain": "hr", "owned_by_dataset": true, "description": "V\\u0103n b\\u1eb1ng/ch\\u1ee9ng ch\\u1ec9 c\\u1ee7a nh\\u00e2n s\\u1ef1 (N-N gi\\u1eefa employee v\\u00e0 qualification).", "file": "hr/employee_qualification.csv", "primary_key": ["employee_id", "qualification_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "qualification", "column": "qualification_id"}}, {"name": "institution", "db_type": "string", "type": "varchar(250)", "nullable": true}, {"name": "year_obtained", "db_type": "int", "type": "int", "nullable": true, "min": 1960, "max": 2100}]}, "assignment": {"domain": "hr", "owned_by_dataset": true, "description": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "file": "hr/assignment.csv", "primary_key": ["assignment_id"], "columns": [{"name": "assignment_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "department", "column": "department_id"}}, {"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "position", "column": "position_id"}}, {"name": "start_date", "db_type": "date", "type": "date", "nullable": false}, {"name": "end_date", "db_type": "date", "type": "date", "nullable": true}, {"name": "is_primary", "db_type": "boolean", "type": "boolean", "nullable": false}, {"name": "assignment_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}]}}, "department_type_prefix": {"University": "UNI", "Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM"}, "position_rules": {"unique_holder_per_department": ["HT", "TK", "TP", "TBM"], "allowed_department_types": {"HT": ["Board"], "PHT": ["Board"], "TK": ["Faculty"], "PTK": ["Faculty"], "TP": ["Office"], "TBM": ["Division"], "GV": ["Faculty", "Division"], "GVC": ["Faculty", "Division"], "TG": ["Faculty", "Division"], "CV": ["Office"], "NV": ["Office"]}, "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "staff_only": ["CV", "NV", "TP"]}, "silver_derived_columns": {"employee": [{"name": "birth_year", "type": "int", "rule": "Thay date_of_birth (PII)"}, {"name": "is_lecturer", "type": "boolean", "rule": "C\\u00f3 d\\u00f2ng trong lecturer"}, {"name": "primary_department_id", "type": "bigint", "rule": "department_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "faculty_id", "type": "bigint", "rule": "\\u0110\\u01a1n v\\u1ecb con tr\\u1ef1c ti\\u1ebfp c\\u1ee7a \\u0111\\u01a1n v\\u1ecb g\\u1ed1c ch\\u1ee9a primary_department_id"}, {"name": "current_position_id", "type": "bigint", "rule": "position_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "seniority_years", "type": "decimal(4,1)", "rule": "T\\u1eeb hire_date (ho\\u1eb7c assignment s\\u1edbm nh\\u1ea5t) t\\u1edbi ng\\u00e0y ch\\u1ed1t ho\\u1eb7c ng\\u00e0y ngh\\u1ec9"}, {"name": "highest_degree", "type": "varchar(30)", "rule": "Ti\\u1ebfn s\\u0129 > Th\\u1ea1c s\\u0129 > K\\u1ef9 s\\u01b0/C\\u1eed nh\\u00e2n theo employee_qualification"}, {"name": "leave_date", "type": "date", "rule": "end_date l\\u1edbn nh\\u1ea5t c\\u1ee7a assignment khi Resigned/Retired"}]}, "quality_rules": [{"id": "HR-DQ-01", "entity": "*", "rule": "PK (k\\u1ec3 c\\u1ea3 PK gh\\u00e9p) v\\u00e0 c\\u1ed9t unique kh\\u00f4ng null, kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-02", "entity": "*", "rule": "FK tr\\u1ecf t\\u1edbi b\\u1ea3n ghi c\\u00f3 th\\u1eadt", "severity": "error"}, {"id": "HR-DQ-03", "entity": "*", "rule": "\\u0110\\u00fang ki\\u1ec3u, \\u0111\\u1ed9 d\\u00e0i, null, gi\\u00e1 tr\\u1ecb tr\\u1ea1ng th\\u00e1i, pattern, min/max", "severity": "error"}, {"id": "HR-DQ-04", "entity": "department", "rule": "M\\u1ed9t g\\u1ed1c, kh\\u00f4ng chu tr\\u00ecnh; department_type kh\\u1edbp ti\\u1ec1n t\\u1ed1 m\\u00e3; b\\u1ed9 m\\u00f4n (Division) thu\\u1ed9c khoa (Faculty)", "severity": "error"}, {"id": "HR-DQ-05", "entity": "employee", "rule": "Khi c\\u00f3 ng\\u00e0y sinh v\\u00e0 hire_date: tu\\u1ed5i khi tuy\\u1ec3n 20 - 60; hire_date = start_date s\\u1edbm nh\\u1ea5t trong assignment", "severity": "error"}, {"id": "HR-DQ-06", "entity": "assignment", "rule": "start_date >= hire_date (n\\u1ebfu c\\u00f3); end_date >= start_date; v\\u1ecb tr\\u00ed ch\\u00ednh kh\\u00f4ng ch\\u1ed3ng l\\u1ea5n", "severity": "error"}, {"id": "HR-DQ-07", "entity": "assignment", "rule": "Active c\\u00f3 \\u0111\\u00fang m\\u1ed9t v\\u1ecb tr\\u00ed ch\\u00ednh \\u0111ang hi\\u1ec7u l\\u1ef1c; Resigned/Retired kh\\u00f4ng c\\u00f2n assignment m\\u1edf", "severity": "error"}, {"id": "HR-DQ-08", "entity": "assignment", "rule": "HT, TK, TP, TBM: m\\u1ed9t ng\\u01b0\\u1eddi/\\u0111\\u01a1n v\\u1ecb/th\\u1eddi \\u0111i\\u1ec3m; ch\\u1ee9c v\\u1ee5 \\u0111\\u00fang lo\\u1ea1i \\u0111\\u01a1n v\\u1ecb", "severity": "error"}, {"id": "HR-DQ-09", "entity": "assignment", "rule": "Ch\\u1ee9c v\\u1ee5 gi\\u1ea3ng d\\u1ea1y ch\\u1ec9 cho gi\\u1ea3ng vi\\u00ean; CV, NV, TP ch\\u1ec9 cho c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh", "severity": "error"}, {"id": "HR-DQ-10", "entity": "employee_qualification", "rule": "N\\u0103m c\\u1ea5p sau 18 tu\\u1ed5i, kh\\u00f4ng sau ng\\u00e0y ch\\u1ed1t; C\\u1eed nh\\u00e2n/K\\u1ef9 s\\u01b0 < Th\\u1ea1c s\\u0129 < Ti\\u1ebfn s\\u0129", "severity": "error"}, {"id": "HR-DQ-11", "entity": "lecturer", "rule": "academic_degree kh\\u1edbp v\\u0103n b\\u1eb1ng cao nh\\u1ea5t; academic_title (Assoc. Prof., Prof.) ch\\u1ec9 khi academic_degree = Ph.D.", "severity": "warning"}, {"id": "HR-DQ-12", "entity": "assignment", "rule": "assignment_status kh\\u1edbp end_date (Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t)", "severity": "error"}, {"id": "HR-DQ-13", "entity": "qualification", "rule": "qualification_name kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-14", "entity": "class_lecturer (Academic)", "rule": "Gi\\u1ea3ng vi\\u00ean c\\u00f3 assignment hi\\u1ec7u l\\u1ef1c su\\u1ed1t kho\\u1ea3ng ph\\u1ee5 tr\\u00e1ch l\\u1edbp (assigned_from..assigned_to, m\\u1eb7c \\u0111\\u1ecbnh l\\u00e0 h\\u1ecdc k\\u1ef3 c\\u1ee7a l\\u1edbp)", "severity": "error"}]}, "contracts": {"hr": {"x-version": "source-1", "$defs": {"employee": {"type": "object", "additionalProperties": false, "required": ["employee_id", "employee_code", "gender", "hire_date", "employee_status", "birth_year", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "employee_code": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "pattern": "^NV[0-9]{4}$", "x-unique-non-null": true}, "gender": {"type": ["string", "null"], "x-physical-type": "varchar(20)", "maxLength": 20, "enum": ["Male", "Female", null]}, "hire_date": {"type": ["string", "null"], "x-physical-type": "date32", "format": "date", "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, "employee_status": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "enum": ["Active", "Resigned", "Retired"]}, "birth_year": {"type": ["integer", "null"], "minimum": 1, "maximum": 9999, "x-physical-type": "int32", "x-derived-from": "date_of_birth", "x-rule": "PII-02"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-grain": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "x-owner": "hr", "x-primary-key": ["employee_id"], "x-unique-together": [], "x-identity-mode": "source"}}}}, "field_mapping": {"employee_id": "employee_id", "employee_code": "employee_code", "gender": "gender", "hire_date": "hire_date", "employee_status": "employee_status", "birth_year": "date_of_birth"}, "ignored_columns": ["full_name", "email", "phone"], "format_options": {"hire_date": {"date_formats": ["%Y-%m-%d"]}, "birth_year": {"date_formats": ["%Y-%m-%d"]}}, "type_options": {}, "null_tokens": {"gender": [""], "employee_id": [""], "hire_date": [""], "employee_status": [""], "employee_code": [""], "birth_year": [""]}, "dictionaries": {"gender": {}, "employee_status": {}}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T14:22:55.388069Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	c45fc8e5-5029-45e9-a41a-92663268ca4e
5733877e-5dbb-446b-8cb6-9a69f3d508a7	8	1	hr	employee	system:gold-storage-v2	auto-e270bc9994f1530b4a540c0daa6c	4acf498d-dfe2-4263-8f07-3b837a7e057e	sha256:0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6	COMPLETED	PUBLISHED	200	200	0	0	200	3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c	{"key": "silver/reports/5733877e-5dbb-446b-8cb6-9a69f3d508a7/report-ba7281be9da6ded292c3704dc84d2681bbb98b6778d52cd9adb7be89c100f57e.json", "sha256": "ba7281be9da6ded292c3704dc84d2681bbb98b6778d52cd9adb7be89c100f57e", "size": 812, "version_id": null}	null	\N	2026-11-16 23:04:03.539885+07	2026-11-16 23:04:04.059738+07	{"profile_id": "automatic-63b18667-eb2a-49e1-8770-97d623d0a2e6", "fingerprint": "d817b1f5ed152b9d55fd66f4239ca687d89c27b9d10fddc6f16997eaacce8f8a", "rule_version": "auto-source-v1:d817b1f5ed152b9d55fd66f4239ca687d89c27b9d10fddc6f16997eaacce8f8a", "definition": {"data_source_id": 1, "dataset_id": 1, "source_system_id": 34, "source_type": "CSV", "source_table": "employee.csv", "domain": "hr", "entity": "employee", "version": "auto-source-v1", "scope": "auto-e270bc9994f1530b4a540c0daa6c", "source_schema_version": "1.2.0", "source_schema": {"dataset": "hr", "version": "1.2.0", "description": "Human Resources Dataset Schema V1 - UniLake AI V1.0. B\\u00e1m \\u0111\\u00fang DB ngu\\u1ed3n docs/data-schema/unilake-db-architecture.txt (t\\u00ean c\\u1ed9t, th\\u1ee9 t\\u1ef1, db_type, nullable, PK, UNIQUE, FK). type l\\u00e0 ki\\u1ec3u SQL \\u0111\\u1ec1 xu\\u1ea5t cho DDL v\\u00ec DB ch\\u1ec9 khai b\\u00e1o ki\\u1ec3u chung (string, decimal). Xem docs/data-schema/hr-dataset-v1.md.", "source_db": "docs/data-schema/unilake-db-architecture.txt", "file_format": {"type": "csv", "encoding": "utf-8", "delimiter": ",", "header": true, "null_value": "", "date_format": "YYYY-MM-DD", "timestamp_format": "YYYY-MM-DD HH:MM:SS", "decimal_separator": ".", "boolean_values": ["true", "false"]}, "load_order": ["department", "position", "qualification", "employee", "lecturer", "employee_qualification", "assignment"], "audit_columns": [{"name": "_source_system", "type": "varchar(50)", "nullable": false, "description": "H\\u1ec7 th\\u1ed1ng ngu\\u1ed3n"}, {"name": "_batch_id", "type": "varchar(64)", "nullable": false, "description": "ID l\\u00f4 ingestion"}, {"name": "_ingested_at", "type": "timestamp", "nullable": false, "description": "Th\\u1eddi \\u0111i\\u1ec3m ghi v\\u00e0o Bronze (UTC)"}], "entities": {"department": {"domain": "hr", "owned_by_dataset": true, "description": "\\u0110\\u01a1n v\\u1ecb t\\u1ed5 ch\\u1ee9c d\\u1ea1ng c\\u00e2y: tr\\u01b0\\u1eddng -> ban gi\\u00e1m hi\\u1ec7u/ph\\u00f2ng/khoa -> b\\u1ed9 m\\u00f4n. \\u0110\\u01b0\\u1ee3c major (Admissions), source_system v\\u00e0 dataset (governance) tham chi\\u1ebfu.", "file": "hr/department.csv", "primary_key": ["department_id"], "columns": [{"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "department_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^(UNI|BGH|P[A-Z]+|K[A-Z]+|BM[A-Z]+)$", "description": "Ti\\u1ec1n t\\u1ed1 kh\\u1edbp department_type: UNI, BGH, P (ph\\u00f2ng), K (khoa), BM (b\\u1ed9 m\\u00f4n)"}, {"name": "department_name", "db_type": "string", "type": "varchar(200)", "nullable": false}, {"name": "department_type", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["University", "Board", "Office", "Faculty", "Division"], "description": "Tr\\u01b0\\u1eddng, Ban, Ph\\u00f2ng, Khoa, B\\u1ed9 m\\u00f4n"}, {"name": "parent_department_id", "db_type": "bigint", "type": "bigint", "nullable": true, "references": {"entity": "department", "column": "department_id"}, "description": "Null = \\u0111\\u01a1n v\\u1ecb g\\u1ed1c"}, {"name": "status", "db_type": "string", "type": "varchar(20)", "nullable": false, "enum": ["Active", "Inactive"]}, {"name": "created_at", "db_type": "timestamp", "type": "timestamp", "nullable": false}]}, "position": {"domain": "hr", "owned_by_dataset": true, "description": "Ch\\u1ee9c danh/ch\\u1ee9c v\\u1ee5.", "file": "hr/position.csv", "primary_key": ["position_id"], "columns": [{"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "position_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^[A-Z]{2,5}$"}, {"name": "position_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "position_level", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Leadership", "Management", "Staff"]}]}, "qualification": {"domain": "hr", "owned_by_dataset": true, "description": "Danh m\\u1ee5c v\\u0103n b\\u1eb1ng v\\u00e0 ch\\u1ee9ng ch\\u1ec9. DB kh\\u00f4ng c\\u00f2n UNIQUE tr\\u00ean qualification_name; HR-DQ-13 ki\\u1ec3m tra tr\\u00f9ng.", "file": "hr/qualification.csv", "primary_key": ["qualification_id"], "columns": [{"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "qualification_name", "db_type": "string", "type": "varchar(150)", "nullable": false}, {"name": "qualification_type", "db_type": "string", "type": "varchar(100)", "nullable": true, "enum": ["Degree", "Certificate"]}]}, "employee": {"domain": "hr", "owned_by_dataset": true, "pii_columns": ["full_name", "date_of_birth", "email", "phone"], "description": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "file": "hr/employee.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "string", "type": "text", "nullable": false, "source_identifier": true}, {"name": "employee_code", "db_type": "string", "type": "varchar(30)", "nullable": false, "unique": true, "pattern": "^NV[0-9]{4}$"}, {"name": "gender", "db_type": "string", "type": "varchar(20)", "nullable": true, "enum": ["Male", "Female"]}, {"name": "hire_date", "db_type": "date", "type": "date", "nullable": true, "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, {"name": "employee_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Resigned", "Retired"]}, {"name": "birth_year", "db_type": "date", "type": "date", "nullable": true}]}, "lecturer": {"domain": "hr", "owned_by_dataset": true, "description": "Gi\\u1ea3ng vi\\u00ean: ki\\u1ec3u con c\\u1ee7a employee, kh\\u00f3a ch\\u00ednh employee_id d\\u00f9ng chung (quan h\\u1ec7 1 - 0..1).", "file": "hr/lecturer.csv", "primary_key": ["employee_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "academic_degree", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["B.Sc.", "M.Sc.", "Ph.D."], "description": "H\\u1ecdc v\\u1ecb cao nh\\u1ea5t"}, {"name": "academic_title", "db_type": "string", "type": "varchar(50)", "nullable": true, "enum": ["Assoc. Prof.", "Prof."], "description": "H\\u1ecdc h\\u00e0m; null n\\u1ebfu ch\\u01b0a c\\u00f3"}, {"name": "research_field", "db_type": "string", "type": "varchar(250)", "nullable": true}]}, "employee_qualification": {"domain": "hr", "owned_by_dataset": true, "description": "V\\u0103n b\\u1eb1ng/ch\\u1ee9ng ch\\u1ec9 c\\u1ee7a nh\\u00e2n s\\u1ef1 (N-N gi\\u1eefa employee v\\u00e0 qualification).", "file": "hr/employee_qualification.csv", "primary_key": ["employee_id", "qualification_id"], "columns": [{"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "qualification_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "qualification", "column": "qualification_id"}}, {"name": "institution", "db_type": "string", "type": "varchar(250)", "nullable": true}, {"name": "year_obtained", "db_type": "int", "type": "int", "nullable": true, "min": 1960, "max": 2100}]}, "assignment": {"domain": "hr", "owned_by_dataset": true, "description": "Qu\\u00e1 tr\\u00ecnh c\\u00f4ng t\\u00e1c: nh\\u00e2n s\\u1ef1 gi\\u1eef ch\\u1ee9c v\\u1ee5 t\\u1ea1i \\u0111\\u01a1n v\\u1ecb trong m\\u1ed9t kho\\u1ea3ng th\\u1eddi gian. end_date null = \\u0111ang gi\\u1eef. is_primary false = ki\\u00eam nhi\\u1ec7m.", "file": "hr/assignment.csv", "primary_key": ["assignment_id"], "columns": [{"name": "assignment_id", "db_type": "bigint", "type": "bigint", "nullable": false}, {"name": "employee_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "employee", "column": "employee_id"}}, {"name": "department_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "department", "column": "department_id"}}, {"name": "position_id", "db_type": "bigint", "type": "bigint", "nullable": false, "references": {"entity": "position", "column": "position_id"}}, {"name": "start_date", "db_type": "date", "type": "date", "nullable": false}, {"name": "end_date", "db_type": "date", "type": "date", "nullable": true}, {"name": "is_primary", "db_type": "boolean", "type": "boolean", "nullable": false}, {"name": "assignment_status", "db_type": "string", "type": "varchar(30)", "nullable": false, "enum": ["Active", "Ended"], "description": "Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t"}]}}, "department_type_prefix": {"University": "UNI", "Board": "BGH", "Office": "P", "Faculty": "K", "Division": "BM"}, "position_rules": {"unique_holder_per_department": ["HT", "TK", "TP", "TBM"], "allowed_department_types": {"HT": ["Board"], "PHT": ["Board"], "TK": ["Faculty"], "PTK": ["Faculty"], "TP": ["Office"], "TBM": ["Division"], "GV": ["Faculty", "Division"], "GVC": ["Faculty", "Division"], "TG": ["Faculty", "Division"], "CV": ["Office"], "NV": ["Office"]}, "lecturer_only": ["GV", "GVC", "TG", "TK", "PTK", "TBM"], "staff_only": ["CV", "NV", "TP"]}, "silver_derived_columns": {"employee": [{"name": "birth_year", "type": "int", "rule": "Thay date_of_birth (PII)"}, {"name": "is_lecturer", "type": "boolean", "rule": "C\\u00f3 d\\u00f2ng trong lecturer"}, {"name": "primary_department_id", "type": "bigint", "rule": "department_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "faculty_id", "type": "bigint", "rule": "\\u0110\\u01a1n v\\u1ecb con tr\\u1ef1c ti\\u1ebfp c\\u1ee7a \\u0111\\u01a1n v\\u1ecb g\\u1ed1c ch\\u1ee9a primary_department_id"}, {"name": "current_position_id", "type": "bigint", "rule": "position_id c\\u1ee7a assignment is_primary \\u0111ang hi\\u1ec7u l\\u1ef1c"}, {"name": "seniority_years", "type": "decimal(4,1)", "rule": "T\\u1eeb hire_date (ho\\u1eb7c assignment s\\u1edbm nh\\u1ea5t) t\\u1edbi ng\\u00e0y ch\\u1ed1t ho\\u1eb7c ng\\u00e0y ngh\\u1ec9"}, {"name": "highest_degree", "type": "varchar(30)", "rule": "Ti\\u1ebfn s\\u0129 > Th\\u1ea1c s\\u0129 > K\\u1ef9 s\\u01b0/C\\u1eed nh\\u00e2n theo employee_qualification"}, {"name": "leave_date", "type": "date", "rule": "end_date l\\u1edbn nh\\u1ea5t c\\u1ee7a assignment khi Resigned/Retired"}]}, "quality_rules": [{"id": "HR-DQ-01", "entity": "*", "rule": "PK (k\\u1ec3 c\\u1ea3 PK gh\\u00e9p) v\\u00e0 c\\u1ed9t unique kh\\u00f4ng null, kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-02", "entity": "*", "rule": "FK tr\\u1ecf t\\u1edbi b\\u1ea3n ghi c\\u00f3 th\\u1eadt", "severity": "error"}, {"id": "HR-DQ-03", "entity": "*", "rule": "\\u0110\\u00fang ki\\u1ec3u, \\u0111\\u1ed9 d\\u00e0i, null, gi\\u00e1 tr\\u1ecb tr\\u1ea1ng th\\u00e1i, pattern, min/max", "severity": "error"}, {"id": "HR-DQ-04", "entity": "department", "rule": "M\\u1ed9t g\\u1ed1c, kh\\u00f4ng chu tr\\u00ecnh; department_type kh\\u1edbp ti\\u1ec1n t\\u1ed1 m\\u00e3; b\\u1ed9 m\\u00f4n (Division) thu\\u1ed9c khoa (Faculty)", "severity": "error"}, {"id": "HR-DQ-05", "entity": "employee", "rule": "Khi c\\u00f3 ng\\u00e0y sinh v\\u00e0 hire_date: tu\\u1ed5i khi tuy\\u1ec3n 20 - 60; hire_date = start_date s\\u1edbm nh\\u1ea5t trong assignment", "severity": "error"}, {"id": "HR-DQ-06", "entity": "assignment", "rule": "start_date >= hire_date (n\\u1ebfu c\\u00f3); end_date >= start_date; v\\u1ecb tr\\u00ed ch\\u00ednh kh\\u00f4ng ch\\u1ed3ng l\\u1ea5n", "severity": "error"}, {"id": "HR-DQ-07", "entity": "assignment", "rule": "Active c\\u00f3 \\u0111\\u00fang m\\u1ed9t v\\u1ecb tr\\u00ed ch\\u00ednh \\u0111ang hi\\u1ec7u l\\u1ef1c; Resigned/Retired kh\\u00f4ng c\\u00f2n assignment m\\u1edf", "severity": "error"}, {"id": "HR-DQ-08", "entity": "assignment", "rule": "HT, TK, TP, TBM: m\\u1ed9t ng\\u01b0\\u1eddi/\\u0111\\u01a1n v\\u1ecb/th\\u1eddi \\u0111i\\u1ec3m; ch\\u1ee9c v\\u1ee5 \\u0111\\u00fang lo\\u1ea1i \\u0111\\u01a1n v\\u1ecb", "severity": "error"}, {"id": "HR-DQ-09", "entity": "assignment", "rule": "Ch\\u1ee9c v\\u1ee5 gi\\u1ea3ng d\\u1ea1y ch\\u1ec9 cho gi\\u1ea3ng vi\\u00ean; CV, NV, TP ch\\u1ec9 cho c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh", "severity": "error"}, {"id": "HR-DQ-10", "entity": "employee_qualification", "rule": "N\\u0103m c\\u1ea5p sau 18 tu\\u1ed5i, kh\\u00f4ng sau ng\\u00e0y ch\\u1ed1t; C\\u1eed nh\\u00e2n/K\\u1ef9 s\\u01b0 < Th\\u1ea1c s\\u0129 < Ti\\u1ebfn s\\u0129", "severity": "error"}, {"id": "HR-DQ-11", "entity": "lecturer", "rule": "academic_degree kh\\u1edbp v\\u0103n b\\u1eb1ng cao nh\\u1ea5t; academic_title (Assoc. Prof., Prof.) ch\\u1ec9 khi academic_degree = Ph.D.", "severity": "warning"}, {"id": "HR-DQ-12", "entity": "assignment", "rule": "assignment_status kh\\u1edbp end_date (Active khi end_date null ho\\u1eb7c sau ng\\u00e0y ch\\u1ed1t)", "severity": "error"}, {"id": "HR-DQ-13", "entity": "qualification", "rule": "qualification_name kh\\u00f4ng tr\\u00f9ng", "severity": "error"}, {"id": "HR-DQ-14", "entity": "class_lecturer (Academic)", "rule": "Gi\\u1ea3ng vi\\u00ean c\\u00f3 assignment hi\\u1ec7u l\\u1ef1c su\\u1ed1t kho\\u1ea3ng ph\\u1ee5 tr\\u00e1ch l\\u1edbp (assigned_from..assigned_to, m\\u1eb7c \\u0111\\u1ecbnh l\\u00e0 h\\u1ecdc k\\u1ef3 c\\u1ee7a l\\u1edbp)", "severity": "error"}]}, "contracts": {"hr": {"x-version": "source-1", "$defs": {"employee": {"type": "object", "additionalProperties": false, "required": ["employee_id", "employee_code", "gender", "hire_date", "employee_status", "birth_year", "_source_system_id", "_source_record_id", "_batch_id", "_run_id", "_processed_at", "_schema_version", "_mapping_version"], "properties": {"employee_id": {"type": "string", "x-physical-type": "utf8", "x-source-identifier": true}, "employee_code": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "pattern": "^NV[0-9]{4}$", "x-unique-non-null": true}, "gender": {"type": ["string", "null"], "x-physical-type": "varchar(20)", "maxLength": 20, "enum": ["Male", "Female", null]}, "hire_date": {"type": ["string", "null"], "x-physical-type": "date32", "format": "date", "description": "Khi c\\u00f3 th\\u00ec b\\u1eb1ng start_date s\\u1edbm nh\\u1ea5t trong assignment"}, "employee_status": {"type": "string", "x-physical-type": "varchar(30)", "maxLength": 30, "enum": ["Active", "Resigned", "Retired"]}, "birth_year": {"type": ["integer", "null"], "minimum": 1, "maximum": 9999, "x-physical-type": "int32", "x-derived-from": "date_of_birth", "x-rule": "PII-02"}, "_source_system_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_source_record_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_batch_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_run_id": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}, "_processed_at": {"type": "string", "minLength": 1, "x-physical-type": "timestamp[us,UTC]", "format": "date-time", "pattern": "Z$"}, "_schema_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8", "const": "source-1"}, "_mapping_version": {"type": "string", "minLength": 1, "x-physical-type": "utf8"}}, "x-grain": "Nh\\u00e2n s\\u1ef1 (gi\\u1ea3ng vi\\u00ean v\\u00e0 c\\u00e1n b\\u1ed9 h\\u00e0nh ch\\u00ednh). \\u0110\\u01a1n v\\u1ecb v\\u00e0 ch\\u1ee9c v\\u1ee5 l\\u1ea5y qua assignment.", "x-owner": "hr", "x-primary-key": ["employee_id"], "x-unique-together": [], "x-identity-mode": "source"}}}}, "field_mapping": {"employee_id": "employee_id", "employee_code": "employee_code", "gender": "gender", "hire_date": "hire_date", "employee_status": "employee_status", "birth_year": "date_of_birth"}, "ignored_columns": ["full_name", "email", "phone"], "format_options": {"hire_date": {"date_formats": ["%Y-%m-%d"]}, "birth_year": {"date_formats": ["%Y-%m-%d"]}}, "type_options": {}, "null_tokens": {"employee_id": [""], "employee_code": [""], "employee_status": [""], "birth_year": [""], "hire_date": [""], "gender": [""]}, "dictionaries": {"gender": {}, "employee_status": {}}, "reference_namespaces": {}, "identity_mode": "source", "crosswalk_release": null, "effective_at": "2026-11-16T16:04:01.823260Z", "collapse_exact_duplicates": false, "source_preview_columns": []}, "approval": {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"}}	63b18667-eb2a-49e1-8770-97d623d0a2e6
\.


--
-- Data for Name: course; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.course (course_id, course_code, course_name, credit_hours, status) FROM stdin;
1	CS101	Nhap mon lap trinh	3	ACTIVE
2	DB201	Co so du lieu	3	ACTIVE
3	SE301	Cong nghe phan mem	3	ACTIVE
4	MA102	Toan roi rac	3	ACTIVE
5	NW202	Mang may tinh	3	ACTIVE
\.


--
-- Data for Name: crosswalk_release; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.crosswalk_release (version, fingerprint, payload) FROM stdin;
\.


--
-- Data for Name: curriculum; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.curriculum (curriculum_id, program_id, curriculum_code, curriculum_name, version, effective_from, effective_to, status) FROM stdin;
\.


--
-- Data for Name: curriculum_course; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.curriculum_course (curriculum_course_id, curriculum_id, course_id, semester_no, course_type, is_required, prerequisite_course_id) FROM stdin;
\.


--
-- Data for Name: dashboard; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.dashboard (dashboard_id, dashboard_name, owner_app_user_id, description, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: data_layer; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_layer (data_layer_id, layer_code, layer_name, storage_type, description) FROM stdin;
1	BRONZE	Bronze	OBJECT_STORAGE	Dữ liệu nguồn đã được nạp và xác nhận
2	SILVER	Silver	OBJECT_STORAGE	Dữ liệu đã làm sạch, chuẩn hóa và kiểm tra
3	GOLD	Gold	OBJECT_STORAGE	Dữ liệu tổng hợp phục vụ báo cáo
\.


--
-- Data for Name: data_lineage; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_lineage (data_lineage_id, transformation_step_id, source_column_id, target_column_id, lineage_type, transformation_expression, created_at) FROM stdin;
\.


--
-- Data for Name: data_processing_run; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_processing_run (run_id, run_type, started_at, finished_at, status, triggered_by, error_message) FROM stdin;
\.


--
-- Data for Name: data_quality_issue; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_quality_issue (issue_id, dataset_id, department_id, domain, title, description, status, created_by, assignee_user_id, assignment_mode, origin_upload_job_id, resolution_job_id, verification_run_id, created_at, updated_at) FROM stdin;
a01f92a5-7e74-4606-9671-773788135311	3	2	academic	Kiểm tra và sửa dữ liệu nguồn		ASSIGNED	2	7	SOURCE_UPLOADER	3	\N	\N	2026-11-16 21:08:11.145874+07	2026-11-16 21:08:11.145874+07
\.


--
-- Data for Name: data_quality_issue_event; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_quality_issue_event (event_id, issue_id, actor_id, event_type, from_status, to_status, details, created_at) FROM stdin;
1	a01f92a5-7e74-4606-9671-773788135311	2	RETURN_APPROVED	\N	ASSIGNED	{"assignee_user_id": 7, "assignment_mode": "SOURCE_UPLOADER", "origin_upload_job_id": 3}	2026-11-16 21:08:11.145874+07
\.


--
-- Data for Name: data_quality_issue_record; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_quality_issue_record (issue_id, record_id) FROM stdin;
a01f92a5-7e74-4606-9671-773788135311	1465c4ad-16a8-43ac-adac-91e8861d8dd1
a01f92a5-7e74-4606-9671-773788135311	2d1499f7-3c39-4919-a504-7d7658adc97d
a01f92a5-7e74-4606-9671-773788135311	571973d3-1990-4dec-b56d-a1f203be0bd0
a01f92a5-7e74-4606-9671-773788135311	57902a3f-8167-4e4c-ad7c-58c5f5605ee3
a01f92a5-7e74-4606-9671-773788135311	57f5d2da-6122-462b-85db-af94b06ff851
a01f92a5-7e74-4606-9671-773788135311	595ec716-2d01-494e-954d-88a4642da69d
a01f92a5-7e74-4606-9671-773788135311	638f8480-3f9b-4fd7-9d21-038827eebeaa
a01f92a5-7e74-4606-9671-773788135311	63a412cd-c103-4e25-a795-0a482e9b22ad
a01f92a5-7e74-4606-9671-773788135311	69caf89e-9b80-4d39-80d1-ab1b6fa75328
a01f92a5-7e74-4606-9671-773788135311	7f8cabb2-627b-4726-b1dd-0ca5aab3d974
a01f92a5-7e74-4606-9671-773788135311	8ed28e56-718f-4320-96d4-543b520a7c4e
a01f92a5-7e74-4606-9671-773788135311	953c6382-bd25-46fa-a126-2324e85cfa15
a01f92a5-7e74-4606-9671-773788135311	95410299-69fa-470d-be60-9d66853a8f05
a01f92a5-7e74-4606-9671-773788135311	981a92df-59d5-47a0-baa2-69afdb6f53a3
a01f92a5-7e74-4606-9671-773788135311	9cbd4c09-634a-431c-bb94-85d5a4f4a8c8
a01f92a5-7e74-4606-9671-773788135311	e74093f9-04e2-4f07-b537-c12b4e53bb9c
a01f92a5-7e74-4606-9671-773788135311	ea20e16d-9412-4bed-8752-b693d6001c83
a01f92a5-7e74-4606-9671-773788135311	ee88fad5-2e2b-4661-b7ec-a2ab4b7b53f7
a01f92a5-7e74-4606-9671-773788135311	f55b16e1-9103-4b74-b3b7-b937d9ac5d2a
a01f92a5-7e74-4606-9671-773788135311	f65a5cb1-b5cf-4294-87fa-c4eacbc51289
\.


--
-- Data for Name: data_quality_rule; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_quality_rule (data_quality_rule_id, catalog_column_id, rule_code, rule_name, rule_type, rule_expression, severity, is_active) FROM stdin;
\.


--
-- Data for Name: data_quality_run; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_quality_run (data_quality_run_id, data_quality_rule_id, ingestion_job_id, run_at, total_records, passed_records, failed_records, pass_rate, execution_status, failure_sample_ref) FROM stdin;
\.


--
-- Data for Name: data_source; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.data_source (data_source_id, source_system_id, source_name, source_type, connection_type, location, configuration, description, is_active, last_ingested_at, last_ingestion_status, ingestion_count, created_by, created_at, updated_at) FROM stdin;
2	35	UPLOAD_2_EXCEL	EXCEL	UPLOAD	\N	{"upload_dataset_id": 2}	\N	t	2026-11-16 14:07:16.347913	SUCCESS	1	7	2026-11-16 21:07:15.977378	\N
3	35	UPLOAD_3_EXCEL	EXCEL	UPLOAD	\N	{"upload_dataset_id": 3}	\N	t	2026-11-16 14:07:34.699488	SUCCESS	1	7	2026-11-16 21:07:33.32173	\N
1	34	UPLOAD_1_CSV	CSV	UPLOAD	\N	{"upload_dataset_id": 1}	\N	t	2026-11-16 16:04:03.180028	SUCCESS	6	9	2026-11-16 21:06:26.900815	\N
\.


--
-- Data for Name: dataset; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.dataset (dataset_id, dataset_code, dataset_name, domain, source_system_id, data_layer_id, owner_department_id, storage_format, location, version, status, description, created_at, updated_at) FROM stdin;
1	UPLOAD_61375266bce14619b0acce07a3096591	HR	hr	34	1	3	\N	\N	\N	ACTIVE	\N	2026-11-16 21:06:26.900815	\N
2	UPLOAD_e803846477454fed89241387a1b98279	chuẩn	academic	35	1	2	\N	\N	\N	ACTIVE	\N	2026-11-16 21:07:15.977378	\N
3	UPLOAD_ebb8b858aaf649579f6e41e928656140	lỗi	academic	35	1	2	\N	\N	\N	ACTIVE	\N	2026-11-16 21:07:33.32173	\N
\.


--
-- Data for Name: department; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.department (department_id, department_code, department_name, department_type, parent_department_id, status, created_at) FROM stdin;
1	PTS	Phòng Tuyển sinh	Office	\N	ACTIVE	2026-11-16 20:22:05.739837
2	PDT	Phòng Đào tạo	Office	\N	ACTIVE	2026-11-16 20:22:05.739837
37	UNI	Trường Đại học Mẫu	University	\N	ACTIVE	2020-01-01 07:00:00
3	PNS	Phòng Nhân sự	Office	37	ACTIVE	2020-01-01 07:00:00
38	KCNTT	Khoa Công nghệ thông tin	Faculty	37	ACTIVE	2020-01-01 07:00:00
39	BMCNTT	Bộ môn Công nghệ thông tin	Division	38	ACTIVE	2020-01-01 07:00:00
\.


--
-- Data for Name: employee; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.employee (employee_id, employee_code, full_name, date_of_birth, gender, email, phone, hire_date, employee_status) FROM stdin;
1	NV0001	Trần Thị Mẫu 1	\N	Male	\N	\N	2000-01-01	ACTIVE
2	NV0002	Lê Minh Mẫu 2	\N	Female	\N	\N	2000-01-01	ACTIVE
3	NV0003	Đặng Ngọc Mẫu 3	\N	Male	\N	\N	2000-01-01	ACTIVE
4	NV0004	Nguyễn Văn Mẫu 4	\N	Female	\N	\N	2000-01-01	ACTIVE
5	NV0005	Trần Thị Mẫu 5	\N	Male	\N	\N	2000-01-01	ACTIVE
6	NV0006	Lê Minh Mẫu 6	\N	Female	\N	\N	2000-01-01	ACTIVE
7	NV0007	Đặng Ngọc Mẫu 7	\N	Male	\N	\N	2000-01-01	ACTIVE
8	NV0008	Nguyễn Văn Mẫu 8	\N	Female	\N	\N	2000-01-01	ACTIVE
9	NV0009	Trần Thị Mẫu 9	\N	Male	\N	\N	2000-01-01	ACTIVE
10	NV0010	Lê Minh Mẫu 10	\N	Female	\N	\N	2000-01-01	ACTIVE
11	NV0011	Đặng Ngọc Mẫu 11	\N	Male	\N	\N	2000-01-01	ACTIVE
12	NV0012	Nguyễn Văn Mẫu 12	\N	Female	\N	\N	2000-01-01	ACTIVE
13	NV0013	Trần Thị Mẫu 13	\N	Male	\N	\N	2000-01-01	ACTIVE
14	NV0014	Lê Minh Mẫu 14	\N	Female	\N	\N	2000-01-01	ACTIVE
15	NV0015	Đặng Ngọc Mẫu 15	\N	Male	\N	\N	2000-01-01	ACTIVE
16	NV0016	Nguyễn Văn Mẫu 16	\N	Female	\N	\N	2000-01-01	ACTIVE
17	NV0017	Trần Thị Mẫu 17	\N	Male	\N	\N	2000-01-01	ACTIVE
18	NV0018	Lê Minh Mẫu 18	\N	Female	\N	\N	2000-01-01	ACTIVE
19	NV0019	Đặng Ngọc Mẫu 19	\N	Male	\N	\N	2000-01-01	ACTIVE
20	NV0020	Nguyễn Văn Mẫu 20	\N	Female	\N	\N	2000-01-01	ACTIVE
21	NV0021	Trần Thị Mẫu 21	\N	Male	\N	\N	2000-01-01	ACTIVE
22	NV0022	Lê Minh Mẫu 22	\N	Female	\N	\N	2000-01-01	ACTIVE
23	NV0023	Đặng Ngọc Mẫu 23	\N	Male	\N	\N	2000-01-01	ACTIVE
24	NV0024	Nguyễn Văn Mẫu 24	\N	Female	\N	\N	2000-01-01	ACTIVE
25	NV0025	Trần Thị Mẫu 25	\N	Male	\N	\N	2000-01-01	ACTIVE
26	NV0026	Lê Minh Mẫu 26	\N	Female	\N	\N	2000-01-01	ACTIVE
27	NV0027	Đặng Ngọc Mẫu 27	\N	Male	\N	\N	2000-01-01	ACTIVE
28	NV0028	Nguyễn Văn Mẫu 28	\N	Female	\N	\N	2000-01-01	ACTIVE
29	NV0029	Trần Thị Mẫu 29	\N	Male	\N	\N	2000-01-01	ACTIVE
30	NV0030	Lê Minh Mẫu 30	\N	Female	\N	\N	2000-01-01	ACTIVE
31	NV0031	Đặng Ngọc Mẫu 31	\N	Male	\N	\N	2000-01-01	ACTIVE
32	NV0032	Nguyễn Văn Mẫu 32	\N	Female	\N	\N	2000-01-01	ACTIVE
33	NV0033	Trần Thị Mẫu 33	\N	Male	\N	\N	2000-01-01	ACTIVE
34	NV0034	Lê Minh Mẫu 34	\N	Female	\N	\N	2000-01-01	ACTIVE
35	NV0035	Đặng Ngọc Mẫu 35	\N	Male	\N	\N	2000-01-01	ACTIVE
36	NV0036	Nguyễn Văn Mẫu 36	\N	Female	\N	\N	2000-01-01	ACTIVE
37	NV0037	Trần Thị Mẫu 37	\N	Male	\N	\N	2000-01-01	ACTIVE
38	NV0038	Lê Minh Mẫu 38	\N	Female	\N	\N	2000-01-01	ACTIVE
39	NV0039	Đặng Ngọc Mẫu 39	\N	Male	\N	\N	2000-01-01	ACTIVE
40	NV0040	Nguyễn Văn Mẫu 40	\N	Female	\N	\N	2000-01-01	ACTIVE
41	NV0041	Trần Thị Mẫu 41	\N	Male	\N	\N	2000-01-01	ACTIVE
42	NV0042	Lê Minh Mẫu 42	\N	Female	\N	\N	2000-01-01	ACTIVE
43	NV0043	Đặng Ngọc Mẫu 43	\N	Male	\N	\N	2000-01-01	ACTIVE
44	NV0044	Nguyễn Văn Mẫu 44	\N	Female	\N	\N	2000-01-01	ACTIVE
45	NV0045	Trần Thị Mẫu 45	\N	Male	\N	\N	2000-01-01	ACTIVE
46	NV0046	Lê Minh Mẫu 46	\N	Female	\N	\N	2000-01-01	ACTIVE
47	NV0047	Đặng Ngọc Mẫu 47	\N	Male	\N	\N	2000-01-01	ACTIVE
48	NV0048	Nguyễn Văn Mẫu 48	\N	Female	\N	\N	2000-01-01	ACTIVE
49	NV0049	Trần Thị Mẫu 49	\N	Male	\N	\N	2000-01-01	ACTIVE
50	NV0050	Lê Minh Mẫu 50	\N	Female	\N	\N	2000-01-01	ACTIVE
51	NV0051	Đặng Ngọc Mẫu 51	\N	Male	\N	\N	2000-01-01	ACTIVE
52	NV0052	Nguyễn Văn Mẫu 52	\N	Female	\N	\N	2000-01-01	ACTIVE
53	NV0053	Trần Thị Mẫu 53	\N	Male	\N	\N	2000-01-01	ACTIVE
54	NV0054	Lê Minh Mẫu 54	\N	Female	\N	\N	2000-01-01	ACTIVE
55	NV0055	Đặng Ngọc Mẫu 55	\N	Male	\N	\N	2000-01-01	ACTIVE
56	NV0056	Nguyễn Văn Mẫu 56	\N	Female	\N	\N	2000-01-01	ACTIVE
57	NV0057	Trần Thị Mẫu 57	\N	Male	\N	\N	2000-01-01	ACTIVE
58	NV0058	Lê Minh Mẫu 58	\N	Female	\N	\N	2000-01-01	ACTIVE
59	NV0059	Đặng Ngọc Mẫu 59	\N	Male	\N	\N	2000-01-01	ACTIVE
60	NV0060	Nguyễn Văn Mẫu 60	\N	Female	\N	\N	2000-01-01	ACTIVE
61	NV0061	Trần Thị Mẫu 61	\N	Male	\N	\N	2000-01-01	ACTIVE
62	NV0062	Lê Minh Mẫu 62	\N	Female	\N	\N	2000-01-01	ACTIVE
63	NV0063	Đặng Ngọc Mẫu 63	\N	Male	\N	\N	2000-01-01	ACTIVE
64	NV0064	Nguyễn Văn Mẫu 64	\N	Female	\N	\N	2000-01-01	ACTIVE
65	NV0065	Trần Thị Mẫu 65	\N	Male	\N	\N	2000-01-01	ACTIVE
66	NV0066	Lê Minh Mẫu 66	\N	Female	\N	\N	2000-01-01	ACTIVE
67	NV0067	Đặng Ngọc Mẫu 67	\N	Male	\N	\N	2000-01-01	ACTIVE
68	NV0068	Nguyễn Văn Mẫu 68	\N	Female	\N	\N	2000-01-01	ACTIVE
69	NV0069	Trần Thị Mẫu 69	\N	Male	\N	\N	2000-01-01	ACTIVE
70	NV0070	Lê Minh Mẫu 70	\N	Female	\N	\N	2000-01-01	ACTIVE
71	NV0071	Đặng Ngọc Mẫu 71	\N	Male	\N	\N	2000-01-01	ACTIVE
72	NV0072	Nguyễn Văn Mẫu 72	\N	Female	\N	\N	2000-01-01	ACTIVE
73	NV0073	Trần Thị Mẫu 73	\N	Male	\N	\N	2000-01-01	ACTIVE
74	NV0074	Lê Minh Mẫu 74	\N	Female	\N	\N	2000-01-01	ACTIVE
75	NV0075	Đặng Ngọc Mẫu 75	\N	Male	\N	\N	2000-01-01	ACTIVE
76	NV0076	Nguyễn Văn Mẫu 76	\N	Female	\N	\N	2000-01-01	ACTIVE
77	NV0077	Trần Thị Mẫu 77	\N	Male	\N	\N	2000-01-01	ACTIVE
78	NV0078	Lê Minh Mẫu 78	\N	Female	\N	\N	2000-01-01	ACTIVE
79	NV0079	Đặng Ngọc Mẫu 79	\N	Male	\N	\N	2000-01-01	ACTIVE
80	NV0080	Nguyễn Văn Mẫu 80	\N	Female	\N	\N	2000-01-01	ACTIVE
81	NV0081	Trần Thị Mẫu 81	\N	Male	\N	\N	2000-01-01	ACTIVE
82	NV0082	Lê Minh Mẫu 82	\N	Female	\N	\N	2000-01-01	ACTIVE
83	NV0083	Đặng Ngọc Mẫu 83	\N	Male	\N	\N	2000-01-01	ACTIVE
84	NV0084	Nguyễn Văn Mẫu 84	\N	Female	\N	\N	2000-01-01	ACTIVE
85	NV0085	Trần Thị Mẫu 85	\N	Male	\N	\N	2000-01-01	ACTIVE
86	NV0086	Lê Minh Mẫu 86	\N	Female	\N	\N	2000-01-01	ACTIVE
87	NV0087	Đặng Ngọc Mẫu 87	\N	Male	\N	\N	2000-01-01	ACTIVE
88	NV0088	Nguyễn Văn Mẫu 88	\N	Female	\N	\N	2000-01-01	ACTIVE
89	NV0089	Trần Thị Mẫu 89	\N	Male	\N	\N	2000-01-01	ACTIVE
90	NV0090	Lê Minh Mẫu 90	\N	Female	\N	\N	2000-01-01	ACTIVE
91	NV0091	Đặng Ngọc Mẫu 91	\N	Male	\N	\N	2000-01-01	ACTIVE
92	NV0092	Nguyễn Văn Mẫu 92	\N	Female	\N	\N	2000-01-01	ACTIVE
93	NV0093	Trần Thị Mẫu 93	\N	Male	\N	\N	2000-01-01	ACTIVE
94	NV0094	Lê Minh Mẫu 94	\N	Female	\N	\N	2000-01-01	ACTIVE
95	NV0095	Đặng Ngọc Mẫu 95	\N	Male	\N	\N	2000-01-01	ACTIVE
96	NV0096	Nguyễn Văn Mẫu 96	\N	Female	\N	\N	2000-01-01	ACTIVE
97	NV0097	Trần Thị Mẫu 97	\N	Male	\N	\N	2000-01-01	ACTIVE
98	NV0098	Lê Minh Mẫu 98	\N	Female	\N	\N	2000-01-01	ACTIVE
99	NV0099	Đặng Ngọc Mẫu 99	\N	Male	\N	\N	2000-01-01	ACTIVE
100	NV0100	Nguyễn Văn Mẫu 100	\N	Female	\N	\N	2000-01-01	ACTIVE
101	NV0101	Trần Thị Mẫu 101	\N	Male	\N	\N	2000-01-01	ACTIVE
102	NV0102	Lê Minh Mẫu 102	\N	Female	\N	\N	2000-01-01	ACTIVE
103	NV0103	Đặng Ngọc Mẫu 103	\N	Male	\N	\N	2000-01-01	ACTIVE
104	NV0104	Nguyễn Văn Mẫu 104	\N	Female	\N	\N	2000-01-01	ACTIVE
105	NV0105	Trần Thị Mẫu 105	\N	Male	\N	\N	2000-01-01	ACTIVE
106	NV0106	Lê Minh Mẫu 106	\N	Female	\N	\N	2000-01-01	ACTIVE
107	NV0107	Đặng Ngọc Mẫu 107	\N	Male	\N	\N	2000-01-01	ACTIVE
108	NV0108	Nguyễn Văn Mẫu 108	\N	Female	\N	\N	2000-01-01	ACTIVE
109	NV0109	Trần Thị Mẫu 109	\N	Male	\N	\N	2000-01-01	ACTIVE
110	NV0110	Lê Minh Mẫu 110	\N	Female	\N	\N	2000-01-01	ACTIVE
111	NV0111	Đặng Ngọc Mẫu 111	\N	Male	\N	\N	2000-01-01	ACTIVE
112	NV0112	Nguyễn Văn Mẫu 112	\N	Female	\N	\N	2000-01-01	ACTIVE
113	NV0113	Trần Thị Mẫu 113	\N	Male	\N	\N	2000-01-01	ACTIVE
114	NV0114	Lê Minh Mẫu 114	\N	Female	\N	\N	2000-01-01	ACTIVE
115	NV0115	Đặng Ngọc Mẫu 115	\N	Male	\N	\N	2000-01-01	ACTIVE
116	NV0116	Nguyễn Văn Mẫu 116	\N	Female	\N	\N	2000-01-01	ACTIVE
117	NV0117	Trần Thị Mẫu 117	\N	Male	\N	\N	2000-01-01	ACTIVE
118	NV0118	Lê Minh Mẫu 118	\N	Female	\N	\N	2000-01-01	ACTIVE
119	NV0119	Đặng Ngọc Mẫu 119	\N	Male	\N	\N	2000-01-01	ACTIVE
120	NV0120	Nguyễn Văn Mẫu 120	\N	Female	\N	\N	2000-01-01	ACTIVE
121	NV0121	Trần Thị Mẫu 121	\N	Male	\N	\N	2000-01-01	ACTIVE
122	NV0122	Lê Minh Mẫu 122	\N	Female	\N	\N	2000-01-01	ACTIVE
123	NV0123	Đặng Ngọc Mẫu 123	\N	Male	\N	\N	2000-01-01	ACTIVE
124	NV0124	Nguyễn Văn Mẫu 124	\N	Female	\N	\N	2000-01-01	ACTIVE
125	NV0125	Trần Thị Mẫu 125	\N	Male	\N	\N	2000-01-01	ACTIVE
126	NV0126	Lê Minh Mẫu 126	\N	Female	\N	\N	2000-01-01	ACTIVE
127	NV0127	Đặng Ngọc Mẫu 127	\N	Male	\N	\N	2000-01-01	ACTIVE
128	NV0128	Nguyễn Văn Mẫu 128	\N	Female	\N	\N	2000-01-01	ACTIVE
129	NV0129	Trần Thị Mẫu 129	\N	Male	\N	\N	2000-01-01	ACTIVE
130	NV0130	Lê Minh Mẫu 130	\N	Female	\N	\N	2000-01-01	ACTIVE
131	NV0131	Đặng Ngọc Mẫu 131	\N	Male	\N	\N	2000-01-01	ACTIVE
132	NV0132	Nguyễn Văn Mẫu 132	\N	Female	\N	\N	2000-01-01	ACTIVE
133	NV0133	Trần Thị Mẫu 133	\N	Male	\N	\N	2000-01-01	ACTIVE
134	NV0134	Lê Minh Mẫu 134	\N	Female	\N	\N	2000-01-01	ACTIVE
135	NV0135	Đặng Ngọc Mẫu 135	\N	Male	\N	\N	2000-01-01	ACTIVE
136	NV0136	Nguyễn Văn Mẫu 136	\N	Female	\N	\N	2000-01-01	ACTIVE
137	NV0137	Trần Thị Mẫu 137	\N	Male	\N	\N	2000-01-01	ACTIVE
138	NV0138	Lê Minh Mẫu 138	\N	Female	\N	\N	2000-01-01	ACTIVE
139	NV0139	Đặng Ngọc Mẫu 139	\N	Male	\N	\N	2000-01-01	ACTIVE
140	NV0140	Nguyễn Văn Mẫu 140	\N	Female	\N	\N	2000-01-01	ACTIVE
141	NV0141	Trần Thị Mẫu 141	\N	Male	\N	\N	2000-01-01	ACTIVE
142	NV0142	Lê Minh Mẫu 142	\N	Female	\N	\N	2000-01-01	ACTIVE
143	NV0143	Đặng Ngọc Mẫu 143	\N	Male	\N	\N	2000-01-01	ACTIVE
144	NV0144	Nguyễn Văn Mẫu 144	\N	Female	\N	\N	2000-01-01	ACTIVE
145	NV0145	Trần Thị Mẫu 145	\N	Male	\N	\N	2000-01-01	ACTIVE
146	NV0146	Lê Minh Mẫu 146	\N	Female	\N	\N	2000-01-01	ACTIVE
147	NV0147	Đặng Ngọc Mẫu 147	\N	Male	\N	\N	2000-01-01	ACTIVE
148	NV0148	Nguyễn Văn Mẫu 148	\N	Female	\N	\N	2000-01-01	ACTIVE
149	NV0149	Trần Thị Mẫu 149	\N	Male	\N	\N	2000-01-01	ACTIVE
150	NV0150	Lê Minh Mẫu 150	\N	Female	\N	\N	2000-01-01	ACTIVE
151	NV0151	Đặng Ngọc Mẫu 151	\N	Male	\N	\N	2000-01-01	ACTIVE
152	NV0152	Nguyễn Văn Mẫu 152	\N	Female	\N	\N	2000-01-01	ACTIVE
153	NV0153	Trần Thị Mẫu 153	\N	Male	\N	\N	2000-01-01	ACTIVE
154	NV0154	Lê Minh Mẫu 154	\N	Female	\N	\N	2000-01-01	ACTIVE
155	NV0155	Đặng Ngọc Mẫu 155	\N	Male	\N	\N	2000-01-01	ACTIVE
156	NV0156	Nguyễn Văn Mẫu 156	\N	Female	\N	\N	2000-01-01	ACTIVE
157	NV0157	Trần Thị Mẫu 157	\N	Male	\N	\N	2000-01-01	ACTIVE
158	NV0158	Lê Minh Mẫu 158	\N	Female	\N	\N	2000-01-01	ACTIVE
159	NV0159	Đặng Ngọc Mẫu 159	\N	Male	\N	\N	2000-01-01	ACTIVE
160	NV0160	Nguyễn Văn Mẫu 160	\N	Female	\N	\N	2000-01-01	ACTIVE
161	NV0161	Trần Thị Mẫu 161	\N	Male	\N	\N	2000-01-01	ACTIVE
162	NV0162	Lê Minh Mẫu 162	\N	Female	\N	\N	2000-01-01	ACTIVE
163	NV0163	Đặng Ngọc Mẫu 163	\N	Male	\N	\N	2000-01-01	ACTIVE
164	NV0164	Nguyễn Văn Mẫu 164	\N	Female	\N	\N	2000-01-01	ACTIVE
165	NV0165	Trần Thị Mẫu 165	\N	Male	\N	\N	2000-01-01	ACTIVE
166	NV0166	Lê Minh Mẫu 166	\N	Female	\N	\N	2000-01-01	ACTIVE
167	NV0167	Đặng Ngọc Mẫu 167	\N	Male	\N	\N	2000-01-01	ACTIVE
168	NV0168	Nguyễn Văn Mẫu 168	\N	Female	\N	\N	2000-01-01	ACTIVE
169	NV0169	Trần Thị Mẫu 169	\N	Male	\N	\N	2000-01-01	ACTIVE
170	NV0170	Lê Minh Mẫu 170	\N	Female	\N	\N	2000-01-01	ACTIVE
171	NV0171	Đặng Ngọc Mẫu 171	\N	Male	\N	\N	2000-01-01	ACTIVE
172	NV0172	Nguyễn Văn Mẫu 172	\N	Female	\N	\N	2000-01-01	ACTIVE
173	NV0173	Trần Thị Mẫu 173	\N	Male	\N	\N	2000-01-01	ACTIVE
174	NV0174	Lê Minh Mẫu 174	\N	Female	\N	\N	2000-01-01	ACTIVE
175	NV0175	Đặng Ngọc Mẫu 175	\N	Male	\N	\N	2000-01-01	ACTIVE
176	NV0176	Nguyễn Văn Mẫu 176	\N	Female	\N	\N	2000-01-01	ACTIVE
177	NV0177	Trần Thị Mẫu 177	\N	Male	\N	\N	2000-01-01	ACTIVE
178	NV0178	Lê Minh Mẫu 178	\N	Female	\N	\N	2000-01-01	ACTIVE
179	NV0179	Đặng Ngọc Mẫu 179	\N	Male	\N	\N	2000-01-01	ACTIVE
180	NV0180	Nguyễn Văn Mẫu 180	\N	Female	\N	\N	2000-01-01	ACTIVE
181	NV0181	Trần Thị Mẫu 181	\N	Male	\N	\N	2000-01-01	ACTIVE
182	NV0182	Lê Minh Mẫu 182	\N	Female	\N	\N	2000-01-01	ACTIVE
183	NV0183	Đặng Ngọc Mẫu 183	\N	Male	\N	\N	2000-01-01	ACTIVE
184	NV0184	Nguyễn Văn Mẫu 184	\N	Female	\N	\N	2000-01-01	ACTIVE
185	NV0185	Trần Thị Mẫu 185	\N	Male	\N	\N	2000-01-01	ACTIVE
186	NV0186	Lê Minh Mẫu 186	\N	Female	\N	\N	2000-01-01	ACTIVE
187	NV0187	Đặng Ngọc Mẫu 187	\N	Male	\N	\N	2000-01-01	ACTIVE
188	NV0188	Nguyễn Văn Mẫu 188	\N	Female	\N	\N	2000-01-01	ACTIVE
189	NV0189	Trần Thị Mẫu 189	\N	Male	\N	\N	2000-01-01	ACTIVE
190	NV0190	Lê Minh Mẫu 190	\N	Female	\N	\N	2000-01-01	ACTIVE
191	NV0191	Đặng Ngọc Mẫu 191	\N	Male	\N	\N	2000-01-01	ACTIVE
192	NV0192	Nguyễn Văn Mẫu 192	\N	Female	\N	\N	2000-01-01	ACTIVE
193	NV0193	Trần Thị Mẫu 193	\N	Male	\N	\N	2000-01-01	ACTIVE
194	NV0194	Lê Minh Mẫu 194	\N	Female	\N	\N	2000-01-01	ACTIVE
195	NV0195	Đặng Ngọc Mẫu 195	\N	Male	\N	\N	2000-01-01	ACTIVE
196	NV0196	Nguyễn Văn Mẫu 196	\N	Female	\N	\N	2000-01-01	ACTIVE
197	NV0197	Trần Thị Mẫu 197	\N	Male	\N	\N	2000-01-01	ACTIVE
198	NV0198	Lê Minh Mẫu 198	\N	Female	\N	\N	2000-01-01	ACTIVE
199	NV0199	Đặng Ngọc Mẫu 199	\N	Male	\N	\N	2000-01-01	ACTIVE
200	NV0200	Nguyễn Văn Mẫu 200	\N	Female	\N	\N	2000-01-01	ACTIVE
\.


--
-- Data for Name: employee_qualification; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.employee_qualification (employee_id, qualification_id, institution, year_obtained) FROM stdin;
\.


--
-- Data for Name: enrollment; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.enrollment (enrollment_id, student_id, class_id, enrollment_date, enrollment_status, created_at) FROM stdin;
\.


--
-- Data for Name: enrollment_grade; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.enrollment_grade (enrollment_grade_id, enrollment_id, midterm_score, final_score, total_score, grade_value, letter_grade, registration_date) FROM stdin;
\.


--
-- Data for Name: generated_sql; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.generated_sql (generated_sql_id, query_request_id, ai_model_id, prompt_template_id, sql_text, generated_at, validation_status, validation_error) FROM stdin;
\.


--
-- Data for Name: gold_attempt; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.gold_attempt (lease_token, job_id, attempt_number, status, worker_id, error_code, started_at, finished_at) FROM stdin;
2dbf723c-7766-4d73-8df8-1d40418079c5	0014c6c1-5516-40f3-ade7-54b129fec649	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 21:06:33.16875+07	2026-11-16 21:06:33.613573+07
38cc7f89-ab11-4374-954a-8a0058b88c66	9c122de6-d11c-4db4-872d-60a8970bcb3f	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 21:07:19.878653+07	2026-11-16 21:07:19.927301+07
732eba56-4b16-4a0f-923f-892729bd7bdf	228dd533-ce62-419e-805e-a3ab4c8b2d65	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 21:15:59.343+07	2026-11-16 21:15:59.407355+07
b21152c8-2cc0-4557-b1bd-9f1b669b82f5	a10359dd-879c-4d62-b226-82d26ee14dd8	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 21:22:59.754984+07	2026-11-16 21:22:59.835721+07
1754e734-d961-4ba7-82ac-5e1465f28ecf	87f44121-73fe-4098-a175-06254ab5ce81	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 21:26:08.816135+07	2026-11-16 21:26:08.875576+07
437fe272-a38c-4823-b425-73754f263530	59e65f53-3d4e-41aa-80a3-b577773e7945	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 21:26:30.991245+07	2026-11-16 21:26:31.064511+07
5dc808e4-997c-47c9-8476-08b9f9c9f39e	a94b1c6b-4f98-409f-8471-964aa49ee81a	1	SUCCEEDED	969358f0d4a1	\N	2026-11-16 23:04:04.799996+07	2026-11-16 23:04:04.903641+07
\.


--
-- Data for Name: gold_job; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.gold_job (job_id, scope, dataset_id, domain, generation, silver_snapshot_id, recipe_version, recipe_fingerprint, status, attempt_number, max_attempts, lease_token, lease_until, worker_id, next_attempt_at, error_code, manifest_ref, result, created_at, finished_at, silver_manifest_ref) FROM stdin;
0014c6c1-5516-40f3-ade7-54b129fec649	production:auto-899188c733e26f94ba29ead9cf0e	1	hr	1	52df8123-672a-425d-93a1-04f53de4a774	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 21:06:31.214306+07	\N	{"key": "gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/0014c6c1-5516-40f3-ade7-54b129fec649/manifest-7f7d6dc075fee38638b9a6af80f26a4577cbcbb4e7365ef64fc072fd1f0a9f45.json", "size": 2894, "sha256": "7f7d6dc075fee38638b9a6af80f26a4577cbcbb4e7365ef64fc072fd1f0a9f45", "version_id": "a48bd1e7-31f1-44d5-bac7-e7b54cd51801"}	{"scope": "production:auto-899188c733e26f94ba29ead9cf0e", "domain": "hr", "format": "unilake-gold-v1", "object": {"key": "gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/0014c6c1-5516-40f3-ade7-54b129fec649/aggregate-2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c.parquet", "size": 1567, "sha256": "2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c", "version_id": "6719e09c-e2da-4741-a574-39546cd2e81c"}, "lineage": {"mode": "production", "run_ref": "0014c6c1-5516-40f3-ade7-54b129fec649", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/52df8123-672a-425d-93a1-04f53de4a774/manifest-d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01.json", "layer": "SILVER", "domain": "hr", "version": "52df8123-672a-425d-93a1-04f53de4a774", "asset_id": "silver:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/0014c6c1-5516-40f3-ade7-54b129fec649/aggregate-2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c.parquet", "layer": "GOLD", "domain": "hr", "version": "0014c6c1-5516-40f3-ade7-54b129fec649", "asset_id": "gold:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c", "producer_run_ref": "0014c6c1-5516-40f3-ade7-54b129fec649"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 1, "generation": 1, "records_read": 200, "aggregate_rows": 6, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 200, "entity": "assignment", "object": {"key": "silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/52df8123-672a-425d-93a1-04f53de4a774/assignment-7ad5df316c13bb88f41b272e7f34ad16c941523ca0baa58028256327a3bd8223.parquet", "size": 20384, "sha256": "7ad5df316c13bb88f41b272e7f34ad16c941523ca0baa58028256327a3bd8223", "version_id": null}}], "silver_manifest": {"key": "silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/52df8123-672a-425d-93a1-04f53de4a774/manifest-d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01.json", "size": 54258, "sha256": "d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01", "version_id": null}, "gold_snapshot_id": "0014c6c1-5516-40f3-ade7-54b129fec649", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "52df8123-672a-425d-93a1-04f53de4a774"}	2026-11-16 21:06:31.214306+07	2026-11-16 21:06:33.613573+07	{"key": "silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/52df8123-672a-425d-93a1-04f53de4a774/manifest-d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01.json", "size": 54258, "sha256": "d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01", "version_id": null}
9c122de6-d11c-4db4-872d-60a8970bcb3f	production:auto-5cb5b4e2548a0f888863ffd97381	2	academic	1	539ae500-2f13-451b-be83-286f62b636f9	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 21:07:17.960015+07	\N	{"key": "gold/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/9c122de6-d11c-4db4-872d-60a8970bcb3f/manifest-77daa903359c5312edb67c066097185d256a1caebb58132864a584435a9daef8.json", "size": 2904, "sha256": "77daa903359c5312edb67c066097185d256a1caebb58132864a584435a9daef8", "version_id": "ae28f62b-fc49-42e1-af62-927c0759c132"}	{"scope": "production:auto-5cb5b4e2548a0f888863ffd97381", "domain": "academic", "format": "unilake-gold-v1", "object": {"key": "gold/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/9c122de6-d11c-4db4-872d-60a8970bcb3f/aggregate-3580f907fd8e0425d9516bf763169c60a6bed640fef96a88127de2f88dc5ef57.parquet", "size": 1306, "sha256": "3580f907fd8e0425d9516bf763169c60a6bed640fef96a88127de2f88dc5ef57", "version_id": "8028a48f-ed95-4c68-a8e1-ec20334d4798"}, "lineage": {"mode": "production", "run_ref": "9c122de6-d11c-4db4-872d-60a8970bcb3f", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/539ae500-2f13-451b-be83-286f62b636f9/manifest-98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0.json", "layer": "SILVER", "domain": "academic", "version": "539ae500-2f13-451b-be83-286f62b636f9", "asset_id": "silver:production:auto-5cb5b4e2548a0f888863ffd97381", "checksum": "98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/9c122de6-d11c-4db4-872d-60a8970bcb3f/aggregate-3580f907fd8e0425d9516bf763169c60a6bed640fef96a88127de2f88dc5ef57.parquet", "layer": "GOLD", "domain": "academic", "version": "9c122de6-d11c-4db4-872d-60a8970bcb3f", "asset_id": "gold:production:auto-5cb5b4e2548a0f888863ffd97381", "checksum": "3580f907fd8e0425d9516bf763169c60a6bed640fef96a88127de2f88dc5ef57", "producer_run_ref": "9c122de6-d11c-4db4-872d-60a8970bcb3f"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 2, "generation": 1, "records_read": 100, "aggregate_rows": 1, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 100, "entity": "file_2", "object": {"key": "silver/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/539ae500-2f13-451b-be83-286f62b636f9/file_2-092448090ef7e52d1f5b473332197a418bda889cfcfb1edf3c74b52a34937cab.parquet", "size": 15662, "sha256": "092448090ef7e52d1f5b473332197a418bda889cfcfb1edf3c74b52a34937cab", "version_id": null}}], "silver_manifest": {"key": "silver/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/539ae500-2f13-451b-be83-286f62b636f9/manifest-98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0.json", "size": 32925, "sha256": "98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0", "version_id": null}, "gold_snapshot_id": "9c122de6-d11c-4db4-872d-60a8970bcb3f", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "539ae500-2f13-451b-be83-286f62b636f9"}	2026-11-16 21:07:17.960015+07	2026-11-16 21:07:19.927301+07	{"key": "silver/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/539ae500-2f13-451b-be83-286f62b636f9/manifest-98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0.json", "size": 32925, "sha256": "98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0", "version_id": null}
228dd533-ce62-419e-805e-a3ab4c8b2d65	production:auto-7858eb8f709a3e0a1583c6d93948	1	hr	1	1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 21:15:57.710363+07	\N	{"key": "gold/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/228dd533-ce62-419e-805e-a3ab4c8b2d65/manifest-8a95446c05ac7413b1f0594ccfd868fc1a5865bd071035780ded8d8aaf9a3442.json", "size": 2888, "sha256": "8a95446c05ac7413b1f0594ccfd868fc1a5865bd071035780ded8d8aaf9a3442", "version_id": "1d39b84f-28c7-4329-9278-63258aa82a95"}	{"scope": "production:auto-7858eb8f709a3e0a1583c6d93948", "domain": "hr", "format": "unilake-gold-v1", "object": {"key": "gold/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/228dd533-ce62-419e-805e-a3ab4c8b2d65/aggregate-5105fe9660208516add603948caff233c0c56fe2a9fea28cfb4de8601a5c5e77.parquet", "size": 1326, "sha256": "5105fe9660208516add603948caff233c0c56fe2a9fea28cfb4de8601a5c5e77", "version_id": "84983895-2186-4916-987b-e61a836a9b1f"}, "lineage": {"mode": "production", "run_ref": "228dd533-ce62-419e-805e-a3ab4c8b2d65", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf/manifest-e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7.json", "layer": "SILVER", "domain": "hr", "version": "1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf", "asset_id": "silver:production:auto-7858eb8f709a3e0a1583c6d93948", "checksum": "e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/228dd533-ce62-419e-805e-a3ab4c8b2d65/aggregate-5105fe9660208516add603948caff233c0c56fe2a9fea28cfb4de8601a5c5e77.parquet", "layer": "GOLD", "domain": "hr", "version": "228dd533-ce62-419e-805e-a3ab4c8b2d65", "asset_id": "gold:production:auto-7858eb8f709a3e0a1583c6d93948", "checksum": "5105fe9660208516add603948caff233c0c56fe2a9fea28cfb4de8601a5c5e77", "producer_run_ref": "228dd533-ce62-419e-805e-a3ab4c8b2d65"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 1, "generation": 1, "records_read": 4, "aggregate_rows": 1, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 4, "entity": "department", "object": {"key": "silver/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf/department-209eefe2aa564f9c8808b9550c0161df4d261b41610b9578ce213fdd311e0d01.parquet", "size": 5432, "sha256": "209eefe2aa564f9c8808b9550c0161df4d261b41610b9578ce213fdd311e0d01", "version_id": null}}], "silver_manifest": {"key": "silver/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf/manifest-e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7.json", "size": 8369, "sha256": "e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7", "version_id": null}, "gold_snapshot_id": "228dd533-ce62-419e-805e-a3ab4c8b2d65", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf"}	2026-11-16 21:15:57.710363+07	2026-11-16 21:15:59.407355+07	{"key": "silver/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf/manifest-e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7.json", "size": 8369, "sha256": "e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7", "version_id": null}
a10359dd-879c-4d62-b226-82d26ee14dd8	production:auto-e270bc9994f1530b4a540c0daa6c	1	hr	1	8079b128-c5b0-4625-917b-e01e5386342d	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 21:22:58.193257+07	\N	{"key": "gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a10359dd-879c-4d62-b226-82d26ee14dd8/manifest-337c97796674dfe93766adc82d86d771d9e60d2a2858bc222879c77d8e5550e6.json", "size": 2890, "sha256": "337c97796674dfe93766adc82d86d771d9e60d2a2858bc222879c77d8e5550e6", "version_id": "7b9af616-fa0a-4e52-bcc3-321ed909567f"}	{"scope": "production:auto-e270bc9994f1530b4a540c0daa6c", "domain": "hr", "format": "unilake-gold-v1", "object": {"key": "gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a10359dd-879c-4d62-b226-82d26ee14dd8/aggregate-9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec.parquet", "size": 1526, "sha256": "9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec", "version_id": "8017d1f1-0937-4896-b8b7-6e991df459ae"}, "lineage": {"mode": "production", "run_ref": "a10359dd-879c-4d62-b226-82d26ee14dd8", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/8079b128-c5b0-4625-917b-e01e5386342d/manifest-5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee.json", "layer": "SILVER", "domain": "hr", "version": "8079b128-c5b0-4625-917b-e01e5386342d", "asset_id": "silver:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a10359dd-879c-4d62-b226-82d26ee14dd8/aggregate-9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec.parquet", "layer": "GOLD", "domain": "hr", "version": "a10359dd-879c-4d62-b226-82d26ee14dd8", "asset_id": "gold:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec", "producer_run_ref": "a10359dd-879c-4d62-b226-82d26ee14dd8"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 1, "generation": 1, "records_read": 200, "aggregate_rows": 5, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 200, "entity": "employee", "object": {"key": "silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/8079b128-c5b0-4625-917b-e01e5386342d/employee-f54153fe8cb485497b8f5061b1d4a113b2fc7399113f843325723aea503588c4.parquet", "size": 20017, "sha256": "f54153fe8cb485497b8f5061b1d4a113b2fc7399113f843325723aea503588c4", "version_id": null}}], "silver_manifest": {"key": "silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/8079b128-c5b0-4625-917b-e01e5386342d/manifest-5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee.json", "size": 54418, "sha256": "5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee", "version_id": null}, "gold_snapshot_id": "a10359dd-879c-4d62-b226-82d26ee14dd8", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "8079b128-c5b0-4625-917b-e01e5386342d"}	2026-11-16 21:22:58.193257+07	2026-11-16 21:22:59.835721+07	{"key": "silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/8079b128-c5b0-4625-917b-e01e5386342d/manifest-5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee.json", "size": 54418, "sha256": "5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee", "version_id": null}
87f44121-73fe-4098-a175-06254ab5ce81	production:auto-714bd628f3e833afe92a14aa6053	1	hr	1	a1efba66-0aa4-4215-a9fc-758af2a59a0d	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 21:26:07.693458+07	\N	{"key": "gold/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/87f44121-73fe-4098-a175-06254ab5ce81/manifest-c23277358d00cb14d93eae5b4ef522a7104882c138e2ee43931c1437f139d2fa.json", "size": 2890, "sha256": "c23277358d00cb14d93eae5b4ef522a7104882c138e2ee43931c1437f139d2fa", "version_id": "9ae55f41-4052-4692-9ff5-aa3cd5bf790f"}	{"scope": "production:auto-714bd628f3e833afe92a14aa6053", "domain": "hr", "format": "unilake-gold-v1", "object": {"key": "gold/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/87f44121-73fe-4098-a175-06254ab5ce81/aggregate-be7203148e4a450e04199c2826cc17d30e450ad3bcc8fcbf4e7087a6fb2ff732.parquet", "size": 1501, "sha256": "be7203148e4a450e04199c2826cc17d30e450ad3bcc8fcbf4e7087a6fb2ff732", "version_id": "540605df-a4a9-4f9e-8ca7-2903649f4172"}, "lineage": {"mode": "production", "run_ref": "87f44121-73fe-4098-a175-06254ab5ce81", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/a1efba66-0aa4-4215-a9fc-758af2a59a0d/manifest-9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db.json", "layer": "SILVER", "domain": "hr", "version": "a1efba66-0aa4-4215-a9fc-758af2a59a0d", "asset_id": "silver:production:auto-714bd628f3e833afe92a14aa6053", "checksum": "9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/87f44121-73fe-4098-a175-06254ab5ce81/aggregate-be7203148e4a450e04199c2826cc17d30e450ad3bcc8fcbf4e7087a6fb2ff732.parquet", "layer": "GOLD", "domain": "hr", "version": "87f44121-73fe-4098-a175-06254ab5ce81", "asset_id": "gold:production:auto-714bd628f3e833afe92a14aa6053", "checksum": "be7203148e4a450e04199c2826cc17d30e450ad3bcc8fcbf4e7087a6fb2ff732", "producer_run_ref": "87f44121-73fe-4098-a175-06254ab5ce81"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 1, "generation": 1, "records_read": 100, "aggregate_rows": 3, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 100, "entity": "lecturer", "object": {"key": "silver/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/a1efba66-0aa4-4215-a9fc-758af2a59a0d/lecturer-4d691c28d205c5898452fb8a177de5c1ead3e4862cc399a67e97e663fbcec391.parquet", "size": 11389, "sha256": "4d691c28d205c5898452fb8a177de5c1ead3e4862cc399a67e97e663fbcec391", "version_id": null}}], "silver_manifest": {"key": "silver/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/a1efba66-0aa4-4215-a9fc-758af2a59a0d/manifest-9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db.json", "size": 30834, "sha256": "9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db", "version_id": null}, "gold_snapshot_id": "87f44121-73fe-4098-a175-06254ab5ce81", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "a1efba66-0aa4-4215-a9fc-758af2a59a0d"}	2026-11-16 21:26:07.693458+07	2026-11-16 21:26:08.875576+07	{"key": "silver/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/a1efba66-0aa4-4215-a9fc-758af2a59a0d/manifest-9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db.json", "size": 30834, "sha256": "9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db", "version_id": null}
59e65f53-3d4e-41aa-80a3-b577773e7945	production:auto-899188c733e26f94ba29ead9cf0e	1	hr	2	58804508-8b12-45f5-98cf-2f1f1e2506e2	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 21:26:30.127713+07	\N	{"key": "gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/59e65f53-3d4e-41aa-80a3-b577773e7945/manifest-eb70c1b7f2ecd27376850c795875bad5c2e25d85044a9da051b41a546668427e.json", "size": 2894, "sha256": "eb70c1b7f2ecd27376850c795875bad5c2e25d85044a9da051b41a546668427e", "version_id": "0a980ae4-8372-4895-b99d-2b13f66ac1b4"}	{"scope": "production:auto-899188c733e26f94ba29ead9cf0e", "domain": "hr", "format": "unilake-gold-v1", "object": {"key": "gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/59e65f53-3d4e-41aa-80a3-b577773e7945/aggregate-2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c.parquet", "size": 1567, "sha256": "2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c", "version_id": "2b8bf864-6fd2-4094-a925-b95c993734e5"}, "lineage": {"mode": "production", "run_ref": "59e65f53-3d4e-41aa-80a3-b577773e7945", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/58804508-8b12-45f5-98cf-2f1f1e2506e2/manifest-3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b.json", "layer": "SILVER", "domain": "hr", "version": "58804508-8b12-45f5-98cf-2f1f1e2506e2", "asset_id": "silver:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/59e65f53-3d4e-41aa-80a3-b577773e7945/aggregate-2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c.parquet", "layer": "GOLD", "domain": "hr", "version": "59e65f53-3d4e-41aa-80a3-b577773e7945", "asset_id": "gold:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c", "producer_run_ref": "59e65f53-3d4e-41aa-80a3-b577773e7945"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 1, "generation": 2, "records_read": 200, "aggregate_rows": 6, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 200, "entity": "assignment", "object": {"key": "silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/58804508-8b12-45f5-98cf-2f1f1e2506e2/assignment-c936553ace01b0c12024dfdedbd0fe81cc165d6b010a853de9a5a26e6f76f03f.parquet", "size": 20353, "sha256": "c936553ace01b0c12024dfdedbd0fe81cc165d6b010a853de9a5a26e6f76f03f", "version_id": null}}], "silver_manifest": {"key": "silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/58804508-8b12-45f5-98cf-2f1f1e2506e2/manifest-3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b.json", "size": 54637, "sha256": "3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b", "version_id": null}, "gold_snapshot_id": "59e65f53-3d4e-41aa-80a3-b577773e7945", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "58804508-8b12-45f5-98cf-2f1f1e2506e2"}	2026-11-16 21:26:30.127713+07	2026-11-16 21:26:31.064511+07	{"key": "silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/58804508-8b12-45f5-98cf-2f1f1e2506e2/manifest-3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b.json", "size": 54637, "sha256": "3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b", "version_id": null}
a94b1c6b-4f98-409f-8471-964aa49ee81a	production:auto-e270bc9994f1530b4a540c0daa6c	1	hr	2	3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c	group-counts-v1	9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa	SUCCEEDED	1	3	\N	\N	969358f0d4a1	2026-11-16 23:04:04.054225+07	\N	{"key": "gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a94b1c6b-4f98-409f-8471-964aa49ee81a/manifest-7752b4f803612eba69d1fb6d3dade11349583863b6a47e42d2abefa425d74211.json", "size": 2890, "sha256": "7752b4f803612eba69d1fb6d3dade11349583863b6a47e42d2abefa425d74211", "version_id": "b570633e-a5d3-401b-b6ec-9bc2a5c3d7f8"}	{"scope": "production:auto-e270bc9994f1530b4a540c0daa6c", "domain": "hr", "format": "unilake-gold-v1", "object": {"key": "gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a94b1c6b-4f98-409f-8471-964aa49ee81a/aggregate-9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec.parquet", "size": 1526, "sha256": "9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec", "version_id": "8ad9c438-0402-4e3c-a6fd-43a5adc8921c"}, "lineage": {"mode": "production", "run_ref": "a94b1c6b-4f98-409f-8471-964aa49ee81a", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c/manifest-f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb.json", "layer": "SILVER", "domain": "hr", "version": "3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c", "asset_id": "silver:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a94b1c6b-4f98-409f-8471-964aa49ee81a/aggregate-9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec.parquet", "layer": "GOLD", "domain": "hr", "version": "a94b1c6b-4f98-409f-8471-964aa49ee81a", "asset_id": "gold:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec", "producer_run_ref": "a94b1c6b-4f98-409f-8471-964aa49ee81a"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "dataset_id": 1, "generation": 2, "records_read": 200, "aggregate_rows": 5, "recipe_version": "group-counts-v1", "source_objects": [{"rows": 200, "entity": "employee", "object": {"key": "silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c/employee-d49c88344a67706419c7d83e0719c1e7121a440efdcbde6395e545377c389f91.parquet", "size": 20024, "sha256": "d49c88344a67706419c7d83e0719c1e7121a440efdcbde6395e545377c389f91", "version_id": null}}], "silver_manifest": {"key": "silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c/manifest-f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb.json", "size": 55187, "sha256": "f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb", "version_id": null}, "gold_snapshot_id": "a94b1c6b-4f98-409f-8471-964aa49ee81a", "recipe_fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa", "silver_snapshot_id": "3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c"}	2026-11-16 23:04:04.054225+07	2026-11-16 23:04:04.903641+07	{"key": "silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c/manifest-f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb.json", "size": 55187, "sha256": "f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb", "version_id": null}
\.


--
-- Data for Name: gold_record_identity; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.gold_record_identity (namespace, entity, source_key, target_key, target_fingerprint, last_gold_job_id, updated_at) FROM stdin;
35	course	{"natural":["CS101"]}	{"course_id": 1}	1d86caca2ccbdc0775954a130f3df978a5331fc463b7eb4dfb49e16774f3d2e7	9c122de6-d11c-4db4-872d-60a8970bcb3f	2026-11-16 22:50:43.94175+07
35	course	{"natural":["DB201"]}	{"course_id": 2}	d8506df83f445433e2d5c78c84f1d0527188669e0b98f130b3b8f92e57b45f7c	9c122de6-d11c-4db4-872d-60a8970bcb3f	2026-11-16 22:50:43.943309+07
35	course	{"natural":["SE301"]}	{"course_id": 3}	9016f4f9d8dfdcea2cf65263a838b40fe6ac63d0244045a0eaa81addaaf66e99	9c122de6-d11c-4db4-872d-60a8970bcb3f	2026-11-16 22:50:43.944272+07
35	course	{"natural":["MA102"]}	{"course_id": 4}	dc9c85977634cc0a187a3a8ac2ca993cfb9f133c416c4b68f8d2b786d218abad	9c122de6-d11c-4db4-872d-60a8970bcb3f	2026-11-16 22:50:43.945289+07
35	course	{"natural":["NW202"]}	{"course_id": 5}	2ff2901dc07db7fe2d32ae96132656045d9807b9e65c69ecc287394c4898d022	9c122de6-d11c-4db4-872d-60a8970bcb3f	2026-11-16 22:50:43.946302+07
34	employee	["1"]	{"employee_id": 1}	472a814a197fa04f674d764b269f7a631c8af3ed36c86ab70dc889784cdc5e37	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.942846+07
34	employee	["2"]	{"employee_id": 2}	16f68b9542d0ec2acd6ecffd9fc138fad049309f307e0ba934704a2f67168bb7	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.944593+07
34	employee	["3"]	{"employee_id": 3}	0f8fa359b92351c4d7fb5880d00f1748c15e3770b50d6f6eac5b45216a46a164	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.945341+07
34	employee	["4"]	{"employee_id": 4}	cb979b77eccaccd0e25f777eac56cee68e25824469c4b34cb7ea323e5289370f	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.94619+07
34	employee	["5"]	{"employee_id": 5}	dd81d9bd3569dfbe3273249e884b7aa69d02fd7a4c0e23338b2deb0b9331960c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.94686+07
34	employee	["6"]	{"employee_id": 6}	d762aef0c8f152ccbbd78fd436a702d975a0bfd4a3b5314bc89fcd15ff7bbae3	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.947602+07
34	employee	["7"]	{"employee_id": 7}	960f50f1de720b051dbd8d1343a1ca6a7ff3f2ca3f2ce04e4df49877d636f02d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.948322+07
34	employee	["8"]	{"employee_id": 8}	f03f96de85aceb1eeab463b293bc1b8cc0274fd829cab9ecee920259bf11000b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.948852+07
34	employee	["9"]	{"employee_id": 9}	698bd0a519ae7a290773c943ee1308fd21fda008a1dc704ef6e554ea3740f8eb	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.949354+07
34	employee	["10"]	{"employee_id": 10}	99f1f4635081f550595b37a62c8df7f9808e7f0796257e65c40ae5e4fe230dd4	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.949867+07
34	employee	["11"]	{"employee_id": 11}	d12c025a706c893ea16b1790ef315a1ee943953f039c0e46551f13cabb3bfbed	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.950413+07
34	employee	["12"]	{"employee_id": 12}	f78bc4dbf40a12c7f758ab93a33923eab51f8d735b991464110c19c1a91e6e14	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.951015+07
34	employee	["13"]	{"employee_id": 13}	13700104222f36ccf908550564e249b5f4b41cbec5415b25bad2d9f614878392	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.95162+07
34	employee	["14"]	{"employee_id": 14}	42628bb1f464da467fe9e2c520fc9101c9f463a7d7f2e12d6a1b00db6fc29fed	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.952207+07
34	employee	["15"]	{"employee_id": 15}	4484ebc99f1e5a97fbaf32a245781f288dec3d0a39e9f91e981a0e92800df2e3	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.952803+07
34	employee	["16"]	{"employee_id": 16}	ab5fdec5bbc370e60da8728d2cf727b7808509080f69f56bf1a82cac59cf228a	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.953388+07
34	employee	["17"]	{"employee_id": 17}	01ee4177f25ca897462a33b7935170a79abf66bba6ad93accaae09c24918844b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.953968+07
34	employee	["18"]	{"employee_id": 18}	d80d0feee2cd1ebf2a613f2ab76d879578afb28abdd83054e65c1dda058c7898	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.954657+07
34	employee	["19"]	{"employee_id": 19}	90ccf5642d0632113acb9b5350160f1339f08acb563ed60b34bc117d2f7eba04	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.955283+07
34	employee	["20"]	{"employee_id": 20}	56c12fd77f021aaae73a361e5206b2ce6d101310207c1784119d8766080e4961	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.95586+07
34	employee	["21"]	{"employee_id": 21}	f4132555f9431dab26dcd3777abcc7002a0fc03cf82181852ee3997346dd9033	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.95644+07
34	employee	["22"]	{"employee_id": 22}	d694cd9bcfd7a6797fdbbe39d1bce04deb5e4d0574c7c88fea3dfb8ba2fa90ec	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.957032+07
34	employee	["23"]	{"employee_id": 23}	4a7ec0c93393fdec18478748cd8bc759b98ba9cbb98f0f4832093ed711a78f5c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.957638+07
34	employee	["24"]	{"employee_id": 24}	154847a00fa9af08531e83e2bf4dec232f10f679dd369ddb8337eac27cb73738	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.958234+07
34	employee	["25"]	{"employee_id": 25}	5a13823f7ffc43a0b01f11dbf714ce466c6071e231ccf4d7ab5025f22c0eef7a	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.958881+07
34	employee	["26"]	{"employee_id": 26}	e49a50ddfe725d89af59aba4a79d4fbdab3ac7b87a75045dc79fdc72c6186ff5	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.95954+07
34	employee	["27"]	{"employee_id": 27}	76878b7506ddb8ce51de9f3d3b28e2f3d5b7cec60486569a55c9f661cec87d24	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.960222+07
34	employee	["28"]	{"employee_id": 28}	516d4791ed757b038cf57ad0ffdd2417511fa8aed76f0a8dbd80fde9f6b00a04	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.960807+07
34	employee	["29"]	{"employee_id": 29}	bce6bcc8aa54867b0c86017914d924c4e5eb6e15bc171ebe0801933746633476	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.961386+07
34	employee	["30"]	{"employee_id": 30}	9699bcac2e199e8a338ec382526ed554e6124db0274a652850d9718608bec364	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.961956+07
34	employee	["31"]	{"employee_id": 31}	e8a71228a6faa83027c825de9aab80f59c8462ac7f367a6564695d93b427c8a9	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.962544+07
34	employee	["32"]	{"employee_id": 32}	e505471d7020b49629846fcdfd64a444f7a8e253d9a30068a6df317e6a3769b7	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.963135+07
34	employee	["33"]	{"employee_id": 33}	4bf1aea485455b1b54dbb3db5d84cc835097b0c64cf30149682371f5c6329ae5	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.963719+07
34	employee	["34"]	{"employee_id": 34}	0d7f7b3a03c8afc475ca9aacd4591c8bbd6f981440bacb54e474cfe47a490e4d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.96448+07
34	employee	["35"]	{"employee_id": 35}	979668c60acfa88a33bb9de7624d0a32a5a06668db8eca8d6569d8aace216f12	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.965064+07
34	employee	["36"]	{"employee_id": 36}	042da89ea790dd16b2fad2bfd603ba1162d9b2c9d7edf6816fee2b0644044951	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.965638+07
34	employee	["37"]	{"employee_id": 37}	42a84532fb906a4b360f59177e749bf81c1f04e21957bdd66adc4950f60fc4ff	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.966214+07
34	employee	["38"]	{"employee_id": 38}	1f33444b958227e8486ec68975c140440bb378527c02f6074d4c5f9a52e9028d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.967111+07
34	employee	["39"]	{"employee_id": 39}	09460275a96e633c4ad68edb1ddfe64069deb0568f037de143f6d05918e5ee0f	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.967725+07
34	employee	["40"]	{"employee_id": 40}	36e1f53ff9682ebe404a10cd26e6b4ee7650b183505be80a46f9a9f0541d6658	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.96832+07
34	employee	["41"]	{"employee_id": 41}	1d2da1b1e19202f7651bf718d0293791eaf8fb90cf07c0589298197f02a1b215	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.968945+07
34	employee	["42"]	{"employee_id": 42}	c654a321397962c4775300bdb7cc4f0a7fe4c39b67d1cc488befa7ea4e8e21ec	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.969542+07
34	employee	["43"]	{"employee_id": 43}	fcaa92d89d7d7c586b21805a0b4c1a872f4d82be07b2f8fcd994bc082a3374eb	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.970128+07
34	employee	["44"]	{"employee_id": 44}	dc6f104dcaac59f4892102b7d62bbeb18b84db1d7a061094b1b485032b6ed57a	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.971029+07
34	employee	["45"]	{"employee_id": 45}	f89479306e65c6bb93aa815614058025a222f0bfa4bb261c3970957ee970d5eb	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.971723+07
34	employee	["46"]	{"employee_id": 46}	7448c4cefa5c76ae3f12426440cbe0302944072ab210d2d3191e79247ac30cdb	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.972516+07
34	employee	["47"]	{"employee_id": 47}	8b99e68db1b0258b20d9a9aaf068293bef3323f249c5d3d1dd41b387f496b229	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.973193+07
34	employee	["48"]	{"employee_id": 48}	7c7e844144d7240c50ef4d3176f02545fd901531014386ac12d17db312fbb998	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.973906+07
34	employee	["49"]	{"employee_id": 49}	8cf7aa1453400f649b40a310485a9dfcbbb27a1226db165a3306b960580f74cc	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.975012+07
34	employee	["50"]	{"employee_id": 50}	1de7e385dd612e548601e5711e2ac5ac03cea582465c092677ece433cd486f99	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.975661+07
34	employee	["51"]	{"employee_id": 51}	ddfc13b2be532d0cd529a805f1f1d85631d623eb7ee9c735bb7444eb6e613304	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.976293+07
34	employee	["52"]	{"employee_id": 52}	f7da98bcfaec151c4a788bc2e271dd1e5deb0e90314db00d197b02abfaa95b62	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.976896+07
34	employee	["53"]	{"employee_id": 53}	33a7e9a171f4852de09d62ed7d14fd9d5c96200700a2a4efee22eec6334e9362	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.977496+07
34	employee	["54"]	{"employee_id": 54}	571b15947482b16bff0c2e1aee59500b6da79b17cae867e932488da876a533b5	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.978103+07
34	employee	["55"]	{"employee_id": 55}	b14fa33602c14473d159c92442e206e12180e9aba003f10643efc68144cef496	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.978688+07
34	employee	["56"]	{"employee_id": 56}	1b122344bd8178b3ec97e8f609cd176020151089705888cbdd3cc44a846f7699	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.979266+07
34	employee	["57"]	{"employee_id": 57}	3c10925a1ec09158795da884e671bdc6aea0cae44c14a784ed36128de7a41caa	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.979883+07
34	employee	["58"]	{"employee_id": 58}	2959df1ebd6f2f8d9aafa2a5733e63437652e2dcb8aeffc1eea42f77f57375b7	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.98053+07
34	employee	["59"]	{"employee_id": 59}	c9bfcbf01c761f6654b27454d9d74709f7168e37489b0368412f7582f1b9c9e3	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.981164+07
34	employee	["60"]	{"employee_id": 60}	d37ee71a5ca90abfa5ad51e567623736fadae75d6b7456cbac3e24bdbe827ef1	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.981783+07
34	employee	["61"]	{"employee_id": 61}	926d727ad792d8ce2dde02331708ed37941253595d6165f7d11ce0e5be9b6a64	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.982389+07
34	employee	["62"]	{"employee_id": 62}	a934a0c8bdbb6f3dcccb5e0730a4928c5d3d4a4c0e707413a14a945be2d41642	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.982993+07
34	employee	["63"]	{"employee_id": 63}	04ebc05b8ab291a0058bde8a1dfaba685d7fb2bfc1f0cb78cec59d87b0fac08d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.983599+07
34	employee	["64"]	{"employee_id": 64}	f957f9ffcbbe34b895f9d5c53a6720b52e0846981f5c2cb0c7796f1d7e80ce4d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.984208+07
34	employee	["65"]	{"employee_id": 65}	43e4e613e8387ce5dfaef6ea4c34103d8d7564595c1825fe0af9a95b9ba24f0f	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.984822+07
34	employee	["66"]	{"employee_id": 66}	fb38b6835d45f0524ee977345c206f4879fd91eb99e0ca6c89f9249ca782da48	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.985392+07
34	employee	["67"]	{"employee_id": 67}	6971a1e6635b9cd44fa924634a6ef8ef47febc82920e44b5ea29d480b3eb909d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.98597+07
34	employee	["68"]	{"employee_id": 68}	0a5640b71817f3c11bf377f69af87bb0ee913547b618a9af099ac5b12d99babf	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.98658+07
34	employee	["69"]	{"employee_id": 69}	f3125c3aa6d63242c4156afbf26c4ff95e38f3e61bc32e1cb9749f0337ebc5d8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.987169+07
34	employee	["70"]	{"employee_id": 70}	d02b5f0ff5bf8e62283142d2e7cabff54debce80fdb9942dd47361547d0efdba	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.987763+07
34	employee	["71"]	{"employee_id": 71}	f9f2838f73fa9d31aad4c81c07f0f1a827a194f96ae148cf022eeb79ffe20dd0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.988342+07
34	employee	["72"]	{"employee_id": 72}	ee7b9e86278d8adcebcae8e27bdc3ad1780ca757a4532c071ca68e34dcc3d4b8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.988987+07
34	employee	["73"]	{"employee_id": 73}	285289121a15dd06c9793bf71e2a553ab99c35581ae2416373f2f9502ba1f888	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.989612+07
34	employee	["74"]	{"employee_id": 74}	b205a8afa05eaa420797686a44c5cf059f52706ad0ac4db0dac60b519ff2b704	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.990172+07
34	employee	["75"]	{"employee_id": 75}	6c8d479cd829539465811e279c3d81b494c7457cec9cafa849559baefe7ad8cf	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.990719+07
34	employee	["76"]	{"employee_id": 76}	814d77f3d99935c453d6d4e7cde4824d84f6771dcab565fb0a19444be6f4ff66	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.991166+07
34	employee	["77"]	{"employee_id": 77}	61119f6c3f51ac374f759e7432f39a187f8307043eed2166ddb2a4014ea66fc2	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.991632+07
34	employee	["78"]	{"employee_id": 78}	beb4c41a37d5281a17fcaf04c841134a817b55b69cac18f9eeeab84655accd7c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.992164+07
34	employee	["79"]	{"employee_id": 79}	72b1cb7cca5ce616f9670f3993fd030b4e345ea1cd1f60815e689c387b76429d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.992642+07
34	employee	["80"]	{"employee_id": 80}	fa864a993bcd67543c2d1ca105a8621ffc3b202b63ef119a41273a9b991d5c31	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.993153+07
34	employee	["81"]	{"employee_id": 81}	baff324708a0d52f91ab51cdf6837b4e8d9f5bf8daed0f39524418636fda184b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.993626+07
34	employee	["82"]	{"employee_id": 82}	cac451da25313cd710c4d1e66099d4bcc6af983e68a0a33b976cf0f7ed5c36a6	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.994217+07
34	employee	["83"]	{"employee_id": 83}	161b41b14d46e602c4c84139387fff181b7086a2fa837b3b475e71321941ace9	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.994666+07
34	employee	["84"]	{"employee_id": 84}	f30005d80c6d967d71c96e4c0a0b295ecfd6fc450ceb6194a86bdfab66575443	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.995108+07
34	employee	["85"]	{"employee_id": 85}	76bd226bdac0e6c4104a191abf5fb04a84a994fe93d2e42354358a30d8e922ba	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.99561+07
34	employee	["86"]	{"employee_id": 86}	bace015c09b652ef3c0fdb16febd826967a273ac0eaecf2fa4b2d371d83a0ed7	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.996066+07
34	employee	["87"]	{"employee_id": 87}	6fe2f720a28787de474bf0a5ebbfb11a7b3094d15c1842ed6044823b413b33e0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.996518+07
34	employee	["88"]	{"employee_id": 88}	9756df8d8e11aee0db82e931a24c599fe40605015c23f196f03f2330873570b1	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.997125+07
34	employee	["89"]	{"employee_id": 89}	9b95bde0a1d03f80f4aebb09be16554df50932fb1fcee36e2cbc0111abe0ae3c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.997646+07
34	employee	["90"]	{"employee_id": 90}	9bccac2399d4093fc1e5198ff87f6c9e13c03c7299f31fd2e4a8acc9dde38ce8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.998192+07
34	employee	["91"]	{"employee_id": 91}	f50218c2363a44fa0296eb53882663f42f823ede1d0bb26745cd83c47b49d8d4	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.998782+07
34	employee	["92"]	{"employee_id": 92}	7da897075d4b47004b842b6092fcdbb44a783d71d857a54c4bd642a173851f23	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.999381+07
34	employee	["93"]	{"employee_id": 93}	5ce41d438f19412d769763e2d3c9286e6af648bbce00292ae2d6edb6262ac4d0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.999977+07
34	employee	["94"]	{"employee_id": 94}	14aa50cf4bfe420438c84f0f1713ead5ef6e0b95c410a116c40ef8431e1ac759	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.000639+07
34	employee	["95"]	{"employee_id": 95}	476131e55194261b4ec1e719cb54cb8240b71934b6b51f81d4db8e35195b8985	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.001234+07
34	employee	["96"]	{"employee_id": 96}	39454d99a7fd3e9ee19c3f9a9184c4a0b6ea5443ff6bae50e1ea252f6766c1ac	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.001853+07
34	employee	["97"]	{"employee_id": 97}	e1922dc55662c5f67264d3af20bdde89a722efcbab164b810d4d8109d30fd028	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.00249+07
34	employee	["98"]	{"employee_id": 98}	0968644b21bfe5b872e86a1e7f13f8f134f31ba5733d9de8f29529c605a05752	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.003103+07
34	employee	["99"]	{"employee_id": 99}	e1df9354c367ed8050543ea3af24c1720aae1e227daeebcd4918bf91ab443638	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.0038+07
34	employee	["100"]	{"employee_id": 100}	e9746b45d6e0fa2afc9a2449b959633cd0f766ebb5364d6471b6ce1adde9e040	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.004487+07
34	employee	["101"]	{"employee_id": 101}	8091399358659590c64a225f281b9462ab7e35a0a6e3ade6dee84f0dbeb2edbc	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.00509+07
34	employee	["102"]	{"employee_id": 102}	f09404e3e0a8b805d703281d5dcd80052fcd06f8b647e2990cc406b95c80f329	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.00569+07
34	employee	["103"]	{"employee_id": 103}	a421a25536e7403fdf27e2403bb96268a46607d8b068ceeed3741a6a42a31819	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.006292+07
34	employee	["104"]	{"employee_id": 104}	49ed3d57c0971d73f3eace0e7292623b8f22f9dc84b6ac3223f12d299ad76307	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.006903+07
34	employee	["105"]	{"employee_id": 105}	0c69fafc395daf9ed9d8b777df6e660ea6d1dddd89e38c3b6c0217cb1ad841d0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.007498+07
34	employee	["106"]	{"employee_id": 106}	61654e2bbb35d8682a3fa21223addd145d487ae4e442dbaab5428b8ec49fbc90	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.008092+07
34	employee	["107"]	{"employee_id": 107}	5e24d05e6e6f7b726f7d56ed7ae0717895e9d646d4d4f77aeb7837e614ca9948	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.008672+07
34	employee	["108"]	{"employee_id": 108}	cc336081a895170d530abe26a6197430b336e34a2ce4dfe1cbea65e57ccf55cc	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.009248+07
34	employee	["109"]	{"employee_id": 109}	51b76ad2cfc4fc3abfc2eb550f4ec9151eb2d93834046737604a7508e6e3cfff	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.009831+07
34	employee	["110"]	{"employee_id": 110}	370167afcb22a9dbc13b6092d22f7f52310b741cd2a991f3d7a961fe70099ed5	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.010485+07
34	employee	["111"]	{"employee_id": 111}	cb7b6dd592d54ec902a925369e8bbddda14c524c8c26183490f35c23362fe2bc	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.011078+07
34	employee	["112"]	{"employee_id": 112}	26eb2047fa7c0fb0fb792f6500ac8c3b5462b6b61e2f2c7bc00dd510ba13e59a	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.011682+07
34	employee	["113"]	{"employee_id": 113}	4f2b59cc3e0473f4c91e6be32dd65a5e793045440a65379f4cac404a240d783f	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.012269+07
34	employee	["114"]	{"employee_id": 114}	c56c41fe6812e45414fa815b0ca2104ff407295cf83bd05fa5d600b630c73077	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.012867+07
34	employee	["115"]	{"employee_id": 115}	3e17517dcff61dba1a474eddcb431020054ffdad8edd49e4b6e958a1bf03efe0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.013473+07
34	employee	["116"]	{"employee_id": 116}	914bdea275d63bb1144e9efc54c5328ec78cf132217f2151112094522083ccdb	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.014136+07
34	employee	["117"]	{"employee_id": 117}	b8388871d2d46d6ee2133ec2eb286612fdb83f3d07d29aec474793e2cef80f12	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.014767+07
34	employee	["118"]	{"employee_id": 118}	5594b433d17a3f54a8a01f0fca1eb2f7aa9691539eb0178fa7d835585bdbd01b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.015374+07
34	employee	["119"]	{"employee_id": 119}	94634ad4b4581737da42198b21b49bf91056308a079e49f722acb653af8f4e41	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.015995+07
34	employee	["120"]	{"employee_id": 120}	e5d8d8d222de168105934e7d5b78cc27cfb47541ee7cc6231ba6bf4f7010f1b3	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.016615+07
34	employee	["121"]	{"employee_id": 121}	c1193414691e7cd348dde7cb540d90e505b600909b5ba6d829d173e2215f6a72	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.017222+07
34	employee	["122"]	{"employee_id": 122}	69e5b396794b2de03cfa649d3075f4142635648d3ed9f8f483c3a4bfd2dd1599	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.017816+07
34	employee	["123"]	{"employee_id": 123}	f90dd153c1fc195cbd298ab0e09b693d58b366bf4702b6d7a2e7a7403e6dd2c6	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.018465+07
34	employee	["124"]	{"employee_id": 124}	4f552d36032eab9491b6522e5190b7dad33f5b75d51f6177cc0b46764dd113e3	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.019074+07
34	employee	["125"]	{"employee_id": 125}	87307dc7fb07937067b565c71be22f98bd21f68e01f45cdc03146eda3400a74e	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.019687+07
34	employee	["126"]	{"employee_id": 126}	40779a0e21bffb298d76bf6ca3b0ad77e2cecbc5923ee21ff9a68a721a2416a6	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.020297+07
34	employee	["127"]	{"employee_id": 127}	bbac227ed35df2b1bea20708e8455b31c2e1d3d31be26fe470ac0c346485c9b6	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.020936+07
34	employee	["128"]	{"employee_id": 128}	c6032871ecdea3620b36095dce2b9a3c124ce8e00f8e6dfe8e12aa4f5f43a148	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.021543+07
34	employee	["129"]	{"employee_id": 129}	28f1e124dd1cfe064440fc833b18af802b011e095187d98c6573a714fbdbc65f	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.022124+07
34	employee	["130"]	{"employee_id": 130}	9816c0beba10a5f30559852f5e59a38341fcab89c742f6ff36fa6615292b4f38	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.022745+07
34	employee	["131"]	{"employee_id": 131}	940fb6b684d59726e1f8ac48a8aa31128785303c32f36c985fb076a0a0e1ba58	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.023365+07
34	employee	["132"]	{"employee_id": 132}	27a2708977caa3cbfde10e1b49c9d5b262ced53a91dc865593e6ac19c818e630	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.024016+07
34	employee	["133"]	{"employee_id": 133}	75d18d520230d799a41e586791b425b4147e4da4cbcae4b624ac2e1f460c863e	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.024633+07
34	employee	["134"]	{"employee_id": 134}	f103ee88637cecca01bd399d74fa0b86ca433d288f43c6ce1d98ae2a2f4c86c7	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.025237+07
34	employee	["135"]	{"employee_id": 135}	f7da21a7e2a56f82482237354317c4b271fb9805e107288e633ddf5b14ec20bd	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.025849+07
34	employee	["136"]	{"employee_id": 136}	3f52f96013d186bb60b6a669d8f810aafb9ebf904b8a7818eb98884162a6e356	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.026461+07
34	employee	["137"]	{"employee_id": 137}	84b3a422ea7275b743ba17c934ed80994c0d6b115dba3125912ed802cdf4a537	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.027074+07
34	employee	["138"]	{"employee_id": 138}	9c1a310fb4835eac0266c330328682032418af03de87f374dda8fbc7ac1509a0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.027679+07
34	employee	["139"]	{"employee_id": 139}	a970536e8e5afc67aad737912db972335545f52e37f9c87612d2b94330fbc387	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.028282+07
34	employee	["140"]	{"employee_id": 140}	d9b862e72399d7301d0c5dea136dbf294d12a56f5f0229f2f0e81616976248ef	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.028889+07
34	employee	["141"]	{"employee_id": 141}	9615f4a3f813718123edde857b90194a6c876d7f5d0254e61f575aab8950afef	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.029522+07
34	employee	["142"]	{"employee_id": 142}	659e745e0df3fcff3938cec47819745dae8cdd36832987dbbfc2aa8e39de87a3	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.030127+07
34	employee	["143"]	{"employee_id": 143}	7eb701a371488660f17e0d3d3093143eb9572be0f5b31b4f23740b8cfd665571	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.030741+07
34	employee	["144"]	{"employee_id": 144}	28b9703b0918a6e19ee0f8cb27e28201f2c54e85cf693cbb48581cf11dfa1672	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.031351+07
34	employee	["145"]	{"employee_id": 145}	6f0b8e89b73801130e98deef25413c8ce702db076530711a6b467652b7ab89f1	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.031953+07
34	employee	["146"]	{"employee_id": 146}	e02279a75ff661b4ff6d144263bf33ce5275ba39ee447ce83418cefbfedc21ba	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.032572+07
34	employee	["147"]	{"employee_id": 147}	8f5cdcfb8e0133240f6ee81f1c19e7de57c70b6077a48f52f04755d06a9bbbd6	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.033186+07
34	employee	["148"]	{"employee_id": 148}	373c08f4b68804129265950e3b22ab35522a8de333d24ef7d1dfd41cdb865058	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.033803+07
34	employee	["149"]	{"employee_id": 149}	a58185af770b08d3076168e38c3cb19205512ef3cfbd5e3726f1104aa4406699	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.034457+07
34	employee	["150"]	{"employee_id": 150}	09758af7382e4a19b3f343a5eeb6f6533ccaebaa46510992116913a0f97fe697	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.035084+07
34	employee	["151"]	{"employee_id": 151}	9ae17dc4eb0727ce2c4996b19ac77c7a4ed714ddb580012c5d6c38f7da2dd84b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.035726+07
34	employee	["152"]	{"employee_id": 152}	edcb142a153d51780b1b0701a0691fe7f6760129ec8c7df36a01d2030096a685	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.036343+07
34	employee	["153"]	{"employee_id": 153}	83b1b3091fe0e76457a31ed5d3cb23ea6c699a549fe0342fafd309d1a98d44eb	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.036946+07
34	employee	["154"]	{"employee_id": 154}	a2a9465441e0e4c1a6e31c74cfe5f5c84e8fbeef5aa63676c21a030c5b0bbb95	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.03758+07
34	employee	["155"]	{"employee_id": 155}	4ae40b7280cdf33eb373141aafdfc2a03d15298048e4d07219d41ad6938011aa	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.038209+07
34	employee	["156"]	{"employee_id": 156}	0d9bb72e6cf374647d9f20a6f0bf947d40b59c2a74f47b6179c0b021e8b479a4	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.038844+07
34	employee	["157"]	{"employee_id": 157}	d0f177d05548f6267080231ef899b12c6cbc65f52f26e2e3e92fef589f533149	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.039496+07
34	employee	["158"]	{"employee_id": 158}	4727178f67fc91ffae29713c75f52b823ec3a2f926adf78027ddb134721b7086	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.040096+07
34	employee	["159"]	{"employee_id": 159}	899ff3f161660eb643600b9fa961ddb6ca91174436a0ffd9b94cbea4bd3a5bb9	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.040759+07
34	employee	["160"]	{"employee_id": 160}	799d1c13e44a9195a4efd7e310a31ef6758bc634b7ef6edabdecc82f704ce4d8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.041344+07
34	employee	["161"]	{"employee_id": 161}	04604a6d156e075c1e354a6946031786148360a59db43e4736f2fc91e299e8cf	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.041916+07
34	employee	["162"]	{"employee_id": 162}	dbbfe4c09ffa74fbd850bc517db038d2cd0146208ce74b11409e6784a8283070	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.042526+07
34	employee	["163"]	{"employee_id": 163}	5a570f70f2ca0e8ef781d0cfd24b058c21d1476420d2d038ad955b5d07b5b87c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.04312+07
34	employee	["164"]	{"employee_id": 164}	fa9f3e944d3ef5e1e304605ebd1fc0e6b9476987f8fe77885fece0f683f2d506	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.043734+07
34	employee	["165"]	{"employee_id": 165}	167a201b5923245801791cc594edd26348dfd447e3b17e55cb8e8668b9c409c1	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.044332+07
34	employee	["166"]	{"employee_id": 166}	fc9046d5d4c6a8826776c3678a40ee1d2370bde3f975fb765e137504bb5903f9	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.044924+07
34	employee	["167"]	{"employee_id": 167}	a9bc2f981b748179dde869cf0a148f87c42a35e0f80a3a76ae6fe37298e09c3a	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.045561+07
34	employee	["168"]	{"employee_id": 168}	c0cc4ff53e41944a14c200a7e4b5ca0727ae82e5365f09a9d554480beb6193a8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.04616+07
34	employee	["169"]	{"employee_id": 169}	9ef63aedbebf0ba26643dc6335699d8050e5ac43f0212843a52acba2c0fa54b9	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.046768+07
34	employee	["170"]	{"employee_id": 170}	686a1d8e497049f606e2a0946a126a503ef5d1eea9d7941bbf9dae4b0c478bda	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.047392+07
34	employee	["171"]	{"employee_id": 171}	df077ab1dd7070d680f2f47df1c1f39f14907aba8d4deddf2f1db9afa8bba4f5	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.048013+07
34	employee	["172"]	{"employee_id": 172}	ca3d594e0118519b59b5b0ec00bdd15fc1c4ba2cc12a1ef3ea68726b437e5de4	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.048622+07
34	employee	["173"]	{"employee_id": 173}	845ea07aa209427d32cec38847532cb121666aa66323b1a567fcf041b551df62	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.049242+07
34	employee	["174"]	{"employee_id": 174}	d8cfdc34e84d8f03e65564f9f89fd7692adde1c63e8ecc2148c6a7f9f6c0126b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.049847+07
34	employee	["175"]	{"employee_id": 175}	05a51f636707f2ccc456b27ee8ec67780737c5d49489136a7e7799942c0963ee	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.050476+07
34	employee	["176"]	{"employee_id": 176}	de76e04c587cf568c566eee653b020cab7993e76680e34ea7b47afb319a472e8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.051067+07
34	employee	["177"]	{"employee_id": 177}	d785cdd4d69cef67841e2547401fd35189114fe57c633b6ee60658070f54bf5b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.051718+07
34	employee	["178"]	{"employee_id": 178}	b2c98864b4eb46382d745c2415e6037ca8067b3c858a073cd7a9234a7a02597d	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.05231+07
34	employee	["179"]	{"employee_id": 179}	68419be6d8a24c4c095c40652f51d05223161a5949d36a61cb59a5329f449c1c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.052915+07
34	employee	["180"]	{"employee_id": 180}	e92ea027d39c2a78f8e6e360ff20804be9fa5cb6ab8cf5fac73a838891dee52e	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.053538+07
34	employee	["181"]	{"employee_id": 181}	458b20497748312e127bc745f14907f89ed637168b88fed37bb29262002c2e7c	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.054149+07
34	employee	["182"]	{"employee_id": 182}	5e13beb3c077293cc3635e5d7a3392eb198431858cc91f883916acaa6c5e04ef	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.054752+07
34	employee	["183"]	{"employee_id": 183}	50677828401a9e489188687f2c2aa6f5f63167fcde0846acbdfff4878b766f25	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.055329+07
34	employee	["184"]	{"employee_id": 184}	c0aa2f9fe91fb25bd1d1443945c0578734aafa7e863ae58be112af69dde316ec	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.055948+07
34	employee	["185"]	{"employee_id": 185}	5eb3f8cc2eafb5265b28930d6a1790cf9980454f59e7527a906b007afe4148a0	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.056538+07
34	employee	["186"]	{"employee_id": 186}	b704fb75f9eb5965c074a04d628cbe7099d40cc0a05e59bfdf6df5b34ad35f1b	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.05714+07
34	employee	["187"]	{"employee_id": 187}	3f1d4ac49162dee0dbcbca73755ecf223d3210c64d6fa9f0f08a9eaaf943f4bf	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.057745+07
34	employee	["188"]	{"employee_id": 188}	772d1e9e160657cf56364efa68a20b7d9ecc43c9c7bb9e784777d35a94d86654	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.058342+07
34	employee	["189"]	{"employee_id": 189}	c4825e52c4d01fd49b197595e77508d7e57780e36e57cf4c9450933991c6ffce	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.058936+07
34	employee	["190"]	{"employee_id": 190}	a3c95943448e800cb36762cd5b16e70bc15ff59e83b41d743b7ae200c48ca308	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.059539+07
34	employee	["191"]	{"employee_id": 191}	2245537c0e221249f6bb26046bdd6e993997decec48018fd144b10a8fa060eff	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.060145+07
34	employee	["192"]	{"employee_id": 192}	25db704718b11d6f5630e68738f4037a03ea619e558f7f41c331d69c1bdf742e	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.060748+07
34	employee	["193"]	{"employee_id": 193}	3206b286777745288044c5b4f5e7a7828998873663f4be45f69fede3f0d80b4e	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.061374+07
34	employee	["194"]	{"employee_id": 194}	346c7e08c20c6b35746afe413c1f26c18a03bb66a4ec0ac4237faaa606438164	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.061997+07
34	employee	["195"]	{"employee_id": 195}	988690068d0a200fa47b77fc09483abad1cd290465a939ad47237ac54272e5b8	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.062612+07
34	employee	["196"]	{"employee_id": 196}	5bb992e82d06a8cc42b78e29adc9b38fc9b99b484b4cb3d49f9ea32c3d65b9b1	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.063234+07
34	employee	["197"]	{"employee_id": 197}	9d232cbc0aadad5143bd44d4f5e83b0004699dee4e6243e451858bbf26418b32	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.063883+07
34	employee	["198"]	{"employee_id": 198}	0ff181af31bc8c0c08a712bcf9143388a4f897c14e73d24ddaab792f1f73ef7e	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.064516+07
34	employee	["199"]	{"employee_id": 199}	5f7ff842e2238704df334c80aaa0359ad677c4facfd4c0e2c842ec574e52e938	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.065136+07
34	employee	["200"]	{"employee_id": 200}	5dfc11e208aa634911d79d9379a4aa261acc592746e476ec7fc63c5da2ddfe83	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:05.06576+07
34	lecturer	["2"]	{"employee_id": 2}	16f68b9542d0ec2acd6ecffd9fc138fad049309f307e0ba934704a2f67168bb7	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.286096+07
34	lecturer	["4"]	{"employee_id": 4}	cb979b77eccaccd0e25f777eac56cee68e25824469c4b34cb7ea323e5289370f	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.287764+07
34	lecturer	["6"]	{"employee_id": 6}	d762aef0c8f152ccbbd78fd436a702d975a0bfd4a3b5314bc89fcd15ff7bbae3	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.289195+07
34	lecturer	["8"]	{"employee_id": 8}	f03f96de85aceb1eeab463b293bc1b8cc0274fd829cab9ecee920259bf11000b	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.290675+07
34	lecturer	["10"]	{"employee_id": 10}	99f1f4635081f550595b37a62c8df7f9808e7f0796257e65c40ae5e4fe230dd4	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.292238+07
34	lecturer	["12"]	{"employee_id": 12}	f78bc4dbf40a12c7f758ab93a33923eab51f8d735b991464110c19c1a91e6e14	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.29392+07
34	lecturer	["14"]	{"employee_id": 14}	42628bb1f464da467fe9e2c520fc9101c9f463a7d7f2e12d6a1b00db6fc29fed	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.295137+07
34	lecturer	["16"]	{"employee_id": 16}	ab5fdec5bbc370e60da8728d2cf727b7808509080f69f56bf1a82cac59cf228a	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.296261+07
34	lecturer	["18"]	{"employee_id": 18}	d80d0feee2cd1ebf2a613f2ab76d879578afb28abdd83054e65c1dda058c7898	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.297682+07
34	lecturer	["20"]	{"employee_id": 20}	56c12fd77f021aaae73a361e5206b2ce6d101310207c1784119d8766080e4961	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.298974+07
34	lecturer	["22"]	{"employee_id": 22}	d694cd9bcfd7a6797fdbbe39d1bce04deb5e4d0574c7c88fea3dfb8ba2fa90ec	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.300217+07
34	lecturer	["24"]	{"employee_id": 24}	154847a00fa9af08531e83e2bf4dec232f10f679dd369ddb8337eac27cb73738	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.301278+07
34	lecturer	["26"]	{"employee_id": 26}	e49a50ddfe725d89af59aba4a79d4fbdab3ac7b87a75045dc79fdc72c6186ff5	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.302402+07
34	lecturer	["28"]	{"employee_id": 28}	516d4791ed757b038cf57ad0ffdd2417511fa8aed76f0a8dbd80fde9f6b00a04	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.303431+07
34	lecturer	["30"]	{"employee_id": 30}	9699bcac2e199e8a338ec382526ed554e6124db0274a652850d9718608bec364	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.304625+07
34	lecturer	["32"]	{"employee_id": 32}	e505471d7020b49629846fcdfd64a444f7a8e253d9a30068a6df317e6a3769b7	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.305775+07
34	lecturer	["34"]	{"employee_id": 34}	0d7f7b3a03c8afc475ca9aacd4591c8bbd6f981440bacb54e474cfe47a490e4d	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.30667+07
34	lecturer	["36"]	{"employee_id": 36}	042da89ea790dd16b2fad2bfd603ba1162d9b2c9d7edf6816fee2b0644044951	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.307828+07
34	lecturer	["38"]	{"employee_id": 38}	1f33444b958227e8486ec68975c140440bb378527c02f6074d4c5f9a52e9028d	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.308816+07
34	lecturer	["40"]	{"employee_id": 40}	36e1f53ff9682ebe404a10cd26e6b4ee7650b183505be80a46f9a9f0541d6658	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.309804+07
34	lecturer	["42"]	{"employee_id": 42}	c654a321397962c4775300bdb7cc4f0a7fe4c39b67d1cc488befa7ea4e8e21ec	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.310735+07
34	lecturer	["44"]	{"employee_id": 44}	dc6f104dcaac59f4892102b7d62bbeb18b84db1d7a061094b1b485032b6ed57a	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.31196+07
34	lecturer	["46"]	{"employee_id": 46}	7448c4cefa5c76ae3f12426440cbe0302944072ab210d2d3191e79247ac30cdb	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.31289+07
34	lecturer	["48"]	{"employee_id": 48}	7c7e844144d7240c50ef4d3176f02545fd901531014386ac12d17db312fbb998	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.313839+07
34	lecturer	["50"]	{"employee_id": 50}	1de7e385dd612e548601e5711e2ac5ac03cea582465c092677ece433cd486f99	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.31471+07
34	lecturer	["52"]	{"employee_id": 52}	f7da98bcfaec151c4a788bc2e271dd1e5deb0e90314db00d197b02abfaa95b62	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.315588+07
34	lecturer	["54"]	{"employee_id": 54}	571b15947482b16bff0c2e1aee59500b6da79b17cae867e932488da876a533b5	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.31643+07
34	lecturer	["56"]	{"employee_id": 56}	1b122344bd8178b3ec97e8f609cd176020151089705888cbdd3cc44a846f7699	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.317286+07
34	lecturer	["58"]	{"employee_id": 58}	2959df1ebd6f2f8d9aafa2a5733e63437652e2dcb8aeffc1eea42f77f57375b7	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.318202+07
34	lecturer	["60"]	{"employee_id": 60}	d37ee71a5ca90abfa5ad51e567623736fadae75d6b7456cbac3e24bdbe827ef1	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.31907+07
34	lecturer	["62"]	{"employee_id": 62}	a934a0c8bdbb6f3dcccb5e0730a4928c5d3d4a4c0e707413a14a945be2d41642	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.319935+07
34	lecturer	["64"]	{"employee_id": 64}	f957f9ffcbbe34b895f9d5c53a6720b52e0846981f5c2cb0c7796f1d7e80ce4d	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.32075+07
34	lecturer	["66"]	{"employee_id": 66}	fb38b6835d45f0524ee977345c206f4879fd91eb99e0ca6c89f9249ca782da48	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.321606+07
34	lecturer	["68"]	{"employee_id": 68}	0a5640b71817f3c11bf377f69af87bb0ee913547b618a9af099ac5b12d99babf	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.322429+07
34	lecturer	["70"]	{"employee_id": 70}	d02b5f0ff5bf8e62283142d2e7cabff54debce80fdb9942dd47361547d0efdba	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.323256+07
34	lecturer	["72"]	{"employee_id": 72}	ee7b9e86278d8adcebcae8e27bdc3ad1780ca757a4532c071ca68e34dcc3d4b8	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.32406+07
34	lecturer	["74"]	{"employee_id": 74}	b205a8afa05eaa420797686a44c5cf059f52706ad0ac4db0dac60b519ff2b704	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.324865+07
34	lecturer	["76"]	{"employee_id": 76}	814d77f3d99935c453d6d4e7cde4824d84f6771dcab565fb0a19444be6f4ff66	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.325654+07
34	lecturer	["78"]	{"employee_id": 78}	beb4c41a37d5281a17fcaf04c841134a817b55b69cac18f9eeeab84655accd7c	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.326478+07
34	lecturer	["80"]	{"employee_id": 80}	fa864a993bcd67543c2d1ca105a8621ffc3b202b63ef119a41273a9b991d5c31	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.327242+07
34	lecturer	["82"]	{"employee_id": 82}	cac451da25313cd710c4d1e66099d4bcc6af983e68a0a33b976cf0f7ed5c36a6	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.328023+07
34	lecturer	["84"]	{"employee_id": 84}	f30005d80c6d967d71c96e4c0a0b295ecfd6fc450ceb6194a86bdfab66575443	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.328901+07
34	lecturer	["86"]	{"employee_id": 86}	bace015c09b652ef3c0fdb16febd826967a273ac0eaecf2fa4b2d371d83a0ed7	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.329732+07
34	lecturer	["88"]	{"employee_id": 88}	9756df8d8e11aee0db82e931a24c599fe40605015c23f196f03f2330873570b1	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.330499+07
34	lecturer	["90"]	{"employee_id": 90}	9bccac2399d4093fc1e5198ff87f6c9e13c03c7299f31fd2e4a8acc9dde38ce8	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.33128+07
34	lecturer	["92"]	{"employee_id": 92}	7da897075d4b47004b842b6092fcdbb44a783d71d857a54c4bd642a173851f23	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.332026+07
34	lecturer	["94"]	{"employee_id": 94}	14aa50cf4bfe420438c84f0f1713ead5ef6e0b95c410a116c40ef8431e1ac759	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.332767+07
34	lecturer	["96"]	{"employee_id": 96}	39454d99a7fd3e9ee19c3f9a9184c4a0b6ea5443ff6bae50e1ea252f6766c1ac	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.333526+07
34	lecturer	["98"]	{"employee_id": 98}	0968644b21bfe5b872e86a1e7f13f8f134f31ba5733d9de8f29529c605a05752	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.334287+07
34	lecturer	["100"]	{"employee_id": 100}	e9746b45d6e0fa2afc9a2449b959633cd0f766ebb5364d6471b6ce1adde9e040	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.335046+07
34	lecturer	["102"]	{"employee_id": 102}	f09404e3e0a8b805d703281d5dcd80052fcd06f8b647e2990cc406b95c80f329	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.335842+07
34	lecturer	["104"]	{"employee_id": 104}	49ed3d57c0971d73f3eace0e7292623b8f22f9dc84b6ac3223f12d299ad76307	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.336588+07
34	lecturer	["106"]	{"employee_id": 106}	61654e2bbb35d8682a3fa21223addd145d487ae4e442dbaab5428b8ec49fbc90	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.337355+07
34	lecturer	["108"]	{"employee_id": 108}	cc336081a895170d530abe26a6197430b336e34a2ce4dfe1cbea65e57ccf55cc	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.338108+07
34	lecturer	["110"]	{"employee_id": 110}	370167afcb22a9dbc13b6092d22f7f52310b741cd2a991f3d7a961fe70099ed5	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.338953+07
34	lecturer	["112"]	{"employee_id": 112}	26eb2047fa7c0fb0fb792f6500ac8c3b5462b6b61e2f2c7bc00dd510ba13e59a	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.339754+07
34	lecturer	["114"]	{"employee_id": 114}	c56c41fe6812e45414fa815b0ca2104ff407295cf83bd05fa5d600b630c73077	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.340561+07
34	lecturer	["116"]	{"employee_id": 116}	914bdea275d63bb1144e9efc54c5328ec78cf132217f2151112094522083ccdb	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.341295+07
34	lecturer	["118"]	{"employee_id": 118}	5594b433d17a3f54a8a01f0fca1eb2f7aa9691539eb0178fa7d835585bdbd01b	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.342089+07
34	lecturer	["120"]	{"employee_id": 120}	e5d8d8d222de168105934e7d5b78cc27cfb47541ee7cc6231ba6bf4f7010f1b3	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.342851+07
34	lecturer	["122"]	{"employee_id": 122}	69e5b396794b2de03cfa649d3075f4142635648d3ed9f8f483c3a4bfd2dd1599	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.34355+07
34	lecturer	["124"]	{"employee_id": 124}	4f552d36032eab9491b6522e5190b7dad33f5b75d51f6177cc0b46764dd113e3	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.344247+07
34	lecturer	["126"]	{"employee_id": 126}	40779a0e21bffb298d76bf6ca3b0ad77e2cecbc5923ee21ff9a68a721a2416a6	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.344998+07
34	lecturer	["128"]	{"employee_id": 128}	c6032871ecdea3620b36095dce2b9a3c124ce8e00f8e6dfe8e12aa4f5f43a148	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.345721+07
34	lecturer	["130"]	{"employee_id": 130}	9816c0beba10a5f30559852f5e59a38341fcab89c742f6ff36fa6615292b4f38	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.346425+07
34	lecturer	["132"]	{"employee_id": 132}	27a2708977caa3cbfde10e1b49c9d5b262ced53a91dc865593e6ac19c818e630	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.347153+07
34	lecturer	["134"]	{"employee_id": 134}	f103ee88637cecca01bd399d74fa0b86ca433d288f43c6ce1d98ae2a2f4c86c7	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.347863+07
34	lecturer	["136"]	{"employee_id": 136}	3f52f96013d186bb60b6a669d8f810aafb9ebf904b8a7818eb98884162a6e356	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.348565+07
34	lecturer	["138"]	{"employee_id": 138}	9c1a310fb4835eac0266c330328682032418af03de87f374dda8fbc7ac1509a0	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.349312+07
34	lecturer	["140"]	{"employee_id": 140}	d9b862e72399d7301d0c5dea136dbf294d12a56f5f0229f2f0e81616976248ef	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.350067+07
34	lecturer	["142"]	{"employee_id": 142}	659e745e0df3fcff3938cec47819745dae8cdd36832987dbbfc2aa8e39de87a3	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.350954+07
34	lecturer	["144"]	{"employee_id": 144}	28b9703b0918a6e19ee0f8cb27e28201f2c54e85cf693cbb48581cf11dfa1672	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.35174+07
34	lecturer	["146"]	{"employee_id": 146}	e02279a75ff661b4ff6d144263bf33ce5275ba39ee447ce83418cefbfedc21ba	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.352461+07
34	lecturer	["148"]	{"employee_id": 148}	373c08f4b68804129265950e3b22ab35522a8de333d24ef7d1dfd41cdb865058	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.35318+07
34	lecturer	["150"]	{"employee_id": 150}	09758af7382e4a19b3f343a5eeb6f6533ccaebaa46510992116913a0f97fe697	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.35393+07
34	lecturer	["152"]	{"employee_id": 152}	edcb142a153d51780b1b0701a0691fe7f6760129ec8c7df36a01d2030096a685	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.354679+07
34	lecturer	["154"]	{"employee_id": 154}	a2a9465441e0e4c1a6e31c74cfe5f5c84e8fbeef5aa63676c21a030c5b0bbb95	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.355493+07
34	lecturer	["156"]	{"employee_id": 156}	0d9bb72e6cf374647d9f20a6f0bf947d40b59c2a74f47b6179c0b021e8b479a4	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.356241+07
34	lecturer	["158"]	{"employee_id": 158}	4727178f67fc91ffae29713c75f52b823ec3a2f926adf78027ddb134721b7086	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.35702+07
34	lecturer	["160"]	{"employee_id": 160}	799d1c13e44a9195a4efd7e310a31ef6758bc634b7ef6edabdecc82f704ce4d8	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.357825+07
34	lecturer	["162"]	{"employee_id": 162}	dbbfe4c09ffa74fbd850bc517db038d2cd0146208ce74b11409e6784a8283070	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.358566+07
34	lecturer	["164"]	{"employee_id": 164}	fa9f3e944d3ef5e1e304605ebd1fc0e6b9476987f8fe77885fece0f683f2d506	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.359257+07
34	lecturer	["166"]	{"employee_id": 166}	fc9046d5d4c6a8826776c3678a40ee1d2370bde3f975fb765e137504bb5903f9	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.359931+07
34	lecturer	["168"]	{"employee_id": 168}	c0cc4ff53e41944a14c200a7e4b5ca0727ae82e5365f09a9d554480beb6193a8	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.360643+07
34	lecturer	["170"]	{"employee_id": 170}	686a1d8e497049f606e2a0946a126a503ef5d1eea9d7941bbf9dae4b0c478bda	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.361354+07
34	lecturer	["172"]	{"employee_id": 172}	ca3d594e0118519b59b5b0ec00bdd15fc1c4ba2cc12a1ef3ea68726b437e5de4	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.362011+07
34	lecturer	["174"]	{"employee_id": 174}	d8cfdc34e84d8f03e65564f9f89fd7692adde1c63e8ecc2148c6a7f9f6c0126b	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.362735+07
34	lecturer	["176"]	{"employee_id": 176}	de76e04c587cf568c566eee653b020cab7993e76680e34ea7b47afb319a472e8	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.363461+07
34	lecturer	["178"]	{"employee_id": 178}	b2c98864b4eb46382d745c2415e6037ca8067b3c858a073cd7a9234a7a02597d	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.364181+07
34	lecturer	["180"]	{"employee_id": 180}	e92ea027d39c2a78f8e6e360ff20804be9fa5cb6ab8cf5fac73a838891dee52e	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.364898+07
34	lecturer	["182"]	{"employee_id": 182}	5e13beb3c077293cc3635e5d7a3392eb198431858cc91f883916acaa6c5e04ef	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.365626+07
34	lecturer	["184"]	{"employee_id": 184}	c0aa2f9fe91fb25bd1d1443945c0578734aafa7e863ae58be112af69dde316ec	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.366414+07
34	lecturer	["186"]	{"employee_id": 186}	b704fb75f9eb5965c074a04d628cbe7099d40cc0a05e59bfdf6df5b34ad35f1b	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.367129+07
34	lecturer	["188"]	{"employee_id": 188}	772d1e9e160657cf56364efa68a20b7d9ecc43c9c7bb9e784777d35a94d86654	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.367829+07
34	lecturer	["190"]	{"employee_id": 190}	a3c95943448e800cb36762cd5b16e70bc15ff59e83b41d743b7ae200c48ca308	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.368669+07
34	lecturer	["192"]	{"employee_id": 192}	25db704718b11d6f5630e68738f4037a03ea619e558f7f41c331d69c1bdf742e	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.369409+07
34	lecturer	["194"]	{"employee_id": 194}	346c7e08c20c6b35746afe413c1f26c18a03bb66a4ec0ac4237faaa606438164	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.370129+07
34	lecturer	["196"]	{"employee_id": 196}	5bb992e82d06a8cc42b78e29adc9b38fc9b99b484b4cb3d49f9ea32c3d65b9b1	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.37087+07
34	lecturer	["198"]	{"employee_id": 198}	0ff181af31bc8c0c08a712bcf9143388a4f897c14e73d24ddaab792f1f73ef7e	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.372088+07
34	lecturer	["200"]	{"employee_id": 200}	5dfc11e208aa634911d79d9379a4aa261acc592746e476ec7fc63c5da2ddfe83	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 23:04:09.372865+07
34	department	["1"]	{"department_id": 37}	9fbb7db1d33a6931525eb48b9f8d552c30ecfc3c61dcf259754278ebd6550845	228dd533-ce62-419e-805e-a3ab4c8b2d65	2026-11-16 23:07:35.091126+07
34	department	["2"]	{"department_id": 3}	46641dae248b03c48dfe9f5af79b250d51c60ae820ce484eba2376c37ce502d5	228dd533-ce62-419e-805e-a3ab4c8b2d65	2026-11-16 23:07:35.093083+07
34	department	["3"]	{"department_id": 38}	03044ac3ec23d6d14bd4041fa2bbc2f599e1f80b56babad8daf62d3a4954c536	228dd533-ce62-419e-805e-a3ab4c8b2d65	2026-11-16 23:07:35.094557+07
34	department	["4"]	{"department_id": 39}	5edc9a84884c492de982a837a7e54c0fbda7100af5a31fbe5df6b84d03014e6c	228dd533-ce62-419e-805e-a3ab4c8b2d65	2026-11-16 23:07:35.095806+07
\.


--
-- Data for Name: gold_scope; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.gold_scope (scope, dataset_id, domain, silver_snapshot_id, generation, active_job_id, updated_at) FROM stdin;
production:auto-5cb5b4e2548a0f888863ffd97381	2	academic	539ae500-2f13-451b-be83-286f62b636f9	1	9c122de6-d11c-4db4-872d-60a8970bcb3f	2026-11-16 21:07:19.927301+07
production:auto-7858eb8f709a3e0a1583c6d93948	1	hr	1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf	1	228dd533-ce62-419e-805e-a3ab4c8b2d65	2026-11-16 21:15:59.407355+07
production:auto-714bd628f3e833afe92a14aa6053	1	hr	a1efba66-0aa4-4215-a9fc-758af2a59a0d	1	87f44121-73fe-4098-a175-06254ab5ce81	2026-11-16 21:26:08.875576+07
production:auto-899188c733e26f94ba29ead9cf0e	1	hr	58804508-8b12-45f5-98cf-2f1f1e2506e2	2	59e65f53-3d4e-41aa-80a3-b577773e7945	2026-11-16 21:26:31.064511+07
production:auto-e270bc9994f1530b4a540c0daa6c	1	hr	3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c	2	a94b1c6b-4f98-409f-8471-964aa49ee81a	2026-11-16 23:04:04.903641+07
\.


--
-- Data for Name: gold_sync_job; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.gold_sync_job (gold_job_id, recipe_version, status, source_manifest_ref, attempt_number, lease_token, lease_until, next_attempt_at, error_code, error_report, result, created_at, finished_at) FROM stdin;
a10359dd-879c-4d62-b226-82d26ee14dd8	gold-business-records-v1	SUPERSEDED	{"key": "gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a10359dd-879c-4d62-b226-82d26ee14dd8/business/manifest-1105cb9947c8508b0f6c1befc8eb6720d933bc5840d699757735e36e620e5fff.json", "size": 1356, "sha256": "1105cb9947c8508b0f6c1befc8eb6720d933bc5840d699757735e36e620e5fff", "version_id": "f93a0abd-1e7b-45e6-a90f-052780c64aa6"}	1	\N	\N	2026-11-16 22:50:34.538008+07	GOLD_SYNC_REQUIRED_FIELD	{"code": "GOLD_SYNC_REQUIRED_FIELD", "field": "full_name", "entity": "employee"}	\N	2026-11-16 22:50:34.538008+07	2026-11-16 23:04:04.911032+07
9c122de6-d11c-4db4-872d-60a8970bcb3f	gold-business-records-v1	SYNCED	{"key": "gold/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/9c122de6-d11c-4db4-872d-60a8970bcb3f/business/manifest-6955a5c4c2c58d135ac2a83c72c262a6dd07e6ba960e3601905a6e8dbbb9ebdc.json", "size": 1350, "sha256": "6955a5c4c2c58d135ac2a83c72c262a6dd07e6ba960e3601905a6e8dbbb9ebdc", "version_id": "de3583b9-a5b7-4343-81b5-d0016a893b66"}	1	\N	\N	2026-11-16 22:50:34.538008+07	\N	\N	{"source": "GOLD", "entities": {"course": 5}, "recipe_version": "gold-business-records-v1", "records_synced": 5}	2026-11-16 22:50:34.538008+07	2026-11-16 22:50:43.932939+07
a94b1c6b-4f98-409f-8471-964aa49ee81a	gold-business-records-v1	SYNCED	{"key": "gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a94b1c6b-4f98-409f-8471-964aa49ee81a/business/manifest-df199234350ad414f75388e8b76020a3e3eecbdcb78d0a907b989d9722cf61ac.json", "size": 1365, "sha256": "df199234350ad414f75388e8b76020a3e3eecbdcb78d0a907b989d9722cf61ac", "version_id": "ce1d569a-95d7-454f-b56b-ec912a6496f7"}	1	\N	\N	2026-11-16 23:04:04.903641+07	\N	\N	{"source": "GOLD", "entities": {"employee": 200}, "recipe_version": "gold-business-records-v1", "records_synced": 200}	2026-11-16 23:04:04.903641+07	2026-11-16 23:04:04.935581+07
228dd533-ce62-419e-805e-a3ab4c8b2d65	gold-business-records-v1	SYNCED	{"key": "gold/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/228dd533-ce62-419e-805e-a3ab4c8b2d65/business/manifest-c81d07be7b542477bd58808491ece3ae4fc1f1bfb3cbc8dc898145fa7d272cf0.json", "size": 1357, "sha256": "c81d07be7b542477bd58808491ece3ae4fc1f1bfb3cbc8dc898145fa7d272cf0", "version_id": "55a98358-252b-47a1-ab2e-845ae5c57888"}	2	\N	\N	2026-11-16 23:07:34.086123+07	\N	\N	{"source": "GOLD", "entities": {"department": 4}, "recipe_version": "gold-business-records-v1", "records_synced": 4}	2026-11-16 22:50:34.538008+07	2026-11-16 23:07:34.962012+07
87f44121-73fe-4098-a175-06254ab5ce81	gold-business-records-v1	SYNCED	{"key": "gold/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/87f44121-73fe-4098-a175-06254ab5ce81/business/manifest-1f9b7c91972b8136c282bb516a72eea7def26f67dd5096dec60f11e998b65d34.json", "size": 1356, "sha256": "1f9b7c91972b8136c282bb516a72eea7def26f67dd5096dec60f11e998b65d34", "version_id": "472c4225-e35a-4e92-93b8-e27815255455"}	2	\N	\N	2026-11-16 23:04:04.935581+07	\N	\N	{"source": "GOLD", "entities": {"lecturer": 100}, "recipe_version": "gold-business-records-v1", "records_synced": 100}	2026-11-16 22:50:34.538008+07	2026-11-16 23:04:09.278136+07
59e65f53-3d4e-41aa-80a3-b577773e7945	gold-business-records-v1	BLOCKED	{"key": "gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/59e65f53-3d4e-41aa-80a3-b577773e7945/business/manifest-28939aa01b217c04e1560b90c3bd8d01043508c303e26bf79a39e444bdc6b522.json", "size": 1362, "sha256": "28939aa01b217c04e1560b90c3bd8d01043508c303e26bf79a39e444bdc6b522", "version_id": "7b03a4dc-47f7-4ab1-9368-05845b89eae4"}	4	\N	\N	2026-11-16 23:07:34.962012+07	GOLD_SYNC_REFERENCE_MISSING	{"code": "GOLD_SYNC_REFERENCE_MISSING", "field": "position_id", "entity": "assignment"}	\N	2026-11-16 22:50:34.538008+07	2026-11-16 23:07:37.128107+07
\.


--
-- Data for Name: gold_worker; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.gold_worker (worker_id, heartbeat_at) FROM stdin;
969358f0d4a1	2026-11-17 13:37:47.82891+07
\.


--
-- Data for Name: identity_crosswalk; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.identity_crosswalk (namespace, entity, source_key, canonical_id, evidence, valid_from, valid_to) FROM stdin;
\.


--
-- Data for Name: ingestion_job; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.ingestion_job (ingestion_job_id, data_source_id, dataset_id, target_layer_id, job_type, started_at, finished_at, status, rows_read, rows_written, rows_rejected, error_message, source_table, created_at, requested_by, stage, lease_expires_at, output, validation_input, validation_report, upload_input, bronze_output, root_job_id, attempt_number, next_attempt_at, retryable) FROM stdin;
1	1	1	1	UPLOAD_V1	2026-11-16 14:06:27.546707	2026-11-16 14:06:27.708924	SUCCESS	200	200	0	\N	assignment.csv	2026-11-16 14:06:27.145583	9	COMPLETED	\N	{"uri": "s3://bronze/hr/2026/11/16/job-1/assignment.csv", "domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "lineage": {"mode": "ingestion", "run_ref": "ingestion:1", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/26226c35a4d84d5d8add2bba721b05ec/assignment.csv", "layer": "SOURCE", "domain": "hr", "version": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22", "asset_id": "source:1:assignment.csv", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-1/assignment.csv", "layer": "BRONZE", "domain": "hr", "version": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "asset_id": "dataset:1", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": "ingestion:1"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "batch-id": "job-1", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:06:27.571071+00:00"}, "size_bytes": 7286, "version_id": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-001", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-002", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-003", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-004", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-005", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "e2ec46e7-7b48-43c4-b1ad-356f1062b4e0", "attempt_id": "job-1", "can_accept": true, "source_type": "CSV", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 200, "ingestion_job_id": 1, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:969fa5ea2c29ff63f586d5075227f09019d6928711b2420b108d605e72074f09"}	{"uri": "s3://ingestion-uploads/incoming/1/26226c35a4d84d5d8add2bba721b05ec/assignment.csv", "domain": "hr", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "filename": "assignment.csv", "dataset_id": 1, "size_bytes": 7286, "version_id": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22", "source_type": "CSV", "data_source_id": 1, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_system_id": 34, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/hr/2026/11/16/job-1/assignment.csv", "domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "lineage": {"mode": "ingestion", "run_ref": "ingestion:1", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/26226c35a4d84d5d8add2bba721b05ec/assignment.csv", "layer": "SOURCE", "domain": "hr", "version": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22", "asset_id": "source:1:assignment.csv", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-1/assignment.csv", "layer": "BRONZE", "domain": "hr", "version": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "asset_id": "dataset:1", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": "ingestion:1"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "batch-id": "job-1", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:06:27.571071+00:00"}, "size_bytes": 7286, "version_id": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "fbc4a870-f9e2-444f-89d5-3d2d57ed6c22"}	1	1	\N	f
3	3	3	1	UPLOAD_V1	2026-11-16 14:07:34.629006	2026-11-16 14:07:34.699488	SUCCESS	100	100	0	\N	academic_du_lieu_loi.xlsx	2026-11-16 14:07:33.418484	7	COMPLETED	\N	{"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "domain": "academic", "sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "lineage": {"mode": "ingestion", "run_ref": "ingestion:3", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/3/e8822a0e9da646d9ad11fcf64295fccd/academic_du_lieu_loi.xlsx", "layer": "SOURCE", "domain": "academic", "version": "69b84f2d-51a0-431c-bd4e-f1e28a7de582", "asset_id": "source:3:academic_du_lieu_loi.xlsx", "checksum": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "producer_run_ref": null}], "output": {"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "layer": "BRONZE", "domain": "academic", "version": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "asset_id": "dataset:3", "checksum": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "producer_run_ref": "ingestion:3"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "batch-id": "job-3", "source-system-id": "35", "ingestion-timestamp": "2026-11-16T14:07:34.643533+00:00"}, "size_bytes": 9522, "version_id": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "source_type": "EXCEL", "records_read": 100, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": "Academic"}, "source_version_id": "69b84f2d-51a0-431c-bd4e-f1e28a7de582"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-001", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-002", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-003", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-004", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-005", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-006", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-007", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "d7570623-e72b-4f6e-95e6-f9c714512729", "attempt_id": "job-3", "can_accept": true, "source_type": "EXCEL", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 100, "ingestion_job_id": 3, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:12a1b2d7e7150f4d322c5be329ddda19747a27d6205c2c8bfe332e95b6765b49"}	{"uri": "s3://ingestion-uploads/incoming/3/e8822a0e9da646d9ad11fcf64295fccd/academic_du_lieu_loi.xlsx", "domain": "academic", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "filename": "academic_du_lieu_loi.xlsx", "dataset_id": 3, "size_bytes": 9522, "version_id": "69b84f2d-51a0-431c-bd4e-f1e28a7de582", "source_type": "EXCEL", "data_source_id": 3, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": null}, "source_system_id": 35, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "domain": "academic", "sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "lineage": {"mode": "ingestion", "run_ref": "ingestion:3", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/3/e8822a0e9da646d9ad11fcf64295fccd/academic_du_lieu_loi.xlsx", "layer": "SOURCE", "domain": "academic", "version": "69b84f2d-51a0-431c-bd4e-f1e28a7de582", "asset_id": "source:3:academic_du_lieu_loi.xlsx", "checksum": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "producer_run_ref": null}], "output": {"uri": "s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx", "layer": "BRONZE", "domain": "academic", "version": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "asset_id": "dataset:3", "checksum": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "producer_run_ref": "ingestion:3"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "batch-id": "job-3", "source-system-id": "35", "ingestion-timestamp": "2026-11-16T14:07:34.643533+00:00"}, "size_bytes": 9522, "version_id": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "source_type": "EXCEL", "records_read": 100, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": "Academic"}, "source_version_id": "69b84f2d-51a0-431c-bd4e-f1e28a7de582"}	3	1	\N	f
2	2	2	1	UPLOAD_V1	2026-11-16 14:07:16.21579	2026-11-16 14:07:16.347913	SUCCESS	100	100	0	\N	academic_du_lieu_chuan.xlsx	2026-11-16 14:07:16.083365	7	COMPLETED	\N	{"uri": "s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx", "domain": "academic", "sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "lineage": {"mode": "ingestion", "run_ref": "ingestion:2", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/2/15fed72597154ac5b9e059c815d41f31/academic_du_lieu_chuan.xlsx", "layer": "SOURCE", "domain": "academic", "version": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6", "asset_id": "source:2:academic_du_lieu_chuan.xlsx", "checksum": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "producer_run_ref": null}], "output": {"uri": "s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx", "layer": "BRONZE", "domain": "academic", "version": "4cde87fd-70f8-4818-8d64-89dd065b3795", "asset_id": "dataset:2", "checksum": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "producer_run_ref": "ingestion:2"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "batch-id": "job-2", "source-system-id": "35", "ingestion-timestamp": "2026-11-16T14:07:16.260404+00:00"}, "size_bytes": 9391, "version_id": "4cde87fd-70f8-4818-8d64-89dd065b3795", "source_type": "EXCEL", "records_read": 100, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": "Academic"}, "source_version_id": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-001", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-002", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-003", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-004", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-005", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-006", "required": true}, {"status": "PASS", "rule_id": "IN-XLS-007", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "1309cb46-c9d7-4d03-8ce8-17e27510188c", "attempt_id": "job-2", "can_accept": true, "source_type": "EXCEL", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 100, "ingestion_job_id": 2, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:0a51e7ff7a092f88acd5c1ed1e9da6813c290094224b31a9beb3217d592d172e"}	{"uri": "s3://ingestion-uploads/incoming/2/15fed72597154ac5b9e059c815d41f31/academic_du_lieu_chuan.xlsx", "domain": "academic", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "filename": "academic_du_lieu_chuan.xlsx", "dataset_id": 2, "size_bytes": 9391, "version_id": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6", "source_type": "EXCEL", "data_source_id": 2, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": null}, "source_system_id": 35, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx", "domain": "academic", "sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "lineage": {"mode": "ingestion", "run_ref": "ingestion:2", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/2/15fed72597154ac5b9e059c815d41f31/academic_du_lieu_chuan.xlsx", "layer": "SOURCE", "domain": "academic", "version": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6", "asset_id": "source:2:academic_du_lieu_chuan.xlsx", "checksum": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "producer_run_ref": null}], "output": {"uri": "s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx", "layer": "BRONZE", "domain": "academic", "version": "4cde87fd-70f8-4818-8d64-89dd065b3795", "asset_id": "dataset:2", "checksum": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "producer_run_ref": "ingestion:2"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "batch-id": "job-2", "source-system-id": "35", "ingestion-timestamp": "2026-11-16T14:07:16.260404+00:00"}, "size_bytes": 9391, "version_id": "4cde87fd-70f8-4818-8d64-89dd065b3795", "source_type": "EXCEL", "records_read": 100, "parser_options": {"skip_rows": 0, "header_row": 1, "sheet_name": "Academic"}, "source_version_id": "2fa37776-04e7-42f3-bc0c-f3c70efdb2f6"}	2	1	\N	f
4	1	1	1	UPLOAD_V1	2026-11-16 14:15:56.013916	2026-11-16 14:15:56.078871	SUCCESS	4	4	0	\N	department.csv	2026-11-16 14:15:55.456478	9	COMPLETED	\N	{"uri": "s3://bronze/hr/2026/11/16/job-4/department.csv", "domain": "hr", "sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "lineage": {"mode": "ingestion", "run_ref": "ingestion:4", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/0a96804285494784b2b30a16bf8f46e5/department.csv", "layer": "SOURCE", "domain": "hr", "version": "686a2ae2-80af-4809-b431-c713393aa9ba", "asset_id": "source:1:department.csv", "checksum": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-4/department.csv", "layer": "BRONZE", "domain": "hr", "version": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "asset_id": "dataset:1", "checksum": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "producer_run_ref": "ingestion:4"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "batch-id": "job-4", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:15:56.029943+00:00"}, "size_bytes": 419, "version_id": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "source_type": "CSV", "records_read": 4, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "686a2ae2-80af-4809-b431-c713393aa9ba"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-001", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-002", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-003", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-004", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-005", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "c8ff3cb8-d5f9-43be-9985-28ed5d747db7", "attempt_id": "job-4", "can_accept": true, "source_type": "CSV", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 4, "ingestion_job_id": 4, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:969fa5ea2c29ff63f586d5075227f09019d6928711b2420b108d605e72074f09"}	{"uri": "s3://ingestion-uploads/incoming/1/0a96804285494784b2b30a16bf8f46e5/department.csv", "domain": "hr", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "filename": "department.csv", "dataset_id": 1, "size_bytes": 419, "version_id": "686a2ae2-80af-4809-b431-c713393aa9ba", "source_type": "CSV", "data_source_id": 1, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_system_id": 34, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/hr/2026/11/16/job-4/department.csv", "domain": "hr", "sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "lineage": {"mode": "ingestion", "run_ref": "ingestion:4", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/0a96804285494784b2b30a16bf8f46e5/department.csv", "layer": "SOURCE", "domain": "hr", "version": "686a2ae2-80af-4809-b431-c713393aa9ba", "asset_id": "source:1:department.csv", "checksum": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-4/department.csv", "layer": "BRONZE", "domain": "hr", "version": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "asset_id": "dataset:1", "checksum": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "producer_run_ref": "ingestion:4"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "batch-id": "job-4", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:15:56.029943+00:00"}, "size_bytes": 419, "version_id": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "source_type": "CSV", "records_read": 4, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "686a2ae2-80af-4809-b431-c713393aa9ba"}	4	1	\N	f
5	1	1	1	UPLOAD_V1	2026-11-16 14:22:55.913182	2026-11-16 14:22:55.964835	SUCCESS	200	200	0	\N	employee.csv	2026-11-16 14:22:55.388069	9	COMPLETED	\N	{"uri": "s3://bronze/hr/2026/11/16/job-5/employee.csv", "domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "lineage": {"mode": "ingestion", "run_ref": "ingestion:5", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/9a49f95055024b449dd1d5ec6c8b6588/employee.csv", "layer": "SOURCE", "domain": "hr", "version": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785", "asset_id": "source:1:employee.csv", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-5/employee.csv", "layer": "BRONZE", "domain": "hr", "version": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "asset_id": "dataset:1", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": "ingestion:5"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "batch-id": "job-5", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:22:55.928460+00:00"}, "size_bytes": 16230, "version_id": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-001", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-002", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-003", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-004", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-005", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "f22acbf1-773b-4868-a0f1-19fd982d155d", "attempt_id": "job-5", "can_accept": true, "source_type": "CSV", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 200, "ingestion_job_id": 5, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:969fa5ea2c29ff63f586d5075227f09019d6928711b2420b108d605e72074f09"}	{"uri": "s3://ingestion-uploads/incoming/1/9a49f95055024b449dd1d5ec6c8b6588/employee.csv", "domain": "hr", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "filename": "employee.csv", "dataset_id": 1, "size_bytes": 16230, "version_id": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785", "source_type": "CSV", "data_source_id": 1, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_system_id": 34, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/hr/2026/11/16/job-5/employee.csv", "domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "lineage": {"mode": "ingestion", "run_ref": "ingestion:5", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/9a49f95055024b449dd1d5ec6c8b6588/employee.csv", "layer": "SOURCE", "domain": "hr", "version": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785", "asset_id": "source:1:employee.csv", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-5/employee.csv", "layer": "BRONZE", "domain": "hr", "version": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "asset_id": "dataset:1", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": "ingestion:5"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "batch-id": "job-5", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:22:55.928460+00:00"}, "size_bytes": 16230, "version_id": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "3c47458f-d3c9-49dc-be0e-1ec0f7cc0785"}	5	1	\N	f
6	1	1	1	UPLOAD_V1	2026-11-16 14:26:05.633457	2026-11-16 14:26:05.700759	SUCCESS	100	100	0	\N	lecturer.csv	2026-11-16 14:26:04.807503	9	COMPLETED	\N	{"uri": "s3://bronze/hr/2026/11/16/job-6/lecturer.csv", "domain": "hr", "sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "lineage": {"mode": "ingestion", "run_ref": "ingestion:6", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/c8f9c761b5ef45ef9a437618a950c546/lecturer.csv", "layer": "SOURCE", "domain": "hr", "version": "9f890512-7708-4d68-be23-ff718489aa6e", "asset_id": "source:1:lecturer.csv", "checksum": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-6/lecturer.csv", "layer": "BRONZE", "domain": "hr", "version": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "asset_id": "dataset:1", "checksum": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "producer_run_ref": "ingestion:6"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "batch-id": "job-6", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:26:05.651450+00:00"}, "size_bytes": 3606, "version_id": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "source_type": "CSV", "records_read": 100, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "9f890512-7708-4d68-be23-ff718489aa6e"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-001", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-002", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-003", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-004", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-005", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "d59b8ca4-2cd2-442a-a446-69016dabdee7", "attempt_id": "job-6", "can_accept": true, "source_type": "CSV", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 100, "ingestion_job_id": 6, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:969fa5ea2c29ff63f586d5075227f09019d6928711b2420b108d605e72074f09"}	{"uri": "s3://ingestion-uploads/incoming/1/c8f9c761b5ef45ef9a437618a950c546/lecturer.csv", "domain": "hr", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "filename": "lecturer.csv", "dataset_id": 1, "size_bytes": 3606, "version_id": "9f890512-7708-4d68-be23-ff718489aa6e", "source_type": "CSV", "data_source_id": 1, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_system_id": 34, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/hr/2026/11/16/job-6/lecturer.csv", "domain": "hr", "sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "lineage": {"mode": "ingestion", "run_ref": "ingestion:6", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/c8f9c761b5ef45ef9a437618a950c546/lecturer.csv", "layer": "SOURCE", "domain": "hr", "version": "9f890512-7708-4d68-be23-ff718489aa6e", "asset_id": "source:1:lecturer.csv", "checksum": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-6/lecturer.csv", "layer": "BRONZE", "domain": "hr", "version": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "asset_id": "dataset:1", "checksum": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "producer_run_ref": "ingestion:6"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "batch-id": "job-6", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:26:05.651450+00:00"}, "size_bytes": 3606, "version_id": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "source_type": "CSV", "records_read": 100, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "9f890512-7708-4d68-be23-ff718489aa6e"}	6	1	\N	f
7	1	1	1	UPLOAD_V1	2026-11-16 14:26:27.877935	2026-11-16 14:26:27.92507	SUCCESS	200	200	0	\N	assignment.csv	2026-11-16 14:26:26.626762	9	COMPLETED	\N	{"uri": "s3://bronze/hr/2026/11/16/job-7/assignment.csv", "domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "lineage": {"mode": "ingestion", "run_ref": "ingestion:7", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/3fbcb552f825410e8f83c7653982ce2d/assignment.csv", "layer": "SOURCE", "domain": "hr", "version": "fab0a178-c122-4a0f-983c-53f8359f1446", "asset_id": "source:1:assignment.csv", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-7/assignment.csv", "layer": "BRONZE", "domain": "hr", "version": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "asset_id": "dataset:1", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": "ingestion:7"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "batch-id": "job-7", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:26:27.891741+00:00"}, "size_bytes": 7286, "version_id": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "fab0a178-c122-4a0f-983c-53f8359f1446"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-001", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-002", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-003", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-004", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-005", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "b88217aa-c1ee-4be0-81c7-9264585da9cc", "attempt_id": "job-7", "can_accept": true, "source_type": "CSV", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 200, "ingestion_job_id": 7, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:969fa5ea2c29ff63f586d5075227f09019d6928711b2420b108d605e72074f09"}	{"uri": "s3://ingestion-uploads/incoming/1/3fbcb552f825410e8f83c7653982ce2d/assignment.csv", "domain": "hr", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "filename": "assignment.csv", "dataset_id": 1, "size_bytes": 7286, "version_id": "fab0a178-c122-4a0f-983c-53f8359f1446", "source_type": "CSV", "data_source_id": 1, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_system_id": 34, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/hr/2026/11/16/job-7/assignment.csv", "domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "lineage": {"mode": "ingestion", "run_ref": "ingestion:7", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/3fbcb552f825410e8f83c7653982ce2d/assignment.csv", "layer": "SOURCE", "domain": "hr", "version": "fab0a178-c122-4a0f-983c-53f8359f1446", "asset_id": "source:1:assignment.csv", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-7/assignment.csv", "layer": "BRONZE", "domain": "hr", "version": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "asset_id": "dataset:1", "checksum": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "producer_run_ref": "ingestion:7"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "batch-id": "job-7", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T14:26:27.891741+00:00"}, "size_bytes": 7286, "version_id": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "fab0a178-c122-4a0f-983c-53f8359f1446"}	7	1	\N	f
8	1	1	1	UPLOAD_V1	2026-11-16 16:04:03.081792	2026-11-16 16:04:03.180028	SUCCESS	200	200	0	\N	employee.csv	2026-11-16 16:04:01.82326	system:gold-storage-v2	COMPLETED	\N	{"uri": "s3://bronze/hr/2026/11/16/job-8/employee.csv", "domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "lineage": {"mode": "ingestion", "run_ref": "ingestion:8", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/590ab0317dd74f5c87841f3bf1019a5f/employee.csv", "layer": "SOURCE", "domain": "hr", "version": "5dbc6d51-c0c6-450e-ad7e-d488673503e8", "asset_id": "source:1:employee.csv", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-8/employee.csv", "layer": "BRONZE", "domain": "hr", "version": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "asset_id": "dataset:1", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": "ingestion:8"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "batch-id": "job-8", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T16:04:03.122021+00:00"}, "size_bytes": 16230, "version_id": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "5dbc6d51-c0c6-450e-ad7e-d488673503e8"}	\N	{"checks": [{"status": "PASS", "rule_id": "IN-COM-001", "required": true}, {"status": "PASS", "rule_id": "IN-COM-002", "required": true}, {"status": "PASS", "rule_id": "IN-COM-003", "required": true}, {"status": "PASS", "rule_id": "IN-COM-004", "required": true}, {"status": "PASS", "rule_id": "IN-COM-005", "required": true}, {"status": "PASS", "rule_id": "IN-COM-006", "required": true}, {"status": "PASS", "rule_id": "IN-COM-007", "required": true}, {"status": "PASS", "rule_id": "IN-COM-008", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-001", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-002", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-003", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-004", "required": true}, {"status": "PASS", "rule_id": "IN-CSV-005", "required": true}], "issues": [], "result": "PASS", "policy_id": "uploaded-file-structure", "report_id": "2196333d-76f3-4534-b1df-5056ab7cb87c", "attempt_id": "job-8", "can_accept": true, "source_type": "CSV", "total_errors": 0, "scan_complete": true, "policy_version": "1", "total_warnings": 0, "records_scanned": 200, "ingestion_job_id": 8, "issues_truncated": false, "blank_rows_skipped": 0, "policy_fingerprint": "sha256:969fa5ea2c29ff63f586d5075227f09019d6928711b2420b108d605e72074f09"}	{"uri": "s3://ingestion-uploads/incoming/1/590ab0317dd74f5c87841f3bf1019a5f/employee.csv", "domain": "hr", "limits": {"max_columns": 500, "max_records": 1000000, "max_cell_chars": 1000000, "max_file_bytes": 52428800, "timeout_seconds": 60, "memory_limit_bytes": 268435456}, "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "filename": "employee.csv", "dataset_id": 1, "size_bytes": 16230, "version_id": "5dbc6d51-c0c6-450e-ad7e-d488673503e8", "source_type": "CSV", "data_source_id": 1, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_system_id": 34, "max_expanded_bytes": 209715200}	{"uri": "s3://bronze/hr/2026/11/16/job-8/employee.csv", "domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "lineage": {"mode": "ingestion", "run_ref": "ingestion:8", "derivations": [{"inputs": [{"uri": "s3://ingestion-uploads/incoming/1/590ab0317dd74f5c87841f3bf1019a5f/employee.csv", "layer": "SOURCE", "domain": "hr", "version": "5dbc6d51-c0c6-450e-ad7e-d488673503e8", "asset_id": "source:1:employee.csv", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": null}], "output": {"uri": "s3://bronze/hr/2026/11/16/job-8/employee.csv", "layer": "BRONZE", "domain": "hr", "version": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "asset_id": "dataset:1", "checksum": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "producer_run_ref": "ingestion:8"}, "transformations": [{"name": "INGESTION_CAPTURE", "version": null, "fingerprint": null}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}, "metadata": {"sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "batch-id": "job-8", "source-system-id": "34", "ingestion-timestamp": "2026-11-16T16:04:03.122021+00:00"}, "size_bytes": 16230, "version_id": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "source_type": "CSV", "records_read": 200, "parser_options": {"encoding": "utf-8", "delimiter": ",", "skip_rows": 0, "has_header": true, "quote_char": "\\""}, "source_version_id": "5dbc6d51-c0c6-450e-ad7e-d488673503e8"}	8	1	\N	f
\.


--
-- Data for Name: ingestion_worker_heartbeat; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.ingestion_worker_heartbeat (worker_id, current_job_id, last_heartbeat_at) FROM stdin;
ingestion-worker	\N	2026-11-17 06:37:45.238134
\.


--
-- Data for Name: kpi; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.kpi (kpi_id, kpi_code, kpi_name, domain, unit, description) FROM stdin;
\.


--
-- Data for Name: kpi_value; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.kpi_value (kpi_value_id, kpi_id, period_date, kpi_value, dimension_json, created_at) FROM stdin;
\.


--
-- Data for Name: lecturer; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.lecturer (employee_id, academic_degree, academic_title, research_field) FROM stdin;
2	B.Sc.	\N	Công nghệ thông tin
4	B.Sc.	\N	Công nghệ thông tin
6	B.Sc.	\N	Công nghệ thông tin
8	B.Sc.	\N	Công nghệ thông tin
10	B.Sc.	\N	Công nghệ thông tin
12	B.Sc.	\N	Công nghệ thông tin
14	B.Sc.	\N	Công nghệ thông tin
16	B.Sc.	\N	Công nghệ thông tin
18	B.Sc.	\N	Công nghệ thông tin
20	B.Sc.	\N	Công nghệ thông tin
22	B.Sc.	\N	Công nghệ thông tin
24	B.Sc.	\N	Công nghệ thông tin
26	B.Sc.	\N	Công nghệ thông tin
28	B.Sc.	\N	Công nghệ thông tin
30	B.Sc.	\N	Công nghệ thông tin
32	B.Sc.	\N	Công nghệ thông tin
34	B.Sc.	\N	Công nghệ thông tin
36	B.Sc.	\N	Công nghệ thông tin
38	B.Sc.	\N	Công nghệ thông tin
40	B.Sc.	\N	Công nghệ thông tin
42	B.Sc.	\N	Công nghệ thông tin
44	B.Sc.	\N	Công nghệ thông tin
46	B.Sc.	\N	Công nghệ thông tin
48	B.Sc.	\N	Công nghệ thông tin
50	B.Sc.	\N	Công nghệ thông tin
52	B.Sc.	\N	Công nghệ thông tin
54	B.Sc.	\N	Công nghệ thông tin
56	B.Sc.	\N	Công nghệ thông tin
58	B.Sc.	\N	Công nghệ thông tin
60	B.Sc.	\N	Công nghệ thông tin
62	B.Sc.	\N	Công nghệ thông tin
64	B.Sc.	\N	Công nghệ thông tin
66	B.Sc.	\N	Công nghệ thông tin
68	B.Sc.	\N	Công nghệ thông tin
70	B.Sc.	\N	Công nghệ thông tin
72	B.Sc.	\N	Công nghệ thông tin
74	B.Sc.	\N	Công nghệ thông tin
76	B.Sc.	\N	Công nghệ thông tin
78	B.Sc.	\N	Công nghệ thông tin
80	B.Sc.	\N	Công nghệ thông tin
82	B.Sc.	\N	Công nghệ thông tin
84	B.Sc.	\N	Công nghệ thông tin
86	B.Sc.	\N	Công nghệ thông tin
88	B.Sc.	\N	Công nghệ thông tin
90	B.Sc.	\N	Công nghệ thông tin
92	B.Sc.	\N	Công nghệ thông tin
94	B.Sc.	\N	Công nghệ thông tin
96	B.Sc.	\N	Công nghệ thông tin
98	B.Sc.	\N	Công nghệ thông tin
100	B.Sc.	\N	Công nghệ thông tin
102	B.Sc.	\N	Công nghệ thông tin
104	B.Sc.	\N	Công nghệ thông tin
106	B.Sc.	\N	Công nghệ thông tin
108	B.Sc.	\N	Công nghệ thông tin
110	B.Sc.	\N	Công nghệ thông tin
112	B.Sc.	\N	Công nghệ thông tin
114	B.Sc.	\N	Công nghệ thông tin
116	B.Sc.	\N	Công nghệ thông tin
118	B.Sc.	\N	Công nghệ thông tin
120	B.Sc.	\N	Công nghệ thông tin
122	B.Sc.	\N	Công nghệ thông tin
124	B.Sc.	\N	Công nghệ thông tin
126	B.Sc.	\N	Công nghệ thông tin
128	B.Sc.	\N	Công nghệ thông tin
130	B.Sc.	\N	Công nghệ thông tin
132	B.Sc.	\N	Công nghệ thông tin
134	B.Sc.	\N	Công nghệ thông tin
136	B.Sc.	\N	Công nghệ thông tin
138	B.Sc.	\N	Công nghệ thông tin
140	B.Sc.	\N	Công nghệ thông tin
142	B.Sc.	\N	Công nghệ thông tin
144	B.Sc.	\N	Công nghệ thông tin
146	B.Sc.	\N	Công nghệ thông tin
148	B.Sc.	\N	Công nghệ thông tin
150	B.Sc.	\N	Công nghệ thông tin
152	B.Sc.	\N	Công nghệ thông tin
154	B.Sc.	\N	Công nghệ thông tin
156	B.Sc.	\N	Công nghệ thông tin
158	B.Sc.	\N	Công nghệ thông tin
160	B.Sc.	\N	Công nghệ thông tin
162	B.Sc.	\N	Công nghệ thông tin
164	B.Sc.	\N	Công nghệ thông tin
166	B.Sc.	\N	Công nghệ thông tin
168	B.Sc.	\N	Công nghệ thông tin
170	B.Sc.	\N	Công nghệ thông tin
172	B.Sc.	\N	Công nghệ thông tin
174	B.Sc.	\N	Công nghệ thông tin
176	B.Sc.	\N	Công nghệ thông tin
178	B.Sc.	\N	Công nghệ thông tin
180	B.Sc.	\N	Công nghệ thông tin
182	B.Sc.	\N	Công nghệ thông tin
184	B.Sc.	\N	Công nghệ thông tin
186	B.Sc.	\N	Công nghệ thông tin
188	B.Sc.	\N	Công nghệ thông tin
190	B.Sc.	\N	Công nghệ thông tin
192	B.Sc.	\N	Công nghệ thông tin
194	B.Sc.	\N	Công nghệ thông tin
196	B.Sc.	\N	Công nghệ thông tin
198	B.Sc.	\N	Công nghệ thông tin
200	B.Sc.	\N	Công nghệ thông tin
\.


--
-- Data for Name: login_session; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.login_session (login_session_id, app_user_id, login_at, logout_at, ip_address, user_agent, refresh_token_hash, session_status) FROM stdin;
2	9	2026-11-16 20:30:29.614545	\N	10.96.134.48	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	443bd643cbb95d42ad1d480f4897f2df3fdef91ad20d869e02a9a02d071e35e2	ACTIVE
3	2	2026-11-16 20:30:31.358247	2026-11-16 20:30:53.84712	10.96.134.223	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	cf09b71fa761842a8ee44b0a94d29244d34912d9aac8c60b57fcac6d069c8953	REVOKED
5	2	2026-11-16 20:34:46.948229	2026-11-16 20:34:48.567431	::1	python-httpx/0.28.1	a055c6aeaa2f8c351b90d4f9b83c1d97e8b262d78fadde75a7141d0b24b604f5	REVOKED
1	7	2026-11-16 20:23:08.507532	2026-11-16 20:40:14.081368	178.128.69.202	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	92ee6c0de9067ade7ddb2c974e438aeca71e6b520e07b0e75fd39507aeb8db80	REVOKED
6	2	2026-11-16 20:40:30.356643	\N	::1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	ef93571421b216a63ec24bafb690af61101444737dd50e06d24d7a3095881bf3	ACTIVE
7	2	2026-11-16 21:06:41.215535	\N	10.96.134.48	Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	b6e605fa8fcc6d5a5b0a032445765977f1616faad6765edf5b19619f8551ca87	ACTIVE
8	7	2026-11-16 22:55:59.979318	2026-11-16 23:31:05.328998	::1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	60594ca134c04475abb04cbe252ae6cfc5eaeb41240c628852f9574e48211cbf	REVOKED
9	8	2026-11-16 23:31:19.722904	2026-11-16 23:31:35.742007	::1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	87f44aa94ec8cd606c1ddd3c3635d463d6b5f9a0008b31920db7741033cc2a6f	REVOKED
10	8	2026-11-17 00:28:33.958809	\N	::1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	1a8c59aa9bdb155b49d373f509de1e05bfd18896d1a55bd7791ef374e676e0a5	ACTIVE
11	8	2026-11-17 00:29:45.432403	\N	::1	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	195aa22610803b6b70ddfee032fca57548989edbb261762ceb5060a859b5149a	ACTIVE
4	7	2026-11-16 20:31:21.661511	2026-11-17 01:04:19.348103	10.96.134.223	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	a37145389cfbf32390f64c8eecc1eea042aff41c33ce0d4b2eec7d08383edcd7	REVOKED
12	8	2026-11-17 09:34:34.01599	2026-11-17 12:21:43.572527	10.96.134.223	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	c20e502665e9390a3375ad78c676af3dbc02eeb7735ca1794f97b257adc47f6f	REVOKED
13	8	2026-11-17 12:25:57.376298	2026-11-17 12:26:17.357614	10.96.134.223	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	081c8b6f69534e8ed97409647de7bc4c4ee28aa48b3ee3bbceb8e506d436ed6c	REVOKED
14	9	2026-11-17 12:26:20.671871	\N	10.96.134.223	Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36	10d297233f45e6e0b5183efa66d48b55c5e07a897777ff81d32c4adcd3e39460	ACTIVE
\.


--
-- Data for Name: major; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.major (major_id, major_code, major_name, department_id, status) FROM stdin;
\.


--
-- Data for Name: notification_read; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.notification_read (app_user_id, event_id, read_at) FROM stdin;
\.


--
-- Data for Name: permission; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.permission (permission_id, permission_code, permission_name, description) FROM stdin;
1	auth.login	Đăng nhập	Cho phép đăng nhập hệ thống
2	user.read	Xem thông tin user	Xem danh sách và chi tiết user
3	user.manage	Quản lý user	Tạo, cập nhật, khóa user
4	data_source.read	Xem Data Source	Xem danh sách và chi tiết nguồn dữ liệu
5	data_source.write	Quản lý Data Source	Tạo và cập nhật cấu hình nguồn dữ liệu
6	dataset.read	Xem Dataset	Xem danh sách, schema và preview dataset
7	dataset.manage	Quản lý Dataset	Cập nhật thông tin dataset
8	catalog.read	Xem Data Catalog	Xem metadata dataset và column
9	catalog.write	Cập nhật Data Catalog	Chỉnh sửa metadata, mô tả, tag
10	lineage.read	Xem Data Lineage	Xem nguồn gốc và quá trình biến đổi dữ liệu
11	quality.rule.read	Xem Quality Rules	Xem danh sách quy tắc chất lượng
12	quality.rule.write	Quản lý Quality Rules	Tạo, sửa, xóa quy tắc chất lượng
13	quality.report.read	Xem Quality Report	Xem báo cáo kết quả kiểm tra chất lượng
14	quality.check.run	Chạy kiểm tra chất lượng	Trigger chạy Data Quality check
15	query.execute	Thực thi truy vấn NL	Gửi câu hỏi ngôn ngữ tự nhiên và nhận kết quả
16	query.history.read	Xem lịch sử truy vấn	Xem Query History
17	dashboard.read	Xem Dashboard	Xem Dashboard và KPI
18	dashboard.manage	Quản lý Dashboard	Tạo và chỉnh sửa Dashboard
19	system.config	Cấu hình hệ thống	Thay đổi cấu hình hệ thống
20	system.log.read	Xem system log	Xem log hoạt động hệ thống
\.


--
-- Data for Name: pipeline_lineage; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.pipeline_lineage (lineage_id, source_layer, source_name, target_layer, target_name, metadata, created_at) FROM stdin;
1	SOURCE	assignment.csv	BRONZE	s3://bronze/hr/2026/11/16/job-1/assignment.csv	{"domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "dataset_id": 1, "size_bytes": 7286, "version_id": "d0f13b5b-371d-4d97-b29c-3dc744cfdb66", "records_read": 200, "data_source_id": 1, "records_passed": 200, "ingestion_job_id": 1, "records_rejected": 0}	2026-11-16 21:06:27.709039+07
2	BRONZE	s3://bronze/hr/2026/11/16/job-1/assignment.csv	SILVER	assignment	{"domain": "hr", "entity": "assignment", "status": "PUBLISHED", "records_read": 200, "records_passed": 200, "cleaning_run_id": "dc17ddcb-0eed-4f83-af4d-87dbd38ab94c", "ingestion_job_id": 1, "records_collapsed": 0, "silver_snapshot_id": "52df8123-672a-425d-93a1-04f53de4a774", "records_quarantined": 0}	2026-11-16 21:06:31.224899+07
3	SILVER	52df8123-672a-425d-93a1-04f53de4a774	GOLD	0014c6c1-5516-40f3-ade7-54b129fec649	{"mode": "production", "run_ref": "0014c6c1-5516-40f3-ade7-54b129fec649", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/52df8123-672a-425d-93a1-04f53de4a774/manifest-d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01.json", "layer": "SILVER", "domain": "hr", "version": "52df8123-672a-425d-93a1-04f53de4a774", "asset_id": "silver:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "d4652024f8fe247413ac830762b9757d52e5e140a95cd4de1ef5dbdbbe721c01", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/0014c6c1-5516-40f3-ade7-54b129fec649/aggregate-2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c.parquet", "layer": "GOLD", "domain": "hr", "version": "0014c6c1-5516-40f3-ade7-54b129fec649", "asset_id": "gold:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c", "producer_run_ref": "0014c6c1-5516-40f3-ade7-54b129fec649"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 21:06:33.619791+07
4	SOURCE	academic_du_lieu_chuan.xlsx	BRONZE	s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx	{"domain": "academic", "sha256": "e4414c5ea420d8cc8b0dab4954b1e814b432e622e43c3680b7e282c14d2722fb", "dataset_id": 2, "size_bytes": 9391, "version_id": "4cde87fd-70f8-4818-8d64-89dd065b3795", "records_read": 100, "data_source_id": 2, "records_passed": 100, "ingestion_job_id": 2, "records_rejected": 0}	2026-11-16 21:07:16.348174+07
5	BRONZE	s3://bronze/academic/2026/11/16/job-2/academic_du_lieu_chuan.xlsx	SILVER	file_2	{"domain": "academic", "entity": "file_2", "status": "PUBLISHED", "records_read": 100, "records_passed": 100, "cleaning_run_id": "c57c99da-0ae6-41e4-bc7a-59755f0627ed", "ingestion_job_id": 2, "records_collapsed": 0, "silver_snapshot_id": "539ae500-2f13-451b-be83-286f62b636f9", "records_quarantined": 0}	2026-11-16 21:07:17.964051+07
6	SILVER	539ae500-2f13-451b-be83-286f62b636f9	GOLD	9c122de6-d11c-4db4-872d-60a8970bcb3f	{"mode": "production", "run_ref": "9c122de6-d11c-4db4-872d-60a8970bcb3f", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/539ae500-2f13-451b-be83-286f62b636f9/manifest-98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0.json", "layer": "SILVER", "domain": "academic", "version": "539ae500-2f13-451b-be83-286f62b636f9", "asset_id": "silver:production:auto-5cb5b4e2548a0f888863ffd97381", "checksum": "98c5249fc1710bdb6075030ead6c36cf31fe7e884ebaee52150f29b5d3f14bf0", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/f0743989a8d9563524ff08060173e9e92db9eb1d6daeffe2e94e5a5c8c0e5c0d/9c122de6-d11c-4db4-872d-60a8970bcb3f/aggregate-3580f907fd8e0425d9516bf763169c60a6bed640fef96a88127de2f88dc5ef57.parquet", "layer": "GOLD", "domain": "academic", "version": "9c122de6-d11c-4db4-872d-60a8970bcb3f", "asset_id": "gold:production:auto-5cb5b4e2548a0f888863ffd97381", "checksum": "3580f907fd8e0425d9516bf763169c60a6bed640fef96a88127de2f88dc5ef57", "producer_run_ref": "9c122de6-d11c-4db4-872d-60a8970bcb3f"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 21:07:19.929144+07
7	SOURCE	academic_du_lieu_loi.xlsx	BRONZE	s3://bronze/academic/2026/11/16/job-3/academic_du_lieu_loi.xlsx	{"domain": "academic", "sha256": "bf9511c42a6d5494e9f4d16bbbff6c4f3fa07286e1c745e1f925e1f9544f6428", "dataset_id": 3, "size_bytes": 9522, "version_id": "c710c36f-f6fb-401a-a9ea-0472f9116b76", "records_read": 100, "data_source_id": 3, "records_passed": 100, "ingestion_job_id": 3, "records_rejected": 0}	2026-11-16 21:07:34.699822+07
8	SOURCE	department.csv	BRONZE	s3://bronze/hr/2026/11/16/job-4/department.csv	{"domain": "hr", "sha256": "efaee1b8087b2f17680933edc4ffc521c6fe5560b848adee80a9bb17b52b0760", "dataset_id": 1, "size_bytes": 419, "version_id": "9bcaacef-d212-4f6c-b8ba-9d21c7daaf03", "records_read": 4, "data_source_id": 1, "records_passed": 4, "ingestion_job_id": 4, "records_rejected": 0}	2026-11-16 21:15:56.078992+07
9	BRONZE	s3://bronze/hr/2026/11/16/job-4/department.csv	SILVER	department	{"domain": "hr", "entity": "department", "status": "PUBLISHED", "records_read": 4, "records_passed": 4, "cleaning_run_id": "cccba0f5-32ff-466a-b1bd-4692a1830a46", "ingestion_job_id": 4, "records_collapsed": 0, "silver_snapshot_id": "1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf", "records_quarantined": 0}	2026-11-16 21:15:57.713013+07
10	SILVER	1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf	GOLD	228dd533-ce62-419e-805e-a3ab4c8b2d65	{"mode": "production", "run_ref": "228dd533-ce62-419e-805e-a3ab4c8b2d65", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf/manifest-e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7.json", "layer": "SILVER", "domain": "hr", "version": "1c5a0e2a-abcd-42f5-96ff-ad74ccbf2faf", "asset_id": "silver:production:auto-7858eb8f709a3e0a1583c6d93948", "checksum": "e79ee60566781f33df687c15bfa652a0681b5772f09854d53570ea51a426bed7", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/2898c1425145fc2570a0749454bfddd804e00ecb418874526a0d92a9d467a76e/228dd533-ce62-419e-805e-a3ab4c8b2d65/aggregate-5105fe9660208516add603948caff233c0c56fe2a9fea28cfb4de8601a5c5e77.parquet", "layer": "GOLD", "domain": "hr", "version": "228dd533-ce62-419e-805e-a3ab4c8b2d65", "asset_id": "gold:production:auto-7858eb8f709a3e0a1583c6d93948", "checksum": "5105fe9660208516add603948caff233c0c56fe2a9fea28cfb4de8601a5c5e77", "producer_run_ref": "228dd533-ce62-419e-805e-a3ab4c8b2d65"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 21:15:59.409424+07
11	SOURCE	employee.csv	BRONZE	s3://bronze/hr/2026/11/16/job-5/employee.csv	{"domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "dataset_id": 1, "size_bytes": 16230, "version_id": "d9fb5d0b-f453-43a7-9943-75fdd9ec0fde", "records_read": 200, "data_source_id": 1, "records_passed": 200, "ingestion_job_id": 5, "records_rejected": 0}	2026-11-16 21:22:55.964984+07
12	BRONZE	s3://bronze/hr/2026/11/16/job-5/employee.csv	SILVER	employee	{"domain": "hr", "entity": "employee", "status": "PUBLISHED", "records_read": 200, "records_passed": 200, "cleaning_run_id": "f139324d-e632-4dd3-8ce2-8da48012acf2", "ingestion_job_id": 5, "records_collapsed": 0, "silver_snapshot_id": "8079b128-c5b0-4625-917b-e01e5386342d", "records_quarantined": 0}	2026-11-16 21:22:58.195837+07
15	BRONZE	s3://bronze/hr/2026/11/16/job-6/lecturer.csv	SILVER	lecturer	{"domain": "hr", "entity": "lecturer", "status": "PUBLISHED", "records_read": 100, "records_passed": 100, "cleaning_run_id": "c97d27b1-e712-4f14-bccd-0c4e3694d441", "ingestion_job_id": 6, "records_collapsed": 0, "silver_snapshot_id": "a1efba66-0aa4-4215-a9fc-758af2a59a0d", "records_quarantined": 0}	2026-11-16 21:26:07.696534+07
18	BRONZE	s3://bronze/hr/2026/11/16/job-7/assignment.csv	SILVER	assignment	{"domain": "hr", "entity": "assignment", "status": "PUBLISHED", "records_read": 200, "records_passed": 200, "cleaning_run_id": "bb9ae615-7497-4fa6-bf73-99e9db7c38f1", "ingestion_job_id": 7, "records_collapsed": 0, "silver_snapshot_id": "58804508-8b12-45f5-98cf-2f1f1e2506e2", "records_quarantined": 0}	2026-11-16 21:26:30.142828+07
13	SILVER	8079b128-c5b0-4625-917b-e01e5386342d	GOLD	a10359dd-879c-4d62-b226-82d26ee14dd8	{"mode": "production", "run_ref": "a10359dd-879c-4d62-b226-82d26ee14dd8", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/8079b128-c5b0-4625-917b-e01e5386342d/manifest-5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee.json", "layer": "SILVER", "domain": "hr", "version": "8079b128-c5b0-4625-917b-e01e5386342d", "asset_id": "silver:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "5d75104c3777a0b53957b72c014d22d507c4a82bd129ec33435642aa7a0120ee", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a10359dd-879c-4d62-b226-82d26ee14dd8/aggregate-9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec.parquet", "layer": "GOLD", "domain": "hr", "version": "a10359dd-879c-4d62-b226-82d26ee14dd8", "asset_id": "gold:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec", "producer_run_ref": "a10359dd-879c-4d62-b226-82d26ee14dd8"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 21:22:59.841874+07
16	SILVER	a1efba66-0aa4-4215-a9fc-758af2a59a0d	GOLD	87f44121-73fe-4098-a175-06254ab5ce81	{"mode": "production", "run_ref": "87f44121-73fe-4098-a175-06254ab5ce81", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/a1efba66-0aa4-4215-a9fc-758af2a59a0d/manifest-9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db.json", "layer": "SILVER", "domain": "hr", "version": "a1efba66-0aa4-4215-a9fc-758af2a59a0d", "asset_id": "silver:production:auto-714bd628f3e833afe92a14aa6053", "checksum": "9923b5f189410b0d7260c2ffb59a73c7bc581371768303efc8b6a2488455c5db", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/f994c784783c791a24ea4e96d2ab4d0773e661dc50b23d2d4d900449b3571646/87f44121-73fe-4098-a175-06254ab5ce81/aggregate-be7203148e4a450e04199c2826cc17d30e450ad3bcc8fcbf4e7087a6fb2ff732.parquet", "layer": "GOLD", "domain": "hr", "version": "87f44121-73fe-4098-a175-06254ab5ce81", "asset_id": "gold:production:auto-714bd628f3e833afe92a14aa6053", "checksum": "be7203148e4a450e04199c2826cc17d30e450ad3bcc8fcbf4e7087a6fb2ff732", "producer_run_ref": "87f44121-73fe-4098-a175-06254ab5ce81"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 21:26:08.877851+07
19	SILVER	58804508-8b12-45f5-98cf-2f1f1e2506e2	GOLD	59e65f53-3d4e-41aa-80a3-b577773e7945	{"mode": "production", "run_ref": "59e65f53-3d4e-41aa-80a3-b577773e7945", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/58804508-8b12-45f5-98cf-2f1f1e2506e2/manifest-3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b.json", "layer": "SILVER", "domain": "hr", "version": "58804508-8b12-45f5-98cf-2f1f1e2506e2", "asset_id": "silver:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "3fa34395575af142067e9c3abdcdc392a2b6368c0dbaffb1ac2342afa601016b", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e1539a2c714ea20df285a0dd457a57278c06fac9f5305c061c6a953d22cd07c6/59e65f53-3d4e-41aa-80a3-b577773e7945/aggregate-2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c.parquet", "layer": "GOLD", "domain": "hr", "version": "59e65f53-3d4e-41aa-80a3-b577773e7945", "asset_id": "gold:production:auto-899188c733e26f94ba29ead9cf0e", "checksum": "2937cb92ca4bea15d90d2a9dd47c0dfd2f4b7793dbc30412677a8dce1c2f247c", "producer_run_ref": "59e65f53-3d4e-41aa-80a3-b577773e7945"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 21:26:31.067526+07
14	SOURCE	lecturer.csv	BRONZE	s3://bronze/hr/2026/11/16/job-6/lecturer.csv	{"domain": "hr", "sha256": "91b0d61688cf2183f6177b3feab94073713d5e79c8c03bb0bf90c94437e27db2", "dataset_id": 1, "size_bytes": 3606, "version_id": "b1cc721d-a063-44b0-a8e3-63b7f80084fa", "records_read": 100, "data_source_id": 1, "records_passed": 100, "ingestion_job_id": 6, "records_rejected": 0}	2026-11-16 21:26:05.700891+07
17	SOURCE	assignment.csv	BRONZE	s3://bronze/hr/2026/11/16/job-7/assignment.csv	{"domain": "hr", "sha256": "3751466973af21fed6d8b4e893c7c90fc97887802029a82d42d3361975a8498e", "dataset_id": 1, "size_bytes": 7286, "version_id": "c63ab48e-b5e9-4e29-a134-011542cbf4b0", "records_read": 200, "data_source_id": 1, "records_passed": 200, "ingestion_job_id": 7, "records_rejected": 0}	2026-11-16 21:26:27.925164+07
20	SOURCE	employee.csv	BRONZE	s3://bronze/hr/2026/11/16/job-8/employee.csv	{"domain": "hr", "sha256": "0ed422887a2c0a750ffe6eb199c22752eecd2f907d505f8429755efc902bacb6", "dataset_id": 1, "size_bytes": 16230, "version_id": "4acf498d-dfe2-4263-8f07-3b837a7e057e", "records_read": 200, "data_source_id": 1, "records_passed": 200, "ingestion_job_id": 8, "records_rejected": 0}	2026-11-16 23:04:03.180179+07
21	BRONZE	s3://bronze/hr/2026/11/16/job-8/employee.csv	SILVER	employee	{"domain": "hr", "entity": "employee", "status": "PUBLISHED", "records_read": 200, "records_passed": 200, "cleaning_run_id": "5733877e-5dbb-446b-8cb6-9a69f3d508a7", "ingestion_job_id": 8, "records_collapsed": 0, "silver_snapshot_id": "3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c", "records_quarantined": 0}	2026-11-16 23:04:04.061092+07
22	SILVER	3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c	GOLD	a94b1c6b-4f98-409f-8471-964aa49ee81a	{"mode": "production", "run_ref": "a94b1c6b-4f98-409f-8471-964aa49ee81a", "derivations": [{"inputs": [{"uri": "s3://silver/silver/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c/manifest-f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb.json", "layer": "SILVER", "domain": "hr", "version": "3ec66dbd-4a11-4d36-b04b-bb6e2df9ed5c", "asset_id": "silver:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "f31505968e881973c9236f345740cf7e08a117c05f87d40b441237fbb3e53efb", "producer_run_ref": null}], "output": {"uri": "s3://gold/gold/production/e58c889b9de44570f4dc89f05d8b1a5c6275160bd22f2745b99ce42a5704b02c/a94b1c6b-4f98-409f-8471-964aa49ee81a/aggregate-9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec.parquet", "layer": "GOLD", "domain": "hr", "version": "a94b1c6b-4f98-409f-8471-964aa49ee81a", "asset_id": "gold:production:auto-e270bc9994f1530b4a540c0daa6c", "checksum": "9aeab609fb825a0e0132a1336843c750045c97dcfde571f0009966689375f7ec", "producer_run_ref": "a94b1c6b-4f98-409f-8471-964aa49ee81a"}, "transformations": [{"name": "GROUPED_RECORD_COUNTS", "version": "group-counts-v1", "fingerprint": "9fed47a1e2dbd5c6a0dc52254d3450e59a5866ea7f73582163810249ec120ffa"}]}], "parent_snapshot": null, "contract_version": "dataset-lineage-v1"}	2026-11-16 23:04:04.907155+07
\.


--
-- Data for Name: position; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app."position" (position_id, position_code, position_name, position_level) FROM stdin;
\.


--
-- Data for Name: program; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.program (program_id, program_code, program_name, major_id, degree_level, duration_years, tuition_fee_per_credit, status) FROM stdin;
\.


--
-- Data for Name: prompt_template; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.prompt_template (prompt_template_id, template_name, version, template_text, is_active, created_at) FROM stdin;
\.


--
-- Data for Name: qualification; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.qualification (qualification_id, qualification_name, qualification_type) FROM stdin;
\.


--
-- Data for Name: quarantine_record; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.quarantine_record (record_id, cleaning_run_id, locator, record_position, issues, error_codes) FROM stdin;
ea20e16d-9412-4bed-8752-b693d6001c83	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:2	1	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:2"}]	["INVALID_STRING_TYPE"]
69caf89e-9b80-4d39-80d1-ab1b6fa75328	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:3	2	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:3"}]	["INVALID_STRING_TYPE"]
57f5d2da-6122-462b-85db-af94b06ff851	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:4	3	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:4"}]	["INVALID_STRING_TYPE"]
f55b16e1-9103-4b74-b3b7-b937d9ac5d2a	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:5	4	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:5"}]	["INVALID_STRING_TYPE"]
8ed28e56-718f-4320-96d4-543b520a7c4e	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:6	5	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:6"}]	["INVALID_STRING_TYPE"]
ee88fad5-2e2b-4661-b7ec-a2ab4b7b53f7	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:7	6	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:7"}]	["INVALID_STRING_TYPE"]
595ec716-2d01-494e-954d-88a4642da69d	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:8	7	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:8"}]	["INVALID_STRING_TYPE"]
f65a5cb1-b5cf-4294-87fa-c4eacbc51289	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:9	8	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:9"}]	["INVALID_STRING_TYPE"]
95410299-69fa-470d-be60-9d66853a8f05	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:10	9	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:10"}]	["INVALID_STRING_TYPE"]
e74093f9-04e2-4f07-b537-c12b4e53bb9c	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:11	10	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:11"}]	["INVALID_STRING_TYPE"]
57902a3f-8167-4e4c-ad7c-58c5f5605ee3	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:12	11	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:12"}]	["INVALID_STRING_TYPE"]
63a412cd-c103-4e25-a795-0a482e9b22ad	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:13	12	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:13"}]	["INVALID_STRING_TYPE"]
2d1499f7-3c39-4919-a504-7d7658adc97d	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:14	13	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:14"}]	["INVALID_STRING_TYPE"]
9cbd4c09-634a-431c-bb94-85d5a4f4a8c8	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:15	14	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:15"}]	["INVALID_STRING_TYPE"]
638f8480-3f9b-4fd7-9d21-038827eebeaa	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:16	15	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:16"}]	["INVALID_STRING_TYPE"]
1465c4ad-16a8-43ac-adac-91e8861d8dd1	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:17	16	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:17"}]	["INVALID_STRING_TYPE"]
571973d3-1990-4dec-b56d-a1f203be0bd0	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:18	17	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:18"}]	["INVALID_STRING_TYPE"]
981a92df-59d5-47a0-baa2-69afdb6f53a3	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:19	18	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:19"}]	["INVALID_STRING_TYPE"]
7f8cabb2-627b-4726-b1dd-0ca5aab3d974	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:20	19	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:20"}]	["INVALID_STRING_TYPE"]
953c6382-bd25-46fa-a126-2324e85cfa15	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:21	20	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:21"}]	["INVALID_STRING_TYPE"]
bbbd1b96-0fda-4d13-aee2-f0b80e70645a	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:22	21	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:22"}]	["INVALID_STRING_TYPE"]
609e1db2-e71a-4db3-a2a9-3d57aba53823	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:23	22	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:23"}]	["INVALID_STRING_TYPE"]
8b894ca7-f59a-4d32-b405-3bb54bbb59a0	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:24	23	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:24"}]	["INVALID_STRING_TYPE"]
eb8d8156-d29a-4f3f-89b5-1a8d99ea1c3c	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:25	24	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:25"}]	["INVALID_STRING_TYPE"]
719eb1d6-5c8c-4383-b218-b7917dcf4803	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:26	25	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:26"}]	["INVALID_STRING_TYPE"]
efd5a487-7b00-494a-91b8-a33ffdfe3a39	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:27	26	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:27"}]	["INVALID_STRING_TYPE"]
3149a3fa-6800-474e-bb97-c8a75a43c102	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:28	27	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:28"}]	["INVALID_STRING_TYPE"]
0ba842de-60db-4687-b541-a0866b9c06f4	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:29	28	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:29"}]	["INVALID_STRING_TYPE"]
a26ad978-58b9-4e0b-9356-47b4a3c976c1	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:30	29	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:30"}]	["INVALID_STRING_TYPE"]
82414bea-6386-45ac-9c8c-5176c6b17c4d	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:31	30	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:31"}]	["INVALID_STRING_TYPE"]
c355834e-d692-48f6-934d-87de49f78ef3	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:32	31	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:32"}]	["INVALID_STRING_TYPE"]
9e47abd2-06d3-4c61-92dd-a78492294285	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:33	32	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:33"}]	["INVALID_STRING_TYPE"]
57953274-290c-46ad-8504-3f341f6545cb	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:34	33	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:34"}]	["INVALID_STRING_TYPE"]
6d727dd5-379a-4eb1-845c-c794381c6dc7	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:35	34	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:35"}]	["INVALID_STRING_TYPE"]
01ffc055-52f0-4cc3-96f6-64c9c1f47412	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:36	35	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:36"}]	["INVALID_STRING_TYPE"]
3fb27d69-8817-496e-b73b-2a054324e0d6	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:37	36	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:37"}]	["INVALID_STRING_TYPE"]
51a17f80-5391-4e08-b068-0ce56cf29494	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:38	37	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:38"}]	["INVALID_STRING_TYPE"]
d4767588-6c35-4ced-8b19-636de45c9e72	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:39	38	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:39"}]	["INVALID_STRING_TYPE"]
99b5cc89-478e-43a1-becd-ee3af3503b71	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:40	39	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:40"}]	["INVALID_STRING_TYPE"]
6ec5c713-ad81-450c-8437-c2ae325424af	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:41	40	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:41"}]	["INVALID_STRING_TYPE"]
e1bf7b59-ad3b-414f-a0a9-ba704128f2a4	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:42	41	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:42"}]	["INVALID_STRING_TYPE"]
0ecdb085-c71e-4153-b2e7-56f9c6dd3a73	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:43	42	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:43"}]	["INVALID_STRING_TYPE"]
ca216d8e-c162-4e5a-b968-397a7fd8f1aa	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:44	43	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:44"}]	["INVALID_STRING_TYPE"]
4da257c5-33fc-4e16-aad2-4e4d65d0e002	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:45	44	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:45"}]	["INVALID_STRING_TYPE"]
8b2e1d15-be6e-41e6-a8af-05d78378d811	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:46	45	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:46"}]	["INVALID_STRING_TYPE"]
a336bcb5-2d55-49aa-bde8-067f99cc2f92	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:47	46	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:47"}]	["INVALID_STRING_TYPE"]
f6940c5b-4522-4443-a7c4-20ee0c99644f	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:48	47	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:48"}]	["INVALID_STRING_TYPE"]
76d5d493-c774-4d1b-a997-34663e0257e4	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:49	48	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:49"}]	["INVALID_STRING_TYPE"]
02876cbe-12ca-4883-8d14-9dffb975a2f5	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:50	49	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:50"}]	["INVALID_STRING_TYPE"]
9790dd57-9584-4920-b571-1dec2bbe8e07	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:51	50	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:51"}]	["INVALID_STRING_TYPE"]
cf40b000-d66f-42fc-ab01-7feca3d90cb3	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:52	51	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:52"}]	["INVALID_STRING_TYPE"]
144916b1-269b-434d-a104-6ec24396708c	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:53	52	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:53"}]	["INVALID_STRING_TYPE"]
a37a9eb8-9a06-4b1c-a998-da9d2cabd4c8	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:54	53	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:54"}]	["INVALID_STRING_TYPE"]
5bb510ee-1ea4-43e3-83fb-4489715201e7	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:55	54	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:55"}]	["INVALID_STRING_TYPE"]
94b5c54b-c739-44f1-8f78-c44dfef7db7d	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:56	55	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:56"}]	["INVALID_STRING_TYPE"]
ce51a94a-9f64-4b1a-8755-4a80bb66d0bc	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:57	56	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:57"}]	["INVALID_STRING_TYPE"]
e9332ec4-bf9d-49ca-a715-b01745268e8b	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:58	57	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:58"}]	["INVALID_STRING_TYPE"]
5903c7ba-ad89-400d-924e-58663f939efb	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:59	58	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:59"}]	["INVALID_STRING_TYPE"]
217b9e14-0630-4988-95de-bded62dca730	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:60	59	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:60"}]	["INVALID_STRING_TYPE"]
2dcba239-6532-44b3-878f-bafbf7ac0714	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:61	60	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:61"}]	["INVALID_STRING_TYPE"]
aff6f808-5aaa-46a8-81c1-aaf7f97f81e7	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:62	61	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:62"}]	["INVALID_STRING_TYPE"]
984a64c6-cb66-4e5e-a75d-d533259f8e91	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:63	62	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:63"}]	["INVALID_STRING_TYPE"]
ed40481f-a2c2-4356-88cb-dd9ee0e962cb	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:64	63	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:64"}]	["INVALID_STRING_TYPE"]
f0fdbe17-367c-45c5-ae4c-3a05adc78d99	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:65	64	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:65"}]	["INVALID_STRING_TYPE"]
f447e92e-f26a-4865-8cb5-b537ff3fc1bf	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:66	65	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:66"}]	["INVALID_STRING_TYPE"]
76fce996-140b-42d4-be94-b6f96a916b77	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:67	66	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:67"}]	["INVALID_STRING_TYPE"]
6befe86f-e998-4a05-b188-2604012c2545	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:68	67	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:68"}]	["INVALID_STRING_TYPE"]
e1a49b37-20d7-49b3-ad93-11174148d400	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:69	68	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:69"}]	["INVALID_STRING_TYPE"]
739b89ef-9a6a-4b3d-8441-f325bc91ce81	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:70	69	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:70"}]	["INVALID_STRING_TYPE"]
dc4c1866-94f3-4242-b95d-15248d147c75	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:71	70	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:71"}]	["INVALID_STRING_TYPE"]
4ddab78b-2f7d-4ee2-821a-3b64189b2203	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:72	71	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:72"}]	["INVALID_STRING_TYPE"]
dc596fd2-e57d-47ee-8b46-9b20cf5958c5	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:73	72	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:73"}]	["INVALID_STRING_TYPE"]
658dcfe5-9962-4364-a797-e1e04f2d6ee7	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:74	73	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:74"}]	["INVALID_STRING_TYPE"]
963bdb37-53d8-4d3c-8f1a-bd35d668a9f4	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:75	74	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:75"}]	["INVALID_STRING_TYPE"]
159d9048-2405-4e60-b42e-11ced24fc78e	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:77	76	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:77"}]	["INVALID_STRING_TYPE"]
b1946ab8-5d5a-4a4d-b516-54227a46f21e	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:78	77	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:78"}]	["INVALID_STRING_TYPE"]
16622518-8e57-4347-af9f-aa075793a1bc	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:79	78	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:79"}]	["INVALID_STRING_TYPE"]
4ada08ca-8997-4e52-bdc5-123b1dd7c12b	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:80	79	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:80"}]	["INVALID_STRING_TYPE"]
7f9bbb75-809b-472b-8fb9-bdf97c918b83	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:81	80	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:81"}]	["INVALID_STRING_TYPE"]
bdd8a3ed-7ece-45bb-8e70-1adca50d143a	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:82	81	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:82"}]	["INVALID_STRING_TYPE"]
80e5449e-8061-4c8b-a370-e7970363b4f5	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:83	82	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:83"}]	["INVALID_STRING_TYPE"]
7904d685-7b41-4c41-94b9-9b89a1405329	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:84	83	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:84"}]	["INVALID_STRING_TYPE"]
60f6b7b9-4b83-4b9b-a48e-d06ccc4a0879	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:85	84	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:85"}]	["INVALID_STRING_TYPE"]
285d0e00-9df6-44ef-8c42-1570887e44a5	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:86	85	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:86"}]	["INVALID_STRING_TYPE"]
00a49450-1581-4684-a2e8-c949abf2b547	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:87	86	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:87"}]	["INVALID_STRING_TYPE"]
a0b4881e-647f-400e-baa5-4d273d26630b	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:88	87	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:88"}]	["INVALID_STRING_TYPE"]
4e07a8e9-9a57-4423-88a4-329c5db3949a	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:89	88	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:89"}]	["INVALID_STRING_TYPE"]
ed043532-0058-48c7-ad7c-ba09775bf6f1	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:90	89	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:90"}]	["INVALID_STRING_TYPE"]
b214d06b-7a12-4d58-b9e1-3d96b425d0cc	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:91	90	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:91"}]	["INVALID_STRING_TYPE"]
9c303e44-5d74-4c5e-9e35-88c2334bcbd6	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:92	91	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:92"}]	["INVALID_STRING_TYPE"]
3a29097d-9901-4d6c-8221-1136a23f8520	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:93	92	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:93"}]	["INVALID_STRING_TYPE"]
11891f0a-b4da-47d7-962f-28acaa405cae	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:94	93	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:94"}]	["INVALID_STRING_TYPE"]
c11bc5b6-8b49-44ce-920f-cc5042565582	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:95	94	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:95"}]	["INVALID_STRING_TYPE"]
b17a2078-f8ec-4917-ab96-b7d95fc20399	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:96	95	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:96"}]	["INVALID_STRING_TYPE"]
b2669405-bf53-42a9-a9aa-c2ae1c24b118	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:97	96	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:97"}]	["INVALID_STRING_TYPE"]
f6d332bb-8c5b-4758-aca6-bd2919bf8b32	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:98	97	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:98"}]	["INVALID_STRING_TYPE"]
ff908f8f-f220-4e2d-9822-0cd0a44f4c85	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:99	98	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:99"}]	["INVALID_STRING_TYPE"]
cd6214b9-95c0-4272-b1a7-30d22dc4f60a	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:100	99	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:100"}]	["INVALID_STRING_TYPE"]
014e86e9-f0cf-4fa5-a3aa-f47767c2ff85	dee1dae0-81cd-47ce-affd-5ac9e8af266b	sheet:Academic:row:101	100	[{"rule_id": "CLN-VAL-01", "rule_version": "auto-source-v1:74a3b5a28f138e6cbae3eee586488f456b0998fa769b8ca13c073daba8f486c5", "domain": "academic", "entity": "file_3", "field": "score", "code": "INVALID_STRING_TYPE", "severity": "ERROR", "disposition": "QUARANTINED", "locator": "sheet:Academic:row:101"}]	["INVALID_STRING_TYPE"]
\.


--
-- Data for Name: query_execution; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.query_execution (query_execution_id, generated_sql_id, executed_at, execution_status, row_count, execution_time_ms, result_ref, error_message) FROM stdin;
\.


--
-- Data for Name: query_request; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.query_request (query_request_id, app_user_id, question_text, language, detected_intent, submitted_at, status) FROM stdin;
\.


--
-- Data for Name: role; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.role (role_id, role_code, role_name, description) FROM stdin;
1	ADMIN	Quản trị viên hệ thống	Quản trị hệ thống, tài khoản và phân quyền
2	DATA_ADMIN	Quản trị dữ liệu	Quản lý nguồn, pipeline, chất lượng dữ liệu; truy vấn AI và phân tích xuyên phòng ban
3	VIEWER	Ban Giám hiệu	Xem dashboard và báo cáo tổng hợp
4	ADMISSIONS_MANAGER	Trưởng phòng Tuyển sinh	Trưởng phòng Tuyển sinh
5	ADMISSIONS_STAFF	Nhân viên Tuyển sinh	Nhân viên phòng Tuyển sinh
6	ACADEMIC_MANAGER	Trưởng phòng Đào tạo	Trưởng phòng Đào tạo
7	ACADEMIC_STAFF	Nhân viên Đào tạo	Nhân viên phòng Đào tạo
8	HR_MANAGER	Trưởng phòng Nhân sự	Trưởng phòng Nhân sự
9	HR_STAFF	Nhân viên Nhân sự	Nhân viên phòng Nhân sự
\.


--
-- Data for Name: role_permission; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.role_permission (role_id, permission_id) FROM stdin;
1	1
1	2
1	3
1	4
1	5
1	6
1	7
1	8
1	9
1	10
1	11
1	12
1	13
1	14
1	15
1	16
1	17
1	18
1	19
1	20
2	1
2	4
2	5
2	6
2	7
2	8
2	9
2	10
2	11
2	12
2	13
2	14
2	15
2	16
2	17
3	1
3	4
3	17
4	1
4	6
4	8
4	15
4	16
4	17
5	1
5	6
5	8
5	15
5	16
5	17
6	1
6	6
6	8
6	15
6	16
6	17
7	1
7	6
7	8
7	15
7	16
7	17
8	1
8	6
8	8
8	15
8	16
8	17
9	1
9	6
9	8
9	15
9	16
9	17
\.


--
-- Data for Name: schema_migration; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.schema_migration (migration_name, applied_at) FROM stdin;
20261015_ingestion_execution.sql	2026-11-16 20:33:49.574273+07
20261015_p5_silver.sql	2026-11-16 20:33:49.587834+07
20261015_s4_crosswalk.sql	2026-11-16 20:33:49.621939+07
20261022_bronze_validation.sql	2026-11-16 20:33:49.622679+07
20261103_catalog_metadata.sql	2026-11-16 20:33:49.623202+07
20261106_cleaning_runs.sql	2026-11-16 20:33:49.624405+07
20261106_data_quality_issues.sql	2026-11-16 20:33:49.625611+07
20261106_issue_assignees.sql	2026-11-16 20:33:49.626511+07
20261106_pipeline_lineage.sql	2026-11-16 20:33:49.627684+07
20261106_quarantine_records.sql	2026-11-16 20:33:49.628648+07
20261109_ingestion_uploads.sql	2026-11-16 20:33:49.629417+07
20261110_ingestion_reliability_mvp.sql	2026-11-16 20:33:49.630732+07
20261110_silver_management.sql	2026-11-16 20:33:49.63342+07
20261112_silver_profile_governance.sql	2026-11-16 20:33:49.635635+07
20261115_auto_silver_pipeline.sql	2026-11-16 20:33:49.638759+07
20261116_department_catalog.sql	2026-11-16 20:33:49.639825+07
20261116_gold_pipeline.sql	2026-11-16 20:33:49.659865+07
20261116_notifications.sql	2026-11-16 20:33:49.662325+07
20261116_return_to_uploader.sql	2026-11-16 20:33:49.663097+07
20261116_runtime_reference_data.sql	2026-11-16 20:33:49.663893+07
20261116_silver_default_automation.sql	2026-11-16 20:33:49.665362+07
20261116_storage_model_v2.sql	2026-11-16 23:02:28.218489+07
\.


--
-- Data for Name: schema_retrieval; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.schema_retrieval (schema_retrieval_id, query_request_id, catalog_table_id, retrieval_rank, relevance_score) FROM stdin;
\.


--
-- Data for Name: semester; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.semester (semester_id, semester_code, academic_year, start_date, end_date, status) FROM stdin;
\.


--
-- Data for Name: source_system; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.source_system (source_system_id, source_code, source_name, source_type, description, owner_department_id, is_active, created_at) FROM stdin;
1	ADM_SYS	Admissions System	FILE_STORAGE	Hệ thống quản lý tuyển sinh - cung cấp dữ liệu thí sinh, hồ sơ, phương thức tuyển sinh	\N	t	2026-11-16 20:22:05.760471
2	ACA_SYS	Academic System	RDBMS	Hệ thống quản lý đào tạo - cung cấp dữ liệu sinh viên, chương trình, môn học, điểm số	\N	t	2026-11-16 20:22:05.760471
3	HR_SYS	Human Resources System	RDBMS	Hệ thống quản lý nhân sự - cung cấp dữ liệu nhân viên, giảng viên, đơn vị, chức vụ	\N	t	2026-11-16 20:22:05.760471
4	FILE_SHARE	Shared File Storage	FILE_STORAGE	Kho lưu trữ file dùng chung (CSV, Excel) phục vụ ingestion thủ công	\N	t	2026-11-16 20:22:05.760471
34	UPLOAD_HR	File tải lên — hr	UPLOAD	\N	\N	t	2026-11-16 21:06:26.900815
35	UPLOAD_ACADEMIC	File tải lên — academic	UPLOAD	\N	\N	t	2026-11-16 21:07:15.977378
\.


--
-- Data for Name: storage_migration_checkpoint; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.storage_migration_checkpoint (table_name, row_count, fingerprint, archive_key, verified_at) FROM stdin;
silver_source_profile	0	d41d8cd98f00b204e9800998ecf8427e	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_profile_revision	0	d41d8cd98f00b204e9800998ecf8427e	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_domain_owner	0	d41d8cd98f00b204e9800998ecf8427e	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_profile_approval	0	d41d8cd98f00b204e9800998ecf8427e	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_run	6	5004caca88210d07102c291ed6d2f6c1	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_snapshot	6	fbc75126dd3f88f814b6a9f4d008584f	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_head	5	43c33cf4e76521651808ef7ba5176684	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_activation	0	d41d8cd98f00b204e9800998ecf8427e	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
silver_worker_heartbeat	1	8221912e996e487fc443037cb1f712eb	silver/control/v1/b72826c0da9bd195845fcaa2463b5c089a0754999c886f6389ae6ba8e6fa747d/legacy-export/20261116T230227773893.json	2026-11-16 23:02:27.764816+07
\.


--
-- Data for Name: student; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.student (student_id, student_code, applicant_id, admission_id, full_name, date_of_birth, gender, program_id, admission_year_id, student_status, created_at) FROM stdin;
\.


--
-- Data for Name: transformation_step; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.transformation_step (transformation_step_id, ingestion_job_id, source_dataset_id, target_dataset_id, step_name, step_order, transformation_type, transformation_expression, rows_input, rows_output, status, executed_at) FROM stdin;
\.


--
-- Data for Name: user_role; Type: TABLE DATA; Schema: app; Owner: -
--

COPY app.user_role (app_user_id, role_id) FROM stdin;
1	1
2	2
3	3
4	4
5	5
6	6
7	7
8	8
9	9
\.


--
-- Data for Name: aggregate; Type: TABLE DATA; Schema: gold; Owner: -
--

COPY gold.aggregate (job_id, entity, metric, dimensions, value) FROM stdin;
0014c6c1-5516-40f3-ade7-54b129fec649	assignment	record_count	{}	200
0014c6c1-5516-40f3-ade7-54b129fec649	assignment	record_count_by_department_id	{"department_id": "2"}	100
0014c6c1-5516-40f3-ade7-54b129fec649	assignment	record_count_by_department_id	{"department_id": "4"}	100
0014c6c1-5516-40f3-ade7-54b129fec649	assignment	record_count_by_position_id	{"position_id": "1"}	100
0014c6c1-5516-40f3-ade7-54b129fec649	assignment	record_count_by_position_id	{"position_id": "2"}	100
0014c6c1-5516-40f3-ade7-54b129fec649	assignment	record_count_by_assignment_status	{"assignment_status": "Active"}	200
9c122de6-d11c-4db4-872d-60a8970bcb3f	file_2	record_count	{}	100
228dd533-ce62-419e-805e-a3ab4c8b2d65	department	record_count	{}	4
a10359dd-879c-4d62-b226-82d26ee14dd8	employee	record_count	{}	200
a10359dd-879c-4d62-b226-82d26ee14dd8	employee	record_count_by_gender	{"gender": "Female"}	100
a10359dd-879c-4d62-b226-82d26ee14dd8	employee	record_count_by_gender	{"gender": "Male"}	100
a10359dd-879c-4d62-b226-82d26ee14dd8	employee	record_count_by_employee_status	{"employee_status": "Active"}	200
a10359dd-879c-4d62-b226-82d26ee14dd8	employee	record_count_by_birth_year	{"birth_year": 1975}	200
87f44121-73fe-4098-a175-06254ab5ce81	lecturer	record_count	{}	100
87f44121-73fe-4098-a175-06254ab5ce81	lecturer	record_count_by_academic_degree	{"academic_degree": "B.Sc."}	100
87f44121-73fe-4098-a175-06254ab5ce81	lecturer	record_count_by_academic_title	{"academic_title": null}	100
59e65f53-3d4e-41aa-80a3-b577773e7945	assignment	record_count	{}	200
59e65f53-3d4e-41aa-80a3-b577773e7945	assignment	record_count_by_department_id	{"department_id": "2"}	100
59e65f53-3d4e-41aa-80a3-b577773e7945	assignment	record_count_by_department_id	{"department_id": "4"}	100
59e65f53-3d4e-41aa-80a3-b577773e7945	assignment	record_count_by_position_id	{"position_id": "1"}	100
59e65f53-3d4e-41aa-80a3-b577773e7945	assignment	record_count_by_position_id	{"position_id": "2"}	100
59e65f53-3d4e-41aa-80a3-b577773e7945	assignment	record_count_by_assignment_status	{"assignment_status": "Active"}	200
a94b1c6b-4f98-409f-8471-964aa49ee81a	employee	record_count	{}	200
a94b1c6b-4f98-409f-8471-964aa49ee81a	employee	record_count_by_gender	{"gender": "Female"}	100
a94b1c6b-4f98-409f-8471-964aa49ee81a	employee	record_count_by_gender	{"gender": "Male"}	100
a94b1c6b-4f98-409f-8471-964aa49ee81a	employee	record_count_by_employee_status	{"employee_status": "Active"}	200
a94b1c6b-4f98-409f-8471-964aa49ee81a	employee	record_count_by_birth_year	{"birth_year": 1975}	200
\.


--
-- Name: admission_admission_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.admission_admission_id_seq', 1, false);


--
-- Name: admission_method_admission_method_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.admission_method_admission_method_id_seq', 1, false);


--
-- Name: admission_year_admission_year_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.admission_year_admission_year_id_seq', 1, false);


--
-- Name: ai_model_ai_model_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.ai_model_ai_model_id_seq', 1, false);


--
-- Name: app_user_app_user_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.app_user_app_user_id_seq', 33, true);


--
-- Name: applicant_applicant_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.applicant_applicant_id_seq', 1, false);


--
-- Name: assignment_assignment_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.assignment_assignment_id_seq', 1, false);


--
-- Name: canonical_identity_canonical_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.canonical_identity_canonical_id_seq', 1, false);


--
-- Name: catalog_column_catalog_column_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.catalog_column_catalog_column_id_seq', 1, false);


--
-- Name: catalog_table_catalog_table_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.catalog_table_catalog_table_id_seq', 1, false);


--
-- Name: chart_widget_chart_widget_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.chart_widget_chart_widget_id_seq', 1, false);


--
-- Name: class_class_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.class_class_id_seq', 1, false);


--
-- Name: class_lecturer_class_lecturer_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.class_lecturer_class_lecturer_id_seq', 1, false);


--
-- Name: course_course_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.course_course_id_seq', 5, true);


--
-- Name: curriculum_course_curriculum_course_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.curriculum_course_curriculum_course_id_seq', 1, false);


--
-- Name: curriculum_curriculum_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.curriculum_curriculum_id_seq', 1, false);


--
-- Name: dashboard_dashboard_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.dashboard_dashboard_id_seq', 1, false);


--
-- Name: data_layer_data_layer_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_layer_data_layer_id_seq', 15, true);


--
-- Name: data_lineage_data_lineage_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_lineage_data_lineage_id_seq', 1, false);


--
-- Name: data_processing_run_run_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_processing_run_run_id_seq', 1, false);


--
-- Name: data_quality_issue_event_event_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_quality_issue_event_event_id_seq', 1, true);


--
-- Name: data_quality_rule_data_quality_rule_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_quality_rule_data_quality_rule_id_seq', 1, false);


--
-- Name: data_quality_run_data_quality_run_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_quality_run_data_quality_run_id_seq', 1, false);


--
-- Name: data_source_data_source_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.data_source_data_source_id_seq', 3, true);


--
-- Name: dataset_dataset_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.dataset_dataset_id_seq', 3, true);


--
-- Name: department_department_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.department_department_id_seq', 51, true);


--
-- Name: employee_employee_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.employee_employee_id_seq', 200, true);


--
-- Name: enrollment_enrollment_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.enrollment_enrollment_id_seq', 1, false);


--
-- Name: enrollment_grade_enrollment_grade_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.enrollment_grade_enrollment_grade_id_seq', 1, false);


--
-- Name: generated_sql_generated_sql_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.generated_sql_generated_sql_id_seq', 1, false);


--
-- Name: ingestion_job_ingestion_job_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.ingestion_job_ingestion_job_id_seq', 8, true);


--
-- Name: kpi_kpi_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.kpi_kpi_id_seq', 1, false);


--
-- Name: kpi_value_kpi_value_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.kpi_value_kpi_value_id_seq', 1, false);


--
-- Name: login_session_login_session_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.login_session_login_session_id_seq', 14, true);


--
-- Name: major_major_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.major_major_id_seq', 1, false);


--
-- Name: permission_permission_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.permission_permission_id_seq', 33, true);


--
-- Name: pipeline_lineage_lineage_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.pipeline_lineage_lineage_id_seq', 22, true);


--
-- Name: position_position_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.position_position_id_seq', 1, false);


--
-- Name: program_program_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.program_program_id_seq', 1, false);


--
-- Name: prompt_template_prompt_template_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.prompt_template_prompt_template_id_seq', 1, false);


--
-- Name: qualification_qualification_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.qualification_qualification_id_seq', 1, false);


--
-- Name: query_execution_query_execution_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.query_execution_query_execution_id_seq', 1, false);


--
-- Name: query_request_query_request_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.query_request_query_request_id_seq', 1, false);


--
-- Name: role_role_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.role_role_id_seq', 33, true);


--
-- Name: schema_retrieval_schema_retrieval_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.schema_retrieval_schema_retrieval_id_seq', 1, false);


--
-- Name: semester_semester_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.semester_semester_id_seq', 1, false);


--
-- Name: source_system_source_system_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.source_system_source_system_id_seq', 36, true);


--
-- Name: student_student_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.student_student_id_seq', 1, false);


--
-- Name: transformation_step_transformation_step_id_seq; Type: SEQUENCE SET; Schema: app; Owner: -
--

SELECT pg_catalog.setval('app.transformation_step_transformation_step_id_seq', 1, false);


--
-- Name: admission_method admission_method_method_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission_method
    ADD CONSTRAINT admission_method_method_code_key UNIQUE (method_code);


--
-- Name: admission_method admission_method_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission_method
    ADD CONSTRAINT admission_method_pkey PRIMARY KEY (admission_method_id);


--
-- Name: admission admission_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission
    ADD CONSTRAINT admission_pkey PRIMARY KEY (admission_id);


--
-- Name: admission_year admission_year_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission_year
    ADD CONSTRAINT admission_year_pkey PRIMARY KEY (admission_year_id);


--
-- Name: admission_year admission_year_year_label_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission_year
    ADD CONSTRAINT admission_year_year_label_key UNIQUE (year_label);


--
-- Name: ai_model ai_model_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ai_model
    ADD CONSTRAINT ai_model_pkey PRIMARY KEY (ai_model_id);


--
-- Name: app_user app_user_employee_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.app_user
    ADD CONSTRAINT app_user_employee_id_key UNIQUE (employee_id);


--
-- Name: app_user app_user_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.app_user
    ADD CONSTRAINT app_user_pkey PRIMARY KEY (app_user_id);


--
-- Name: app_user app_user_username_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.app_user
    ADD CONSTRAINT app_user_username_key UNIQUE (username);


--
-- Name: applicant applicant_national_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.applicant
    ADD CONSTRAINT applicant_national_id_key UNIQUE (national_id);


--
-- Name: applicant applicant_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.applicant
    ADD CONSTRAINT applicant_pkey PRIMARY KEY (applicant_id);


--
-- Name: assignment assignment_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.assignment
    ADD CONSTRAINT assignment_pkey PRIMARY KEY (assignment_id);


--
-- Name: auto_pipeline_attempt auto_pipeline_attempt_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_attempt
    ADD CONSTRAINT auto_pipeline_attempt_pkey PRIMARY KEY (job_id, attempt_number);


--
-- Name: auto_pipeline_job auto_pipeline_job_ingestion_job_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_job
    ADD CONSTRAINT auto_pipeline_job_ingestion_job_id_key UNIQUE (ingestion_job_id);


--
-- Name: auto_pipeline_job auto_pipeline_job_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_job
    ADD CONSTRAINT auto_pipeline_job_pkey PRIMARY KEY (job_id);


--
-- Name: auto_pipeline_policy auto_pipeline_policy_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_policy
    ADD CONSTRAINT auto_pipeline_policy_pkey PRIMARY KEY (dataset_id);


--
-- Name: canonical_identity canonical_identity_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.canonical_identity
    ADD CONSTRAINT canonical_identity_pkey PRIMARY KEY (canonical_id);


--
-- Name: catalog_column catalog_column_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_column
    ADD CONSTRAINT catalog_column_pkey PRIMARY KEY (catalog_column_id);


--
-- Name: catalog_metadata catalog_metadata_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_metadata
    ADD CONSTRAINT catalog_metadata_pkey PRIMARY KEY (domain_id, dataset);


--
-- Name: catalog_table catalog_table_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_table
    ADD CONSTRAINT catalog_table_pkey PRIMARY KEY (catalog_table_id);


--
-- Name: chart_widget chart_widget_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.chart_widget
    ADD CONSTRAINT chart_widget_pkey PRIMARY KEY (chart_widget_id);


--
-- Name: class class_class_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class
    ADD CONSTRAINT class_class_code_key UNIQUE (class_code);


--
-- Name: class_lecturer class_lecturer_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class_lecturer
    ADD CONSTRAINT class_lecturer_pkey PRIMARY KEY (class_lecturer_id);


--
-- Name: class class_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class
    ADD CONSTRAINT class_pkey PRIMARY KEY (class_id);


--
-- Name: cleaning_run cleaning_run_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.cleaning_run
    ADD CONSTRAINT cleaning_run_pkey PRIMARY KEY (run_id);


--
-- Name: course course_course_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.course
    ADD CONSTRAINT course_course_code_key UNIQUE (course_code);


--
-- Name: course course_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.course
    ADD CONSTRAINT course_pkey PRIMARY KEY (course_id);


--
-- Name: crosswalk_release crosswalk_release_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.crosswalk_release
    ADD CONSTRAINT crosswalk_release_pkey PRIMARY KEY (version);


--
-- Name: curriculum_course curriculum_course_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum_course
    ADD CONSTRAINT curriculum_course_pkey PRIMARY KEY (curriculum_course_id);


--
-- Name: curriculum curriculum_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum
    ADD CONSTRAINT curriculum_pkey PRIMARY KEY (curriculum_id);


--
-- Name: dashboard dashboard_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dashboard
    ADD CONSTRAINT dashboard_pkey PRIMARY KEY (dashboard_id);


--
-- Name: data_layer data_layer_layer_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_layer
    ADD CONSTRAINT data_layer_layer_code_key UNIQUE (layer_code);


--
-- Name: data_layer data_layer_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_layer
    ADD CONSTRAINT data_layer_pkey PRIMARY KEY (data_layer_id);


--
-- Name: data_lineage data_lineage_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_lineage
    ADD CONSTRAINT data_lineage_pkey PRIMARY KEY (data_lineage_id);


--
-- Name: data_processing_run data_processing_run_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_processing_run
    ADD CONSTRAINT data_processing_run_pkey PRIMARY KEY (run_id);


--
-- Name: data_quality_issue_event data_quality_issue_event_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue_event
    ADD CONSTRAINT data_quality_issue_event_pkey PRIMARY KEY (event_id);


--
-- Name: data_quality_issue data_quality_issue_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_pkey PRIMARY KEY (issue_id);


--
-- Name: data_quality_issue_record data_quality_issue_record_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue_record
    ADD CONSTRAINT data_quality_issue_record_pkey PRIMARY KEY (issue_id, record_id);


--
-- Name: data_quality_rule data_quality_rule_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_rule
    ADD CONSTRAINT data_quality_rule_pkey PRIMARY KEY (data_quality_rule_id);


--
-- Name: data_quality_rule data_quality_rule_rule_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_rule
    ADD CONSTRAINT data_quality_rule_rule_code_key UNIQUE (rule_code);


--
-- Name: data_quality_run data_quality_run_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_run
    ADD CONSTRAINT data_quality_run_pkey PRIMARY KEY (data_quality_run_id);


--
-- Name: data_source data_source_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_source
    ADD CONSTRAINT data_source_pkey PRIMARY KEY (data_source_id);


--
-- Name: dataset dataset_dataset_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dataset
    ADD CONSTRAINT dataset_dataset_code_key UNIQUE (dataset_code);


--
-- Name: dataset dataset_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dataset
    ADD CONSTRAINT dataset_pkey PRIMARY KEY (dataset_id);


--
-- Name: department department_department_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.department
    ADD CONSTRAINT department_department_code_key UNIQUE (department_code);


--
-- Name: department department_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.department
    ADD CONSTRAINT department_pkey PRIMARY KEY (department_id);


--
-- Name: employee employee_email_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.employee
    ADD CONSTRAINT employee_email_key UNIQUE (email);


--
-- Name: employee employee_employee_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.employee
    ADD CONSTRAINT employee_employee_code_key UNIQUE (employee_code);


--
-- Name: employee employee_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.employee
    ADD CONSTRAINT employee_pkey PRIMARY KEY (employee_id);


--
-- Name: employee_qualification employee_qualification_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.employee_qualification
    ADD CONSTRAINT employee_qualification_pkey PRIMARY KEY (employee_id, qualification_id);


--
-- Name: enrollment_grade enrollment_grade_enrollment_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment_grade
    ADD CONSTRAINT enrollment_grade_enrollment_id_key UNIQUE (enrollment_id);


--
-- Name: enrollment_grade enrollment_grade_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment_grade
    ADD CONSTRAINT enrollment_grade_pkey PRIMARY KEY (enrollment_grade_id);


--
-- Name: enrollment enrollment_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment
    ADD CONSTRAINT enrollment_pkey PRIMARY KEY (enrollment_id);


--
-- Name: generated_sql generated_sql_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.generated_sql
    ADD CONSTRAINT generated_sql_pkey PRIMARY KEY (generated_sql_id);


--
-- Name: gold_attempt gold_attempt_job_id_attempt_number_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_attempt
    ADD CONSTRAINT gold_attempt_job_id_attempt_number_key UNIQUE (job_id, attempt_number);


--
-- Name: gold_attempt gold_attempt_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_attempt
    ADD CONSTRAINT gold_attempt_pkey PRIMARY KEY (lease_token);


--
-- Name: gold_job gold_job_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_job
    ADD CONSTRAINT gold_job_pkey PRIMARY KEY (job_id);


--
-- Name: gold_job gold_job_scope_generation_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_job
    ADD CONSTRAINT gold_job_scope_generation_key UNIQUE (scope, generation);


--
-- Name: gold_record_identity gold_record_identity_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_record_identity
    ADD CONSTRAINT gold_record_identity_pkey PRIMARY KEY (namespace, entity, source_key);


--
-- Name: gold_scope gold_scope_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_scope
    ADD CONSTRAINT gold_scope_pkey PRIMARY KEY (scope);


--
-- Name: gold_sync_job gold_sync_job_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_sync_job
    ADD CONSTRAINT gold_sync_job_pkey PRIMARY KEY (gold_job_id);


--
-- Name: gold_worker gold_worker_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_worker
    ADD CONSTRAINT gold_worker_pkey PRIMARY KEY (worker_id);


--
-- Name: identity_crosswalk identity_crosswalk_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.identity_crosswalk
    ADD CONSTRAINT identity_crosswalk_pkey PRIMARY KEY (namespace, entity, source_key);


--
-- Name: ingestion_job ingestion_job_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_job
    ADD CONSTRAINT ingestion_job_pkey PRIMARY KEY (ingestion_job_id);


--
-- Name: ingestion_worker_heartbeat ingestion_worker_heartbeat_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_worker_heartbeat
    ADD CONSTRAINT ingestion_worker_heartbeat_pkey PRIMARY KEY (worker_id);


--
-- Name: kpi kpi_kpi_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.kpi
    ADD CONSTRAINT kpi_kpi_code_key UNIQUE (kpi_code);


--
-- Name: kpi kpi_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.kpi
    ADD CONSTRAINT kpi_pkey PRIMARY KEY (kpi_id);


--
-- Name: kpi_value kpi_value_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.kpi_value
    ADD CONSTRAINT kpi_value_pkey PRIMARY KEY (kpi_value_id);


--
-- Name: lecturer lecturer_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.lecturer
    ADD CONSTRAINT lecturer_pkey PRIMARY KEY (employee_id);


--
-- Name: login_session login_session_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.login_session
    ADD CONSTRAINT login_session_pkey PRIMARY KEY (login_session_id);


--
-- Name: major major_major_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.major
    ADD CONSTRAINT major_major_code_key UNIQUE (major_code);


--
-- Name: major major_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.major
    ADD CONSTRAINT major_pkey PRIMARY KEY (major_id);


--
-- Name: notification_read notification_read_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.notification_read
    ADD CONSTRAINT notification_read_pkey PRIMARY KEY (app_user_id, event_id);


--
-- Name: permission permission_permission_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.permission
    ADD CONSTRAINT permission_permission_code_key UNIQUE (permission_code);


--
-- Name: permission permission_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.permission
    ADD CONSTRAINT permission_pkey PRIMARY KEY (permission_id);


--
-- Name: pipeline_lineage pipeline_lineage_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.pipeline_lineage
    ADD CONSTRAINT pipeline_lineage_pkey PRIMARY KEY (lineage_id);


--
-- Name: position position_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app."position"
    ADD CONSTRAINT position_pkey PRIMARY KEY (position_id);


--
-- Name: position position_position_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app."position"
    ADD CONSTRAINT position_position_code_key UNIQUE (position_code);


--
-- Name: program program_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.program
    ADD CONSTRAINT program_pkey PRIMARY KEY (program_id);


--
-- Name: program program_program_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.program
    ADD CONSTRAINT program_program_code_key UNIQUE (program_code);


--
-- Name: prompt_template prompt_template_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.prompt_template
    ADD CONSTRAINT prompt_template_pkey PRIMARY KEY (prompt_template_id);


--
-- Name: qualification qualification_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.qualification
    ADD CONSTRAINT qualification_pkey PRIMARY KEY (qualification_id);


--
-- Name: quarantine_record quarantine_record_cleaning_run_id_locator_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.quarantine_record
    ADD CONSTRAINT quarantine_record_cleaning_run_id_locator_key UNIQUE (cleaning_run_id, locator);


--
-- Name: quarantine_record quarantine_record_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.quarantine_record
    ADD CONSTRAINT quarantine_record_pkey PRIMARY KEY (record_id);


--
-- Name: query_execution query_execution_generated_sql_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.query_execution
    ADD CONSTRAINT query_execution_generated_sql_id_key UNIQUE (generated_sql_id);


--
-- Name: query_execution query_execution_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.query_execution
    ADD CONSTRAINT query_execution_pkey PRIMARY KEY (query_execution_id);


--
-- Name: query_request query_request_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.query_request
    ADD CONSTRAINT query_request_pkey PRIMARY KEY (query_request_id);


--
-- Name: role_permission role_permission_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.role_permission
    ADD CONSTRAINT role_permission_pkey PRIMARY KEY (role_id, permission_id);


--
-- Name: role role_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.role
    ADD CONSTRAINT role_pkey PRIMARY KEY (role_id);


--
-- Name: role role_role_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.role
    ADD CONSTRAINT role_role_code_key UNIQUE (role_code);


--
-- Name: schema_migration schema_migration_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.schema_migration
    ADD CONSTRAINT schema_migration_pkey PRIMARY KEY (migration_name);


--
-- Name: schema_retrieval schema_retrieval_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.schema_retrieval
    ADD CONSTRAINT schema_retrieval_pkey PRIMARY KEY (schema_retrieval_id);


--
-- Name: semester semester_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.semester
    ADD CONSTRAINT semester_pkey PRIMARY KEY (semester_id);


--
-- Name: semester semester_semester_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.semester
    ADD CONSTRAINT semester_semester_code_key UNIQUE (semester_code);


--
-- Name: source_system source_system_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.source_system
    ADD CONSTRAINT source_system_pkey PRIMARY KEY (source_system_id);


--
-- Name: source_system source_system_source_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.source_system
    ADD CONSTRAINT source_system_source_code_key UNIQUE (source_code);


--
-- Name: storage_migration_checkpoint storage_migration_checkpoint_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.storage_migration_checkpoint
    ADD CONSTRAINT storage_migration_checkpoint_pkey PRIMARY KEY (table_name);


--
-- Name: student student_admission_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_admission_id_key UNIQUE (admission_id);


--
-- Name: student student_applicant_id_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_applicant_id_key UNIQUE (applicant_id);


--
-- Name: student student_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_pkey PRIMARY KEY (student_id);


--
-- Name: student student_student_code_key; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_student_code_key UNIQUE (student_code);


--
-- Name: transformation_step transformation_step_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.transformation_step
    ADD CONSTRAINT transformation_step_pkey PRIMARY KEY (transformation_step_id);


--
-- Name: admission uq_admission; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission
    ADD CONSTRAINT uq_admission UNIQUE (applicant_id, program_id, admission_year_id, admission_method_id);


--
-- Name: catalog_column uq_catalog_column; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_column
    ADD CONSTRAINT uq_catalog_column UNIQUE (catalog_table_id, column_name);


--
-- Name: catalog_table uq_catalog_table; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_table
    ADD CONSTRAINT uq_catalog_table UNIQUE (dataset_id, table_name);


--
-- Name: class_lecturer uq_class_lecturer; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class_lecturer
    ADD CONSTRAINT uq_class_lecturer UNIQUE (class_id, lecturer_id);


--
-- Name: curriculum_course uq_curriculum_course; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum_course
    ADD CONSTRAINT uq_curriculum_course UNIQUE (curriculum_id, course_id);


--
-- Name: curriculum uq_curriculum_version; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum
    ADD CONSTRAINT uq_curriculum_version UNIQUE (program_id, version);


--
-- Name: enrollment uq_enrollment; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment
    ADD CONSTRAINT uq_enrollment UNIQUE (student_id, class_id);


--
-- Name: prompt_template uq_prompt_template; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.prompt_template
    ADD CONSTRAINT uq_prompt_template UNIQUE (template_name, version);


--
-- Name: schema_retrieval uq_schema_retrieval; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.schema_retrieval
    ADD CONSTRAINT uq_schema_retrieval UNIQUE (query_request_id, catalog_table_id);


--
-- Name: user_role user_role_pkey; Type: CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.user_role
    ADD CONSTRAINT user_role_pkey PRIMARY KEY (app_user_id, role_id);


--
-- Name: aggregate aggregate_pkey; Type: CONSTRAINT; Schema: gold; Owner: -
--

ALTER TABLE ONLY gold.aggregate
    ADD CONSTRAINT aggregate_pkey PRIMARY KEY (job_id, entity, metric, dimensions);


--
-- Name: idx_kpi_value_period; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX idx_kpi_value_period ON app.kpi_value USING btree (kpi_id, period_date);


--
-- Name: ix_auto_pipeline_queue; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_auto_pipeline_queue ON app.auto_pipeline_job USING btree (status, next_attempt_at, created_at);


--
-- Name: ix_cleaning_auto_job; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_cleaning_auto_job ON app.cleaning_run USING btree (auto_job_id);


--
-- Name: ix_cleaning_run_dataset_started; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_cleaning_run_dataset_started ON app.cleaning_run USING btree (dataset_id, started_at DESC);


--
-- Name: ix_cleaning_run_job_started; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_cleaning_run_job_started ON app.cleaning_run USING btree (ingestion_job_id, started_at DESC);


--
-- Name: ix_cleaning_run_started; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_cleaning_run_started ON app.cleaning_run USING btree (started_at DESC, run_id DESC);


--
-- Name: ix_gold_job_queue; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_gold_job_queue ON app.gold_job USING btree (status, next_attempt_at);


--
-- Name: ix_gold_record_target; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_gold_record_target ON app.gold_record_identity USING btree (entity, target_fingerprint);


--
-- Name: ix_gold_sync_queue; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_gold_sync_queue ON app.gold_sync_job USING btree (status, next_attempt_at, created_at);


--
-- Name: ix_ingestion_execution_queue; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_ingestion_execution_queue ON app.ingestion_job USING btree (status, ingestion_job_id) WHERE ((job_type)::text = 'EXECUTION_V1'::text);


--
-- Name: ix_ingestion_job_root_attempt; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_ingestion_job_root_attempt ON app.ingestion_job USING btree (root_job_id, attempt_number);


--
-- Name: ix_ingestion_upload_queue; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_ingestion_upload_queue ON app.ingestion_job USING btree (status, ingestion_job_id) WHERE ((job_type)::text = 'UPLOAD_V1'::text);


--
-- Name: ix_pipeline_lineage_created; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_pipeline_lineage_created ON app.pipeline_lineage USING btree (created_at DESC);


--
-- Name: ix_pipeline_lineage_metadata; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_pipeline_lineage_metadata ON app.pipeline_lineage USING gin (metadata jsonb_path_ops);


--
-- Name: ix_pipeline_lineage_source; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_pipeline_lineage_source ON app.pipeline_lineage USING btree (source_layer, source_name);


--
-- Name: ix_pipeline_lineage_target; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_pipeline_lineage_target ON app.pipeline_lineage USING btree (target_layer, target_name);


--
-- Name: ix_quality_issue_assignee_updated; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_quality_issue_assignee_updated ON app.data_quality_issue USING btree (assignee_user_id, updated_at DESC);


--
-- Name: ix_quality_issue_department_status; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_quality_issue_department_status ON app.data_quality_issue USING btree (department_id, status, updated_at DESC);


--
-- Name: ix_quality_issue_event_issue; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_quality_issue_event_issue ON app.data_quality_issue_event USING btree (issue_id, event_id);


--
-- Name: ix_quality_issue_updated; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_quality_issue_updated ON app.data_quality_issue USING btree (updated_at DESC, issue_id DESC);


--
-- Name: ix_quarantine_record_error_codes; Type: INDEX; Schema: app; Owner: -
--

CREATE INDEX ix_quarantine_record_error_codes ON app.quarantine_record USING gin (error_codes jsonb_path_ops);


--
-- Name: uq_ingestion_execution_active_source; Type: INDEX; Schema: app; Owner: -
--

CREATE UNIQUE INDEX uq_ingestion_execution_active_source ON app.ingestion_job USING btree (data_source_id) WHERE (((job_type)::text = 'EXECUTION_V1'::text) AND ((status)::text = ANY ((ARRAY['QUEUED'::character varying, 'RUNNING'::character varying])::text[])));


--
-- Name: ux_auto_pipeline_dataset_running; Type: INDEX; Schema: app; Owner: -
--

CREATE UNIQUE INDEX ux_auto_pipeline_dataset_running ON app.auto_pipeline_job USING btree (dataset_id) WHERE ((status)::text = 'RUNNING'::text);


--
-- Name: ingestion_job enqueue_auto_silver; Type: TRIGGER; Schema: app; Owner: -
--

CREATE TRIGGER enqueue_auto_silver AFTER INSERT OR UPDATE OF status ON app.ingestion_job FOR EACH ROW EXECUTE FUNCTION app.enqueue_silver_after_ingestion();


--
-- Name: gold_job enqueue_gold_business_sync; Type: TRIGGER; Schema: app; Owner: -
--

CREATE TRIGGER enqueue_gold_business_sync AFTER INSERT OR UPDATE OF status ON app.gold_job FOR EACH ROW EXECUTE FUNCTION app.enqueue_gold_business_sync();


--
-- Name: admission admission_admission_method_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission
    ADD CONSTRAINT admission_admission_method_id_fkey FOREIGN KEY (admission_method_id) REFERENCES app.admission_method(admission_method_id);


--
-- Name: admission admission_admission_year_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission
    ADD CONSTRAINT admission_admission_year_id_fkey FOREIGN KEY (admission_year_id) REFERENCES app.admission_year(admission_year_id);


--
-- Name: admission admission_applicant_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission
    ADD CONSTRAINT admission_applicant_id_fkey FOREIGN KEY (applicant_id) REFERENCES app.applicant(applicant_id);


--
-- Name: admission admission_program_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.admission
    ADD CONSTRAINT admission_program_id_fkey FOREIGN KEY (program_id) REFERENCES app.program(program_id);


--
-- Name: app_user app_user_employee_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.app_user
    ADD CONSTRAINT app_user_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES app.employee(employee_id);


--
-- Name: assignment assignment_department_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.assignment
    ADD CONSTRAINT assignment_department_id_fkey FOREIGN KEY (department_id) REFERENCES app.department(department_id);


--
-- Name: assignment assignment_employee_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.assignment
    ADD CONSTRAINT assignment_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES app.employee(employee_id);


--
-- Name: assignment assignment_position_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.assignment
    ADD CONSTRAINT assignment_position_id_fkey FOREIGN KEY (position_id) REFERENCES app."position"(position_id);


--
-- Name: auto_pipeline_attempt auto_pipeline_attempt_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_attempt
    ADD CONSTRAINT auto_pipeline_attempt_job_id_fkey FOREIGN KEY (job_id) REFERENCES app.auto_pipeline_job(job_id);


--
-- Name: auto_pipeline_job auto_pipeline_job_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_job
    ADD CONSTRAINT auto_pipeline_job_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: auto_pipeline_job auto_pipeline_job_ingestion_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_job
    ADD CONSTRAINT auto_pipeline_job_ingestion_job_id_fkey FOREIGN KEY (ingestion_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: auto_pipeline_policy auto_pipeline_policy_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.auto_pipeline_policy
    ADD CONSTRAINT auto_pipeline_policy_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: catalog_column catalog_column_catalog_table_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_column
    ADD CONSTRAINT catalog_column_catalog_table_id_fkey FOREIGN KEY (catalog_table_id) REFERENCES app.catalog_table(catalog_table_id);


--
-- Name: catalog_table catalog_table_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.catalog_table
    ADD CONSTRAINT catalog_table_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: chart_widget chart_widget_dashboard_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.chart_widget
    ADD CONSTRAINT chart_widget_dashboard_id_fkey FOREIGN KEY (dashboard_id) REFERENCES app.dashboard(dashboard_id);


--
-- Name: chart_widget chart_widget_kpi_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.chart_widget
    ADD CONSTRAINT chart_widget_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES app.kpi(kpi_id);


--
-- Name: chart_widget chart_widget_query_request_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.chart_widget
    ADD CONSTRAINT chart_widget_query_request_id_fkey FOREIGN KEY (query_request_id) REFERENCES app.query_request(query_request_id);


--
-- Name: class class_course_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class
    ADD CONSTRAINT class_course_id_fkey FOREIGN KEY (course_id) REFERENCES app.course(course_id);


--
-- Name: class_lecturer class_lecturer_class_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class_lecturer
    ADD CONSTRAINT class_lecturer_class_id_fkey FOREIGN KEY (class_id) REFERENCES app.class(class_id);


--
-- Name: class_lecturer class_lecturer_lecturer_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class_lecturer
    ADD CONSTRAINT class_lecturer_lecturer_id_fkey FOREIGN KEY (lecturer_id) REFERENCES app.lecturer(employee_id);


--
-- Name: class class_semester_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.class
    ADD CONSTRAINT class_semester_id_fkey FOREIGN KEY (semester_id) REFERENCES app.semester(semester_id);


--
-- Name: cleaning_run cleaning_run_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.cleaning_run
    ADD CONSTRAINT cleaning_run_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: cleaning_run cleaning_run_ingestion_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.cleaning_run
    ADD CONSTRAINT cleaning_run_ingestion_job_id_fkey FOREIGN KEY (ingestion_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: curriculum_course curriculum_course_course_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum_course
    ADD CONSTRAINT curriculum_course_course_id_fkey FOREIGN KEY (course_id) REFERENCES app.course(course_id);


--
-- Name: curriculum_course curriculum_course_curriculum_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum_course
    ADD CONSTRAINT curriculum_course_curriculum_id_fkey FOREIGN KEY (curriculum_id) REFERENCES app.curriculum(curriculum_id);


--
-- Name: curriculum_course curriculum_course_prerequisite_course_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum_course
    ADD CONSTRAINT curriculum_course_prerequisite_course_id_fkey FOREIGN KEY (prerequisite_course_id) REFERENCES app.course(course_id);


--
-- Name: curriculum curriculum_program_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.curriculum
    ADD CONSTRAINT curriculum_program_id_fkey FOREIGN KEY (program_id) REFERENCES app.program(program_id);


--
-- Name: dashboard dashboard_owner_app_user_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dashboard
    ADD CONSTRAINT dashboard_owner_app_user_id_fkey FOREIGN KEY (owner_app_user_id) REFERENCES app.app_user(app_user_id);


--
-- Name: data_lineage data_lineage_source_column_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_lineage
    ADD CONSTRAINT data_lineage_source_column_id_fkey FOREIGN KEY (source_column_id) REFERENCES app.catalog_column(catalog_column_id);


--
-- Name: data_lineage data_lineage_target_column_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_lineage
    ADD CONSTRAINT data_lineage_target_column_id_fkey FOREIGN KEY (target_column_id) REFERENCES app.catalog_column(catalog_column_id);


--
-- Name: data_lineage data_lineage_transformation_step_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_lineage
    ADD CONSTRAINT data_lineage_transformation_step_id_fkey FOREIGN KEY (transformation_step_id) REFERENCES app.transformation_step(transformation_step_id);


--
-- Name: data_processing_run data_processing_run_triggered_by_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_processing_run
    ADD CONSTRAINT data_processing_run_triggered_by_fkey FOREIGN KEY (triggered_by) REFERENCES app.app_user(app_user_id);


--
-- Name: data_quality_issue data_quality_issue_assignee_user_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_assignee_user_id_fkey FOREIGN KEY (assignee_user_id) REFERENCES app.app_user(app_user_id);


--
-- Name: data_quality_issue data_quality_issue_created_by_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_created_by_fkey FOREIGN KEY (created_by) REFERENCES app.app_user(app_user_id);


--
-- Name: data_quality_issue data_quality_issue_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: data_quality_issue data_quality_issue_department_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_department_id_fkey FOREIGN KEY (department_id) REFERENCES app.department(department_id);


--
-- Name: data_quality_issue_event data_quality_issue_event_actor_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue_event
    ADD CONSTRAINT data_quality_issue_event_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES app.app_user(app_user_id);


--
-- Name: data_quality_issue_event data_quality_issue_event_issue_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue_event
    ADD CONSTRAINT data_quality_issue_event_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES app.data_quality_issue(issue_id);


--
-- Name: data_quality_issue data_quality_issue_origin_upload_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_origin_upload_job_id_fkey FOREIGN KEY (origin_upload_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: data_quality_issue_record data_quality_issue_record_issue_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue_record
    ADD CONSTRAINT data_quality_issue_record_issue_id_fkey FOREIGN KEY (issue_id) REFERENCES app.data_quality_issue(issue_id);


--
-- Name: data_quality_issue_record data_quality_issue_record_record_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue_record
    ADD CONSTRAINT data_quality_issue_record_record_id_fkey FOREIGN KEY (record_id) REFERENCES app.quarantine_record(record_id);


--
-- Name: data_quality_issue data_quality_issue_resolution_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_resolution_job_id_fkey FOREIGN KEY (resolution_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: data_quality_issue data_quality_issue_verification_run_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_issue
    ADD CONSTRAINT data_quality_issue_verification_run_id_fkey FOREIGN KEY (verification_run_id) REFERENCES app.cleaning_run(run_id);


--
-- Name: data_quality_rule data_quality_rule_catalog_column_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_rule
    ADD CONSTRAINT data_quality_rule_catalog_column_id_fkey FOREIGN KEY (catalog_column_id) REFERENCES app.catalog_column(catalog_column_id);


--
-- Name: data_quality_run data_quality_run_data_quality_rule_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_run
    ADD CONSTRAINT data_quality_run_data_quality_rule_id_fkey FOREIGN KEY (data_quality_rule_id) REFERENCES app.data_quality_rule(data_quality_rule_id);


--
-- Name: data_quality_run data_quality_run_ingestion_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_quality_run
    ADD CONSTRAINT data_quality_run_ingestion_job_id_fkey FOREIGN KEY (ingestion_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: data_source data_source_created_by_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_source
    ADD CONSTRAINT data_source_created_by_fkey FOREIGN KEY (created_by) REFERENCES app.app_user(app_user_id);


--
-- Name: data_source data_source_source_system_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.data_source
    ADD CONSTRAINT data_source_source_system_id_fkey FOREIGN KEY (source_system_id) REFERENCES app.source_system(source_system_id);


--
-- Name: dataset dataset_data_layer_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dataset
    ADD CONSTRAINT dataset_data_layer_id_fkey FOREIGN KEY (data_layer_id) REFERENCES app.data_layer(data_layer_id);


--
-- Name: dataset dataset_owner_department_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dataset
    ADD CONSTRAINT dataset_owner_department_id_fkey FOREIGN KEY (owner_department_id) REFERENCES app.department(department_id);


--
-- Name: dataset dataset_source_system_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.dataset
    ADD CONSTRAINT dataset_source_system_id_fkey FOREIGN KEY (source_system_id) REFERENCES app.source_system(source_system_id);


--
-- Name: department department_parent_department_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.department
    ADD CONSTRAINT department_parent_department_id_fkey FOREIGN KEY (parent_department_id) REFERENCES app.department(department_id);


--
-- Name: employee_qualification employee_qualification_employee_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.employee_qualification
    ADD CONSTRAINT employee_qualification_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES app.employee(employee_id);


--
-- Name: employee_qualification employee_qualification_qualification_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.employee_qualification
    ADD CONSTRAINT employee_qualification_qualification_id_fkey FOREIGN KEY (qualification_id) REFERENCES app.qualification(qualification_id);


--
-- Name: enrollment enrollment_class_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment
    ADD CONSTRAINT enrollment_class_id_fkey FOREIGN KEY (class_id) REFERENCES app.class(class_id);


--
-- Name: enrollment_grade enrollment_grade_enrollment_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment_grade
    ADD CONSTRAINT enrollment_grade_enrollment_id_fkey FOREIGN KEY (enrollment_id) REFERENCES app.enrollment(enrollment_id);


--
-- Name: enrollment enrollment_student_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.enrollment
    ADD CONSTRAINT enrollment_student_id_fkey FOREIGN KEY (student_id) REFERENCES app.student(student_id);


--
-- Name: generated_sql generated_sql_ai_model_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.generated_sql
    ADD CONSTRAINT generated_sql_ai_model_id_fkey FOREIGN KEY (ai_model_id) REFERENCES app.ai_model(ai_model_id);


--
-- Name: generated_sql generated_sql_prompt_template_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.generated_sql
    ADD CONSTRAINT generated_sql_prompt_template_id_fkey FOREIGN KEY (prompt_template_id) REFERENCES app.prompt_template(prompt_template_id);


--
-- Name: generated_sql generated_sql_query_request_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.generated_sql
    ADD CONSTRAINT generated_sql_query_request_id_fkey FOREIGN KEY (query_request_id) REFERENCES app.query_request(query_request_id);


--
-- Name: gold_attempt gold_attempt_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_attempt
    ADD CONSTRAINT gold_attempt_job_id_fkey FOREIGN KEY (job_id) REFERENCES app.gold_job(job_id);


--
-- Name: gold_job gold_job_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_job
    ADD CONSTRAINT gold_job_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: gold_job gold_job_scope_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_job
    ADD CONSTRAINT gold_job_scope_fkey FOREIGN KEY (scope) REFERENCES app.gold_scope(scope);


--
-- Name: gold_scope gold_scope_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_scope
    ADD CONSTRAINT gold_scope_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: gold_sync_job gold_sync_job_gold_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.gold_sync_job
    ADD CONSTRAINT gold_sync_job_gold_job_id_fkey FOREIGN KEY (gold_job_id) REFERENCES app.gold_job(job_id);


--
-- Name: identity_crosswalk identity_crosswalk_canonical_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.identity_crosswalk
    ADD CONSTRAINT identity_crosswalk_canonical_id_fkey FOREIGN KEY (canonical_id) REFERENCES app.canonical_identity(canonical_id);


--
-- Name: ingestion_job ingestion_job_data_source_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_job
    ADD CONSTRAINT ingestion_job_data_source_id_fkey FOREIGN KEY (data_source_id) REFERENCES app.data_source(data_source_id);


--
-- Name: ingestion_job ingestion_job_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_job
    ADD CONSTRAINT ingestion_job_dataset_id_fkey FOREIGN KEY (dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: ingestion_job ingestion_job_root_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_job
    ADD CONSTRAINT ingestion_job_root_job_id_fkey FOREIGN KEY (root_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: ingestion_job ingestion_job_target_layer_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_job
    ADD CONSTRAINT ingestion_job_target_layer_id_fkey FOREIGN KEY (target_layer_id) REFERENCES app.data_layer(data_layer_id);


--
-- Name: ingestion_worker_heartbeat ingestion_worker_heartbeat_current_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.ingestion_worker_heartbeat
    ADD CONSTRAINT ingestion_worker_heartbeat_current_job_id_fkey FOREIGN KEY (current_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: kpi_value kpi_value_kpi_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.kpi_value
    ADD CONSTRAINT kpi_value_kpi_id_fkey FOREIGN KEY (kpi_id) REFERENCES app.kpi(kpi_id);


--
-- Name: lecturer lecturer_employee_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.lecturer
    ADD CONSTRAINT lecturer_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES app.employee(employee_id);


--
-- Name: login_session login_session_app_user_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.login_session
    ADD CONSTRAINT login_session_app_user_id_fkey FOREIGN KEY (app_user_id) REFERENCES app.app_user(app_user_id);


--
-- Name: major major_department_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.major
    ADD CONSTRAINT major_department_id_fkey FOREIGN KEY (department_id) REFERENCES app.department(department_id);


--
-- Name: notification_read notification_read_app_user_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.notification_read
    ADD CONSTRAINT notification_read_app_user_id_fkey FOREIGN KEY (app_user_id) REFERENCES app.app_user(app_user_id);


--
-- Name: notification_read notification_read_event_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.notification_read
    ADD CONSTRAINT notification_read_event_id_fkey FOREIGN KEY (event_id) REFERENCES app.data_quality_issue_event(event_id);


--
-- Name: program program_major_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.program
    ADD CONSTRAINT program_major_id_fkey FOREIGN KEY (major_id) REFERENCES app.major(major_id);


--
-- Name: quarantine_record quarantine_record_cleaning_run_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.quarantine_record
    ADD CONSTRAINT quarantine_record_cleaning_run_id_fkey FOREIGN KEY (cleaning_run_id) REFERENCES app.cleaning_run(run_id);


--
-- Name: query_execution query_execution_generated_sql_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.query_execution
    ADD CONSTRAINT query_execution_generated_sql_id_fkey FOREIGN KEY (generated_sql_id) REFERENCES app.generated_sql(generated_sql_id);


--
-- Name: query_request query_request_app_user_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.query_request
    ADD CONSTRAINT query_request_app_user_id_fkey FOREIGN KEY (app_user_id) REFERENCES app.app_user(app_user_id);


--
-- Name: role_permission role_permission_permission_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.role_permission
    ADD CONSTRAINT role_permission_permission_id_fkey FOREIGN KEY (permission_id) REFERENCES app.permission(permission_id);


--
-- Name: role_permission role_permission_role_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.role_permission
    ADD CONSTRAINT role_permission_role_id_fkey FOREIGN KEY (role_id) REFERENCES app.role(role_id);


--
-- Name: schema_retrieval schema_retrieval_catalog_table_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.schema_retrieval
    ADD CONSTRAINT schema_retrieval_catalog_table_id_fkey FOREIGN KEY (catalog_table_id) REFERENCES app.catalog_table(catalog_table_id);


--
-- Name: schema_retrieval schema_retrieval_query_request_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.schema_retrieval
    ADD CONSTRAINT schema_retrieval_query_request_id_fkey FOREIGN KEY (query_request_id) REFERENCES app.query_request(query_request_id);


--
-- Name: source_system source_system_owner_department_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.source_system
    ADD CONSTRAINT source_system_owner_department_id_fkey FOREIGN KEY (owner_department_id) REFERENCES app.department(department_id);


--
-- Name: student student_admission_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_admission_id_fkey FOREIGN KEY (admission_id) REFERENCES app.admission(admission_id);


--
-- Name: student student_admission_year_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_admission_year_id_fkey FOREIGN KEY (admission_year_id) REFERENCES app.admission_year(admission_year_id);


--
-- Name: student student_applicant_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_applicant_id_fkey FOREIGN KEY (applicant_id) REFERENCES app.applicant(applicant_id);


--
-- Name: student student_program_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.student
    ADD CONSTRAINT student_program_id_fkey FOREIGN KEY (program_id) REFERENCES app.program(program_id);


--
-- Name: transformation_step transformation_step_ingestion_job_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.transformation_step
    ADD CONSTRAINT transformation_step_ingestion_job_id_fkey FOREIGN KEY (ingestion_job_id) REFERENCES app.ingestion_job(ingestion_job_id);


--
-- Name: transformation_step transformation_step_source_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.transformation_step
    ADD CONSTRAINT transformation_step_source_dataset_id_fkey FOREIGN KEY (source_dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: transformation_step transformation_step_target_dataset_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.transformation_step
    ADD CONSTRAINT transformation_step_target_dataset_id_fkey FOREIGN KEY (target_dataset_id) REFERENCES app.dataset(dataset_id);


--
-- Name: user_role user_role_app_user_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.user_role
    ADD CONSTRAINT user_role_app_user_id_fkey FOREIGN KEY (app_user_id) REFERENCES app.app_user(app_user_id);


--
-- Name: user_role user_role_role_id_fkey; Type: FK CONSTRAINT; Schema: app; Owner: -
--

ALTER TABLE ONLY app.user_role
    ADD CONSTRAINT user_role_role_id_fkey FOREIGN KEY (role_id) REFERENCES app.role(role_id);


--
-- Name: aggregate aggregate_job_id_fkey; Type: FK CONSTRAINT; Schema: gold; Owner: -
--

ALTER TABLE ONLY gold.aggregate
    ADD CONSTRAINT aggregate_job_id_fkey FOREIGN KEY (job_id) REFERENCES app.gold_job(job_id);


--
-- PostgreSQL database dump complete
--

\unrestrict CMT2VPuOAVcGzCe6tbXNZkqUXZ6I3Vgz9cDOdUvFCVBilrR6Bk95rDg31dO6VZd

