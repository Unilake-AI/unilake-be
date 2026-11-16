"""Khởi chạy worker riêng bằng python -m app.silver_worker."""

from __future__ import annotations

import logging

from sqlalchemy import URL, create_engine

from app.core.config import settings
from app.modules.pipeline.services.silver_worker import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        ),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    try:
        main(engine, settings.silver_worker_id)
    finally:
        engine.dispose()
