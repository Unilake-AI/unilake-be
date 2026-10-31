"""Các tuyến API bảng điều khiển."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
