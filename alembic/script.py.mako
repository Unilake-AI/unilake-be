"""${message}

Mã phiên bản: ${up_revision}
Phiên bản trước: ${down_revision | comma,n}
Ngày tạo: ${create_date}

UniLake AI - Migration cơ sở dữ liệu
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# Các mã định danh phiên bản được Alembic sử dụng.
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Nâng cấp schema cơ sở dữ liệu."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Hạ cấp schema cơ sở dữ liệu."""
    ${downgrades if downgrades else "pass"}
