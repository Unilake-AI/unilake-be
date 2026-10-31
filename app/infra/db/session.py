"""Cấu hình cơ sở dữ liệu SQLAlchemy bất đồng bộ cho UniLake AI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=20,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """Cung cấp phiên cơ sở dữ liệu có giao dịch cho các dependency FastAPI."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


readonly_engine = create_async_engine(
    settings.readonly_database_url,
    echo=settings.db_echo,
    pool_pre_ping=True,
    pool_size=5,
)

readonly_session_factory = async_sessionmaker(
    readonly_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
