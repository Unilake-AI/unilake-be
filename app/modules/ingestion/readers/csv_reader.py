from __future__ import annotations

from pathlib import Path

import duckdb

from .contract import ReadRequest, ReadResult


class CSVReader:
    """Đọc dữ liệu CSV bằng DuckDB."""

    def read(self, request: ReadRequest) -> ReadResult:
        """Đọc một file CSV local và chuẩn hóa kết quả về ReadResult."""

        # 1. Reader này chỉ xử lý nguồn CSV.
        # Dùng upper() để caller truyền "csv" hay "CSV" đều đọc được.
        if request.source_type.upper() != "CSV":
            raise ValueError("Source type phải là CSV")

        # 2. location là đường dẫn file hoặc thư mục chứa file CSV.
        if not request.location:
            raise ValueError("Location không được để trống")

        # 3. Lấy cấu hình parser từ data_source.configuration.
        # Các key bắt buộc hiện là delimiter, encoding và has_header.
        config = request.configuration

        delimiter = config.get("delimiter")
        encoding = config.get("encoding")
        has_header = config.get("has_header")
        quote_char = config.get("quote_char")
        skip_rows = config.get("skip_rows", 0)

        # 4. Validate cấu hình trước khi gọi DuckDB để lỗi trả về dễ hiểu hơn.
        if not isinstance(delimiter, str) or len(delimiter) != 1:
            raise ValueError("Delimiter phải là một ký tự")

        if not isinstance(encoding, str) or not encoding.strip():
            raise ValueError("Encoding không hợp lệ")

        if not isinstance(has_header, bool):
            raise ValueError("has_header phải là boolean")

        if quote_char is not None and (not isinstance(quote_char, str) or len(quote_char) != 1):
            raise ValueError("quote_char phải là một ký tự nếu được cấu hình")

        if not isinstance(skip_rows, int) or skip_rows < 0:
            raise ValueError("skip_rows phải là số nguyên không âm")

        # 5. Xác định đường dẫn file cần đọc.
        # Reader này chỉ đọc file local; S3/HTTP sẽ cần reader riêng.
        if "://" in request.location:
            raise ValueError("CSVReader hiện chỉ hỗ trợ file local")

        path = Path(request.location).expanduser()

        if path.is_dir():
            if not request.source_table:
                raise ValueError("Thiếu source_table")

            # Khi location là thư mục, source_table phải là tên file trực tiếp.
            # Không nhận đường dẫn con để tránh đọc nhầm file ngoài thư mục nguồn.
            if Path(request.source_table).name != request.source_table:
                raise ValueError("source_table không hợp lệ")

            path = path / request.source_table

        elif request.source_table and path.name != request.source_table:
            raise ValueError("source_table không khớp với location")

        if not path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file CSV: {path}")

        # 6. Đọc dữ liệu bằng DuckDB trong memory.
        # all_varchar=True giữ dữ liệu thô ở dạng chuỗi, phù hợp tầng Bronze.
        conn = duckdb.connect(database=":memory:")

        try:
            if quote_char is None:
                relation = conn.read_csv(
                    str(path),
                    delimiter=delimiter,
                    header=has_header,
                    encoding=encoding.strip(),
                    all_varchar=True,
                    skiprows=skip_rows,
                )
            else:
                relation = conn.read_csv(
                    str(path),
                    delimiter=delimiter,
                    header=has_header,
                    encoding=encoding.strip(),
                    all_varchar=True,
                    skiprows=skip_rows,
                    quotechar=quote_char,
                )

            # 7. Trả dữ liệu đã parse về format chung của ingestion reader.
            return ReadResult(
                columns=list(relation.columns),
                rows=relation.fetchall(),
                source_table=request.source_table or path.name,
            )

        finally:
            conn.close()
