"""Đối chiếu chuẩn hóa DuckDB với kết quả tham chiếu và kiểm tra xung đột toàn lô."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.modules.ingestion.contracts import BronzeBatch, MappingBinding
from app.modules.pipeline.standardization.duckdb_engine import DuckDBStandardizationEngine
from app.modules.pipeline.standardization.mapping_profiles import build_profile, pointer
from app.modules.pipeline.standardization.schema_mapping import (
    MappingRegistry,
    SchemaMappingService,
)
from tests.test_cleaning_domains import _cleaning_task
from tests.test_identity_standardization import CONTRACTS, RECORDS, setup

ENTITIES = [
    (domain, entity) for domain, contract in CONTRACTS.items() for entity in contract["$defs"]
]


@pytest.mark.parametrize(("domain", "entity"), ENTITIES)
def test_duckdb_preserves_standardization_results_for_all_entities(domain, entity):
    types, identity = setup(domain, entity)
    record = dict(RECORDS[entity][0])
    key = types.key
    paths = tuple(pointer(field) for field in record)
    batch = BronzeBatch(
        batch_id="parity",
        domain_id=domain,
        source_type="CSV",
        records=(record,),
        record_locators=("row:1",),
        source_paths=paths,
        metadata={"input_version": "v1"},
        mapping_binding=MappingBinding(
            key.source_system_id,
            entity,
            key.source_schema_version,
            key.mapping_id,
            key.mapping_version,
            key.target_schema_version,
        ),
    )
    properties = types.spec["properties"]
    profile = build_profile(
        key=key,
        source_type="CSV",
        source_columns=paths,
        target_fields=tuple(properties),
        field_paths={field: (pointer(field),) for field in record},
        deferred_fields=tuple(field for field in properties if field not in record),
    )
    mapping = SchemaMappingService(MappingRegistry((profile,)))
    inputs = (
        {"row:1": {"date_of_birth": f"{record['birth_year']}-01-01"}}
        if "birth_year" in record
        else None
    )
    mapped = mapping.map_batch(batch)
    typed = identity.types.standardize(
        mapped, derivation_inputs=inputs, derivation_input_version="v1" if inputs else None
    )
    expected = identity.standardize(typed)
    engine = DuckDBStandardizationEngine()
    assert engine.map_batch(batch, mapping) == mapped
    assert (
        engine.types(
            mapped,
            identity,
            derivation_inputs=inputs,
            derivation_input_version="v1" if inputs else None,
        )
        == typed
    )
    assert engine.identities(typed, identity) == expected
    assert (
        engine.standardize(
            batch,
            mapping,
            identity,
            derivation_inputs=inputs,
            derivation_input_version="v1" if inputs else None,
        )
        == expected
    )


@pytest.mark.parametrize("variant", ["duplicate", "unmapped", "invalid_type", "empty"])
def test_duckdb_preserves_errors_and_rejects_all_canonical_collisions(variant):
    task, (mapping, identity) = _cleaning_task("hr", "position")
    batch = task.batch
    if variant == "empty":
        batch = replace(batch, records=(), record_locators=())
    else:
        row = dict(batch.records[0])
        if variant == "unmapped":
            row["position_id"] = "007"
        if variant == "invalid_type":
            row["position_code"] = 7
        batch = replace(batch, records=(batch.records[0], row), record_locators=("row:1", "row:2"))
    mapped = mapping.map_batch(batch)
    typed = identity.types.standardize(mapped)
    assert DuckDBStandardizationEngine().standardize(
        batch, mapping, identity
    ) == identity.standardize(typed)
