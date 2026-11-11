"""Khởi chạy worker bằng lệnh python -m app.ingestion_worker."""

from __future__ import annotations

import asyncio
import logging

from app.infra.db.session import async_session_factory
from app.modules.ingestion.services.worker import main

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(async_session_factory))
