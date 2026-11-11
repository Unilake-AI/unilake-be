"""Cách gọi dùng chung để đọc bản dữ liệu và bàn giao cho bước xử lý tiếp theo."""

from __future__ import annotations

from app.modules.ingestion.readers.contract import ReadRequest as ReadRequest
from app.modules.ingestion.readers.contract import ReadResult as ReadResult
from app.modules.ingestion.readers.contract import SourceReader as SourceReader
from app.modules.ingestion.readers.factory import SourceReaderFactory as SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot as read_file_snapshot
from app.modules.ingestion.services.reader_batch import reader_to_batch as reader_to_batch
