from __future__ import annotations

import os

import pytest

from app.infra.db.session import async_session_factory, engine
from app.modules.ingestion.readers.source_loader import read_data_source
from app.repositories.datasource_repo import DataSourceRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_id", "source_table"),
    [
        (1, "applicants.csv"),
        (2, "applicants.xlsx"),
    ],
)
async def test_read_data_source_from_real_minio(
    source_id: int,
    source_table: str,
) -> None:
    if os.getenv("RUN_MINIO_INTEGRATION") != "1":
        pytest.skip("Real MinIO integration test is disabled")

    try:
        async with async_session_factory() as db:
            repo = DataSourceRepository(db)

            result = await read_data_source(
                repo,
                data_source_id=source_id,
                source_table=source_table,
            )
    finally:
        # Pytest-asyncio tạo event loop riêng cho từng parametrized case.
        # Dispose pool sau mỗi case để asyncpg không tái dùng connection thuộc loop cũ.
        await engine.dispose()

    assert result.columns == [
        "applicant_id",
        "full_name",
        "status",
    ]

    assert result.rows_read == 3

    print(f"\nPASSED: DataSource {source_id}, rows_read={result.rows_read}")
