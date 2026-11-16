"""Lưu lựa chọn xử lý cùng lịch sử thay đổi để những lần nạp sau dùng lại."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import insert, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException
from app.modules.pipeline.models.profiles import SilverProfileRevision, SilverSourceProfile
from app.repositories.silver_profile_repo import validate_profile_source

if TYPE_CHECKING:
    from app.modules.pipeline.services.source_profiles import SourceProfile


def save_profile(
    db: Engine, name: str, definition: SourceProfile, actor: str, revision: int | None
) -> dict[str, Any]:
    validate_profile_source(db, definition)
    values = {
        "definition": definition.model_dump(mode="json"),
        "fingerprint": definition.fingerprint,
        "updated_by": actor[:100],
        "updated_at": datetime.now(UTC),
    }
    try:
        with db.begin() as connection:
            if revision is None:
                connection.execute(
                    insert(SilverSourceProfile).values(profile_id=name, revision=1, **values)
                )
                new_revision = 1
            else:
                changed = connection.execute(
                    update(SilverSourceProfile)
                    .where(
                        SilverSourceProfile.profile_id == name,
                        SilverSourceProfile.revision == revision,
                    )
                    .values(revision=revision + 1, **values)
                    .returning(SilverSourceProfile.profile_id)
                ).scalar_one_or_none()
                if changed is None:
                    raise AppException(
                        "Cấu hình đã thay đổi, hãy tải lại trước khi sửa",
                        code="SILVER_PROFILE_REVISION_CONFLICT",
                        status_code=409,
                    )
                new_revision = revision + 1
            connection.execute(
                insert(SilverProfileRevision).values(
                    profile_id=name,
                    revision=new_revision,
                    definition=values["definition"],
                    fingerprint=values["fingerprint"],
                    created_by=actor,
                    created_at=values["updated_at"],
                )
            )
    except IntegrityError:
        raise AppException(
            "Mã cấu hình đã tồn tại", code="SILVER_PROFILE_CONFLICT", status_code=409
        ) from None
    return {
        "profile_id": name,
        "definition": definition,
        "fingerprint": definition.fingerprint,
        "revision": new_revision,
        "storage": "database",
    }
