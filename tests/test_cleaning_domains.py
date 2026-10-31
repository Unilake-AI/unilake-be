from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import create_engine, event

from app.infra.db.base import Base
from app.modules.ingestion.contracts import BronzeBatch, FileProvenance, MappingBinding
from app.modules.ingestion.readers.file_snapshot import fingerprint
from app.modules.pipeline.cleaning_pipeline import (
    CleaningNormalizationTask,
    CleaningPipeline,
    CleaningSilverPipeline,
    CleaningTask,
)
from app.modules.pipeline.duplicate_records import build_duplicate_profile
from app.modules.pipeline.format_validation import (
    SourceFormatOptions,
    build_format_profile,
)
from app.modules.pipeline.mapping_profiles import build_profile
from app.modules.pipeline.missing_values import MissingValueProfile, MissingValueRule
from app.modules.pipeline.schema_mapping import MappingRegistry, SchemaMappingService
from app.modules.pipeline.silver_pipeline import SilverPipeline
from app.modules.pipeline.silver_publication import SilverPublicationService
from app.modules.pipeline.silver_repository import SILVER_TABLES, SilverRepository
from app.modules.pipeline.silver_storage import LocalSilverStore
from tests.test_identity_standardization import CONTRACTS, RECORDS
from tests.test_identity_standardization import setup as identity_setup

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.modules.pipeline.identity_standardization import IdentityStandardizationService

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ("admissions", "admission_method"),
    ("academic", "course"),
    ("hr", "position"),
)


@pytest.fixture
def publisher(tmp_path) -> Iterator[SilverPublicationService]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'silver.db'}",
        execution_options={"schema_translate_map": {"app": None}},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin_transaction(connection):
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(engine, tables=SILVER_TABLES)
    service = SilverPublicationService(
        repository=SilverRepository(engine),
        storage=LocalSilverStore(tmp_path / "objects"),
        contracts=CONTRACTS,
        scope="cleaning-integration",
        mode="rehearsal",
    )
    yield service
    engine.dispose()


def _cleaning_task(
    domain: str, entity: str
) -> tuple[
    CleaningTask,
    tuple[SchemaMappingService, IdentityStandardizationService],
]:
    schema = json.loads(
        (ROOT / f"docs/data-schema/{domain}_v1.schema.json").read_text(encoding="utf-8")
    )
    entity_schema = schema["entities"][entity]
    columns = tuple(column["name"] for column in entity_schema["columns"])
    source_row = dict(RECORDS[entity][0])
    for column in entity_schema["columns"]:
        name = column["name"]
        if name not in source_row:
            assert column["nullable"], f"{domain}.{entity}.{name} has no fixture value"
            source_row[name] = None
    source_row = cast(
        "dict[str, object]",
        {
            name: ("true" if value is True else "false" if value is False else str(value))
            if value is not None
            else None
            for name, value in source_row.items()
        },
    )
    assert set(source_row) == set(columns)
    types, identity = identity_setup(domain, entity)

    checksum = "a" * 64
    provenance = FileProvenance(
        source_system_id="SYNTH",
        data_source_id="test-source",
        dataset_id=entity,
        source_type="CSV",
        source_ref=f"s3://bronze/{domain}/test/{entity}.csv",
        object_version_id="fixture-version-1",
        content_sha256=checksum,
        parser_fingerprint="fixture-parser-v1",
        columns_fingerprint=fingerprint(list(columns)),
        captured_at="2026-10-19T00:00:00+00:00",
        locator_kind="parsed_record_ordinal",
    )
    batch = BronzeBatch(
        batch_id=f"fixture-{domain}-{entity}",
        domain_id=domain,
        source_type="CSV",
        records=(source_row,),
        metadata={"input_version": provenance.input_version},
        mapping_binding=MappingBinding(
            source_system_id=types.key.source_system_id,
            entity=types.key.entity,
            source_schema_version=types.key.source_schema_version,
            mapping_id=types.key.mapping_id,
            mapping_version=types.key.mapping_version,
            target_schema_version=types.key.target_schema_version,
        ),
        record_locators=("record:1",),
        source_paths=tuple("/" + column for column in columns),
        provenance=provenance,
    )
    missing_profile = MissingValueProfile(
        domain=domain,
        entity=entity,
        version="cleaning-test-1",
        required_source_columns=columns,
        rules=tuple(
            MissingValueRule(column["name"], nullable=column["nullable"])
            for column in entity_schema["columns"]
        ),
    )
    target_to_source = {column: column for column in columns}
    format_profile = build_format_profile(
        schema,
        domain=domain,
        entity=entity,
        version="cleaning-test-1",
        target_to_source=target_to_source,
        source_options={
            column["name"]: SourceFormatOptions(timestamp_formats=("%Y-%m-%dT%H:%M:%S%z",))
            for column in entity_schema["columns"]
            if column["type"] == "timestamp"
        },
    )
    duplicate_profile = build_duplicate_profile(
        schema,
        domain=domain,
        entity=entity,
        version="cleaning-test-1",
        target_to_source=target_to_source,
    )
    target_fields = tuple(types.spec["properties"])
    mapping_profile = build_profile(
        key=types.key,
        source_type="CSV",
        source_columns=batch.source_paths,
        target_fields=target_fields,
        field_paths={name: ("/" + name,) for name in columns if name in target_fields},
        deferred_fields=tuple(name for name in types.spec["properties"] if name.startswith("_")),
        ignored_sources=tuple("/" + name for name in columns if name not in target_fields),
    )
    mapping = SchemaMappingService(MappingRegistry((mapping_profile,)))
    return CleaningTask(batch, missing_profile, format_profile, duplicate_profile), (
        mapping,
        identity,
    )


@pytest.mark.parametrize(("domain", "entity"), CASES)
def test_cleaning_standardization_and_silver_publication_for_each_domain(
    publisher, domain: str, entity: str
):
    cleaning, services = _cleaning_task(domain, entity)
    mapping, identity = services
    pipeline = CleaningSilverPipeline(SilverPipeline(publisher))

    result = pipeline.run(
        (
            CleaningNormalizationTask(
                cleaning=cleaning,
                mapping=mapping,
                identity=identity,
            ),
        )
    )

    assert len(result.cleaning_reports) == 1
    report = result.cleaning_reports[0]
    assert report.domain == domain
    assert report.entity == entity
    assert (report.records_read, report.records_passed, report.records_quarantined) == (
        1,
        1,
        0,
    )
    assert all(
        issue.severity == "INFO" for issue in (*report.missing_issues, *report.format_issues)
    )
    assert not report.duplicate_issues
    if domain == "admissions":
        assert [issue.code for issue in report.missing_issues] == ["NULL_VALUE_PRESERVED"]
    else:
        assert not report.issues
    output = publisher.read_snapshot(result.publication.snapshot_id)[entity]
    assert len(output) == 1
    assert output[0]["_batch_id"] == cleaning.batch.batch_id
    assert output[0]["_source_record_id"].startswith("sha256:")
    assert output[0]["_source_system_id"] == "SYNTH"


@pytest.mark.parametrize(
    ("domain", "entity", "field", "value"),
    [
        ("admissions", "admission_method", "max_score", "-1"),
        ("academic", "course", "credit_hours", "99"),
        ("hr", "position", "position_level", "Director"),
    ],
)
def test_invalid_domain_values_are_quarantined(domain: str, entity: str, field: str, value: str):
    task, _ = _cleaning_task(domain, entity)
    invalid_row = {**task.batch.records[0], field: value}
    invalid_batch = replace(task.batch, records=(invalid_row,))

    result = CleaningPipeline().clean(replace(task, batch=invalid_batch))

    assert (result.report.records_passed, result.report.records_quarantined) == (0, 1)
    assert result.report.format_issues
    assert not result.batch.records


@pytest.mark.parametrize(
    ("domain", "entity", "changed_field"),
    [
        ("admissions", "admission_method", "method_name"),
        ("academic", "course", "course_name"),
        ("hr", "position", "position_name"),
    ],
)
def test_domain_primary_key_collisions_quarantine_both_records(
    domain: str, entity: str, changed_field: str
):
    task, _ = _cleaning_task(domain, entity)
    first = task.batch.records[0]
    second = {**first, changed_field: "Changed duplicate"}
    duplicate_batch = replace(
        task.batch,
        records=(first, second),
        record_locators=("record:1", "record:2"),
    )

    result = CleaningPipeline().clean(replace(task, batch=duplicate_batch))

    assert (result.report.records_read, result.report.records_passed) == (2, 0)
    assert result.report.records_quarantined == 2
    assert result.report.duplicate_issues
    assert not result.batch.records
