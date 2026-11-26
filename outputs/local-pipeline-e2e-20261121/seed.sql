BEGIN;

-- Chờ lượt chạy seed khác hoàn tất trước khi kiểm tra danh mục bằng cấp.
SELECT pg_advisory_xact_lock(hashtext('unilake-local-pipeline-e2e-20261121'));

DO $$
BEGIN
    IF (SELECT count(*) FROM app.department
        WHERE department_code IN ('PDT', 'PNS', 'PTS') AND status = 'ACTIVE') <> 3 THEN
        RAISE EXCEPTION 'Cần các phòng PDT, PNS, PTS đang ACTIVE';
    END IF;
END $$;

INSERT INTO app.major (major_code, major_name, department_id)
SELECT 'E2E26-CNTT', 'Công nghệ thông tin (kiểm thử)', department_id
FROM app.department WHERE department_code = 'PDT'
ON CONFLICT (major_code) DO NOTHING;

INSERT INTO app.program (program_code, program_name, major_id, degree_level)
SELECT variants.code, variants.name, major.major_id, 'BACHELOR'
FROM app.major AS major
CROSS JOIN (VALUES
    ('E2E26-CNTT', 'Công nghệ thông tin (kiểm thử)'),
    ('E2E26-CNTT-UD', 'Công nghệ thông tin ứng dụng (kiểm thử)')
) AS variants(code, name)
WHERE major.major_code = 'E2E26-CNTT'
ON CONFLICT (program_code) DO NOTHING;

INSERT INTO app.course (course_code, course_name, credit_hours) VALUES
    ('E2E26-LT01', 'Nhập môn lập trình (kiểm thử)', 3),
    ('E2E26-CSDL', 'Cơ sở dữ liệu (kiểm thử)', 3)
ON CONFLICT (course_code) DO NOTHING;

INSERT INTO app.semester (semester_code, academic_year, start_date, end_date, status)
VALUES ('E2E26-HK1', '2026-2027', DATE '2026-09-07', DATE '2027-01-15', 'ACTIVE')
ON CONFLICT (semester_code) DO NOTHING;

INSERT INTO app.class (class_code, course_id, semester_id, max_capacity, room, class_status)
SELECT classes.class_code, course.course_id, semester.semester_id, 35, classes.room, 'ACTIVE'
FROM (VALUES
    ('E2E26-LT01-01', 'E2E26-LT01', 'TEST-A01'),
    ('E2E26-CSDL-01', 'E2E26-CSDL', 'TEST-A02')
) AS classes(class_code, course_code, room)
JOIN app.course AS course USING (course_code)
CROSS JOIN app.semester AS semester
WHERE semester.semester_code = 'E2E26-HK1'
ON CONFLICT (class_code) DO NOTHING;

INSERT INTO app.position (position_code, position_name) VALUES
    ('E2E26-GV', 'Giảng viên (kiểm thử)'),
    ('E2E26-CV', 'Chuyên viên (kiểm thử)')
ON CONFLICT (position_code) DO NOTHING;

INSERT INTO app.qualification (qualification_name, qualification_type)
SELECT reference.name, reference.type
FROM (VALUES
    ('Tiến sĩ (kiểm thử E2E26)', 'Degree'),
    ('Cử nhân (kiểm thử E2E26)', 'Degree')
) AS reference(name, type)
WHERE NOT EXISTS (
    SELECT 1 FROM app.qualification AS current
    WHERE current.qualification_name = reference.name AND current.qualification_type = reference.type
);

INSERT INTO app.admission_year (year_label, start_date, end_date, status)
VALUES ('E2E26-2026', DATE '2026-01-01', DATE '2026-12-31', 'OPEN')
ON CONFLICT (year_label) DO NOTHING;

INSERT INTO app.admission_method (method_code, method_name, max_score)
VALUES ('E2E26-THPT', 'Điểm thi THPT (kiểm thử)', 30.00)
ON CONFLICT (method_code) DO NOTHING;

COMMIT;
