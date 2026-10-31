"""Logic dịch vụ nguồn dữ liệu."""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.modules.datasources.schemas import (
    DataSourceCreate,
    DataSourceFilter,
    DataSourceItem,
    DataSourceResponse,
    DataSourceUpdate,
)
from app.repositories.datasource_repo import DataSourceRepository

if TYPE_CHECKING:
    from app.modules.datasources.models import DataSource

ALLOWED_DATA_SOURCE_ROLES = {"DATA_ADMIN", "ADMIN", "ANALYST", "VIEWER"}
ALLOWED_DATA_SOURCE_ROLES_RESPONSE = tuple(sorted(ALLOWED_DATA_SOURCE_ROLES))
ALLOWED_DATA_SOURCE_WRITE_ROLES = {"DATA_ADMIN", "ADMIN"}
ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE = tuple(sorted(ALLOWED_DATA_SOURCE_WRITE_ROLES))


class DataSourceService:
    def __init__(self, db: AsyncSession) -> None:
        self.datasource_repo = DataSourceRepository(db)

    async def get_data_source(
        self,
        req: DataSourceFilter,
        current_user: dict[str, Any],
    ) -> DataSourceResponse:
        """Lấy danh sách data source có phân quyền, filter và phân trang."""

        # 2. Kiểm tra role lấy từ access token; user không đủ quyền sẽ nhận 403.
        self._ensure_can_read_data_sources(current_user)

        # 3. Query params đã được Pydantic validate trong DataSourceFilter.
        #    Ở đây chỉ chuyển page/page_size sang offset/limit cho SQL.
        offset = (req.page - 1) * req.page_size

        # 4. Repository chịu trách nhiệm SELECT + JOIN source_system,
        #    áp dụng filter, đếm total và phân trang bằng LIMIT/OFFSET.
        rows, total = await self.datasource_repo.list_with_source_system(
            req,
            limit=req.page_size,
            offset=offset,
        )

        # 5. Map dữ liệu sang response public.
        #    Không đưa configuration/credential ra ngoài để tránh lộ thông tin kết nối.
        items = [
            self._to_public_item(data_source, source_system_name)
            for data_source, source_system_name in rows
        ]

        # 6. Trả response 200 qua router với items + total + thông tin phân trang.
        return DataSourceResponse(
            items=items,
            total=total,
            page=req.page,
            page_size=req.page_size,
        )

    async def get_data_source_detail(
        self,
        data_source_id: int,
        current_user: dict[str, Any],
    ) -> DataSourceItem:
        """Lấy chi tiết một data source theo id, có phân quyền và response public."""

        # Dùng cùng rule đọc danh sách: DATA_ADMIN, ADMIN, ANALYST, VIEWER.
        self._ensure_can_read_data_sources(current_user)

        # Repository JOIN source_system để response có source_system_name.
        row = await self.datasource_repo.get_with_source_system(data_source_id)
        if row is None:
            raise AppException(
                "Không tìm thấy nguồn dữ liệu",
                code="DATA_SOURCE_NOT_FOUND",
                status_code=404,
                details={"data_source_id": data_source_id},
            )

        data_source, source_system_name = row
        return self._to_public_item(data_source, source_system_name)

    @staticmethod
    def _to_public_item(data_source: DataSource, source_system_name: str) -> DataSourceItem:
        """Map ORM DataSource sang payload public, không trả configuration/credential."""

        return DataSourceItem(
            data_source_id=data_source.data_source_id,
            source_system_id=data_source.source_system_id,
            source_system_name=source_system_name,
            source_name=data_source.source_name,
            source_type=data_source.source_type,
            connection_type=data_source.connection_type,
            location=data_source.location,
            description=data_source.description,
            is_active=data_source.is_active,
            last_ingested_at=data_source.last_ingested_at,
            last_ingestion_status=data_source.last_ingestion_status,
            ingestion_count=data_source.ingestion_count,
            created_by=data_source.created_by,
            created_at=data_source.created_at,
            updated_at=data_source.updated_at,
        )

    @staticmethod
    def _ensure_can_read_data_sources(current_user: dict[str, Any]) -> None:
        """Chỉ cho phép các role được đọc danh sách data source."""

        # Token payload chứa roles do AuthService đưa vào lúc login/refresh.
        role_set = DataSourceService._role_set(current_user)

        if role_set.isdisjoint(ALLOWED_DATA_SOURCE_ROLES):
            raise AppException(
                "Bạn không có quyền xem danh sách nguồn dữ liệu",
                code="FORBIDDEN",
                status_code=403,
                details={"allowed_roles": list(ALLOWED_DATA_SOURCE_ROLES_RESPONSE)},
            )

    @staticmethod
    def _ensure_can_write_data_sources(current_user: dict[str, Any]) -> None:
        """Chỉ cho phép các role được ghi data source."""

        role_set = DataSourceService._role_set(current_user)

        if role_set.isdisjoint(ALLOWED_DATA_SOURCE_WRITE_ROLES):
            raise AppException(
                "Bạn không có quyền ghi data source",
                code="FORBIDDEN",
                status_code=403,
                details={"allowed_roles": list(ALLOWED_DATA_SOURCE_WRITE_ROLES_RESPONSE)},
            )

    @staticmethod
    def _role_set(current_user: dict[str, Any]) -> set[str]:
        """Chuẩn hóa roles trong token payload thành set[str]."""

        roles: object = current_user.get("roles", ())
        if isinstance(roles, str):
            return {roles}
        if isinstance(roles, Collection):
            return {role for role in roles if isinstance(role, str)}
        return set()

    async def create_data_source(
        self,
        current_user: dict[str, Any],
        req: DataSourceCreate,
    ) -> DataSourceItem:
        """Tạo data source mới sau khi kiểm tra quyền, uniqueness và configuration."""

        # Kiểm tra user có quyền ghi data source.
        self._ensure_can_write_data_sources(current_user)

        # Create luôn có đủ field bắt buộc, nên validate trực tiếp bộ giá trị request.
        await self._validate_data_source_values(
            source_system_id=req.source_system_id,
            source_name=req.source_name,
            source_type=req.source_type,
            configuration=req.configuration,
        )

        # Tạo bản ghi mới
        data_source = await self.datasource_repo.create_data_source(
            req,
            created_by=self._current_user_id(current_user),
        )
        row = await self.datasource_repo.get_with_source_system(data_source.data_source_id)
        if row is None:
            raise AppException(
                "Không thể lấy nguồn dữ liệu vừa tạo",
                code="DATA_SOURCE_RETRIEVAL_FAILED",
                status_code=500,
            )
        # Chuyển sang public response
        created_data_source, source_system_name = row
        return self._to_public_item(created_data_source, source_system_name)

    async def _validate_data_source_values(
        self,
        *,
        source_system_id: int,
        source_name: str,
        source_type: str,
        configuration: dict[str, Any],
        exclude_data_source_id: int | None = None,
    ) -> None:
        """Validate bộ giá trị cuối cùng của data source cho cả create và update."""

        # 1. Kiểm tra source_system_id có tồn tại và is_active = true.
        #    Update cũng dùng rule này nếu user đổi source_system_id.
        await self._ensure_source_system_exists(source_system_id)

        # 2. Kiểm tra source_name không bị trùng trong cùng source_system.
        #    Khi update, exclude_data_source_id giúp không tự trùng với chính record hiện tại.
        await self._ensure_source_name_available(
            source_name,
            source_system_id,
            exclude_data_source_id=exclude_data_source_id,
        )

        # 3. Kiểm tra source_type thuộc danh sách hỗ trợ validate configuration.
        await self._ensure_source_type_supported(source_type)

        # 4. Kiểm tra configuration theo source_type cuối cùng.
        #    Quan trọng cho update partial: nếu đổi source_type thì config cũ/mới vẫn phải hợp lệ.
        self._ensure_configuration_valid(source_type, configuration)

    async def _ensure_source_system_exists(self, source_system_id: int) -> None:
        """Bảo đảm source system tồn tại và đang active."""

        if not await self.datasource_repo.check_exist_source_system(source_system_id):
            raise AppException(
                "Source system không tồn tại hoặc đã bị vô hiệu hóa",
                code="SOURCE_SYSTEM_NOT_FOUND",
                status_code=404,
            )

    async def _ensure_source_name_available(
        self,
        source_name: str,
        source_system_id: int,
        *,
        exclude_data_source_id: int | None = None,
    ) -> None:
        """Bảo đảm source_name không bị trùng trong cùng source_system."""

        if await self.datasource_repo.check_source_name(
            source_name,
            source_system_id,
            exclude_data_source_id=exclude_data_source_id,
        ):
            raise AppException(
                "Source name đã tồn tại",
                code="SOURCE_NAME_EXIST",
                status_code=409,
            )

    async def _ensure_source_type_supported(self, source_type: str) -> None:
        """Bảo đảm source_type nằm trong danh sách đang hỗ trợ."""

        if source_type not in await self.datasource_repo.help_source_type():
            raise AppException(
                "Không tìm thấy loại nguồn dữ liệu",
                code="SOURCE_TYPE_NOT_FOUND",
                status_code=422,
            )

    def _ensure_configuration_valid(
        self,
        source_type: str,
        configuration: dict[str, Any],
    ) -> None:
        """Bảo đảm configuration đủ key bắt buộc và đúng kiểu theo source_type."""

        configuration_errors = self.datasource_repo.validate_configuration(
            source_type,
            configuration,
        )
        if configuration_errors:
            raise AppException(
                "Configuration không hợp lệ",
                code="CONFIGURATION_INVALID",
                status_code=422,
                details={"errors": configuration_errors},
            )

    @staticmethod
    def _current_user_id(current_user: dict[str, Any]) -> int | None:
        """Lấy app_user_id từ token payload nếu có."""

        subject = current_user.get("sub")
        if subject is None:
            return None
        try:
            return int(subject)
        except (TypeError, ValueError) as exc:
            raise AppException(
                "Phiên đăng nhập không hợp lệ",
                code="INVALID_TOKEN",
                status_code=401,
            ) from exc

    async def update_data_source(
        self,
        current_user: dict[str, Any],
        data_source_id: int,
        req: DataSourceUpdate,
    ) -> DataSourceItem:
        """Cập nhật data source bằng payload optional, có validate lại trạng thái cuối."""

        # 1. Kiểm tra user có quyền ghi data source.
        self._ensure_can_write_data_sources(current_user)

        # 2. Lấy record hiện tại trước để update partial có thể merge field cũ + field mới.
        current_data_source = await self.datasource_repo.get(data_source_id)
        if current_data_source is None:
            raise AppException(
                "Không tìm thấy nguồn dữ liệu",
                code="DATA_SOURCE_NOT_FOUND",
                status_code=404,
                details={"data_source_id": data_source_id},
            )

        # 3. Chỉ lấy các field client thật sự gửi lên.
        #    Field không gửi sẽ giữ nguyên giá trị trong DB.
        update_values = req.model_dump(exclude_unset=True)
        if not update_values:
            row = await self.datasource_repo.get_with_source_system(data_source_id)
            if row is None:
                raise AppException(
                    "Không thể lấy nguồn dữ liệu sau khi cập nhật",
                    code="DATA_SOURCE_RETRIEVAL_FAILED",
                    status_code=500,
                )
            data_source, source_system_name = row
            return self._to_public_item(data_source, source_system_name)

        # 4. Tính trạng thái cuối cùng sau update.
        effective_source_system_id = (
            req.source_system_id
            if req.source_system_id is not None
            else current_data_source.source_system_id
        )
        effective_source_name = (
            req.source_name if req.source_name is not None else current_data_source.source_name
        )
        effective_source_type = (
            req.source_type if req.source_type is not None else current_data_source.source_type
        )
        effective_configuration = (
            req.configuration
            if req.configuration is not None
            else current_data_source.configuration
        )

        # 5. Validate lại toàn bộ bộ giá trị cuối cùng bằng helper dùng chung với create.
        await self._validate_data_source_values(
            source_system_id=effective_source_system_id,
            source_name=effective_source_name,
            source_type=effective_source_type,
            configuration=effective_configuration,
            exclude_data_source_id=data_source_id,
        )

        # 6. Ghi các field được gửi lên DB, không đụng các field còn lại.
        updated_data_source = await self.datasource_repo.update_data_source(
            current_data_source,
            update_values,
        )

        # 7. Refetch bằng JOIN source_system để response có source_system_name và vẫn không trả config.
        row = await self.datasource_repo.get_with_source_system(updated_data_source.data_source_id)
        if row is None:
            raise AppException(
                "Không thể lấy nguồn dữ liệu sau khi cập nhật",
                code="DATA_SOURCE_RETRIEVAL_FAILED",
                status_code=500,
            )

        data_source, source_system_name = row
        return self._to_public_item(data_source, source_system_name)
