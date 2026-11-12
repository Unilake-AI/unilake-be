"""Run isolated cleaning acceptance/regression tests and export reproducible evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SUITE = (
    "test_cleaning_acceptance",
    "test_missing_values",
    "test_format_validation",
    "test_duplicate_records",
    "test_cleaning_pipeline",
    "test_cleaning_domains",
    "test_source_cleaning",
    "test_quarantine_records",
    "test_schema_mapping",
    "test_type_standardization",
    "test_identity_standardization",
    "test_silver_publication",
    "test_file_handoff",
    "test_reader_standardization_integration",
    "test_lineage_contract",
    "test_silver_profile_governance",
    "test_silver_management_api",
    "test_module_boundaries",
    "test_cleaning_runs_api",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "docs/testing/cleaning-acceptance/latest"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    xml = output / "junit.xml"
    # A failed/aborted invocation must never reuse an earlier successful XML report.
    if xml.exists():
        xml.unlink()
    command = [
        sys.executable,
        "-X",
        "utf8",
        "-m",
        "pytest",
        *[f"tests/{name}.py" for name in SUITE],
        "-o",
        "addopts=",
        "-o",
        "junit_family=legacy",
        "-v",
        "--tb=short",
        f"--junitxml={xml}",
    ]
    with (output / "pytest.log").open("w", encoding="utf-8") as log:
        run = subprocess.run(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUTF8": "1"},
            check=False,
        )
    cases = []
    if xml.exists():
        for case in ET.parse(xml).iter("testcase"):
            properties = {
                p.attrib["name"]: p.attrib.get("value", "")
                for p in case.findall("properties/property")
            }
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            status = (
                "FAIL"
                if failure is not None or error is not None
                else "SKIP"
                if skipped is not None
                else "PASS"
            )
            reason = failure if failure is not None else error if error is not None else skipped
            cases.append(
                {
                    "test": f"{case.get('classname')}::{case.get('name')}",
                    **properties,
                    "result": status,
                    "details": "" if reason is None else reason.get("message", ""),
                }
            )
    files = sorted(
        {
            p
            for folder in (
                "app",
                "tests",
                "scripts",
                "config",
                "infra/postgres",
                "docs/data-schema",
                "docs/standardization/silver-schemas",
            )
            for p in (ROOT / folder).rglob("*")
            if p.is_file() and p.suffix in {".py", ".json", ".sql"}
        }
    )
    metadata = {
        "executed_at": datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": {
            name: version(name)
            for name in (
                "pytest",
                "fastapi",
                "starlette",
                "anyio",
                "sqlalchemy",
                "duckdb",
                "pyarrow",
                "openpyxl",
            )
        },
        "command": command,
        "exit_code": run.returncode,
        "counts": {
            status: sum(c["result"] == status for c in cases) for status in ("PASS", "FAIL", "SKIP")
        },
        "matrix_counts": dict(
            Counter(c["result"] for c in cases if "test_cleaning_acceptance" in c["test"])
        ),
        "scope": "isolated local fixtures; no live PostgreSQL/MinIO certification",
        "approval": "All fixture rules NEEDS_DOMAIN_OWNER_APPROVAL; test PASS is not approval",
        "source_sha256": {
            str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in files
        },
    }
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    metadata["base_commit"] = git.stdout.strip()
    (output / "results.json").write_text(
        json.dumps({"run": metadata, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Cleaning acceptance results",
        "",
        f"Executed: {metadata['executed_at']}",
        f"Counts: {metadata['counts']}; pytest exit: {run.returncode}",
        "",
        "Synthetic fixtures only. All rules: **NEEDS DOMAIN OWNER APPROVAL**.",
        "Input, expected/actual report, fingerprint, rule version and source hashes: `results.json`.",
        "Regression assertions are in their named tests; the table below is the new acceptance matrix.",
        "",
        "| Case | Domain/entity | Input (synthetic) | Expected | Result | Details |",
        "|---|---|---|---|---|---|",
    ]
    for case in cases:
        if "test_cleaning_acceptance" not in case["test"]:
            continue
        cells = [
            case.get("case_id", case["test"].split("::")[-1]),
            f"{case.get('domain')}/{case.get('entity')}",
            case.get("input", "Not applicable; see SKIP reason"),
            case.get("expected", ""),
            case["result"],
            case["details"],
        ]
        lines.append(
            "| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |"
        )
    (output / "matrix.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"counts": metadata["counts"], "exit_code": run.returncode, "output": str(output)}
        )
    )
    return run.returncode


if __name__ == "__main__":
    raise SystemExit(main())
