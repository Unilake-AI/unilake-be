from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from app.modules.ingestion.contracts import MappingBinding
from app.modules.ingestion.reader_batch import reader_to_batch

from .factory import SourceReaderFactory
from .file_snapshot import read_file_snapshot
from .minio_file_resolver import resolve_file_request
from .request_builder import build_read_request

if TYPE_CHECKING:
    from app.modules.ingestion.contracts import BronzeBatch
    from app.modules.ingestion.readers.contract import ReadResult
    from app.repositories.datasource_repo import DataSourceRepository


async def read_data_source(
    repo: DataSourceRepository,
    *,
    data_source_id: int,
    source_table: str,
) -> ReadResult:
    """
    Đọc dữ liệu từ một DataSource đã đăng ký.

    Không tạo ingestion job.
    Không ghi Bronze.
    Không cập nhật trạng thái ingestion.
    """

    # 1. Validate id từ API/service trước khi query DB.
    # bool là subclass của int trong Python, nên cần loại riêng True/False.
    _validate_data_source_id(data_source_id)

    # 2. Lấy DataSource từ catalog, kèm tên SourceSystem nếu repository trả về.
    # Loader chỉ cần object DataSource; tên SourceSystem hiện chưa dùng trong bước đọc.
    record = await repo.get_with_source_system(data_source_id)

    if record is None:
        raise LookupError(f"Không tìm thấy DataSource: {data_source_id}")

    data_source, _source_system_name = record

    # 3. Chuyển DataSource ORM/catalog thành ReadRequest gọn cho reader.
    # build_read_request chịu trách nhiệm validate DataSource.is_active,
    # source_type, location, configuration và source_table.
    request = build_read_request(
        data_source,
        source_table=source_table,
    )

    # 4. Kiểm tra SourceSystem còn tồn tại/đang active.
    # DataSource active nhưng SourceSystem bị tắt thì ingestion vẫn không nên đọc.
    source_system_active = await repo.check_exist_source_system(data_source.source_system_id)

    if not source_system_active:
        raise ValueError("SourceSystem không tồn tại hoặc đã bị vô hiệu hóa")

    # 5. Chọn reader phù hợp với source_type đã được chuẩn hóa trong request.
    reader = SourceReaderFactory.get_reader(request.source_type)

    # 6. Resolve file remote nếu location là s3://...
    # CSVReader/ExcelReader chỉ đọc file local, nên MinIO object được tải tạm trước.
    # PostgreSQL hoặc file local sẽ đi qua context này mà không bị thay đổi.
    with resolve_file_request(request) as resolved_request:
        # 7. Reader hiện là code đồng bộ; đưa sang worker thread để không block event loop.
        # Điều này giữ API async của ingestion service vẫn phản hồi tốt khi file/DB đọc lâu.
        result = await asyncio.to_thread(
            reader.read,
            resolved_request,
        )

    # 8. Trả dữ liệu cho Ingestion Service. Loader không ghi Bronze và không update status.
    return result


async def read_data_source_batch(
    repo: DataSourceRepository,
    *,
    data_source_id: int,
    source_table: str,
    dataset_id: str,
    domain_id: str,
    batch_id: str,
    binding: MappingBinding,
    object_version_id: str | None = None,
    expected_input_version: str | None = None,
) -> BronzeBatch:
    """Điểm vào chuẩn hóa với bằng chứng tệp đã cố định; không ghi lưu trữ.

    Reader cơ sở dữ liệu cần hợp đồng xuất/chụp bền vững trước bước bàn giao này.
    API đọc thô read_data_source cũ không thể chứng nhận nguồn gốc dữ liệu.
    """
    _validate_data_source_id(data_source_id)
    if not isinstance(binding, MappingBinding) or any(
        not isinstance(v, str) or not v.strip() for v in vars(binding).values()
    ):
        raise ValueError("INVALID_MAPPING_BINDING")
    if any(not isinstance(v, str) or not v.strip() for v in (dataset_id, domain_id, batch_id)):
        raise ValueError("INVALID_BATCH_CONTEXT")
    record = await repo.get_with_source_system(data_source_id)
    if record is None:
        raise LookupError(f"Không tìm thấy DataSource: {data_source_id}")
    data_source, _ = record
    request = build_read_request(data_source, source_table=source_table)
    if binding.source_system_id != str(data_source.source_system_id):
        raise ValueError("SOURCE_BINDING_MISMATCH")
    if not await repo.check_exist_source_system(data_source.source_system_id):
        raise ValueError("SourceSystem không tồn tại hoặc đã bị vô hiệu hóa")
    if request.source_type not in {"CSV", "EXCEL"}:
        raise ValueError("FILE_SNAPSHOT_REQUIRED")
    request = replace(request, object_version_id=object_version_id)
    result = await asyncio.to_thread(
        read_file_snapshot,
        request,
        SourceReaderFactory.get_reader(request.source_type),
        source_system_id=str(data_source.source_system_id),
        data_source_id=str(data_source_id),
        dataset_id=dataset_id,
    )
    return reader_to_batch(
        result,
        batch_id=batch_id,
        domain_id=domain_id,
        source_type=request.source_type,
        binding=binding,
        input_version=expected_input_version,
    )


def _validate_data_source_id(data_source_id: int) -> None:
    """Kiểm tra data_source_id là số nguyên dương."""

    if (
        isinstance(data_source_id, bool)
        or not isinstance(data_source_id, int)
        or data_source_id <= 0
    ):
        raise ValueError("data_source_id không hợp lệ")
