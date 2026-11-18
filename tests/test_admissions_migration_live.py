"""Upgrade an actual legacy record and verify database-wide revision/audit triggers."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.core.config import settings

pytestmark = pytest.mark.skipif(
    os.getenv("AUTO_PIPELINE_LIVE_TEST") != "1", reason="Local PostgreSQL required"
)


def test_legacy_upgrade_is_repeatable_and_every_database_writer_advances_revision():
    assert settings.db_host in {"localhost", "127.0.0.1", "::1"}
    name = "unilake_admissions_migration_" + uuid4().hex
    options = {
        "host": settings.db_host,
        "port": settings.db_port,
        "user": settings.db_user,
        "password": settings.db_password,
        "autocommit": True,
    }
    root = Path(__file__).resolve().parents[1]
    target = root / "infra/postgres/migrations/20261118_update_admissions_workflows.sql"
    with psycopg.connect(dbname=settings.db_name, **options) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with psycopg.connect(dbname=name, **options) as conn:
                conn.execute((root / "infra/postgres/init/02-schema.sql").read_bytes())
                for migration in sorted((root / "infra/postgres/migrations").glob("*.sql")):
                    if migration != target:
                        conn.execute(migration.read_bytes())
                conn.execute(
                    "INSERT INTO app.major(major_code,major_name,department_id) SELECT 'LEGACY','Legacy',department_id FROM app.department WHERE department_code='PDT'"
                )
                conn.execute(
                    "INSERT INTO app.program(program_code,program_name,major_id,degree_level) SELECT 'LEGACY','Legacy',major_id,'BACHELOR' FROM app.major"
                )
                conn.execute(
                    "INSERT INTO app.admission_year(year_label,start_date,end_date,status) VALUES('2026','2026-01-01','2026-12-31','OPEN')"
                )
                conn.execute(
                    "INSERT INTO app.admission_method(method_code,method_name,max_score) VALUES('THPT','THPT',30)"
                )
                conn.execute("INSERT INTO app.applicant(full_name) VALUES('Legacy applicant')")
                conn.execute(
                    "INSERT INTO app.admission(applicant_id,program_id,admission_year_id,admission_method_id,preference_rank,admission_status,admission_score) SELECT a.applicant_id,p.program_id,y.admission_year_id,m.admission_method_id,1,'Accepted',20 FROM app.applicant a CROSS JOIN app.program p CROSS JOIN app.admission_year y CROSS JOIN app.admission_method m"
                )
                for _ in range(2):
                    conn.execute(target.read_bytes())
                    assert conn.execute(
                        "SELECT admission_status,enrollment_status,enrollment_date,revision FROM app.admission"
                    ).fetchone() == ("Accepted", None, None, 1)
                    assert conn.execute("SELECT count(*) FROM app.admission_event").fetchone() == (
                        0,
                    )
                conn.execute("UPDATE app.admission SET admission_score=21,revision=9999")
                assert conn.execute("SELECT revision FROM app.admission").fetchone() == (2,)
                assert conn.execute(
                    "SELECT old_revision,new_revision,operation FROM app.admission_event"
                ).fetchone() == (1, 2, "UNATTRIBUTED")
                conn.execute("UPDATE app.admission SET admission_score=21,revision=9999")
                assert conn.execute("SELECT revision FROM app.admission").fetchone() == (2,)
                assert conn.execute("SELECT count(*) FROM app.admission_event").fetchone() == (1,)
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute("UPDATE app.admission SET enrollment_status='CONFIRMED'")
                conn.execute(target.read_bytes())
                assert conn.execute(
                    "SELECT revision,enrollment_status FROM app.admission"
                ).fetchone() == (2, None)
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
