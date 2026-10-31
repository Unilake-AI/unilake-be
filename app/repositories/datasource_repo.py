from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.modules.datasources.models import DataSource, SourceSystem
from app.modules.users.models import User as _User
from app.repositories.base_repo import BaseRepository

if TYPE_CHECKING:
    from app.modules.datasources.schemas import DataSourceCreate, DataSourceFilter

# Import User ở runtime để SQLAlchemy resolve relationship("User") trên DataSource.
_REGISTERED_RELATIONSHIP_MODELS = (_User,)


REQUIRED_CONFIG: dict[str, dict[str, type[Any]]] = {
    "CSV": {
        "delimiter": str,
        "encoding": str,
        "has_header": bool,
    },
    "EXCEL": {
        "sheet_name": str,
        "header_row": int,
        "skip_rows": int,
    },
    "JSON": {
        "encoding": str,
    },
    "REST_API": {
        "method": str,
        "headers": dict,
        "pagination": dict,
        "timeout_seconds": int,
    },
    "POSTGRESQL": {
        "host": str,
        "port": int,
        "database": str,
        "username": str,
        "password": str,
    },
    "RDBMS": {
        "host": str,
        "port": int,
        "database": str,
        "username": str,
        "password": str,
    },
}


class DataSourceRepository(BaseRepository[DataSource]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(DataSource, db)

    async def get_with_source_system(self, data_source_id: int) -> tuple[DataSource, str] | None:
        """Lấy một data source theo id, kèm tên source system từ bảng join."""

        query = (
            select(DataSource, SourceSystem.source_name.label("source_system_name"))
            .join(SourceSystem, SourceSystem.source_system_id == DataSource.source_system_id)
            .where(DataSource.data_source_id == data_source_id)
        )
        result = await self.db.execute(query)
        row = result.tuples().one_or_none()
        if row is None:
            return None
        return row

    async def create_data_source(
        self,
        req: DataSourceCreate,
        *,
        created_by: int | None,
    ) -> DataSource:
        """Tạo data source mới và flush để lấy generated primary key."""

        data_source = DataSource(
            source_system_id=req.source_system_id,
            source_name=req.source_name,
            source_type=req.source_type,
            connection_type=req.connection_type,
            location=req.location,
            configuration=req.configuration,
            description=req.description,
            is_active=req.is_active,
            created_by=created_by,
        )
        self.db.add(data_source)
        await self.db.flush()
        return data_source

    # Hàm lấy danh sách data source có filter và pagination.
    async def list_with_source_system(
        self,
        filters: DataSourceFilter,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[DataSource, str]], int]:
        # Chỉ thêm điều kiện cho filter thật sự được truyền từ query params.
        conditions: list[ColumnElement[bool]] = []
        if filters.source_system_id is not None:
            conditions.append(DataSource.source_system_id == filters.source_system_id)
        if filters.source_type is not None:
            conditions.append(DataSource.source_type == filters.source_type)
        if filters.is_active is not None:
            conditions.append(DataSource.is_active == filters.is_active)

        # SELECT data_source và JOIN source_system để lấy source_system_name cho response.
        base_query = (
            select(DataSource, SourceSystem.source_name.label("source_system_name"))
            .join(SourceSystem, SourceSystem.source_system_id == DataSource.source_system_id)
            .where(*conditions)
        )

        # Count dùng cùng JOIN/filter nhưng không limit/offset,
        # để client biết tổng số bản ghi theo bộ lọc hiện tại.
        count_query = (
            select(func.count())
            .select_from(DataSource)
            .join(SourceSystem, SourceSystem.source_system_id == DataSource.source_system_id)
            .where(*conditions)
        )

        total_result = await self.db.execute(count_query)
        total = int(total_result.scalar_one())

        # Query dữ liệu trang hiện tại. Service sẽ map thủ công các field public,
        # không trả configuration/credential ra response.
        rows_result = await self.db.execute(
            self._apply_pagination(base_query, limit=limit, offset=offset)
        )
        rows = cast("list[tuple[DataSource, str]]", rows_result.tuples().all())
        return rows, total

    @staticmethod
    def _apply_pagination(
        query: Any,
        *,
        limit: int,
        offset: int,
    ) -> Any:
        # Sắp xếp ổn định trước khi phân trang để dữ liệu không bị nhảy giữa các page.
        return (
            query.order_by(DataSource.created_at.desc(), DataSource.data_source_id.desc())
            .limit(limit)
            .offset(offset)
        )

    async def check_exist_source_system(self, source_system_id: int) -> bool:
        """Kiểm tra source system tồn tại và còn active."""

        query = (
            select(func.count())
            .select_from(SourceSystem)
            .where(SourceSystem.source_system_id == source_system_id)
            .where(SourceSystem.is_active.is_(True))
        )
        result = await self.db.execute(query)
        return result.scalar_one() > 0

    async def check_source_name(
        self,
        source_name: str,
        source_system_id: int,
        *,
        exclude_data_source_id: int | None = None,
    ) -> bool:
        """Kiểm tra trùng source_name trong cùng source_system."""

        if not source_name or not source_system_id:
            return False

        query = (
            select(func.count())
            .select_from(DataSource)
            .where(DataSource.source_name == source_name)
            .where(DataSource.source_system_id == source_system_id)
        )
        if exclude_data_source_id is not None:
            query = query.where(DataSource.data_source_id != exclude_data_source_id)

        result = await self.db.execute(query)
        return result.scalar_one() > 0

    async def help_source_type(self) -> list[str]:
        """Danh sách source_type đang được hỗ trợ validate configuration."""

        return sorted(REQUIRED_CONFIG)

    def validate_configuration(
        self,
        source_type: str,
        configuration: dict[str, Any],
    ) -> list[str]:
        """Validate configuration trước khi lưu data source.

        Hàm này là chốt chặn chung cho create/update:
        - Kiểm tra source_type có được hỗ trợ không.
        - Kiểm tra các key configuration bắt buộc.
        - Kiểm tra kiểu dữ liệu và một số rule hợp lệ theo từng source_type.
        """

        if source_type not in REQUIRED_CONFIG:
            return [f"Unsupported source type: {source_type}"]

        required_fields = REQUIRED_CONFIG[source_type]
        errors: list[str] = []

        # 1. Check tất cả key bắt buộc và kiểu dữ liệu cơ bản.
        for key, expected_type in required_fields.items():
            if key not in configuration:
                errors.append(f"Missing required configuration: {key}")
                continue

            if not isinstance(configuration[key], expected_type):
                expected_name = expected_type.__name__
                actual_name = type(configuration[key]).__name__
                errors.append(
                    f"Invalid type for configuration '{key}': expected {expected_name}, got {actual_name}"
                )

        # Nếu thiếu key hoặc sai kiểu cơ bản, dừng sớm để tránh validate sâu trên dữ liệu sai dạng.
        if errors:
            return errors

        # 2. Check rule chi tiết theo từng loại nguồn dữ liệu.
        if source_type == "CSV":
            errors.extend(self._validate_csv_configuration(configuration))
        elif source_type == "EXCEL":
            errors.extend(self._validate_excel_configuration(configuration))
        elif source_type == "JSON":
            errors.extend(self._validate_json_configuration(configuration))
        elif source_type == "REST_API":
            errors.extend(self._validate_rest_api_configuration(configuration))

        return errors

    @staticmethod
    def _validate_csv_configuration(configuration: dict[str, Any]) -> list[str]:
        """Validate rule riêng cho CSV."""

        errors: list[str] = []
        delimiter = configuration["delimiter"]
        encoding = configuration["encoding"]

        # delimiter/encoding là thông tin tối thiểu để parser đọc file CSV đúng cột và đúng charset.
        if delimiter == "":
            errors.append("Configuration 'delimiter' must not be empty")
        if encoding == "":
            errors.append("Configuration 'encoding' must not be empty")

        # quote_char là optional, nhưng nếu gửi lên thì phải là chuỗi để engine đọc CSV dùng được.
        quote_char = configuration.get("quote_char")
        if quote_char is not None and not isinstance(quote_char, str):
            errors.append("Invalid type for configuration 'quote_char': expected str")

        # skip_rows là optional, dùng để bỏ qua các dòng metadata đầu file; không được âm.
        skip_rows = configuration.get("skip_rows")
        if skip_rows is not None:
            if not isinstance(skip_rows, int):
                errors.append("Invalid type for configuration 'skip_rows': expected int")
            elif skip_rows < 0:
                errors.append("Configuration 'skip_rows' must be greater than or equal to 0")

        return errors

    @staticmethod
    def _validate_excel_configuration(configuration: dict[str, Any]) -> list[str]:
        """Validate rule riêng cho Excel."""

        errors: list[str] = []
        sheet_name = configuration["sheet_name"]
        header_row = configuration["header_row"]
        skip_rows = configuration["skip_rows"]

        # sheet_name rỗng sẽ khiến ingestion không biết đọc sheet nào trong workbook.
        if sheet_name == "":
            errors.append("Configuration 'sheet_name' must not be empty")

        # header_row dùng cách đếm thân thiện với user: dòng đầu tiên là 1, không phải 0.
        if header_row < 1:
            errors.append("Configuration 'header_row' must be greater than or equal to 1")

        # skip_rows cho Excel cũng không được âm vì nó là số dòng bỏ qua trước khi parse.
        if skip_rows < 0:
            errors.append("Configuration 'skip_rows' must be greater than or equal to 0")

        return errors

    @staticmethod
    def _validate_json_configuration(configuration: dict[str, Any]) -> list[str]:
        """Validate rule riêng cho JSON."""

        # JSON hiện chỉ yêu cầu encoding để đọc file đúng charset.
        if configuration["encoding"] == "":
            return ["Configuration 'encoding' must not be empty"]
        return []

    @staticmethod
    def _validate_rest_api_configuration(configuration: dict[str, Any]) -> list[str]:
        """Validate rule riêng cho REST API, gồm method và pagination config."""

        errors: list[str] = []
        method = configuration["method"].upper()
        headers = configuration["headers"]
        pagination = configuration["pagination"]
        timeout_seconds = configuration["timeout_seconds"]

        # Chỉ cho phép các HTTP method phổ biến mà ingestion client dự kiến hỗ trợ.
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            errors.append("Configuration 'method' must be one of: GET, POST, PUT, PATCH, DELETE")

        # Header phải là map string-string để có thể truyền thẳng vào HTTP client.
        if not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            errors.append(
                "Configuration 'headers' must be an object with string keys and string values"
            )

        # timeout_seconds phải dương để tránh request treo vô hạn hoặc fail ngay lập tức.
        if timeout_seconds <= 0:
            errors.append("Configuration 'timeout_seconds' must be greater than 0")

        # Pagination được tách rule theo type để API page/cursor có đủ param cần thiết.
        pagination_type = pagination.get("type")
        if pagination_type not in {"page", "cursor", "none"}:
            errors.append("Configuration 'pagination.type' must be one of: page, cursor, none")
        if pagination_type == "page":
            if not isinstance(pagination.get("page_param"), str):
                errors.append(
                    "Configuration 'pagination.page_param' is required for page pagination"
                )
            if not isinstance(pagination.get("size_param"), str):
                errors.append(
                    "Configuration 'pagination.size_param' is required for page pagination"
                )
        if pagination_type == "cursor" and not isinstance(pagination.get("cursor_param"), str):
            errors.append(
                "Configuration 'pagination.cursor_param' is required for cursor pagination"
            )

        return errors

    async def update_data_source(
        self,
        data_source: DataSource,
        values: dict[str, Any],
    ) -> DataSource:
        """Cập nhật các field được gửi lên và flush để ORM giữ trạng thái mới nhất."""

        for field_name, field_value in values.items():
            setattr(data_source, field_name, field_value)

        data_source.updated_at = datetime.now()
        await self.db.flush()
        return data_source
