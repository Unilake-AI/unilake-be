from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app.modules.ingestion.contracts import BronzeBatch, MappingBinding
from app.modules.pipeline.mapping_profiles import build_profile, pointer
from app.modules.pipeline.schema_mapping import (
    FieldMapping,
    MappingConfigurationError,
    MappingKey,
    MappingProfile,
    MappingRegistry,
    SchemaMappingService,
)

ROOT = Path(__file__).resolve().parents[1]
KEY = MappingKey("sys", "hr", "position", "source-1", "position-flat", "1", "target-1")


def profile(fields=(FieldMapping("id", ("/id",)),)):
    return MappingProfile(KEY, "CSV", tuple(f.target for f in fields), fields)


def batch(records=({"id": "007"},), paths=("/id",), key=KEY):
    return BronzeBatch(
        "batch-1",
        key.domain_id,
        "CSV",
        records,
        metadata={"input_version": "object-version-1"},
        mapping_binding=MappingBinding(
            key.source_system_id,
            key.entity,
            key.source_schema_version,
            key.mapping_id,
            key.mapping_version,
            key.target_schema_version,
        ),
        record_locators=tuple(f"object-version-1:line:{i + 2}" for i in range(len(records))),
        source_paths=paths,
    )


def service(p=None):
    return SchemaMappingService(MappingRegistry((p or profile(),)))


@pytest.mark.parametrize("domain", ["admissions", "academic", "hr"])
@pytest.mark.parametrize("layout", ["flat", "nested"])
def test_two_layouts_every_entity_against_s1(domain, layout):
    schema = json.loads(
        (ROOT / f"docs/standardization/silver-schemas/{domain}.json").read_text(encoding="utf-8")
    )
    source = json.loads(
        (ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (ROOT / "data/standardization/s1/expected-business-records.json").read_text(
            encoding="utf-8"
        )
    )
    for entity, target in schema["$defs"].items():
        columns = source["entities"][entity]["columns"]
        # Chủ động giữ chuỗi có dạng số; S2 không được ép kiểu giá trị.
        raw = {c["name"]: None if c["nullable"] else f"raw:{c['name']}" for c in columns}
        record = raw if layout == "flat" else {"payload": raw, "extra": "do-not-copy"}
        prefix = "" if layout == "flat" else "/payload"
        paths = tuple(prefix + pointer(c["name"]) for c in columns)
        ordinary = {n for n in target["properties"] if not n.startswith("_") and n != "birth_year"}
        ignored = tuple(prefix + pointer(c["name"]) for c in columns if c["name"] not in ordinary)
        key = MappingKey(
            "SYNTH_" + domain,
            domain,
            entity,
            source["version"],
            f"{entity}-{layout}",
            "1",
            schema["x-version"],
        )
        p = build_profile(
            key=key,
            source_type="CSV",
            source_columns=paths,
            target_fields=tuple(target["properties"]),
            field_paths={n: (prefix + pointer(n),) for n in target["properties"] if n in ordinary},
            ignored_sources=ignored,
            deferred_fields=tuple(n for n in target["properties"] if n not in ordinary),
        )
        b = batch((record,), paths + (() if layout == "flat" else ("/extra",)), key)
        original = copy.deepcopy(b.records)
        result = service(p).map_batch(b)
        assert result.accepted[0].values == {n: raw[n] for n in ordinary}
        assert set(result.accepted[0].values) <= set(expected[entity][0])
        assert not result.rejected_locators
        assert not result.silver_ready
        assert b.records == original
        assert all(n not in result.accepted[0].values for n in p.deferred_fields)


def test_versions_and_sources_never_fallback():
    registry = MappingRegistry((profile(),))
    for name in (
        "source_system_id",
        "entity",
        "source_schema_version",
        "mapping_id",
        "mapping_version",
        "target_schema_version",
        "domain_id",
    ):
        with pytest.raises(MappingConfigurationError, match="MAPPING_NOT_FOUND"):
            registry.get(replace(KEY, **{name: "other"}))
    with pytest.raises(MappingConfigurationError, match="ALREADY_REGISTERED"):
        registry.register(profile())
    other = replace(profile(), key=replace(KEY, mapping_version="2"))
    registry.register(other)
    assert registry.get(KEY) == profile()


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ((FieldMapping("id", ("/id",)), FieldMapping("id", ("/other",))), "DUPLICATE_TARGET"),
        ((FieldMapping("id", ("id",)),), "INVALID_SOURCE_PATH"),
        ((FieldMapping("id", ("/a~2b",)),), "INVALID_SOURCE_PATH"),
        ((FieldMapping("id", ("/id",), "eval"),), "UNSUPPORTED_OPERATION"),
        ((FieldMapping("id", ("/id",), "split", separator=""),), "INVALID_SPLIT_CONFIG"),
    ],
)
def test_invalid_config(fields, code):
    with pytest.raises(MappingConfigurationError, match=code):
        service(profile(fields))


def test_missing_target_and_declared_source():
    with pytest.raises(MappingConfigurationError, match="TARGET_COVERAGE_ERROR"):
        service(replace(profile(), target_fields=("id", "name")))
    with pytest.raises(MappingConfigurationError, match="REQUIRED_SOURCE_FIELD_MISSING"):
        service().map_batch(batch((), ("/wrong",)))
    with pytest.raises(MappingConfigurationError, match="SOURCE_SHAPE_REQUIRED"):
        service().map_batch(batch((), ()))


def test_missing_row_vs_explicit_null_and_locator():
    result = service().map_batch(batch(({"id": None}, {}, {"id": "007"})))
    assert [r.values for r in result.accepted] == [{"id": None}, {"id": "007"}]
    assert result.rejected_locators == ("object-version-1:line:3",)
    assert result.issues[0].code == "REQUIRED_SOURCE_FIELD_MISSING"


def test_alias_conflict_no_raw_values_in_issue():
    p = profile((FieldMapping("id", ("/id", "/alias"), "coalesce"),))
    result = service(p).map_batch(
        batch(({"id": "secret-a", "alias": "secret-b"},), ("/id", "/alias"))
    )
    assert result.rejected_locators
    assert result.issues[0].code == "MAPPING_CONFLICT"
    assert "secret" not in repr(result.issues)
    chosen = service(
        replace(p, fields=(replace(p.fields[0], conflict="first_present"),))
    ).map_batch(batch(({"id": None, "alias": "007"},), ("/id", "/alias")))
    assert chosen.accepted[0].values == {"id": None}
    alias_only = service(p).map_batch(batch(({"alias": "007"},), ("/alias",)))
    assert alias_only.accepted[0].values == {"id": "007"}


def test_pointer_arrays_escapes_concat_split_and_drift():
    p = profile(
        (
            FieldMapping("id", ("/a~1b/~0key/0/id",)),
            FieldMapping("name", ("/first", "/last"), "concat"),
            FieldMapping("part", ("/code",), "split", separator="-", index=1),
        )
    )
    record = {
        "a/b": {"~key": [{"id": "007", "unexpected": "x"}]},
        "first": "An",
        "last": "Bình",
        "code": "A-02",
    }
    result = service(p).map_batch(
        batch((record,), ("/a~1b/~0key/0/id", "/first", "/last", "/code"))
    )
    assert result.accepted[0].values == {"id": "007", "name": "An Bình", "part": "02"}
    assert any(i.field == "/a~1b/~0key/0/unexpected" for i in result.issues)


def test_preview_is_partial_no_publish_or_source_mutation():
    b = batch(({"id": "007"}, {}))
    result = service().preview(b, limit=1)
    assert result.preview
    assert result.inspected_count == 1
    assert result.total_count == 2
    assert result.accepted[0].before == {"/id": "007"}
    assert not result.silver_ready
    assert service().map_batch(b).rejected_locators
    result.accepted[0].values["id"] = "changed"
    assert b.records[0]["id"] == "007"


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"mapping_binding": None}, "MAPPING_BINDING_REQUIRED"),
        ({"record_locators": ()}, "INVALID_RECORD_LOCATORS"),
        ({"source_type": "EXCEL"}, "SOURCE_TYPE_MISMATCH"),
    ],
)
def test_invalid_handoff(change, code):
    with pytest.raises(MappingConfigurationError, match=code):
        service().map_batch(replace(batch(), **change))


def test_version_required_and_no_legacy_publish():
    from app.modules.pipeline.service import PipelineOrchestrator

    with pytest.raises(MappingConfigurationError, match="INPUT_VERSION_REQUIRED"):
        service().map_batch(replace(batch(), metadata={}))

    class NoPublisher:
        def publish(self, domain_id, layer, datasets):
            pytest.fail("S2 must never publish")

    with pytest.raises(ValueError, match="S3/S4"):
        PipelineOrchestrator(NoPublisher()).run(batch())


def test_dropped_fields_do_not_enter_preview():
    p = replace(profile(), ignored_sources=("/full_name",))
    result = service(p).preview(
        batch(({"id": "007", "full_name": "private"},), ("/id", "/full_name"))
    )
    assert "private" not in repr(result)


@pytest.mark.parametrize(
    "case",
    json.loads((ROOT / "data/standardization/s2/examples.json").read_text(encoding="utf-8")),
    ids=lambda c: c["id"],
)
def test_preview_command_with_independent_expected_outputs(case):
    process = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "scripts/standardization/preview_s2.py"),
            "--case",
            case["id"],
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    result = json.loads(process.stdout)
    assert result["accepted"][0]["values"] == case["expected"]
    assert result["preview"] is True
    assert result["silver_ready"] is False
    assert result["input_version"] == "fixture-v1"


def test_operation_failures_and_empty_batch():
    p = profile((FieldMapping("part", ("/code",), "split", separator="-", index=2),))
    result = service(p).map_batch(batch(({"code": "A-1"}, {"code": None}), ("/code",)))
    assert {i.code for i in result.issues} == {
        "SPLIT_INDEX_MISSING",
        "STRUCTURAL_OPERATION_TYPE_ERROR",
    }
    result = service().map_batch(batch((), ("/id",)))
    assert result.inspected_count == 0
    assert result.accepted == ()


def test_cannot_copy_parent_of_ignored_sensitive_field():
    p = profile((FieldMapping("id", ("/payload",)),))
    with pytest.raises(MappingConfigurationError, match="SOURCE_POLICY_CONFLICT"):
        service(replace(p, ignored_sources=("/payload/private",)))
