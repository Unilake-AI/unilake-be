BEGIN READ ONLY;

SELECT c.class_code, m.course_code, m.course_name, m.credit_hours, s.semester_code
FROM app.class AS c
JOIN app.course AS m USING (course_id)
JOIN app.semester AS s USING (semester_id)
WHERE c.class_code IN ('E2E26-LT01-01', 'E2E26-CSDL-01')
ORDER BY c.class_code;

SELECT 'employee' AS entity, count(*) AS rows
FROM app.employee WHERE employee_code LIKE 'E2E26-NV%'
UNION ALL
SELECT 'lecturer', count(*) FROM app.lecturer
JOIN app.employee USING (employee_id) WHERE employee_code LIKE 'E2E26-NV%'
UNION ALL
SELECT 'assignment', count(*) FROM app.assignment
JOIN app.employee USING (employee_id) WHERE employee_code LIKE 'E2E26-NV%'
UNION ALL
SELECT 'employee_qualification', count(*) FROM app.employee_qualification
JOIN app.employee USING (employee_id) WHERE employee_code LIKE 'E2E26-NV%'
UNION ALL
SELECT 'curriculum', count(*) FROM app.curriculum WHERE curriculum_code = 'E2E26-KCT'
UNION ALL
SELECT 'curriculum_course', count(*) FROM app.curriculum_course
JOIN app.curriculum USING (curriculum_id) WHERE curriculum_code = 'E2E26-KCT'
UNION ALL
SELECT 'curriculum_invalid', count(*) FROM app.curriculum WHERE curriculum_code = 'E2E26-KCT-LOI'
UNION ALL
SELECT 'applicant', count(DISTINCT a.applicant_id) FROM app.admission AS a
JOIN app.admission_year AS y USING (admission_year_id) WHERE y.year_label = 'E2E26-2026'
UNION ALL
SELECT 'admission', count(*) FROM app.admission AS a
JOIN app.admission_year AS y USING (admission_year_id) WHERE y.year_label = 'E2E26-2026';

SELECT p.program_code, a.preference_rank, a.admission_score, a.admission_status,
       a.enrollment_status, a.enrollment_date, a.revision
FROM app.admission AS a
JOIN app.program AS p USING (program_id)
JOIN app.admission_year AS y USING (admission_year_id)
WHERE y.year_label = 'E2E26-2026'
ORDER BY p.program_code, a.preference_rank, a.admission_id;

SELECT d.dataset_id, d.domain, i.ingestion_job_id, i.source_table AS file,
       i.status AS bronze, run.silver, run.gold, run.database_status,
       coalesce(run.database_status = 'SYNCED', false) AS database_committed,
       run.database_error
FROM app.dataset AS d
JOIN app.ingestion_job AS i USING (dataset_id)
LEFT JOIN LATERAL (
    SELECT a.status AS silver, g.status AS gold, q.status AS database_status,
           q.error_code AS database_error
    FROM app.auto_pipeline_job AS a
    LEFT JOIN app.gold_job AS g ON g.silver_snapshot_id = a.result->'publication'->>'snapshot_id'
    LEFT JOIN app.gold_sync_job AS q ON q.gold_job_id = g.job_id
    WHERE a.ingestion_job_id = i.ingestion_job_id
    ORDER BY a.created_at DESC, g.created_at DESC
    LIMIT 1
) AS run ON true
WHERE d.dataset_name IN ('E2E26 - Nhân sự', 'E2E26 - Đào tạo', 'E2E26 - Tuyển sinh')
  AND i.source_table IN ('01-nhan-vien.csv', '02-phan-cong.csv', '03-bang-cap.csv',
                        '04-khung-chuong-trinh.csv', '05-dang-ky-tuyen-sinh.csv',
                        '06-ket-qua-xet-tuyen.csv', '07-xac-nhan-nhap-hoc.csv',
                        '08-khung-sai-ma-mon.csv')
ORDER BY i.ingestion_job_id;

COMMIT;
