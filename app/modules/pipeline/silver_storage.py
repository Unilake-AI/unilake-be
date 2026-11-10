"""Lưu các tệp Silver và dùng mã kiểm tra nội dung để phát hiện dữ liệu bị thay đổi."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from minio import Minio


class SilverError(ValueError):
    pass


@dataclass(frozen=True)
class ObjectRef:
    key: str
    sha256: str
    size: int
    version_id: str | None = None


def checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify(ref: ObjectRef, data: bytes) -> bytes:
    if checksum(data) != ref.sha256 or len(data) != ref.size:
        raise SilverError("OBJECT_CHECKSUM_MISMATCH")
    return data


def valid_key(key: str) -> None:
    if (
        not key
        or "\\" in key
        or ":" in key
        or PurePosixPath(key).is_absolute()
        or any(p in {"", ".", ".."} for p in key.split("/"))
    ):
        raise SilverError("INVALID_OBJECT_KEY")


class SilverObjectStore(Protocol):
    def put(self, key: str, data: bytes) -> ObjectRef: ...
    def get(self, ref: ObjectRef) -> bytes: ...


class LocalSilverStore:
    """Lưu tệp chạy thử trên máy. Tệp tồn tại chưa có nghĩa là dữ liệu đã được công bố."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        # Cho phép dùng đường dẫn dài trên Windows khi tên tệp có cả mã lần chạy và mã kiểm tra nội dung.
        if os.name == "nt" and not str(self.root).startswith("\\\\?\\"):
            name = str(self.root)
            self.root = Path(
                "\\\\?\\UNC\\" + name[2:] if name.startswith("\\\\") else "\\\\?\\" + name
            )

    def _path(self, key: str) -> Path:
        valid_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise SilverError("INVALID_OBJECT_KEY")
        return path

    def put(self, key: str, data: bytes) -> ObjectRef:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        ref = ObjectRef(key, checksum(data), len(data))
        try:
            with path.open("xb") as stream:
                stream.write(data)
        except FileExistsError:
            verify(ref, path.read_bytes())
        return ref

    def get(self, ref: ObjectRef) -> bytes:
        return verify(ref, self._path(ref.key).read_bytes())


class MinioSilverStore:
    """Lưu tệp trên MinIO theo nội dung; kho lưu trữ vẫn cần được phân quyền và quản lý phiên bản."""

    def __init__(self, client: Minio, bucket: str) -> None:
        self.client, self.bucket = client, bucket

    def put(self, key: str, data: bytes) -> ObjectRef:
        valid_key(key)
        digest = checksum(data)
        # Tên tệp phải chứa đầy đủ mã kiểm tra nội dung của tệp đó.
        if not PurePosixPath(key).stem.endswith("-" + digest):
            raise SilverError("CONTENT_ADDRESSED_KEY_REQUIRED")
        result = self.client.put_object(
            self.bucket, key, BytesIO(data), len(data), content_type="application/octet-stream"
        )
        version = (
            result.version_id
            if isinstance(result.version_id, str) and result.version_id != "null"
            else None
        )
        return ObjectRef(key, digest, len(data), version)

    def get(self, ref: ObjectRef) -> bytes:
        valid_key(ref.key)
        response = self.client.get_object(self.bucket, ref.key, version_id=ref.version_id)
        try:
            return verify(ref, response.read())
        finally:
            response.close()
            response.release_conn()
