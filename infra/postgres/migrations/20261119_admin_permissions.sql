BEGIN;

DELETE FROM app.role_permission rp
USING app.role r, app.permission p
WHERE rp.role_id = r.role_id
  AND rp.permission_id = p.permission_id
  AND r.role_code = 'ADMIN'
  AND p.permission_code NOT IN (
      'auth.login', 'user.read', 'user.manage', 'system.config', 'system.log.read'
  );

COMMIT;
