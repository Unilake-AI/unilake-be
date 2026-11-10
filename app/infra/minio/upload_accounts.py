"""Chuẩn bị tài khoản riêng cho kho tiếp nhận file; không cấp thêm quyền trên Bronze."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from minio.credentials.providers import StaticProvider
from minio.error import MinioAdminException, S3Error
from minio.minioadmin import MinioAdmin

from app.core.config import settings
from app.infra.minio.bronze import client, setup_minio


def policy_for(role: str, bucket: str) -> dict[str, Any]:
    if role not in {"writer", "reader", "cleanup"} or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket
    ):
        raise ValueError("INVALID_UPLOAD_ACCOUNT_SCOPE")
    if bucket in {
        settings.minio_bucket_bronze,
        settings.minio_bucket_silver,
        settings.minio_bucket_gold,
    }:
        raise ValueError("UPLOAD_BUCKET_MUST_BE_SEPARATE")
    actions = ["s3:GetObject", "s3:GetObjectVersion"]
    if role == "writer":
        # API cần đọc lại chính file vừa nhận để xác minh đã lưu đủ nội dung.
        actions += ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    if role == "cleanup":
        actions += ["s3:DeleteObject", "s3:DeleteObjectVersion"]
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:GetBucketVersioning"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
            {
                "Effect": "Allow",
                "Action": actions,
                "Resource": [f"arn:aws:s3:::{bucket}/incoming/*"],
            },
        ],
    }


def write_private(path: Path, content: str) -> None:
    # Không ghi đè file đang có để tránh thay tài khoản của dịch vụ ngoài ý muốn.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def prepare(path: Path) -> None:
    content = "# Thông tin xác thực cục bộ, không đưa vào kho mã nguồn.\n"
    for role in ("WRITER", "READER", "CLEANUP"):
        # Dùng 32 ký tự để tài khoản được MinIO chấp nhận, kể cả bản giới hạn tối đa 40 ký tự.
        content += f"UPLOAD_{role}_ACCESS_KEY=upload-{role.lower()}\nUPLOAD_{role}_SECRET_KEY={secrets.token_hex(16)}\n"
    write_private(path, content)


def export_runtime(path: Path) -> None:
    values = dict(dotenv_values(path))
    for role in ("WRITER", "READER", "CLEANUP"):
        if not all(values.get(f"UPLOAD_{role}_{kind}") for kind in ("ACCESS_KEY", "SECRET_KEY")):
            raise ValueError("UPLOAD_CREDENTIALS_REQUIRED")
    for service, roles in (("backend", ("WRITER",)), ("worker", ("READER", "CLEANUP"))):
        content = "".join(
            f"UPLOAD_{role}_{kind}={values[f'UPLOAD_{role}_{kind}']}\n"
            for role in roles
            for kind in ("ACCESS_KEY", "SECRET_KEY")
        )
        target = path.parent / f".env.upload-{service}.local"
        if target.exists():
            if target.read_text(encoding="utf-8") != content:
                raise ValueError("UPLOAD_RUNTIME_ENV_CONFLICT")
        else:
            write_private(target, content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "export-runtime", "apply"))
    parser.add_argument("--credentials-file", type=Path, default=Path(".env.upload-accounts.local"))
    args = parser.parse_args()
    if args.action == "plan":
        print(
            json.dumps(
                {
                    role: policy_for(role, settings.minio_bucket_uploads)
                    for role in ("writer", "reader", "cleanup")
                },
                indent=2,
            )
        )
    elif args.action == "prepare":
        prepare(args.credentials_file)
    elif args.action == "export-runtime":
        export_runtime(args.credentials_file)
    else:
        values = dict(dotenv_values(args.credentials_file))
        policies = {
            role: policy_for(role, settings.minio_bucket_uploads)
            for role in ("writer", "reader", "cleanup")
        }
        for role in policies:
            if not all(
                values.get(f"UPLOAD_{role.upper()}_{kind}") for kind in ("ACCESS_KEY", "SECRET_KEY")
            ):
                raise ValueError("UPLOAD_CREDENTIALS_REQUIRED")
        # Chỉ công cụ quản trị mới tạo kho và tài khoản; API không tự dùng tài khoản quản trị.
        setup_minio(client(), settings.minio_bucket_uploads)
        admin = MinioAdmin(
            endpoint=settings.minio_endpoint,
            credentials=StaticProvider(settings.minio_access_key, settings.minio_secret_key),
            region=settings.minio_region,
            secure=settings.minio_secure,
        )
        for role, policy in policies.items():
            admin.add_service_account(
                access_key=values[f"UPLOAD_{role.upper()}_ACCESS_KEY"],
                secret_key=values[f"UPLOAD_{role.upper()}_SECRET_KEY"],
                name=f"ingestion-upload-{role}",
                policy=policy,
            )
        print("Đã tạo tài khoản kho tiếp nhận; không xuất thông tin xác thực ra màn hình.")


if __name__ == "__main__":
    try:
        main()
    except (MinioAdminException, S3Error) as exc:
        # Thông báo lỗi quản trị có thể chứa dữ liệu nhạy cảm; chỉ trả loại lỗi.
        raise SystemExit(f"Không thể chuẩn bị tài khoản tiếp nhận ({type(exc).__name__})") from None
