"""Source-profile acceptance matrix; synthetic data, no production policy approval."""

from __future__ import annotations

import csv
import hashlib
import json
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from app.modules.pipeline.cleaning.pipeline import CleaningPipeline
from app.modules.pipeline.services.source_cleaning import _bind_profile, _read_bronze
from app.modules.pipeline.services.source_profiles import SourceProfile
from tests.test_cleaning_domains import _cleaning_task
from tests.test_identity_standardization import CONTRACTS, RECORDS

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = {"admissions": "admission_method", "academic": "course", "hr": "position"}
SCENARIOS = (
    "valid",
    "required_null",
    "nullable_null",
    "unknown_null_token",
    "configured_null_token",
    "invalid_type",
    "enum_unknown",
    "enum_alias",
    "range_min",
    "range_below",
    "range_max",
    "range_above",
    "exact_default",
    "exact_enabled",
    "key_conflict",
    "business_key_conflict",
    "missing_column",
    "wrong_checksum",
    "wrong_version",
    "wrong_row_count",
    "wrong_binding",
    "rule_change",
    "mixed_rows",
    "no_fuzzy_merge",
)


def source_profile(domain, extension):
    entity = DOMAINS[domain]
    schema = json.loads((ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text("utf-8"))
    fields = CONTRACTS[domain]["$defs"][entity]["properties"]
    return SourceProfile(
        data_source_id=21,
        dataset_id=31,
        source_system_id=17,
        source_type="CSV" if extension == "csv" else "EXCEL",
        source_table=f"{entity}.{extension}",
        domain=domain,
        entity=entity,
        version="acceptance-test-v1",
        scope="acceptance-only",
        source_schema_version=schema["version"],
        source_schema=schema,
        contracts=CONTRACTS,
        field_mapping={name: f"src_{name}" for name in fields if not name.startswith("_")},
        dictionaries={name: {} for name, spec in fields.items() if "enum" in spec},
        crosswalk_release="acceptance-only",
        effective_at="2026-11-12T00:00:00Z",
    )


@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize(
    "scenario",
    ["missing_non_key", "invalid_non_key", "normalized_key", "unknown_key", "distinct_keys"],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_partial_keys_across_rejected_stages(record_property, domain, scenario, reverse):
    from app.modules.pipeline.cleaning.partial_safety import partial_key_evidence

    entity = DOMAINS[domain]
    task, _ = _cleaning_task(domain, entity)
    valid = dict(task.batch.records[0])
    invalid = dict(valid)
    primary = next(
        rule.fields[0] for rule in task.duplicate_profile.key_rules if rule.kind == "PRIMARY_KEY"
    )
    name = next(field for field in valid if field.endswith("_name"))
    invalid[name] = None
    if scenario == "invalid_non_key":
        invalid[name] = "X" * 4096
    elif scenario == "normalized_key":
        invalid[primary] = "000" + str(valid[primary])
    elif scenario == "unknown_key":
        invalid[primary] = None
    elif scenario == "distinct_keys":
        kinds = {rule.field: rule.value_kind for rule in task.format_profile.rules}
        for field in {field for rule in task.duplicate_profile.key_rules for field in rule.fields}:
            invalid[field] = (
                str(int(str(valid[field])) + 1)
                if kinds[field] == "integer"
                else str(valid[field]) + "X"
            )
        if domain == "academic":
            invalid["course_code"] = "IT102"  # Preserve the declared course code pattern.
    pairs = [("record:1", valid), ("record:2", invalid)]
    if reverse:
        pairs.reverse()
    task = replace(
        task,
        batch=replace(
            task.batch,
            records=tuple(row for _, row in pairs),
            record_locators=tuple(locator for locator, _ in pairs),
        ),
    )
    cleaned = CleaningPipeline().clean(task)
    assert cleaned.batch.record_locators == ("record:1",)
    proof = partial_key_evidence(task, set(cleaned.quarantined_locators))
    expected = "PASSED" if scenario == "distinct_keys" else "BLOCKED"
    record_property("case_id", f"{domain}-partial-{scenario}-reverse-{reverse}")
    record_property("domain", domain)
    record_property("entity", entity)
    record_property("approval", "NEEDS_DOMAIN_OWNER_APPROVAL; TEST_ONLY")
    record_property("input", json.dumps([row for _, row in pairs], ensure_ascii=False))
    record_property("expected", expected)
    record_property("actual", json.dumps(proof))
    assert proof["status"] == expected
    assert all(
        block["rule_version"] == task.duplicate_profile.version for block in proof["blockers"]
    )
    if scenario == "unknown_key":
        assert any(
            block["code"] == "PARTIAL_KEY_EVIDENCE_INCOMPLETE" for block in proof["blockers"]
        )
    elif scenario != "distinct_keys":
        assert any(
            block["code"] == "PARTIAL_KEY_CONFLICT_WITH_QUARANTINE" for block in proof["blockers"]
        )
    assert task.batch.records == tuple(row for _, row in pairs)


@pytest.mark.parametrize("domain", DOMAINS)
@pytest.mark.parametrize("extension", ["csv", "xlsx"])
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_source_profile_matrix(tmp_path, monkeypatch, record_property, domain, extension, scenario):
    profile = source_profile(domain, extension)
    columns = [
        c
        for c in profile.source_schema["entities"][profile.entity]["columns"]
        if c["name"] in profile.field_mapping
    ]
    pk = profile.source_schema["entities"][profile.entity]["primary_key"][0]
    name = next(c["name"] for c in columns if c["name"].endswith("_name"))
    unique = next(c["name"] for c in columns if c.get("unique"))
    nullable = next((c["name"] for c in columns if c["nullable"]), None)
    enum = next((c for c in columns if "enum" in c), None)
    bounded = next((c for c in columns if "min" in c), None)
    row = {c["name"]: RECORDS[profile.entity][0].get(c["name"]) for c in columns}
    row = {
        k: ("true" if v is True else "false" if v is False else str(v)) if v is not None else None
        for k, v in row.items()
    }
    rows = [row]
    expected = {"passed": 1, "quarantined": 0, "collapsed": 0, "code": None, "batch_error": None}
    record_property("case_id", f"{domain}-{extension}-{scenario}")
    record_property("domain", domain)
    record_property("entity", profile.entity)
    record_property("approval", "NEEDS_DOMAIN_OWNER_APPROVAL; TEST_ONLY")
    if scenario in {"nullable_null", "configured_null_token"} and nullable is None:
        record_property("expected", "NOT_APPLICABLE: selected entity has no nullable field")
        pytest.skip("Selected source profile has no nullable field; not a whole-domain exclusion")
    if scenario.startswith("enum_") and enum is None:
        record_property("expected", "NOT_APPLICABLE: selected entity has no enum")
        pytest.skip(
            "Selected source profile has no enum; covered separately by domain field matrix"
        )
    if scenario.startswith("range_") and (
        bounded is None or (scenario in {"range_max", "range_above"} and "max" not in bounded)
    ):
        record_property("expected", "NOT_APPLICABLE: numeric bound is not declared")
        pytest.skip("No such numeric bound in source profile; do not invent a business rule")
    if scenario == "required_null":
        row[name] = None
        expected.update(passed=0, quarantined=1, code="REQUIRED_VALUE_MISSING")
    elif scenario == "nullable_null":
        row[nullable] = None
    elif scenario == "unknown_null_token":
        row[name] = "N/A"
    elif scenario == "configured_null_token":
        row[nullable] = "N/A"
        profile = SourceProfile.model_validate(
            {**profile.model_dump(), "null_tokens": {nullable: ["", "N/A"]}}
        )
    elif scenario == "invalid_type":
        row[pk] = "not-an-integer"
        expected.update(passed=0, quarantined=1, code="INTEGER_VALUE_INVALID")
    elif scenario == "enum_unknown":
        row[enum["name"]] = "UNDECLARED"
        expected.update(passed=0, quarantined=1, code="ENUM_VALUE_INVALID")
    elif scenario == "enum_alias":
        row[enum["name"]] = "fixture-alias"
        profile = SourceProfile.model_validate(
            {
                **profile.model_dump(),
                "format_options": {enum["name"]: {"aliases": [["fixture-alias", enum["enum"][0]]]}},
            }
        )
    elif scenario.startswith("range_"):
        value = bounded["max"] if scenario in {"range_max", "range_above"} else bounded["min"]
        row[bounded["name"]] = str(
            value + (1 if scenario == "range_above" else -1 if scenario == "range_below" else 0)
        )
        if scenario in {"range_below", "range_above"}:
            expected.update(
                passed=0,
                quarantined=1,
                code="VALUE_BELOW_MINIMUM" if scenario == "range_below" else "VALUE_ABOVE_MAXIMUM",
            )
    elif scenario in {
        "exact_default",
        "exact_enabled",
        "key_conflict",
        "business_key_conflict",
        "no_fuzzy_merge",
    }:
        rows.append(dict(row))
        if scenario in {"exact_enabled", "key_conflict"}:
            profile = SourceProfile.model_validate(
                {**profile.model_dump(), "collapse_exact_duplicates": True}
            )
        if scenario == "exact_enabled":
            expected.update(collapsed=1, code="EXACT_DUPLICATE_COLLAPSED")
        elif scenario == "no_fuzzy_merge":
            rows[1][pk] = str(int(row[pk]) + 1)
            rows[1][unique] = {"admissions": "OTHER", "academic": "ZZ999", "hr": "ZZ"}[domain]
            expected.update(passed=2)
        else:
            if scenario == "key_conflict":
                rows[1][name] = "different payload"
            if scenario == "business_key_conflict":
                rows[1][pk] = str(int(row[pk]) + 1)
            expected.update(
                passed=0,
                quarantined=2,
                code="DUPLICATE_PRIMARY_KEY_CONFLICT"
                if scenario == "key_conflict"
                else "DUPLICATE_KEY_COLLISION",
            )
    elif scenario == "mixed_rows":
        rows.append({**row, pk: str(int(row[pk]) + 1), name: None})
        expected.update(quarantined=1, code="REQUIRED_VALUE_MISSING")
    elif scenario in {
        "missing_column",
        "wrong_checksum",
        "wrong_version",
        "wrong_row_count",
        "wrong_binding",
    }:
        expected["batch_error"] = {
            "missing_column": "SOURCE_LAYOUT_MISMATCH",
            "wrong_checksum": "BRONZE_JOB_CAPTURE_MISMATCH",
            "wrong_version": "BRONZE_JOB_OBJECT_EVIDENCE_MISMATCH",
            "wrong_row_count": "BRONZE_JOB_CAPTURE_MISMATCH",
            "wrong_binding": "SILVER_PROFILE_SOURCE_MISMATCH",
        }[scenario]
    record_property("profile_fingerprint", profile.fingerprint)
    record_property("rule_version", profile.rule_version)
    record_property("expected", json.dumps(expected))
    record_property("input", json.dumps(rows))
    headers = [
        profile.field_mapping[c["name"]]
        for c in columns
        if scenario != "missing_column" or c["name"] != name
    ]
    values = [[r[c.removeprefix("src_")] for c in headers] for r in rows]
    path = tmp_path / profile.source_table
    if extension == "csv":
        with path.open("w", encoding="utf-8", newline="") as stream:
            csv.writer(stream).writerows([headers, *values])
        options = {"encoding": "utf-8", "delimiter": ",", "has_header": True}
    else:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Data"
        for values_row in [headers, *values]:
            sheet.append(values_row)
        workbook.save(path)
        workbook.close()
        options = {"sheet_name": "Data", "header_row": 1, "skip_rows": 0}
    original = path.read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    context = {
        **{
            k: getattr(profile, k)
            for k in (
                "data_source_id",
                "dataset_id",
                "source_system_id",
                "source_type",
                "source_table",
                "domain",
            )
        },
        "ingestion_job_id": 42,
        "rows_read": len(rows),
        "object_key": f"{domain}/{profile.source_table}",
        "output": {
            "uri": f"s3://bronze/{domain}/{profile.source_table}",
            "version_id": "pinned-v1",
            "sha256": sha,
            "size_bytes": len(original),
            "source_type": profile.source_type,
            "parser_options": options,
        },
    }

    @contextmanager
    def resolve(request):
        assert request.object_version_id == "pinned-v1"
        yield replace(request, location=str(path))

    monkeypatch.setattr("app.modules.ingestion.readers.file_snapshot.resolve_file_request", resolve)
    client = SimpleNamespace(
        stat_object=lambda *a, **kw: SimpleNamespace(
            version_id="different" if scenario == "wrong_version" else "pinned-v1",
            size=len(original),
        )
    )
    monkeypatch.setattr(
        "app.modules.pipeline.services.source_cleaning.source_reader_client", lambda *_: client
    )
    if scenario == "wrong_checksum":
        context["output"]["sha256"] = "0" * 64
    if scenario == "wrong_row_count":
        context["rows_read"] += 1
    if scenario == "wrong_binding":
        context["dataset_id"] += 1

    def clean_bound_source():
        _bind_profile(context, profile)
        return CleaningPipeline().clean(_read_bronze(context, profile))

    try:
        if expected["batch_error"]:
            with pytest.raises(ValueError, match=f"^{expected['batch_error']}$") as error:
                clean_bound_source()
            record_property("actual", str(error.value))
            return
        _bind_profile(context, profile)
        task = _read_bronze(context, profile)
        result = CleaningPipeline().clean(task)
        record_property("actual", json.dumps(asdict(result.report)))
        record_property("output_rows", json.dumps(result.batch.records))
        record_property("output_locators", json.dumps(result.batch.record_locators))
        record_property("provenance", json.dumps(asdict(result.batch.provenance)))
        assert (
            result.report.records_passed,
            result.report.records_quarantined,
            result.report.records_collapsed,
        ) == (expected["passed"], expected["quarantined"], expected["collapsed"])
        assert result.report.records_read == sum(
            (
                result.report.records_passed,
                result.report.records_quarantined,
                result.report.records_collapsed,
            )
        )
        assert result.batch.provenance == task.batch.provenance
        assert result.report.input_version == task.batch.provenance.input_version
        assert result.batch.provenance.content_sha256 == sha
        assert result.batch.provenance.object_version_id == "pinned-v1"
        assert all(
            i.rule_version == profile.rule_version and i.locator in task.batch.record_locators
            for i in result.report.issues
        )
        if expected["code"]:
            assert expected["code"] in {i.code for i in result.report.issues}
        if scenario in {"nullable_null", "configured_null_token"}:
            assert result.batch.records[0][profile.field_mapping[nullable]] is None
        if scenario == "unknown_null_token":
            assert result.batch.records[0][profile.field_mapping[name]] == "N/A"
        if scenario == "enum_alias":
            assert result.batch.records[0][profile.field_mapping[enum["name"]]] == enum["enum"][0]
        if scenario in {"key_conflict", "business_key_conflict"}:
            reversed_task = replace(
                task,
                batch=replace(
                    task.batch,
                    records=tuple(reversed(task.batch.records)),
                    record_locators=tuple(reversed(task.batch.record_locators)),
                ),
            )
            reversed_result = CleaningPipeline().clean(reversed_task)
            assert not reversed_result.batch.records
            assert set(reversed_result.quarantined_locators) == set(result.quarantined_locators)
        replay = CleaningPipeline().clean(task)
        assert replay == result
        if scenario == "rule_change":
            changed = SourceProfile.model_validate(
                {**profile.model_dump(), "version": "acceptance-test-v2"}
            )
            rerun = CleaningPipeline().clean(_read_bronze(context, changed))
            assert changed.fingerprint != profile.fingerprint
            assert rerun.batch.processing_steps != result.batch.processing_steps
            assert rerun.batch.provenance.input_version == result.batch.provenance.input_version
    finally:
        assert path.read_bytes() == original, "Bronze fixture bytes must remain unchanged"


FIELD_CASES = (
    ("admissions", "program", "duration_years", None, None),
    ("admissions", "program", "duration_years", "0", "VALUE_BELOW_MINIMUM"),
    ("admissions", "program", "duration_years", "7", "VALUE_ABOVE_MAXIMUM"),
    ("admissions", "program", "degree_level", "Unknown", "ENUM_VALUE_INVALID"),
    ("admissions", "admission_year", "start_date", "2026-02-30", "DATE_FORMAT_INVALID"),
    ("academic", "curriculum", "effective_from", None, None),
    ("academic", "semester", "start_date", "2026-02-30", "DATE_FORMAT_INVALID"),
    ("academic", "course", "credit_hours", "1.5", "INTEGER_VALUE_INVALID"),
    ("hr", "assignment", "start_date", "2026-02-30", "DATE_FORMAT_INVALID"),
    ("hr", "employee_qualification", "year_obtained", "1959", "VALUE_BELOW_MINIMUM"),
    ("hr", "employee_qualification", "year_obtained", "1960", None),
    ("hr", "employee_qualification", "year_obtained", "2100", None),
    ("hr", "employee_qualification", "year_obtained", "2101", "VALUE_ABOVE_MAXIMUM"),
)


@pytest.mark.parametrize(("domain", "entity", "field", "value", "code"), FIELD_CASES)
def test_domain_field_matrix(record_property, domain, entity, field, value, code):
    """Supplement real-file profiles with domain fields absent from the three baseline entities."""
    task, _ = _cleaning_task(domain, entity)
    task = replace(
        task, batch=replace(task.batch, records=({**task.batch.records[0], field: value},))
    )
    record_property("domain", domain)
    record_property("entity", entity)
    record_property("approval", "NEEDS_DOMAIN_OWNER_APPROVAL; COMPONENT_FIXTURE_ONLY")
    record_property("input", json.dumps({field: value}))
    record_property("expected", code or "PASS; preserve nullable null; no imputation")
    record_property("rule_version", task.format_profile.version)
    result = CleaningPipeline().clean(task)
    record_property("actual", json.dumps(asdict(result.report)))
    if code:
        assert result.report.records_quarantined == 1
        assert code in {issue.code for issue in result.report.issues}
        assert not result.batch.records
    else:
        assert result.report.records_passed == 1
        assert result.batch.records[0][field] == value
    assert result.batch.provenance == task.batch.provenance


@pytest.mark.parametrize(
    ("domain", "entity"),
    [
        ("admissions", "admission"),
        ("academic", "curriculum_course"),
        ("hr", "employee_qualification"),
    ],
)
def test_composite_key_conflict(record_property, domain, entity):
    task, _ = _cleaning_task(domain, entity)
    schema = json.loads((ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text("utf-8"))[
        "entities"
    ][entity]
    first = dict(task.batch.records[0])
    second = dict(first)
    if len(schema["primary_key"]) == 1:
        pk = schema["primary_key"][0]
        second[pk] = str(int(first[pk]) + 1)
    else:
        second["institution"] = "different institution"
    record_property("domain", domain)
    record_property("entity", entity)
    record_property("approval", "NEEDS_DOMAIN_OWNER_APPROVAL; COMPONENT_FIXTURE_ONLY")
    record_property("input", json.dumps([first, second]))
    record_property("expected", "Quarantine both composite-key collisions, no winner")
    task = replace(
        task,
        batch=replace(
            task.batch, records=(first, second), record_locators=("record:1", "record:2")
        ),
    )
    result = CleaningPipeline().clean(task)
    record_property("actual", json.dumps(asdict(result.report)))
    assert not result.batch.records
    assert set(result.quarantined_locators) == {"record:1", "record:2"}
    assert result.report.duplicate_issues
