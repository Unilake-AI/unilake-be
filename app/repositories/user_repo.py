from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from app.repositories.base_repo import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__(User, db)

    async def get_by_username(self, username: str) -> User | None:
        # Hold a shared lock until the login session is written. Account locking can
        # then revoke every session created before the account change commits.
        query = select(self.model).where(self.model.username == username).with_for_update(read=True)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
