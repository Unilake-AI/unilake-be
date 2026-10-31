from __future__ import annotations

from typing import Generic, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm.attributes import InstrumentedAttribute

ModelType = TypeVar("ModelType", bound=DeclarativeBase)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: type[ModelType], db: AsyncSession) -> None:
        self.model = model
        self.db = db

    # Hàm này dùng để lấy 1 row theo id
    async def get(self, item_id: int) -> ModelType | None:
        primary_key = self._primary_key()
        query = select(self.model).where(primary_key == item_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    def _primary_key(self) -> InstrumentedAttribute[object]:
        primary_key_columns = self.model.__mapper__.primary_key
        if len(primary_key_columns) != 1:
            msg = f"{self.model.__name__} must have exactly one primary key column"
            raise ValueError(msg)

        key = primary_key_columns[0].key
        if key is None:
            msg = f"{self.model.__name__} primary key column must have a mapped key"
            raise ValueError(msg)

        return cast("InstrumentedAttribute[object]", getattr(self.model, key))
