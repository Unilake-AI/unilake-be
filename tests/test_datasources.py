"""Kiểm thử đơn vị cho logic dịch vụ nguồn dữ liệu."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import AppException
from app.core.responses import ErrorResponse
from app.modules.datasources.router import (
    DATA_SOURCE_ERROR_RESPONSES,
    DATA_SOURCE_WRITE_ERROR_RESPONSES,
)
from app.modules.datasources.schemas import (
    DataSourceCreate,
    DataSourceFilter,
    DataSourceResponse,
    DataSourceUpdate,
)
from app.modules.datasources.service import (
    ALLOWED_DATA_SOURCE_ROLES_RESPONSE,
    ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE,
    DataSourceService,
)
from app.modules.users.models import User as _User
from app.repositories.datasource_repo import DataSourceRepository


def _db() -> MagicMock:
    return MagicMock()


def _repo(service: DataSourceService) -> Any:
    return service.datasource_repo


def _data_source() -> SimpleNamespace:
    return SimpleNamespace(
        data_source_id=10,
        source_system_id=3,
        source_name="Admissions CSV 2026",
        source_type="CSV",
        connection_type="FILE",
        location="s3://bronze/admissions.csv",
        configuration={"access_key": "secret", "delimiter": ","},
        description="Admissions sample source",
        is_active=True,
        last_ingested_at=None,
        last_ingestion_status=None,
        ingestion_count=2,
        created_by=1,
        created_at=datetime(2026, 10, 8, 8, 0, 0),
        updated_at=None,
    )


def _create_request() -> DataSourceCreate:
    return DataSourceCreate(
        source_system_id=3,
        source_name="Admissions CSV 2026",
        source_type="CSV",
        connection_type="FILE",
        location="s3://bronze/admissions.csv",
        configuration={"delimiter": ",", "encoding": "utf-8", "has_header": True},
        description="Admissions sample source",
        is_active=True,
    )


def _update_request() -> DataSourceUpdate:
    return DataSourceUpdate(description="Updated source description")


@pytest.mark.asyncio
async def test_get_data_source_returns_paginated_public_payload() -> None:
    service = DataSourceService(_db())
    list_mock = AsyncMock(return_value=([(_data_source(), "Admissions System")], 1))
    _repo(service).list_with_source_system = list_mock

    response = await service.get_data_source(
        DataSourceFilter(source_type="CSV", is_active=True, page=2, page_size=5),
        {"roles": ["VIEWER"]},
    )

    assert isinstance(response, DataSourceResponse)
    assert response.total == 1
    assert response.page == 2
    assert response.page_size == 5
    assert response.items[0].source_system_name == "Admissions System"
    assert response.items[0].source_name == "Admissions CSV 2026"
    assert "configuration" not in response.items[0].model_dump()
    list_mock.assert_awaited_once()
    await_args = list_mock.await_args
    assert await_args is not None
    _, kwargs = await_args
    assert kwargs == {"limit": 5, "offset": 5}


@pytest.mark.asyncio
async def test_get_data_source_rejects_user_without_allowed_role() -> None:
    service = DataSourceService(_db())
    list_mock = AsyncMock()
    _repo(service).list_with_source_system = list_mock

    with pytest.raises(AppException) as exc_info:
        await service.get_data_source(DataSourceFilter(), {"roles": ["STUDENT"]})

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "FORBIDDEN"
    assert exc_info.value.details == {"allowed_roles": list(ALLOWED_DATA_SOURCE_ROLES_RESPONSE)}
    list_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_data_source_detail_returns_public_payload() -> None:
    service = DataSourceService(_db())
    detail_mock = AsyncMock(return_value=(_data_source(), "Admissions System"))
    _repo(service).get_with_source_system = detail_mock

    response = await service.get_data_source_detail(10, {"roles": ["ANALYST"]})

    assert response.data_source_id == 10
    assert response.source_system_name == "Admissions System"
    assert response.source_name == "Admissions CSV 2026"
    assert "configuration" not in response.model_dump()
    detail_mock.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_get_data_source_detail_raises_404_when_missing() -> None:
    service = DataSourceService(_db())
    detail_mock = AsyncMock(return_value=None)
    _repo(service).get_with_source_system = detail_mock

    with pytest.raises(AppException) as exc_info:
        await service.get_data_source_detail(999, {"roles": ["DATA_ADMIN"]})

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "DATA_SOURCE_NOT_FOUND"
    assert exc_info.value.details == {"data_source_id": 999}


def test_data_source_role_check_accepts_role_string_payload() -> None:
    DataSourceService._ensure_can_read_data_sources({"roles": "DATA_ADMIN"})


def test_data_source_role_check_rejects_missing_roles() -> None:
    with pytest.raises(AppException) as exc_info:
        DataSourceService._ensure_can_read_data_sources({})

    assert exc_info.value.status_code == 403
    assert exc_info.value.details["allowed_roles"] == list(ALLOWED_DATA_SOURCE_ROLES_RESPONSE)


def test_data_source_route_documents_standard_error_response() -> None:
    forbidden_response = DATA_SOURCE_ERROR_RESPONSES[403]

    assert forbidden_response["model"] is ErrorResponse
    assert "DATA_ADMIN" in forbidden_response["description"]
    assert "VIEWER" in forbidden_response["description"]


def test_create_data_source_route_documents_write_error_response() -> None:
    forbidden_response = DATA_SOURCE_WRITE_ERROR_RESPONSES[403]

    assert forbidden_response["model"] is ErrorResponse
    assert "DATA_ADMIN" in forbidden_response["description"]
    assert "VIEWER" not in forbidden_response["description"]


@pytest.mark.asyncio
async def test_create_data_source_returns_public_payload_and_sets_created_by() -> None:
    service = DataSourceService(_db())
    created = _data_source()
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=False)
    help_mock = AsyncMock(return_value=["CSV", "JSON"])
    validate_mock = MagicMock(return_value=[])
    create_mock = AsyncMock(return_value=created)
    detail_mock = AsyncMock(return_value=(created, "Admissions System"))
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).help_source_type = help_mock
    _repo(service).validate_configuration = validate_mock
    _repo(service).create_data_source = create_mock
    _repo(service).get_with_source_system = detail_mock

    response = await service.create_data_source(
        {"roles": ["DATA_ADMIN"], "sub": "7"},
        _create_request(),
    )

    assert response.data_source_id == 10
    assert response.source_system_name == "Admissions System"
    assert response.created_by == 1
    assert "configuration" not in response.model_dump()
    create_mock.assert_awaited_once()
    await_args = create_mock.await_args
    assert await_args is not None
    args, kwargs = await_args
    assert args == (_create_request(),)
    assert kwargs == {"created_by": 7}
    detail_mock.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_create_data_source_rejects_viewer_role() -> None:
    service = DataSourceService(_db())
    exists_mock = AsyncMock()
    _repo(service).check_exist_source_system = exists_mock

    with pytest.raises(AppException) as exc_info:
        await service.create_data_source({"roles": ["VIEWER"], "sub": "7"}, _create_request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.details == {
        "allowed_roles": list(ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE)
    }
    exists_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_data_source_rejects_missing_source_system() -> None:
    service = DataSourceService(_db())
    exists_mock = AsyncMock(return_value=False)
    create_mock = AsyncMock()
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).create_data_source = create_mock

    with pytest.raises(AppException) as exc_info:
        await service.create_data_source({"roles": ["ADMIN"], "sub": "7"}, _create_request())

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "SOURCE_SYSTEM_NOT_FOUND"
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_data_source_rejects_duplicate_source_name() -> None:
    service = DataSourceService(_db())
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=True)
    create_mock = AsyncMock()
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).create_data_source = create_mock

    with pytest.raises(AppException) as exc_info:
        await service.create_data_source({"roles": ["ADMIN"], "sub": "7"}, _create_request())

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SOURCE_NAME_EXIST"
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_data_source_rejects_invalid_configuration() -> None:
    service = DataSourceService(_db())
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=False)
    help_mock = AsyncMock(return_value=["CSV", "JSON"])
    validate_mock = MagicMock(return_value=["Missing encoding"])
    create_mock = AsyncMock()
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).help_source_type = help_mock
    _repo(service).validate_configuration = validate_mock
    _repo(service).create_data_source = create_mock

    with pytest.raises(AppException) as exc_info:
        await service.create_data_source({"roles": ["ADMIN"], "sub": "7"}, _create_request())

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "CONFIGURATION_INVALID"
    assert exc_info.value.details == {"errors": ["Missing encoding"]}
    create_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_data_source_merges_current_values_and_updates_partial_payload() -> None:
    service = DataSourceService(_db())
    current = _data_source()
    updated = _data_source()
    updated.description = "Updated source description"
    get_mock = AsyncMock(return_value=current)
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=False)
    help_mock = AsyncMock(return_value=["CSV", "JSON"])
    validate_mock = MagicMock(return_value=[])
    update_mock = AsyncMock(return_value=updated)
    detail_mock = AsyncMock(return_value=(updated, "Admissions System"))
    _repo(service).get = get_mock
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).help_source_type = help_mock
    _repo(service).validate_configuration = validate_mock
    _repo(service).update_data_source = update_mock
    _repo(service).get_with_source_system = detail_mock

    response = await service.update_data_source(
        {"roles": ["ADMIN"], "sub": "7"},
        10,
        _update_request(),
    )

    assert response.description == "Updated source description"
    assert "configuration" not in response.model_dump()
    get_mock.assert_awaited_once_with(10)
    exists_mock.assert_awaited_once_with(3)
    duplicate_mock.assert_awaited_once_with(
        "Admissions CSV 2026",
        3,
        exclude_data_source_id=10,
    )
    validate_mock.assert_called_once_with("CSV", current.configuration)
    update_mock.assert_awaited_once_with(
        current,
        {"description": "Updated source description"},
    )
    detail_mock.assert_awaited_once_with(10)


@pytest.mark.asyncio
async def test_update_data_source_rejects_missing_record() -> None:
    service = DataSourceService(_db())
    get_mock = AsyncMock(return_value=None)
    update_mock = AsyncMock()
    _repo(service).get = get_mock
    _repo(service).update_data_source = update_mock

    with pytest.raises(AppException) as exc_info:
        await service.update_data_source({"roles": ["ADMIN"], "sub": "7"}, 999, _update_request())

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "DATA_SOURCE_NOT_FOUND"
    assert exc_info.value.details == {"data_source_id": 999}
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_data_source_validates_effective_source_type_and_configuration() -> None:
    service = DataSourceService(_db())
    current = _data_source()
    req = DataSourceUpdate(
        source_type="EXCEL",
        configuration={"sheet_name": "Sheet1", "header_row": 1, "skip_rows": 0},
    )
    get_mock = AsyncMock(return_value=current)
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=False)
    help_mock = AsyncMock(return_value=["CSV", "EXCEL"])
    validate_mock = MagicMock(return_value=[])
    update_mock = AsyncMock(return_value=current)
    detail_mock = AsyncMock(return_value=(current, "Admissions System"))
    _repo(service).get = get_mock
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).help_source_type = help_mock
    _repo(service).validate_configuration = validate_mock
    _repo(service).update_data_source = update_mock
    _repo(service).get_with_source_system = detail_mock

    await service.update_data_source({"roles": ["ADMIN"], "sub": "7"}, 10, req)

    validate_mock.assert_called_once_with(
        "EXCEL",
        {"sheet_name": "Sheet1", "header_row": 1, "skip_rows": 0},
    )
    update_mock.assert_awaited_once_with(
        current,
        {
            "source_type": "EXCEL",
            "configuration": {"sheet_name": "Sheet1", "header_row": 1, "skip_rows": 0},
        },
    )


@pytest.mark.asyncio
async def test_update_data_source_rejects_viewer_role() -> None:
    service = DataSourceService(_db())
    get_mock = AsyncMock()
    _repo(service).get = get_mock

    with pytest.raises(AppException) as exc_info:
        await service.update_data_source({"roles": ["VIEWER"], "sub": "7"}, 10, _update_request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "FORBIDDEN"
    assert exc_info.value.details == {
        "allowed_roles": list(ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE)
    }
    get_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_data_source_empty_payload_returns_current_public_payload() -> None:
    service = DataSourceService(_db())
    current = _data_source()
    get_mock = AsyncMock(return_value=current)
    detail_mock = AsyncMock(return_value=(current, "Admissions System"))
    update_mock = AsyncMock()
    exists_mock = AsyncMock()
    duplicate_mock = AsyncMock()
    _repo(service).get = get_mock
    _repo(service).get_with_source_system = detail_mock
    _repo(service).update_data_source = update_mock
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock

    response = await service.update_data_source(
        {"roles": ["ADMIN"], "sub": "7"},
        10,
        DataSourceUpdate(),
    )

    assert response.data_source_id == 10
    assert response.source_system_name == "Admissions System"
    assert "configuration" not in response.model_dump()
    get_mock.assert_awaited_once_with(10)
    detail_mock.assert_awaited_once_with(10)
    update_mock.assert_not_awaited()
    exists_mock.assert_not_awaited()
    duplicate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_data_source_rejects_duplicate_effective_name() -> None:
    service = DataSourceService(_db())
    current = _data_source()
    get_mock = AsyncMock(return_value=current)
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=True)
    update_mock = AsyncMock()
    _repo(service).get = get_mock
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).update_data_source = update_mock

    with pytest.raises(AppException) as exc_info:
        await service.update_data_source(
            {"roles": ["ADMIN"], "sub": "7"},
            10,
            DataSourceUpdate(source_name="Existing Source Name"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "SOURCE_NAME_EXIST"
    duplicate_mock.assert_awaited_once_with(
        "Existing Source Name",
        3,
        exclude_data_source_id=10,
    )
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_data_source_rejects_unsupported_source_type() -> None:
    service = DataSourceService(_db())
    current = _data_source()
    get_mock = AsyncMock(return_value=current)
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=False)
    help_mock = AsyncMock(return_value=["CSV", "EXCEL", "JSON", "REST_API"])
    update_mock = AsyncMock()
    _repo(service).get = get_mock
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).help_source_type = help_mock
    _repo(service).update_data_source = update_mock

    with pytest.raises(AppException) as exc_info:
        await service.update_data_source(
            {"roles": ["ADMIN"], "sub": "7"},
            10,
            DataSourceUpdate(source_type="KAFKA"),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "SOURCE_TYPE_NOT_FOUND"
    update_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_data_source_rejects_invalid_effective_configuration() -> None:
    service = DataSourceService(_db())
    current = _data_source()
    get_mock = AsyncMock(return_value=current)
    exists_mock = AsyncMock(return_value=True)
    duplicate_mock = AsyncMock(return_value=False)
    help_mock = AsyncMock(return_value=["CSV", "EXCEL"])
    validate_mock = MagicMock(return_value=["Missing required configuration: header_row"])
    update_mock = AsyncMock()
    _repo(service).get = get_mock
    _repo(service).check_exist_source_system = exists_mock
    _repo(service).check_source_name = duplicate_mock
    _repo(service).help_source_type = help_mock
    _repo(service).validate_configuration = validate_mock
    _repo(service).update_data_source = update_mock

    with pytest.raises(AppException) as exc_info:
        await service.update_data_source(
            {"roles": ["ADMIN"], "sub": "7"},
            10,
            DataSourceUpdate(source_type="EXCEL", configuration={"sheet_name": "Sheet1"}),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "CONFIGURATION_INVALID"
    assert exc_info.value.details == {"errors": ["Missing required configuration: header_row"]}
    validate_mock.assert_called_once_with("EXCEL", {"sheet_name": "Sheet1"})
    update_mock.assert_not_awaited()


def test_validate_configuration_accepts_valid_csv_config() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration(
        "CSV",
        {"delimiter": ",", "encoding": "utf-8", "has_header": True},
    )

    assert errors == []


def test_validate_configuration_accepts_valid_excel_config() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration(
        "EXCEL",
        {"sheet_name": "Sheet1", "header_row": 1, "skip_rows": 0},
    )

    assert errors == []


def test_validate_configuration_accepts_valid_rest_api_config() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration(
        "REST_API",
        {
            "method": "GET",
            "headers": {
                "Authorization": "Bearer xxx",
                "Content-Type": "application/json",
            },
            "pagination": {
                "type": "page",
                "page_param": "page",
                "size_param": "page_size",
            },
            "timeout_seconds": 30,
        },
    )

    assert errors == []


def test_validate_configuration_reports_invalid_excel_values() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration(
        "EXCEL",
        {"sheet_name": "", "header_row": 0, "skip_rows": -1},
    )

    assert "Configuration 'sheet_name' must not be empty" in errors
    assert "Configuration 'header_row' must be greater than or equal to 1" in errors
    assert "Configuration 'skip_rows' must be greater than or equal to 0" in errors


def test_validate_configuration_reports_invalid_rest_api_values() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration(
        "REST_API",
        {
            "method": "CONNECT",
            "headers": {"Authorization": 123},
            "pagination": {"type": "page", "page_param": "page"},
            "timeout_seconds": 0,
        },
    )

    assert "Configuration 'method' must be one of: GET, POST, PUT, PATCH, DELETE" in errors
    assert "Configuration 'headers' must be an object with string keys and string values" in errors
    assert "Configuration 'timeout_seconds' must be greater than 0" in errors
    assert "Configuration 'pagination.size_param' is required for page pagination" in errors


def test_validate_configuration_reports_invalid_csv_optional_values() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration(
        "CSV",
        {
            "delimiter": "",
            "encoding": "",
            "has_header": True,
            "quote_char": 1,
            "skip_rows": -1,
        },
    )

    assert "Configuration 'delimiter' must not be empty" in errors
    assert "Configuration 'encoding' must not be empty" in errors
    assert "Invalid type for configuration 'quote_char': expected str" in errors
    assert "Configuration 'skip_rows' must be greater than or equal to 0" in errors


@pytest.mark.asyncio
async def test_create_data_source_repository_preserves_valid_configuration() -> None:
    db = _db()
    db.flush = AsyncMock()
    repo = DataSourceRepository(db)
    req = _create_request()

    data_source = await repo.create_data_source(req, created_by=7)

    assert _User.__tablename__ == "app_user"
    assert data_source.configuration == req.configuration
    assert data_source.source_type == "CSV"
    assert data_source.created_by == 7
    db.add.assert_called_once_with(data_source)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_help_source_type_includes_excel() -> None:
    repo = DataSourceRepository(_db())

    source_types = await repo.help_source_type()

    assert "EXCEL" in source_types
    assert "REST_API" in source_types


def test_validate_configuration_reports_missing_and_invalid_fields() -> None:
    repo = DataSourceRepository(_db())

    errors = repo.validate_configuration("CSV", {"delimiter": ",", "has_header": "yes"})

    assert "Missing required configuration: encoding" in errors
    assert "Invalid type for configuration 'has_header': expected bool, got str" in errors


def test_validate_configuration_reports_unsupported_source_type() -> None:
    repo = DataSourceRepository(_db())

    assert repo.validate_configuration("KAFKA", {}) == ["Unsupported source type: KAFKA"]
