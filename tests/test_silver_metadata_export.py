from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.pipeline.services.migrate_silver_metadata import merge_verified


def test_export_accepts_equal_timestamps_and_preserves_every_field():
    rows = {"profile": {"revision": 1, "updated_at": "2026-11-16T07:00:00+07:00"}}
    source = {"revision": 1, "updated_at": datetime(2026, 11, 16, tzinfo=UTC).isoformat()}
    merge_verified(rows, "profile", source)
    assert rows["profile"] == source


def test_export_conflict_never_silently_overwrites_destination():
    rows = {"profile": {"revision": 2}}
    with pytest.raises(ValueError, match="SILVER_METADATA_EXPORT_CONFLICT"):
        merge_verified(rows, "profile", {"revision": 1})
    assert rows["profile"] == {"revision": 2}
