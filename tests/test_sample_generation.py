"""The checked-in CSV fixtures must be reproducible from the public generator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.dataset_utils import DATASETS, REPO_ROOT, SAMPLES_DIR, entities


def test_generated_samples_match_checked_in_schema_fixtures(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "data/synthetic/generate_samples.py"), str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    files = {spec["file"] for dataset in DATASETS for spec in entities(dataset).values()}
    assert {str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*.csv")} == files
    for relative in sorted(files):
        assert (tmp_path / relative).read_bytes() == (SAMPLES_DIR / relative).read_bytes(), relative
    assert not (SAMPLES_DIR / "admissions/enrollment.csv").exists()
