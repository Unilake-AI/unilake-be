"""Chuẩn bị quyền đọc Silver, ghi Gold; không dùng tài khoản quản trị khi xử lý dữ liệu."""

from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from minio.credentials.providers import StaticProvider
from minio.error import MinioAdminException
from minio.minioadmin import MinioAdmin

from app.core.config import settings
from app.infra.minio.bronze import client, setup_minio
from app.infra.minio.upload_accounts import write_private


def policy() -> dict[str, Any]:
    silver, gold = settings.minio_bucket_silver, settings.minio_bucket_gold
    if silver == gold or gold in {settings.minio_bucket_bronze, settings.minio_bucket_uploads}:
        raise ValueError("GOLD_BUCKET_MUST_BE_SEPARATE")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation"],
                "Resource": [f"arn:aws:s3:::{silver}", f"arn:aws:s3:::{gold}"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": [f"arn:aws:s3:::{silver}/silver/production/*"],
            },
            {
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:PutObject",
                    "s3:AbortMultipartUpload",
                    "s3:ListMultipartUploadParts",
                ],
                "Resource": [f"arn:aws:s3:::{gold}/gold/production/*"],
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "prepare", "apply"))
    parser.add_argument("--credentials-file", type=Path, default=Path(".env.gold-worker.local"))
    args = parser.parse_args()
    if args.action == "plan":
        print(json.dumps(policy(), indent=2))
    elif args.action == "prepare":
        write_private(
            args.credentials_file,
            "# Tài khoản Gold; không đưa vào kho mã nguồn.\nGOLD_WRITER_ACCESS_KEY=gold-writer\nGOLD_WRITER_SECRET_KEY="
            + secrets.token_hex(16)
            + "\n",
        )
    else:
        values = dotenv_values(args.credentials_file)
        access, secret = values.get("GOLD_WRITER_ACCESS_KEY"), values.get("GOLD_WRITER_SECRET_KEY")
        if not access or not secret:
            raise ValueError("GOLD_CREDENTIALS_REQUIRED")
        setup_minio(client(), settings.minio_bucket_gold)
        admin = MinioAdmin(
            endpoint=settings.minio_endpoint,
            credentials=StaticProvider(settings.minio_access_key, settings.minio_secret_key),
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
        # Cho phép chạy lại bước cấp quyền với chính tài khoản đã chuẩn bị trước đó.
        try:
            admin.get_service_account(access)
        except MinioAdminException as exc:
            if json.loads(exc._body).get("Code") not in {
                "XMinioAdminNoSuchServiceAccount",
                "XMinioAdminNoSuchUser",
                "XMinioInvalidIAMCredentials",
            }:
                raise
            admin.add_service_account(
                access_key=access, secret_key=secret, policy=policy(), name="gold-writer"
            )
        else:
            admin.update_service_account(access_key=access, secret_key=secret, policy=policy())
        print("Đã chuẩn bị quyền đọc Silver và ghi Gold.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        raise SystemExit(f"Chưa chuẩn bị được quyền Gold ({type(exc).__name__}).") from None
