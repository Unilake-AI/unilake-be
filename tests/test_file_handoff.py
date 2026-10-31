from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.ingestion.contracts import MappingBinding
from app.modules.ingestion.reader_batch import reader_to_batch
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.csv_reader import CSVReader
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot
from app.modules.ingestion.readers.source_loader import read_data_source_batch
from app.modules.pipeline.mapping_profiles import build_profile
from app.modules.pipeline.schema_mapping import (
    MappingConfigurationError,
    MappingKey,
    MappingRegistry,
    SchemaMappingService,
)

CONFIG = {"delimiter": ",", "encoding": "utf-8", "has_header": True}
BINDING = MappingBinding("10", "position", "1", "m", "1", "0.1.0-draft")


def capture(path, reader=None, config=None, version=None):
    request = ReadRequest("CSV", str(path), config or CONFIG, "input.csv", version)
    return read_file_snapshot(
        request,
        reader or CSVReader(),
        source_system_id="10",
        data_source_id="7",
        dataset_id="positions",
    )


def batch(result, **kwargs):
    return reader_to_batch(
        result, batch_id="b", domain_id="hr", source_type="CSV", binding=BINDING, **kwargs
    )


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "input.csv"
    path.write_bytes(b'id,name\n001,"a\nb"\n002,c\n')
    return path


def test_snapshot_keeps_original_identity_and_logical_csv_locator(csv_path):
    result = capture(csv_path)
    handoff = batch(result)
    assert result.rows == [("001", "a\nb"), ("002", "c")]
    assert handoff.record_locators == ("record:1", "record:2")
    assert (
        handoff.metadata["input_version"]
        == "sha256:" + hashlib.sha256(csv_path.read_bytes()).hexdigest()
    )
    assert handoff.provenance is not None
    assert handoff.provenance.source_ref == str(csv_path.resolve())
    assert handoff.provenance.dataset_id == "positions"
    assert handoff.provenance.data_source_id == "7"
    assert handoff.provenance.object_version_id is None
    assert handoff.provenance.locator_kind == "parsed_record_ordinal"


def test_original_changes_after_capture_do_not_change_parsed_snapshot(csv_path):
    original = csv_path.read_bytes()
    seen = []

    class ChangeOriginal:
        def read(self, request):
            seen.append(Path(request.location))
            csv_path.write_text("id,name\n999,new\n", encoding="utf-8")
            return CSVReader().read(request)

    result = capture(csv_path, ChangeOriginal())
    assert result.rows[0][0] == "001"
    assert result.provenance is not None
    assert result.provenance.content_sha256 == hashlib.sha256(original).hexdigest()
    assert not seen[0].exists()


def test_reader_cannot_mutate_snapshot_unnoticed(csv_path):
    class ChangeSnapshot:
        def read(self, request):
            Path(request.location).write_text("id\n2\n", encoding="utf-8")
            return CSVReader().read(request)

    with pytest.raises(ValueError, match="SNAPSHOT_CHANGED_DURING_READ"):
        capture(csv_path, ChangeSnapshot())


def test_same_bytes_different_parser_has_different_fingerprint(csv_path):
    first = capture(csv_path)
    second = capture(csv_path, config={**CONFIG, "skip_rows": 0})
    assert first.provenance is not None
    assert second.provenance is not None
    assert first.provenance.input_version == second.provenance.input_version
    assert first.provenance.parser_fingerprint != second.provenance.parser_fingerprint


def test_version_cannot_be_overridden(csv_path):
    with pytest.raises(ValueError, match="INPUT_VERSION_MISMATCH"):
        batch(capture(csv_path), input_version="made-up")


def test_s2_rejects_version_changed_after_adapter(csv_path):
    handoff = batch(capture(csv_path))
    key = MappingKey("10", "hr", "position", "1", "m", "1", "0.1.0-draft")
    profile = build_profile(
        key=key,
        source_type="CSV",
        source_columns=("/id", "/name"),
        target_fields=("id", "name"),
        field_paths={"id": ("/id",), "name": ("/name",)},
    )
    service = SchemaMappingService(MappingRegistry((profile,)))
    with pytest.raises(MappingConfigurationError, match="PROVENANCE_MISMATCH"):
        service.map_batch(replace(handoff, metadata={"input_version": "wrong"}))


def test_g1_rule_disposition_covers_source_without_claiming_approval():
    root = Path(__file__).resolve().parents[1] / "docs/standardization"
    source = json.loads((root / "business-rule-review.json").read_text(encoding="utf-8"))
    disposition = json.loads((root / "g1-rule-disposition.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "silver-schemas/manifest.json").read_text(encoding="utf-8"))
    classified = [rule for group in disposition["groups"].values() for rule in group]
    assert len(classified) == len(set(classified)) == len(source) == 39
    assert set(classified) == {rule["id"] for rule in source}
    assert disposition["activation_allowed"] is manifest["activation_allowed"] is False
    assert disposition["approved_by"] is None


def test_raw_reader_cannot_claim_provenance(csv_path):
    raw = CSVReader().read(ReadRequest("CSV", str(csv_path), CONFIG))
    with pytest.raises(ValueError, match="READER_PROVENANCE_REQUIRED"):
        batch(raw, input_version="sha256:" + "a" * 64)


@pytest.mark.parametrize(
    "locators", [("record:1", "record:1"), ("record:1",), ("", "record:2"), (None, "record:2")]
)
def test_bad_locators_rejected(csv_path, locators):
    result = capture(csv_path)
    with pytest.raises(ValueError, match="INVALID_READER_LOCATORS"):
        batch(replace(result, record_locators=locators))


def test_binding_namespace_and_column_shape_checked(csv_path):
    result = capture(csv_path)
    assert result.provenance is not None
    with pytest.raises(ValueError, match="SOURCE_BINDING_MISMATCH"):
        batch(replace(result, provenance=replace(result.provenance, source_system_id="other")))
    with pytest.raises(ValueError, match="READER_COLUMNS_CHANGED"):
        batch(replace(result, columns=["other", "name"]))


@pytest.mark.parametrize("version", ["v-old", None, "null"])
def test_minio_version_pinned_and_origin_survives_cleanup(version):
    content = b"id,name\n001,first\n"
    client = MagicMock()
    client.stat_object.return_value = SimpleNamespace(version_id=version)
    downloaded = []

    def download(**kwargs):
        downloaded.append(kwargs)
        Path(kwargs["file_path"]).write_bytes(content)
        return SimpleNamespace(version_id=version)

    client.fget_object.side_effect = download
    with patch(
        "app.modules.ingestion.readers.minio_file_resolver._create_minio_client",
        return_value=client,
    ):
        result = capture("s3://bronze/hr/input.csv")
    assert result.provenance is not None
    assert result.provenance.source_ref == "s3://bronze/hr/input.csv"
    assert result.provenance.object_version_id == ("v-old" if version == "v-old" else None)
    assert result.provenance.content_sha256 == hashlib.sha256(content).hexdigest()
    assert downloaded[0].get("version_id") == ("v-old" if version == "v-old" else None)
    assert not Path(downloaded[0]["file_path"]).exists()


def test_minio_explicit_historical_version_ignores_current():
    client = MagicMock()
    client.fget_object.side_effect = lambda **kw: (
        Path(kw["file_path"]).write_bytes(b"id\n1\n"),
        SimpleNamespace(version_id="historical"),
    )[1]
    with patch(
        "app.modules.ingestion.readers.minio_file_resolver._create_minio_client",
        return_value=client,
    ):
        result = capture("s3://bronze/hr/input.csv", version="historical")
    client.stat_object.assert_not_called()
    assert client.fget_object.call_args.kwargs["version_id"] == "historical"
    assert result.provenance is not None
    assert result.provenance.object_version_id == "historical"


def test_minio_wrong_download_version_rejected():
    client = MagicMock()
    client.fget_object.return_value = SimpleNamespace(version_id="different")
    with (
        patch(
            "app.modules.ingestion.readers.minio_file_resolver._create_minio_client",
            return_value=client,
        ),
        pytest.raises(ValueError, match="OBJECT_VERSION_MISMATCH"),
    ):
        capture("s3://bronze/hr/input.csv", version="historical")


@pytest.mark.asyncio
async def test_catalog_loader_file_handoff(csv_path):
    source = SimpleNamespace(
        source_system_id=10,
        source_type="CSV",
        location=str(csv_path),
        configuration=CONFIG,
        is_active=True,
    )
    repo = MagicMock()
    repo.get_with_source_system = AsyncMock(return_value=(source, "HR"))
    repo.check_exist_source_system = AsyncMock(return_value=True)
    result = await read_data_source_batch(
        repo,
        data_source_id=7,
        source_table="input.csv",
        dataset_id="positions",
        domain_id="hr",
        batch_id="b",
        binding=BINDING,
    )
    assert result.records[0]["id"] == "001"
    assert result.provenance is not None
    assert result.provenance.source_system_id == "10"
    assert result.metadata["input_version"] == result.provenance.input_version


@pytest.mark.asyncio
async def test_catalog_rejects_namespace_before_io(csv_path):
    source = SimpleNamespace(
        source_system_id=99,
        source_type="CSV",
        location=str(csv_path),
        configuration=CONFIG,
        is_active=True,
    )
    repo = MagicMock()
    repo.get_with_source_system = AsyncMock(return_value=(source, "Other"))
    with (
        patch(
            "app.modules.ingestion.readers.source_loader.SourceReaderFactory.get_reader"
        ) as factory,
        pytest.raises(ValueError, match="SOURCE_BINDING_MISMATCH"),
    ):
        await read_data_source_batch(
            repo,
            data_source_id=7,
            source_table="input.csv",
            dataset_id="positions",
            domain_id="hr",
            batch_id="b",
            binding=BINDING,
        )
    factory.assert_not_called()
