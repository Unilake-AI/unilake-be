"""Cấu hình môi trường Alembic cho UniLake AI."""

from __future__ import annotations

import asyncio
import importlib
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.infra.db.base import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

MODULE_MODEL_PATHS = (
    "app.modules.auth.models",
    "app.modules.users.models",
    "app.modules.datasources.models",
    "app.modules.ingestion.models",
    "app.modules.pipeline.models",
    "app.modules.governance.models",
    "app.modules.nlq.models",
    "app.modules.dashboard.models",
    "app.modules.query_history.models",
)

for module_path in MODULE_MODEL_PATHS:
    importlib.import_module(module_path)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    """Trả về URL cơ sở dữ liệu bất đồng bộ từ cấu hình môi trường."""
    return settings.database_url


config.set_main_option("sqlalchemy.url", get_url())


def run_migrations_offline() -> None:
    """Chạy migration ngoại tuyến mà không mở kết nối cơ sở dữ liệu."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Chạy migration thông qua giao diện kết nối đồng bộ."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Chạy migration bằng engine SQLAlchemy bất đồng bộ."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Chạy migration ở chế độ trực tuyến."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
