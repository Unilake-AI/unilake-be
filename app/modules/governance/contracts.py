"""Quy định thông tin nguồn gốc dữ liệu được trao đổi giữa các bước xử lý."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AssetReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: NonBlank
    layer: Literal["SOURCE", "BRONZE", "SILVER", "GOLD"] | None
    domain: NonBlank
    version: NonBlank | None = None
    uri: str | None = None
    checksum: str | None = None
    producer_run_ref: NonBlank | None = None

    @model_validator(mode="after")
    def versioned_layer(self) -> Self:
        if self.layer in {"BRONZE", "SILVER", "GOLD"} and (
            self.version is None or self.version == "null"
        ):
            raise ValueError("PUBLISHED_LAYER_VERSION_REQUIRED")
        return self


class TransformationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonBlank
    version: str | None = None
    fingerprint: str | None = None


class DatasetDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    inputs: tuple[AssetReference, ...] = Field(min_length=1)
    output: AssetReference
    transformations: tuple[TransformationEvidence, ...] = Field(min_length=1)


class LineageEvidence(BaseModel):
    """Thông tin nguồn gốc chỉ được công nhận khi lần xử lý tương ứng đã công bố thành công."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_version: Literal["dataset-lineage-v1"] = "dataset-lineage-v1"
    run_ref: str = Field(min_length=1)
    # Giữ giá trị cũ để đọc lịch sử; các lần xử lý mới không dùng giá trị này.
    mode: Literal["ingestion", "test", "production", "rehearsal"]
    parent_snapshot: str | None = None
    derivations: tuple[DatasetDerivation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def run_layers(self) -> Self:
        if self.mode == "ingestion":
            if not self.run_ref.startswith("ingestion:") or self.parent_snapshot is not None:
                raise ValueError("INGESTION_RUN_REFERENCE_MISMATCH")
            if any(
                edge.output.layer != "BRONZE"
                or any(source.layer != "SOURCE" for source in edge.inputs)
                for edge in self.derivations
            ):
                raise ValueError("INGESTION_LAYER_MISMATCH")
        elif any(edge.output.layer not in {"SILVER", "GOLD"} for edge in self.derivations):
            raise ValueError("PROCESSING_OUTPUT_LAYER_MISMATCH")
        return self


class SnapshotLineage(BaseModel):
    """Chỉ trả nguồn gốc đã công bố; thông tin còn thiếu được ghi rõ thay vì tự suy đoán."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: NonBlank
    evidence: tuple[LineageEvidence, ...]
    missing_entities: tuple[str, ...]
