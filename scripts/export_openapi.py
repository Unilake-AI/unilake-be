"""Xuất lại danh sách API để tài liệu lưu trong repository khớp Swagger hiện tại."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "docs/api/openapi.json"
    content = json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI đã cũ; chạy python -m scripts.export_openapi để cập nhật.")
    else:
        path.write_text(content, encoding="utf-8")
        print(f"Đã cập nhật {path}")


if __name__ == "__main__":
    main()
