"""Tầng API FastAPI routers của mô-đun pipeline."""

from __future__ import annotations

from app.modules.pipeline.routers.cleaning_runs import router as cleaning_runs_router
from app.modules.pipeline.routers.data_quality_issues import router as data_quality_issues_router
from app.modules.pipeline.routers.pipeline import router as pipeline_router
from app.modules.pipeline.routers.profiles import router as profiles_router
from app.modules.pipeline.routers.quarantine_records import router as quarantine_records_router
from app.modules.pipeline.routers.snapshots import router as snapshots_router

__all__ = [
    "cleaning_runs_router",
    "data_quality_issues_router",
    "pipeline_router",
    "profiles_router",
    "quarantine_records_router",
    "snapshots_router",
]
