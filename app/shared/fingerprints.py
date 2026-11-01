"""Dấu vân tay JSON xác định dùng chung giữa các module."""

from __future__ import annotations

import hashlib
import json


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()
