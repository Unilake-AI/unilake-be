from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.csv_reader import CSVReader


def _csv_request(location: str, source_table: str | None = None) -> ReadRequest:
    """Tạo request CSV hợp lệ để các test không lặp lại cấu hình."""

    return ReadRequest(
        source_type="CSV",
        location=location,
        configuration={
            "delimiter": ",",
            "encoding": "utf-8",
            "has_header": True,
        },
        source_table=source_table,
    )


def test_csv_reader_success(tmp_path: Path) -> None:
    """Đọc CSV thành công khi location là thư mục và source_table là tên file."""

    # 1. Tạo file CSV giả lập trong thư mục tạm của pytest.
    csv_file = tmp_path / "applicant.csv"

    csv_file.write_text(
        "applicant_id,full_name\n1,Nguyen Van A\n2,Tran Thi B\n",
        encoding="utf-8",
    )

    # 2. Tạo request giống tình huống data source trỏ tới một thư mục ingest.
    request = _csv_request(str(tmp_path), source_table="applicant.csv")

    # 3. Gọi CSVReader để parse file bằng DuckDB.
    reader = CSVReader()
    result = reader.read(request)

    # 4. Kiểm tra cột, số dòng, dữ liệu và metadata nguồn trả về.
    assert result.columns == [
        "applicant_id",
        "full_name",
    ]

    assert result.rows_read == 2
    assert result.rows == [
        ("1", "Nguyen Van A"),
        ("2", "Tran Thi B"),
    ]
    assert result.source_table == "applicant.csv"


def test_csv_reader_success_when_location_is_file(tmp_path: Path) -> None:
    """Đọc CSV thành công khi location trỏ thẳng tới file."""

    # File nhỏ này giúp kiểm tra nhánh path.is_file() trong CSVReader.
    csv_file = tmp_path / "major.csv"
    csv_file.write_text(
        "major_id,major_name\n1,Software Engineering\n",
        encoding="utf-8",
    )

    result = CSVReader().read(_csv_request(str(csv_file)))

    assert result.columns == ["major_id", "major_name"]
    assert result.rows == [("1", "Software Engineering")]
    assert result.rows_read == 1
    assert result.source_table == "major.csv"


def test_csv_reader_file_not_found(tmp_path: Path) -> None:
    """Báo lỗi rõ ràng khi source_table không tồn tại trong thư mục."""

    request = _csv_request(str(tmp_path), source_table="not_found.csv")

    reader = CSVReader()

    with pytest.raises(FileNotFoundError):
        reader.read(request)


def test_csv_reader_invalid_configuration(tmp_path: Path) -> None:
    """Báo lỗi sớm khi delimiter không hợp lệ."""

    # delimiter phải đúng một ký tự; ",," làm parser không biết tách cột thế nào.
    request = ReadRequest(
        source_type="CSV",
        location=str(tmp_path),
        configuration={
            "delimiter": ",,",
            "encoding": "utf-8",
            "has_header": True,
        },
        source_table="applicant.csv",
    )

    reader = CSVReader()

    with pytest.raises(ValueError, match="Delimiter phải là một ký tự"):
        reader.read(request)


def test_csv_reader_rejects_nested_source_table(tmp_path: Path) -> None:
    """Không cho source_table chứa đường dẫn con khi location là thư mục."""

    # Rule này tránh caller truyền "../file.csv" hoặc "subdir/file.csv"
    # khiến reader đọc nhầm file ngoài thư mục nguồn đã đăng ký.
    request = _csv_request(str(tmp_path), source_table="nested/applicant.csv")

    with pytest.raises(ValueError, match="source_table không hợp lệ"):
        CSVReader().read(request)
