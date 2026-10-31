from __future__ import annotations

import sys
from datetime import date
from unittest.mock import Mock

import pytest

from app.infra.minio.bronze import local_path, main, object_key, partition, setup_minio


def test_layout_preserves_reader_filename():
    assert object_key("admissions", date(2026, 1, 2), "batch1", "applicant.csv") == (
        "admissions/2026/01/02/batch1/applicant.csv"
    )


@pytest.mark.parametrize("source", ["../hr", "/hr", "HR", "hr/x", "", "hr\\x"])
def test_invalid_source(source):
    with pytest.raises(ValueError, match=r"Source|safe single path|escapes Bronze"):
        partition(source, date.today())


@pytest.mark.parametrize("value", ["..", "../file.csv", "C:\\x", "/tmp/x", "a/b"])
def test_invalid_filename_or_batch(value):
    with pytest.raises(ValueError, match=r"Source|safe single path|escapes Bronze"):
        object_key("hr", date.today(), value, "file.csv")
    with pytest.raises(ValueError, match=r"Source|safe single path|escapes Bronze"):
        object_key("hr", date.today(), "batch", value)


def test_local_escape(tmp_path):
    with pytest.raises(ValueError, match=r"Source|safe single path|escapes Bronze"):
        local_path(tmp_path, "../outside")


@pytest.mark.parametrize("exists", [True, False])
def test_setup_preserves_existing_bucket_and_enables_versioning(exists):
    storage = Mock()
    storage.bucket_exists.return_value = exists
    setup_minio(storage, "bronze")
    assert storage.make_bucket.call_count == (0 if exists else 1)
    assert storage.set_bucket_versioning.call_args.args[1].status == "Enabled"


def test_local_cli_roundtrip_and_no_overwrite(tmp_path, monkeypatch):
    root = tmp_path / "bronze"
    monkeypatch.setattr(
        sys,
        "argv",
        ["bronze", "init", "--backend", "local", "--root", str(root), "--date", "2026-10-15"],
    )
    main()
    main()
    assert (root / "hr/2026/10/15").is_dir()
    source = tmp_path / "applicant.csv"
    source.write_bytes(b"id,name\n1,example\n")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bronze",
            "upload",
            "--backend",
            "local",
            "--root",
            str(root),
            "--source",
            "admissions",
            "--date",
            "2026-10-15",
            "--batch-id",
            "batch1",
            "--file",
            str(source),
        ],
    )
    main()
    assert (root / "admissions/2026/10/15/batch1/applicant.csv").read_bytes() == source.read_bytes()
    with pytest.raises(FileExistsError):
        main()
