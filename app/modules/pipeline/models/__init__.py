"""Các mô hình SQLAlchemy và định nghĩa bảng của mô-đun pipeline."""

from __future__ import annotations

from app.modules.pipeline.models.core import (
    CLEANING_RUNS,
    DATA_QUALITY_ISSUE_EVENTS,
    DATA_QUALITY_ISSUE_RECORDS,
    DATA_QUALITY_ISSUES,
    DATASET_CATALOG,
    PIPELINE_LINEAGE,
    QUARANTINE_RECORDS,
    Base,
    DataProcessingRun,
    TransformationStep,
)
from app.modules.pipeline.models.identity import (
    CanonicalIdentity,
    CrosswalkRelease,
    IdentityCrosswalk,
)
from app.modules.pipeline.models.profiles import (
    SilverActivation,
    SilverDomainOwner,
    SilverProfileApproval,
    SilverProfileRevision,
    SilverSourceProfile,
)
from app.modules.pipeline.models.silver import SilverHead, SilverRun, SilverSnapshot

__all__ = [
    "CLEANING_RUNS",
    "DATASET_CATALOG",
    "DATA_QUALITY_ISSUES",
    "DATA_QUALITY_ISSUE_EVENTS",
    "DATA_QUALITY_ISSUE_RECORDS",
    "PIPELINE_LINEAGE",
    "QUARANTINE_RECORDS",
    "Base",
    "CanonicalIdentity",
    "CrosswalkRelease",
    "DataProcessingRun",
    "IdentityCrosswalk",
    "SilverActivation",
    "SilverDomainOwner",
    "SilverHead",
    "SilverProfileApproval",
    "SilverProfileRevision",
    "SilverRun",
    "SilverSnapshot",
    "SilverSourceProfile",
    "TransformationStep",
]
