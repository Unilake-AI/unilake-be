from __future__ import annotations

import pytest

from app.modules.ingestion.readers.csv_reader import CSVReader
from app.modules.ingestion.readers.excel_reader import ExcelReader
from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.postgresql_reader import PostgreSQLReader


def test_reader_factory_returns_csv_reader() -> None:
    """Trả về CSVReader khi source_type là CSV."""

    reader = SourceReaderFactory.get_reader("CSV")

    assert isinstance(reader, CSVReader)


def test_reader_factory_returns_excel_reader() -> None:
    """Trả về ExcelReader khi source_type là EXCEL."""

    reader = SourceReaderFactory.get_reader("EXCEL")

    assert isinstance(reader, ExcelReader)


def test_reader_factory_returns_postgresql_reader() -> None:
    """Trả về PostgreSQLReader khi source_type là POSTGRESQL."""

    reader = SourceReaderFactory.get_reader("POSTGRESQL")

    assert isinstance(reader, PostgreSQLReader)


def test_reader_factory_normalizes_source_type() -> None:
    """Factory chấp nhận source_type có khoảng trắng hoặc khác chữ hoa/thường."""

    reader = SourceReaderFactory.get_reader(" csv ")

    assert isinstance(reader, CSVReader)


def test_reader_factory_accepts_postgres_alias() -> None:
    """POSTGRES là alias ngắn, được gom về PostgreSQLReader."""

    reader = SourceReaderFactory.get_reader("postgres")

    assert isinstance(reader, PostgreSQLReader)


def test_reader_factory_rejects_empty_source_type() -> None:
    """source_type rỗng là input không hợp lệ và phải báo lỗi rõ ràng."""

    with pytest.raises(ValueError, match="source_type không hợp lệ"):
        SourceReaderFactory.get_reader(" ")


def test_reader_factory_rejects_unsupported_source_type() -> None:
    """Báo lỗi khi source_type chưa có reader tương ứng."""

    with pytest.raises(ValueError, match="Unsupported source_type: JSON"):
        SourceReaderFactory.get_reader("JSON")
