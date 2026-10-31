from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.ingestion.readers.contract import ReadRequest, ReadResult
from app.modules.ingestion.readers.source_loader import read_data_source


def _repo() -> MagicMock:
    """Tạo repository giả lập với các method async mà source_loader cần."""

    repo = MagicMock()
    repo.get_with_source_system = AsyncMock()
    repo.check_exist_source_system = AsyncMock()
    return repo


def _data_source() -> SimpleNamespace:
    """Tạo DataSource giả lập đủ field cho loader và request_builder."""

    return SimpleNamespace(
        source_system_id=10,
        source_type="CSV",
        location="/data/csv",
        configuration={"delimiter": ",", "encoding": "utf-8", "has_header": True},
        is_active=True,
    )


def _read_request() -> ReadRequest:
    """Tạo ReadRequest giả lập sau bước build_read_request."""

    return ReadRequest(
        source_type="CSV",
        location="/data/csv",
        configuration={"delimiter": ",", "encoding": "utf-8", "has_header": True},
        source_table="applicant.csv",
    )


@pytest.mark.asyncio
async def test_read_data_source_success() -> None:
    """Loader lấy DataSource, build request, chọn reader rồi trả ReadResult."""

    repo = _repo()
    data_source = _data_source()
    request = _read_request()
    result = ReadResult(
        columns=["applicant_id", "full_name"],
        rows=[("1", "Nguyen Van A")],
        source_table="applicant.csv",
    )

    reader = MagicMock()
    reader.read = MagicMock()
    repo.get_with_source_system.return_value = (data_source, "Admissions System")
    repo.check_exist_source_system.return_value = True

    with (
        patch(
            "app.modules.ingestion.readers.source_loader.build_read_request",
            return_value=request,
        ) as build_mock,
        patch(
            "app.modules.ingestion.readers.source_loader.SourceReaderFactory.get_reader",
            return_value=reader,
        ) as factory_mock,
        patch(
            "app.modules.ingestion.readers.source_loader.asyncio.to_thread",
            new=AsyncMock(return_value=result),
        ) as to_thread_mock,
    ):
        actual = await read_data_source(repo, data_source_id=7, source_table="applicant.csv")

    assert actual is result
    repo.get_with_source_system.assert_awaited_once_with(7)
    build_mock.assert_called_once_with(data_source, source_table="applicant.csv")
    repo.check_exist_source_system.assert_awaited_once_with(10)
    factory_mock.assert_called_once_with("CSV")
    to_thread_mock.assert_awaited_once_with(reader.read, request)


@pytest.mark.asyncio
@pytest.mark.parametrize("data_source_id", [0, -1, True, "1"])
async def test_read_data_source_rejects_invalid_id(data_source_id: object) -> None:
    """data_source_id phải là int dương trước khi loader query repository."""

    repo = _repo()

    with pytest.raises(ValueError, match="data_source_id không hợp lệ"):
        await read_data_source(repo, data_source_id=data_source_id, source_table="applicant.csv")  # type: ignore[arg-type]

    repo.get_with_source_system.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_data_source_raises_when_data_source_missing() -> None:
    """Báo LookupError khi repository không tìm thấy DataSource."""

    repo = _repo()
    repo.get_with_source_system.return_value = None

    with pytest.raises(LookupError, match="Không tìm thấy DataSource: 999"):
        await read_data_source(repo, data_source_id=999, source_table="applicant.csv")

    repo.check_exist_source_system.assert_not_awaited()


@pytest.mark.asyncio
async def test_read_data_source_rejects_inactive_source_system() -> None:
    """Không đọc dữ liệu nếu SourceSystem của DataSource đã bị tắt."""

    repo = _repo()
    data_source = _data_source()
    request = _read_request()
    repo.get_with_source_system.return_value = (data_source, "Admissions System")
    repo.check_exist_source_system.return_value = False

    with (
        patch(
            "app.modules.ingestion.readers.source_loader.build_read_request",
            return_value=request,
        ) as build_mock,
        pytest.raises(ValueError, match="SourceSystem không tồn tại"),
    ):
        await read_data_source(repo, data_source_id=7, source_table="applicant.csv")

    build_mock.assert_called_once_with(data_source, source_table="applicant.csv")
    repo.check_exist_source_system.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_read_data_source_propagates_request_builder_error() -> None:
    """Lỗi validation từ build_read_request được giữ nguyên cho caller xử lý."""

    repo = _repo()
    data_source = _data_source()
    repo.get_with_source_system.return_value = (data_source, "Admissions System")

    with (
        patch(
            "app.modules.ingestion.readers.source_loader.build_read_request",
            side_effect=ValueError("Location không hợp lệ"),
        ),
        pytest.raises(ValueError, match="Location không hợp lệ"),
    ):
        await read_data_source(repo, data_source_id=7, source_table="applicant.csv")

    repo.check_exist_source_system.assert_not_awaited()
