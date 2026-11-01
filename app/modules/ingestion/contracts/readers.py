"""Public API cho việc đọc snapshot và bàn giao batch sang pipeline."""

from __future__ import annotations

from app.modules.ingestion.reader_batch import reader_to_batch as reader_to_batch
from app.modules.ingestion.readers.contract import ReadRequest as ReadRequest
from app.modules.ingestion.readers.contract import ReadResult as ReadResult
from app.modules.ingestion.readers.contract import SourceReader as SourceReader
from app.modules.ingestion.readers.factory import SourceReaderFactory as SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot as read_file_snapshot
