"""Lưu bảng đối chiếu mã nguồn thật và tạo phiên bản cố định để chuẩn hóa Silver."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from sqlalchemy import URL, create_engine  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.modules.pipeline.repositories.identity import CrosswalkRepository  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, required=True, help="Tệp JSON chứa phiên bản và danh sách đối chiếu"
    )
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        )
    )
    try:
        with engine.begin() as connection:
            repository = CrosswalkRepository(connection)
            for entry in payload["entries"]:
                repository.register(
                    namespace=str(entry["source_system_id"]),
                    entity=entry["entity"],
                    source=tuple(entry["source"]),
                    evidence=entry["evidence"],
                    valid_from=datetime.fromisoformat(entry["valid_from"]),
                    valid_to=datetime.fromisoformat(entry["valid_to"])
                    if entry.get("valid_to")
                    else None,
                    canonical_id=entry.get("canonical_id"),
                )
            snapshot = repository.freeze(payload["version"])
        print(
            json.dumps(
                {
                    "version": snapshot.version,
                    "fingerprint": snapshot.fingerprint,
                    "entries": len(snapshot.entries),
                }
            )
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
