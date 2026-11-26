"""Read just the headers of a staff upload, using the ingestion parser settings."""

from __future__ import annotations

import csv
from contextlib import closing
from io import TextIOWrapper
from time import monotonic
from typing import TYPE_CHECKING, Any
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.ingestion.readers.excel_reader import excel_column_names
from app.modules.ingestion.schemas import UploadFileLayout
from app.modules.ingestion.services.access import upload_domains
from app.modules.ingestion.services.input_validator import (
    InputValidationError,
    check_excel_size,
    csv_records,
)
from app.modules.ingestion.services.uploads import file_type, parse_options
from app.modules.ingestion.validation import CSVOptions
from app.shared.async_io import run_io

if TYPE_CHECKING:
    from typing import BinaryIO

    from fastapi import UploadFile


async def inspect_upload_file(
    file: UploadFile, domain: str, parser_options: str, user: dict[str, Any]
) -> UploadFileLayout:
    try:
        allowed = upload_domains(user)
        if allowed is not None and domain not in allowed:
            raise AppException(
                "Bạn không có quyền trong lĩnh vực này",
                code="UPLOAD_DOMAIN_FORBIDDEN",
                status_code=403,
            )
        _, source_type = file_type(file.filename)
        return await run_io(
            read_upload_layout, file.file, source_type, parse_options(parser_options, source_type)
        )
    finally:
        await file.close()


def read_upload_layout(
    stream: BinaryIO, source_type: str, options: dict[str, Any]
) -> UploadFileLayout:
    size = stream.seek(0, 2)
    stream.seek(0)
    if size > settings.upload_max_bytes:
        raise AppException(
            "File vượt dung lượng cho phép", code="UPLOAD_TOO_LARGE", status_code=413
        )
    if not size:
        raise AppException("File không có nội dung", code="UPLOAD_EMPTY", status_code=422)

    deadline = monotonic() + settings.upload_timeout_seconds

    def check_budget() -> None:
        if monotonic() > deadline:
            raise InputValidationError("IN-COM-005", "RESOURCE_LIMIT_EXCEEDED")

    sheet_names: list[str] = []
    sheet_name = None
    try:
        if source_type == "CSV":
            parser = CSVOptions.model_validate({"kind": "CSV", **options})
            text = TextIOWrapper(stream, encoding=parser.encoding, newline="")
            try:
                columns = next(csv_records(text, parser, check_budget), [])
            finally:
                text.detach()
            if columns:
                columns[0] = columns[0].removeprefix("\ufeff")
        else:
            check_excel_size(stream, settings.upload_max_expanded_bytes)
            workbook = load_workbook(stream, read_only=True, data_only=False)
            try:
                sheet_names = [sheet.title for sheet in workbook.worksheets]
                sheet_name = options["sheet_name"]
                if sheet_name is None and len(sheet_names) > 1:
                    return UploadFileLayout(sheet_names=sheet_names)
                if sheet_name is None and sheet_names:
                    sheet_name = sheet_names[0]
                if sheet_name not in sheet_names:
                    raise AppException(
                        "Không tìm thấy sheet cần đọc", code="SHEET_NOT_FOUND", status_code=422
                    )
                sheet = workbook[sheet_name]
                sheet.reset_dimensions()
                with closing(sheet.iter_rows()) as rows:
                    for _ in range(options["skip_rows"] + options["header_row"] - 1):
                        if next(rows, None) is None:
                            break
                        check_budget()
                    cells = next(rows, ())
                if any(cell.data_type in {"f", "e"} for cell in cells):
                    raise AppException(
                        "Dòng tiêu đề có ô công thức hoặc ô lỗi",
                        code="UPLOAD_HEADER_CELL_INVALID",
                        status_code=422,
                    )
                columns = excel_column_names(tuple(cell.value for cell in cells))
            finally:
                workbook.close()
        check_budget()
    except InputValidationError as exc:
        raise AppException("Tệp vượt giới hạn kiểm tra", code=exc.code, status_code=413) from None
    except (BadZipFile, InvalidFileException, ParseError, ValueError, KeyError, csv.Error, OSError):
        raise AppException(
            "Không đọc được tệp với cách đọc đã chọn",
            code="UPLOAD_FILE_LAYOUT_INVALID",
            status_code=422,
        ) from None

    if not columns or any(not name.strip() for name in columns):
        raise AppException(
            "Dòng tiêu đề trống hoặc có cột chưa đặt tên", code="HEADER_EMPTY", status_code=422
        )
    if len(set(columns)) != len(columns):
        raise AppException(
            "Dòng tiêu đề có tên cột trùng nhau", code="HEADER_DUPLICATE", status_code=422
        )
    if len(columns) > settings.upload_max_columns or any(
        len(name) > settings.upload_max_cell_chars for name in columns
    ):
        raise AppException(
            "Dòng tiêu đề vượt giới hạn kiểm tra", code="RESOURCE_LIMIT_EXCEEDED", status_code=413
        )
    return UploadFileLayout(columns=columns, sheet_names=sheet_names, sheet_name=sheet_name)
