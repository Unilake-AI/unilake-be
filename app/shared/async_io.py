"""Chờ việc đọc hoặc lưu file kết thúc trước khi dọn dữ liệu tạm."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


async def run_io(function: Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
    """Chờ thao tác đọc hoặc lưu kết thúc trước khi dọn file, kể cả khi yêu cầu bị hủy."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise
