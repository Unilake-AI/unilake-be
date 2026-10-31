"""Các tuyến API lịch sử truy vấn."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/query-history", tags=["query-history"])
