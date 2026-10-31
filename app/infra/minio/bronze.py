"""Cấu trúc lưu trữ Bronze và CLI khởi tạo/upload tường minh (không I/O khi import)."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from minio import Minio
from minio.commonconfig import ENABLED
from minio.versioningconfig import VersioningConfig

from app.core.config import settings


def partition(source: str, day: date) -> str:
    """Phân vùng theo ngày nạp dữ liệu tại Asia/Ho_Chi_Minh, không theo ngày sự kiện nguồn."""
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,62}", source):
        raise ValueError("Source must be a lowercase slug (1-63 characters)")
    return f"{source}/{day:%Y/%m/%d}"


def object_key(source: str, day: date, batch_id: str, filename: str) -> str:
    for value in (batch_id, filename):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,199}", value):
            raise ValueError("Batch ID and filename must be safe single path components")
    return f"{partition(source, day)}/{batch_id}/{filename}"


def client() -> Minio:
    """Client quản trị chỉ cho CLI/provisioning và fixture quản trị của live tests."""
    if not settings.minio_access_key or not settings.minio_secret_key:
        raise ValueError("MINIO_ADMIN_CREDENTIALS_REQUIRED")
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region=settings.minio_region,
    )


def setup_minio(storage: Minio, bucket: str) -> None:
    if not storage.bucket_exists(bucket):
        # Tiến trình khởi tạo khác có thể tạo bucket đồng thời.
        from minio.error import S3Error

        try:
            storage.make_bucket(bucket)
        except S3Error as exc:
            if exc.code != "BucketAlreadyOwnedByYou":
                raise
    storage.set_bucket_versioning(bucket, VersioningConfig(ENABLED))


def local_path(root: Path, key: str) -> Path:
    root = root.resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise ValueError("Path escapes Bronze root")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("init", "upload"))
    parser.add_argument("--backend", choices=("minio", "local"), default="minio")
    parser.add_argument("--root", type=Path, default=Path("data/bronze"))
    parser.add_argument("--sources", nargs="+", default=["admissions", "hr"])
    parser.add_argument(
        "--date", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
    )
    parser.add_argument("--source")
    parser.add_argument("--file", type=Path)
    parser.add_argument("--batch-id", default=uuid4().hex)
    args = parser.parse_args()
    if args.action == "init":
        prefixes = [partition(source, args.date) for source in args.sources]
        if args.backend == "local":
            for prefix in prefixes:
                local_path(args.root, prefix).mkdir(parents=True, exist_ok=True)
        else:
            setup_minio(client(), settings.minio_bucket_bronze)
        print(json.dumps({"backend": args.backend, "partitions": prefixes}))
        return
    if not args.source or not args.file or not args.file.is_file():
        parser.error("upload requires --source and --file pointing to an existing file")
    key = object_key(args.source, args.date, args.batch_id, args.file.name)
    result: dict[str, str | None]
    if args.backend == "local":
        import shutil

        target = local_path(args.root, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Chỉ tạo tệp mới để tránh vô tình thay thế dữ liệu thô.
        with args.file.open("rb") as source, target.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        result = {"uri": target.as_uri(), "key": key}
    else:
        storage = client()
        bucket = settings.minio_bucket_bronze
        if storage.get_bucket_versioning(bucket).status != ENABLED:
            raise RuntimeError("Bronze versioning must be enabled; run init first")
        uploaded = storage.fput_object(bucket, key, str(args.file))
        result = {"uri": f"s3://{bucket}/{key}", "key": key, "version_id": uploaded.version_id}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
