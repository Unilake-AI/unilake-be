from __future__ import annotations

import re
from typing import Any

import duckdb

from .contract import ReadRequest, ReadResult

POSTGRES_SOURCE_TYPES = {"POSTGRESQL", "POSTGRES"}


def sql_literal(value: str) -> str:
    """Escape giá trị chuỗi trước khi ghép vào câu SQL của DuckDB."""
    return "'" + value.replace("'", "''") + "'"


def validate_identifier(value: str) -> str:
    """Validate tên schema/bảng và quote lại thành SQL identifier an toàn.

    Chỉ chấp nhận identifier đơn giản như public, student, admission_2026.
    Không nhận biểu thức SQL để tránh injection qua request.source_table.
    """

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Tên schema hoặc bảng không hợp lệ: {value}")

    return f'"{value}"'


def _required_text(config: dict[str, Any], key: str) -> str:
    """Lấy một field cấu hình dạng chuỗi và báo lỗi nếu thiếu hoặc rỗng."""

    value = config.get(key)

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Configuration không hợp lệ: {key}")

    return value.strip()


def _required_port(config: dict[str, Any]) -> int:
    """Lấy port PostgreSQL và kiểm tra nằm trong dải TCP hợp lệ."""

    port = config.get("port")

    # bool là subclass của int trong Python, nên cần loại riêng True/False.
    if isinstance(port, bool) or not isinstance(port, int) or not (1 <= port <= 65535):
        raise ValueError("Port không hợp lệ")

    return port


def _parse_source_table(source_table: str) -> tuple[str, str]:
    """Tách source_table thành schema và table.

    Hỗ trợ hai dạng:
    - "student": mặc định schema là public.
    - "academic.student": đọc table student trong schema academic.
    """

    parts = source_table.split(".")

    if len(parts) == 1:
        return "public", parts[0]

    if len(parts) == 2:
        return parts[0], parts[1]

    raise ValueError("source_table không hợp lệ")


class PostgreSQLReader:
    """Đọc bảng PostgreSQL thông qua DuckDB."""

    def read(self, request: ReadRequest) -> ReadResult:
        """Đọc toàn bộ một bảng PostgreSQL và chuẩn hóa về ReadResult."""

        # 1. Reader này chỉ xử lý nguồn PostgreSQL.
        # Cho phép cả "POSTGRESQL" và "postgres" để caller không quá phụ thuộc chữ hoa/thường.
        if request.source_type.upper() not in POSTGRES_SOURCE_TYPES:
            raise ValueError("Source type phải là POSTGRESQL")

        # 2. Validate cấu hình kết nối trước khi mở kết nối tới database nguồn.
        config = request.configuration

        host = _required_text(config, "host")
        database = _required_text(config, "database")
        username = _required_text(config, "username")
        password = _required_text(config, "password")
        port = _required_port(config)

        # 3. source_table là bắt buộc vì PostgreSQLReader đọc theo bảng.
        if not request.source_table:
            raise ValueError("Thiếu source_table")

        # Hỗ trợ "table" hoặc "schema.table"; sau đó quote identifier để an toàn.
        schema_name, table_name = _parse_source_table(request.source_table)
        schema_sql = validate_identifier(schema_name)
        table_sql = validate_identifier(table_name)

        # 4. Khởi tạo DuckDB trong memory.
        # DuckDB đóng vai trò bridge: attach PostgreSQL rồi SELECT dữ liệu về Python.
        conn = duckdb.connect(database=":memory:")

        try:
            # Extension postgres phải có sẵn trong môi trường DuckDB.
            # Nếu chưa có, câu lệnh này sẽ ném lỗi rõ ràng cho caller/log ingestion.
            conn.execute("LOAD postgres")

            # 5. Tạo secret tạm thời cho kết nối PostgreSQL.
            # Secret chỉ sống trong DuckDB connection hiện tại và được close ở finally.
            conn.execute(
                f"""
                CREATE SECRET ingestion_pg_secret (
                    TYPE postgres,
                    HOST {sql_literal(host)},
                    PORT {port},
                    DATABASE {sql_literal(database)},
                    USER {sql_literal(username)},
                    PASSWORD {sql_literal(password)}
                )
                """
            )

            # 6. Attach database nguồn ở chế độ chỉ đọc để reader không thể ghi nhầm dữ liệu.
            conn.execute(
                """
                ATTACH '' AS source_pg (
                    TYPE postgres,
                    SECRET ingestion_pg_secret,
                    READ_ONLY
                )
                """
            )

            # 7. Đọc toàn bộ bảng. Identifier đã được validate/quote ở trên.
            result = conn.execute(
                f"""
                SELECT *
                FROM source_pg.{schema_sql}.{table_sql}
                """
            )

            columns = [column[0] for column in result.description]

            rows = result.fetchall()

            # 8. Trả dữ liệu về format chung của ingestion reader.
            return ReadResult(
                columns=columns,
                rows=rows,
                source_table=request.source_table,
            )

        finally:
            conn.close()
