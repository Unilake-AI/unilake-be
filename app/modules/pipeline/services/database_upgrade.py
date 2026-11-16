"""Nâng cấp schema, export metadata Silver đã có trước migration DROP có guard."""

from __future__ import annotations

import subprocess
from pathlib import Path

from sqlalchemy import URL, create_engine, text

from app.core.config import settings
from app.modules.pipeline.services.migrate_silver_metadata import LEGACY_TABLES, export_metadata


def main() -> None:
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        ),
        connect_args={"connect_timeout": 5},
    )
    try:
        with engine.connect() as connection:
            populated = False
            for name in LEGACY_TABLES:
                if (
                    connection.execute(
                        text("SELECT to_regclass(:name)"), {"name": "app." + name}
                    ).scalar_one()
                    and connection.execute(
                        text("SELECT EXISTS(SELECT 1 FROM app." + name + ")")
                    ).scalar_one()
                ):
                    populated = True
                    break
        if populated:
            print("Export và kiểm chứng metadata Silver trên MinIO...")
            print(export_metadata(engine))
    finally:
        engine.dispose()
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-U",
            settings.db_user,
            "-d",
            settings.db_name,
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/opt/migrations/upgrade.psql",
        ],
        cwd=Path(__file__).resolve().parents[4],
        check=True,
    )


if __name__ == "__main__":
    main()
