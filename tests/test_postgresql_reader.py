from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.postgresql_reader import (
    PostgreSQLReader,
    sql_literal,
    validate_identifier,
)


def _postgres_request(source_table: str | None = "app.student") -> ReadRequest:
    """Tạo request PostgreSQL hợp lệ để các test không lặp lại cấu hình."""

    return ReadRequest(
        source_type="POSTGRESQL",
        location=None,
        configuration={
            "host": "localhost",
            "port": 5432,
            "database": "unilake_source",
            "username": "reader",
            "password": "secret",
        },
        source_table=source_table,
    )


def test_postgresql_reader_success() -> None:
    """Đọc PostgreSQL thành công và trả kết quả theo ReadResult."""

    # 1. Giả lập DuckDB connection để test không cần PostgreSQL thật.
    conn = MagicMock()
    query_result = MagicMock()
    query_result.description = [("student_id",), ("full_name",)]
    query_result.fetchall.return_value = [
        (1, "Nguyen Van A"),
        (2, "Tran Thi B"),
    ]
    conn.execute.side_effect = [conn, conn, conn, query_result]

    # 2. Patch đúng nơi PostgreSQLReader gọi duckdb.connect.
    with patch(
        "app.modules.ingestion.readers.postgresql_reader.duckdb.connect",
        return_value=conn,
    ) as connect_mock:
        result = PostgreSQLReader().read(_postgres_request())

    # 3. Kiểm tra DuckDB được mở in-memory và luôn được close ở finally.
    connect_mock.assert_called_once_with(database=":memory:")
    conn.close.assert_called_once()

    # 4. Kiểm tra dữ liệu được chuẩn hóa đúng contract chung của reader.
    assert result.columns == ["student_id", "full_name"]
    assert result.rows == [
        (1, "Nguyen Van A"),
        (2, "Tran Thi B"),
    ]
    assert result.rows_read == 2
    assert result.source_table == "app.student"

    # 5. Câu SELECT phải dùng schema/table đã quote để tránh SQL injection.
    select_sql = conn.execute.call_args_list[-1].args[0]
    assert 'FROM source_pg."app"."student"' in select_sql


def test_postgresql_reader_uses_public_schema_when_table_has_no_schema() -> None:
    """Nếu source_table chỉ có tên bảng, reader mặc định dùng schema public."""

    conn = MagicMock()
    query_result = MagicMock()
    query_result.description = [("major_id",)]
    query_result.fetchall.return_value = [(1,)]
    conn.execute.side_effect = [conn, conn, conn, query_result]

    with patch(
        "app.modules.ingestion.readers.postgresql_reader.duckdb.connect",
        return_value=conn,
    ):
        result = PostgreSQLReader().read(_postgres_request(source_table="major"))

    select_sql = conn.execute.call_args_list[-1].args[0]
    assert 'FROM source_pg."public"."major"' in select_sql
    assert result.source_table == "major"


def test_postgresql_reader_rejects_invalid_source_type() -> None:
    """Không cho PostgreSQLReader xử lý nhầm source_type khác."""

    request = _postgres_request()
    request.source_type = "CSV"

    with pytest.raises(ValueError, match="Source type phải là POSTGRESQL"):
        PostgreSQLReader().read(request)


def test_postgresql_reader_rejects_missing_source_table() -> None:
    """source_table là bắt buộc vì reader cần biết bảng nào trong PostgreSQL."""

    with pytest.raises(ValueError, match="Thiếu source_table"):
        PostgreSQLReader().read(_postgres_request(source_table=None))


def test_postgresql_reader_rejects_invalid_port() -> None:
    """Port phải là số nguyên TCP hợp lệ, không nhận bool hoặc số ngoài dải."""

    request = _postgres_request()
    request.configuration["port"] = True

    with pytest.raises(ValueError, match="Port không hợp lệ"):
        PostgreSQLReader().read(request)


def test_postgresql_reader_rejects_unsafe_table_identifier() -> None:
    """Tên schema/table không được chứa biểu thức SQL."""

    request = _postgres_request(source_table="public.student;drop_table")

    with pytest.raises(ValueError, match="Tên schema hoặc bảng không hợp lệ"):
        PostgreSQLReader().read(request)


def test_validate_identifier_quotes_safe_identifier() -> None:
    """Identifier hợp lệ được bọc dấu nháy kép trước khi ghép vào SQL."""

    assert validate_identifier("student_2026") == '"student_2026"'


def test_sql_literal_escapes_single_quote() -> None:
    """Chuỗi cấu hình được escape dấu nháy đơn trước khi ghép vào CREATE SECRET."""

    assert sql_literal("reader's_db") == "'reader''s_db'"
