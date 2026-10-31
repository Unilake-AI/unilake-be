"""Các tuyến API người dùng."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])
