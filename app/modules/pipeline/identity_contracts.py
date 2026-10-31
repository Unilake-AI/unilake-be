"""Bằng chứng đối chiếu định danh S4 bất biến. Không ép khóa nguồn sang kiểu số."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime


class IdentityConfigurationError(ValueError):
    pass


def source_key(values: tuple[object, ...]) -> tuple[str, ...]:
    if not values or any(
        isinstance(v, bool) or not isinstance(v, str | int) or not str(v).strip() for v in values
    ):
        raise IdentityConfigurationError("SOURCE_ID_INVALID")
    # Số nguyên 7 và chuỗi '7' tương đương, nhưng '007' vẫn được phân biệt.
    return tuple(str(v) for v in values)


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IdentityConfigurationError("EFFECTIVE_TIME_REQUIRES_OFFSET")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class CrosswalkEntry:
    namespace: str
    entity: str
    source: tuple[str, ...]
    canonical_id: int
    evidence: str
    valid_from: datetime
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if any(
            not isinstance(v, str) or not v.strip()
            for v in (self.namespace, self.entity, self.evidence)
        ):
            raise IdentityConfigurationError("CROSSWALK_EVIDENCE_REQUIRED")
        if type(self.source) is not tuple or self.source != source_key(self.source):
            raise IdentityConfigurationError("INVALID_CROSSWALK_KEY")
        if type(self.canonical_id) is not int or not 0 < self.canonical_id <= 2**63 - 1:
            raise IdentityConfigurationError("CANONICAL_ID_INVALID")
        utc(self.valid_from)
        if self.valid_to is not None and utc(self.valid_to) <= utc(self.valid_from):
            raise IdentityConfigurationError("INVALID_EFFECTIVE_INTERVAL")


@dataclass(frozen=True)
class CrosswalkSnapshot:
    version: str
    entries: tuple[CrosswalkEntry, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.version, str)
            or not self.version.strip()
            or type(self.entries) is not tuple
        ):
            raise IdentityConfigurationError("CROSSWALK_VERSION_REQUIRED")
        seen = set()
        for entry in self.entries:
            if not isinstance(entry, CrosswalkEntry):
                raise IdentityConfigurationError("INVALID_CROSSWALK_ENTRY")
            key = (entry.namespace, entry.entity, entry.source)
            if key in seen:
                raise IdentityConfigurationError("AMBIGUOUS_IDENTITY")
            seen.add(key)
        employees = {e.canonical_id for e in self.entries if e.entity == "employee"}
        if any(e.entity == "lecturer" and e.canonical_id not in employees for e in self.entries):
            raise IdentityConfigurationError("SHARED_IDENTITY_MISMATCH")
        owners: dict[int, str] = {}
        for entry in self.entries:
            owner = "employee" if entry.entity == "lecturer" else entry.entity
            if owners.setdefault(entry.canonical_id, owner) != owner:
                raise IdentityConfigurationError("CANONICAL_ENTITY_MISMATCH")

    def payload(self) -> list[dict[str, object]]:
        return [
            {
                **asdict(e),
                "valid_from": utc(e.valid_from).isoformat(),
                "valid_to": utc(e.valid_to).isoformat() if e.valid_to else None,
            }
            for e in sorted(self.entries, key=lambda e: (e.namespace, e.entity, e.source))
        ]

    @property
    def fingerprint(self) -> str:
        return digest({"version": self.version, "entries": self.payload()})
