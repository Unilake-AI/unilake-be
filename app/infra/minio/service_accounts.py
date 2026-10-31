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
from minio.error import MinioAdminException
from minio.minioadmin import MinioAdmin

from app.core.config import settings

# Đây là SA MinIO thật (child credentials), không phải role PostgreSQL hay domain.
ACCOUNTS = {
    "writer": "bronze-ingestion-writer",
    "verifier": "bronze-verifier-reader",
    "reader": "bronze-pipeline-reader",
    "silver-writer": "silver-pipeline-writer",
}


def policy_for(role: str, bucket: str) -> dict[str, Any]:
    """Chỉ cho phép đúng thao tác cần thiết; không cấp quyền admin hoặc xóa object."""
    if role not in ACCOUNTS or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("INVALID_MINIO_ACCOUNT_SCOPE")
    objects = f"arn:aws:s3:::{bucket}/*"
    bucket_arn = f"arn:aws:s3:::{bucket}"
    if role == "writer":
        object_actions = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        bucket_actions = ["s3:GetBucketLocation", "s3:GetBucketVersioning"]
    elif role == "silver-writer":
        object_actions = [
            "s3:GetObject",
            "s3:GetObjectVersion",
            "s3:PutObject",
            "s3:AbortMultipartUpload",
            "s3:ListMultipartUploadParts",
        ]
        bucket_actions = ["s3:GetBucketLocation"]
    else:
        object_actions = ["s3:GetObject", "s3:GetObjectVersion"]
        bucket_actions = ["s3:GetBucketLocation"]
        if role == "reader":
            bucket_actions += ["s3:ListBucket", "s3:ListBucketVersions"]
    policy: dict[str, Any] = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": bucket_actions, "Resource": [bucket_arn]},
            {"Effect": "Allow", "Action": object_actions, "Resource": [objects]},
        ],
    }
    if role == "writer":
        # Deny tường minh quyền đọc/xóa dữ liệu; vẫn cho phép đọc cấu hình bucket
        # để kiểm tra location/versioning. Abort multipart không xóa object đã lưu.
        policy["Statement"].append(
            {
                "Effect": "Deny",
                "Action": [
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:DeleteObject",
                    "s3:DeleteObjectVersion",
                ],
                "Resource": [objects],
            }
        )
    elif role == "reader":
        # Downstream không được sửa bytes, tags, retention hoặc xóa bất kỳ version nào.
        # Wildcard chỉ nằm trong Deny, bao phủ cả các biến thể Put/DeleteObject.
        policy["Statement"].append(
            {
                "Effect": "Deny",
                "Action": ["s3:PutObject*", "s3:DeleteObject*", "s3:AbortMultipartUpload"],
                "Resource": [objects],
            }
        )
    elif role == "silver-writer":
        policy["Statement"].append(
            {
                "Effect": "Deny",
                "Action": ["s3:DeleteObject", "s3:DeleteObjectVersion"],
                "Resource": [objects],
            }
        )
    return policy


def _update_policy(admin: MinioAdmin, access_key: str, bucket: str, role: str) -> None:
    """Gắn inline policy rồi đọc lại kiểm chứng, không đổi secret của SA."""
    prefix = "SILVER_WRITER" if role == "silver-writer" else f"BRONZE_{role.upper()}"
    if not access_key:
        raise ValueError(f"{prefix}_ACCESS_KEY_REQUIRED")
    policy = policy_for(role, bucket)
    admin.update_service_account(access_key=access_key, policy=policy)
    # MinIO có thể đổi thứ tự action/resource. So sánh nội dung thay vì chuỗi JSON.
    actual = json.loads(admin.get_service_account(access_key))["policy"]
    if isinstance(actual, str):
        actual = json.loads(actual)

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        if isinstance(value, list):
            return sorted(
                (normalize(item) for item in value),
                key=lambda item: json.dumps(item, sort_keys=True),
            )
        return value

    if normalize(actual) != normalize(policy):
        raise RuntimeError(f"{prefix}_POLICY_VERIFICATION_FAILED")
    print(f"Updated and verified: {ACCOUNTS[role]}")


def update_writer_policy(admin: MinioAdmin, access_key: str, bucket: str) -> None:
    _update_policy(admin, access_key, bucket, "writer")


def update_reader_policy(admin: MinioAdmin, access_key: str, bucket: str) -> None:
    _update_policy(admin, access_key, bucket, "reader")


def update_silver_writer_policy(admin: MinioAdmin, access_key: str, bucket: str) -> None:
    _update_policy(admin, access_key, bucket, "silver-writer")


def prepare(path: Path) -> None:
    """Tạo secret ngẫu nhiên trong file local mode 600; không ghi đè file đã có."""
    lines = ["# UDL-142: secret local, không commit hoặc gửi nội dung file này."]
    for role in ACCOUNTS:
        prefix = "SILVER_WRITER" if role == "silver-writer" else f"BRONZE_{role.upper()}"
        access_name = "silver-writer" if role == "silver-writer" else f"bronze-{role}"
        # Access key ngắn để tương thích giới hạn IAM; name mô tả vẫn giữ đầy đủ.
        lines += [
            f"{prefix}_ACCESS_KEY={access_name}",
            f"{prefix}_SECRET_KEY={secrets.token_hex(16)}",
        ]
    # O_EXCL cũng từ chối symlink/file cũ để tránh vô tình ghi đè credentials.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def prepare_source(path: Path) -> None:
    """SA đọc nguồn tách khỏi reader Bronze để không mở rộng policy Bronze."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(
            "SOURCE_MINIO_ACCESS_KEY=source-reader\n"
            f"SOURCE_MINIO_SECRET_KEY={secrets.token_hex(16)}\n"
        )


def export_runtime(credentials_file: Path) -> None:
    """Xuất env theo service; không đưa tài khoản quản trị hay Bronze writer vào backend."""
    values = dict(dotenv_values(credentials_file))
    source_file = credentials_file.parent / ".env.source-reader.local"
    if source_file.exists():
        values.update(dotenv_values(source_file))
    silver_names = ("SILVER_WRITER_ACCESS_KEY", "SILVER_WRITER_SECRET_KEY")
    required_names = {
        f"BRONZE_{role}_{kind}"
        for roles in (("READER",), ("WRITER", "VERIFIER", "READER"))
        for role in roles
        for kind in ("ACCESS_KEY", "SECRET_KEY")
    } | set(silver_names)
    if any(not values.get(name) for name in required_names):
        raise ValueError("MINIO_RUNTIME_CREDENTIALS_REQUIRED")
    for service, roles in (("backend", ("READER",)), ("worker", ("WRITER", "VERIFIER", "READER"))):
        names = [f"BRONZE_{role}_{kind}" for role in roles for kind in ("ACCESS_KEY", "SECRET_KEY")]
        names += [
            name
            for name in ("SOURCE_MINIO_ACCESS_KEY", "SOURCE_MINIO_SECRET_KEY")
            if values.get(name)
        ]
        if any(not values.get(name) for name in names):
            raise ValueError("BRONZE_ACCOUNT_CREDENTIALS_REQUIRED")
        content = "\n".join(f"{name}={values[name]}" for name in names) + "\n"
        path = credentials_file.parent / f".env.bronze-{service}.local"
        if path.exists():
            if path.read_text(encoding="utf-8") != content:
                raise ValueError("RUNTIME_ENV_EXISTS_WITH_DIFFERENT_CREDENTIALS")
            continue
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(content)
    silver_content = "\n".join(f"{name}={values[name]}" for name in silver_names) + "\n"
    silver_path = credentials_file.parent / ".env.silver-backend.local"
    if silver_path.exists():
        if silver_path.read_text(encoding="utf-8") != silver_content:
            raise ValueError("RUNTIME_ENV_EXISTS_WITH_DIFFERENT_CREDENTIALS")
    else:
        descriptor = os.open(silver_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(silver_content)
    print("Runtime env files prepared; no admin credentials exported.")


def provision(
    admin: MinioAdmin,
    credentials: dict[str, str | None],
    bucket: str,
    silver_bucket: str,
) -> None:
    """Tạo SA với inline policy. Không in response Admin API vì chứa secret key."""
    # Kiểm tra đủ credentials trước khi tạo bất kỳ tài khoản nào trên server.
    for role in ACCOUNTS:
        prefix = "SILVER_WRITER" if role == "silver-writer" else f"BRONZE_{role.upper()}"
        if not credentials.get(f"{prefix}_ACCESS_KEY") or not credentials.get(
            f"{prefix}_SECRET_KEY"
        ):
            raise ValueError("MINIO_ACCOUNT_CREDENTIALS_REQUIRED")
        policy_for(role, silver_bucket if role == "silver-writer" else bucket)
    for role, name in ACCOUNTS.items():
        prefix = "SILVER_WRITER" if role == "silver-writer" else f"BRONZE_{role.upper()}"
        account_bucket = silver_bucket if role == "silver-writer" else bucket
        admin.add_service_account(
            access_key=credentials[f"{prefix}_ACCESS_KEY"],
            secret_key=credentials[f"{prefix}_SECRET_KEY"],
            name=name,
            description=f"UDL-142 {'Silver' if role == 'silver-writer' else 'Bronze'} {role}",
            policy=policy_for(role, account_bucket),
        )
        print(f"Created: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "plan",
            "prepare",
            "apply",
            "update-writer",
            "update-reader",
            "update-silver-writer",
            "prepare-source",
            "apply-source",
            "export-runtime",
        ],
    )
    parser.add_argument("--credentials-file", type=Path, default=Path(".env.bronze-accounts.local"))
    parser.add_argument("--source-bucket")
    args = parser.parse_args()
    if args.action == "plan":
        print(
            json.dumps(
                {
                    role: policy_for(
                        role,
                        settings.minio_bucket_silver
                        if role == "silver-writer"
                        else settings.minio_bucket_bronze,
                    )
                    for role in ACCOUNTS
                },
                indent=2,
            )
        )
        return
    if args.action == "prepare":
        prepare(args.credentials_file)
        print("Created local credentials file (mode 600); no accounts created on MinIO yet.")
        return
    if args.action == "prepare-source":
        prepare_source(args.credentials_file)
        print("Source reader credentials prepared; no account created yet.")
        return
    if args.action == "export-runtime":
        export_runtime(args.credentials_file)
        return
    credentials = dict(dotenv_values(args.credentials_file))
    admin = MinioAdmin(
        endpoint=settings.minio_endpoint,
        credentials=StaticProvider(settings.minio_access_key, settings.minio_secret_key),
        region=settings.minio_region,
        secure=settings.minio_secure,
    )
    try:
        if args.action == "apply-source":
            if not args.source_bucket:
                raise ValueError("SOURCE_BUCKET_REQUIRED")
            key = credentials.get("SOURCE_MINIO_ACCESS_KEY")
            secret = credentials.get("SOURCE_MINIO_SECRET_KEY")
            if not key or not secret:
                raise ValueError("SOURCE_CREDENTIALS_REQUIRED")
            admin.add_service_account(
                access_key=key,
                secret_key=secret,
                name="ingestion-source-reader",
                policy=policy_for("verifier", args.source_bucket),
            )
            print("Created: ingestion-source-reader (read-only source bucket)")
        elif args.action == "update-reader":
            update_reader_policy(
                admin,
                credentials.get("BRONZE_READER_ACCESS_KEY") or "",
                settings.minio_bucket_bronze,
            )
        elif args.action == "update-writer":
            update_writer_policy(
                admin,
                credentials.get("BRONZE_WRITER_ACCESS_KEY") or "",
                settings.minio_bucket_bronze,
            )
        elif args.action == "update-silver-writer":
            update_silver_writer_policy(
                admin,
                credentials.get("SILVER_WRITER_ACCESS_KEY") or "",
                settings.minio_bucket_silver,
            )
        else:
            provision(
                admin,
                credentials,
                settings.minio_bucket_bronze,
                settings.minio_bucket_silver,
            )
    except MinioAdminException as exc:
        # Chỉ xuất HTTP status và mã lỗi định danh, không xuất body hoặc Message.
        try:
            code = json.loads(exc._body).get("Code", "UNKNOWN")
        except (ValueError, AttributeError):
            code = "UNKNOWN"
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]+", code):
            code = "UNKNOWN"
        raise SystemExit(f"Provisioning stopped: HTTP {exc._code}, code={code}.") from None
    except Exception as exc:
        # Ngoại lệ Admin API có thể chứa request/secret. Giữ nguyên tài khoản đã tạo,
        # không tự xóa hoặc xoay secret khi một bước sau thất bại.
        raise SystemExit(
            f"Provisioning stopped ({type(exc).__name__}); check MinIO and account inventory before retrying."
        ) from None


if __name__ == "__main__":
    main()
