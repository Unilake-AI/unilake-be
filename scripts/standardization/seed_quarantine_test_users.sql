-- Chỉ chạy trên database local/dev. Tạo tài khoản giả riêng, không đổi tài khoản có sẵn.
BEGIN;
DO $$
DECLARE
    demo_role bigint;
    demo_hash text;
    demo_position bigint;
    demo_department bigint;
    demo_employee bigint;
    demo_user bigint;
    account record;
BEGIN
    SELECT role_id INTO STRICT demo_role FROM app.role WHERE role_code = 'ACADEMIC_STAFF';
    SELECT password_hash INTO STRICT demo_hash FROM app.app_user WHERE username = 'academic_staff';

    INSERT INTO app.position (position_code, position_name)
    VALUES ('QATEST', 'Vị trí kiểm thử Quarantine') ON CONFLICT (position_code) DO NOTHING;
    SELECT position_id INTO STRICT demo_position FROM app.position WHERE position_code = 'QATEST';

    INSERT INTO app.department (department_code, department_name, department_type)
    VALUES ('PQADEMO', 'Phòng Đào tạo demo QA', 'P'),
           ('PQAOTHER', 'Phòng khác demo QA', 'P')
    ON CONFLICT (department_code) DO NOTHING;

    FOR account IN SELECT * FROM (VALUES
        ('qa_quarantine_owner', 'PQADEMO'),
        ('qa_quarantine_peer', 'PQADEMO'),
        ('qa_quarantine_outside', 'PQAOTHER')
    ) AS fixture(username, department_code)
    LOOP
        SELECT department_id INTO STRICT demo_department
        FROM app.department WHERE department_code = account.department_code;

        INSERT INTO app.employee (employee_code, full_name, employee_status)
        VALUES (account.username, account.username, 'Active')
        ON CONFLICT (employee_code) DO NOTHING;
        SELECT employee_id INTO STRICT demo_employee
        FROM app.employee WHERE employee_code = account.username;

        INSERT INTO app.app_user (username, password_hash, employee_id)
        VALUES (account.username, demo_hash, demo_employee) ON CONFLICT (username) DO NOTHING;
        SELECT app_user_id INTO STRICT demo_user FROM app.app_user
        WHERE username = account.username AND employee_id = demo_employee AND is_active;

        INSERT INTO app.user_role (app_user_id, role_id)
        VALUES (demo_user, demo_role) ON CONFLICT DO NOTHING;
        IF NOT EXISTS (
            SELECT 1 FROM app.assignment
            WHERE employee_id = demo_employee AND department_id = demo_department
              AND upper(assignment_status) = 'ACTIVE'
              AND start_date <= CURRENT_DATE AND (end_date IS NULL OR end_date >= CURRENT_DATE)
        ) THEN
            INSERT INTO app.assignment (
                employee_id, department_id, position_id, start_date, assignment_status
            ) VALUES (demo_employee, demo_department, demo_position, CURRENT_DATE, 'Active');
        END IF;
    END LOOP;
END $$;
COMMIT;

SELECT u.username, u.app_user_id, d.department_id, d.department_code
FROM app.app_user u
JOIN app.assignment a ON a.employee_id = u.employee_id
JOIN app.department d ON d.department_id = a.department_id
WHERE u.username IN ('qa_quarantine_owner', 'qa_quarantine_peer', 'qa_quarantine_outside')
  AND d.department_code IN ('PQADEMO', 'PQAOTHER')
  AND upper(a.assignment_status) = 'ACTIVE'
  AND a.start_date <= CURRENT_DATE AND (a.end_date IS NULL OR a.end_date >= CURRENT_DATE)
ORDER BY u.username;
