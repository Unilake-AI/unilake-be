"""Đọc cấu hình làm sạch và chuẩn hóa được khai báo riêng cho từng nguồn dữ liệu."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.modules.pipeline.cleaning.duplicate_records import build_duplicate_profile
from app.modules.pipeline.cleaning.format_validation import (
    SourceFormatOptions,
    build_format_profile,
)
from app.modules.pipeline.contracts.identity import CrosswalkSnapshot, digest
from app.modules.pipeline.standardization.identity_standardization import (
    IdentityStandardizationService,
    ValueDictionary,
)
from app.modules.pipeline.standardization.mapping_profiles import build_profile, pointer
from app.modules.pipeline.standardization.schema_mapping import (
    MappingKey,
    MappingRegistry,
    SchemaMappingService,
)
from app.modules.pipeline.standardization.type_standardization import (
    FieldOptions,
    TypeStandardizationService,
)


class SourceProfile(BaseModel):
    """Các quy tắc của một bảng nguồn; thay đổi cấu hình được ghi nhận bằng mã nội dung mới."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: int = Field(gt=0)
    dataset_id: int = Field(gt=0)
    source_system_id: int = Field(gt=0)
    source_type: Literal["CSV", "EXCEL"]
    source_table: str = Field(min_length=1)
    domain: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,62}$")
    entity: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1, max_length=40)
    scope: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    source_schema_version: str = Field(min_length=1)
    source_schema: dict[str, Any]
    contracts: dict[str, Any]
    field_mapping: dict[str, str]
    ignored_columns: list[str] = Field(default_factory=list)
    format_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    type_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    null_tokens: dict[str, list[str]] = Field(default_factory=dict)
    dictionaries: dict[str, dict[str, str]] = Field(default_factory=dict)
    reference_namespaces: dict[str, str] = Field(default_factory=dict)
    crosswalk_release: str = Field(min_length=1)
    effective_at: AwareDatetime
    collapse_exact_duplicates: bool = False
    source_preview_columns: list[str] = Field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        return digest(self.model_dump(mode="json"))

    @property
    def rule_version(self) -> str:
        return f"{self.version}:{self.fingerprint}"

    def key(self) -> MappingKey:
        return MappingKey(
            str(self.source_system_id),
            self.domain,
            self.entity,
            self.source_schema_version,
            f"source-{self.data_source_id}-dataset-{self.dataset_id}",
            self.rule_version,
            self.contracts[self.domain]["x-version"],
        )

    def services(
        self, crosswalk: CrosswalkSnapshot
    ) -> tuple[SchemaMappingService, IdentityStandardizationService]:
        contract = self.contracts[self.domain]
        props = contract["$defs"][self.entity]["properties"]
        key = self.key()
        profile = build_profile(
            key=key,
            source_type=self.source_type,
            source_columns=tuple(
                pointer(n) for n in [*self.field_mapping.values(), *self.ignored_columns]
            ),
            target_fields=tuple(props),
            field_paths={
                target: (pointer(source),) for target, source in self.field_mapping.items()
            },
            deferred_fields=tuple(n for n in props if n not in self.field_mapping),
            ignored_sources=tuple(pointer(n) for n in self.ignored_columns),
        )
        types = TypeStandardizationService(
            key=key,
            conversion_version=self.rule_version,
            contract=contract,
            options={
                name: FieldOptions(**_tuples(options))
                for name, options in self.type_options.items()
            },
        )
        identity = IdentityStandardizationService(
            types=types,
            version=self.rule_version,
            crosswalk=crosswalk,
            dictionaries={
                name: ValueDictionary(self.rule_version, tuple(aliases.items()))
                for name, aliases in self.dictionaries.items()
            },
            reference_namespaces=self.reference_namespaces,
            effective_at=self.effective_at,
        )
        return SchemaMappingService(MappingRegistry((profile,))), identity

    @model_validator(mode="after")
    def validate_definition(self) -> SourceProfile:
        if not self.field_mapping or len(set(self.field_mapping.values())) != len(
            self.field_mapping
        ):
            raise ValueError("SOURCE_FIELD_MAPPING_REQUIRED_OR_DUPLICATE")
        if set(self.ignored_columns) & set(self.field_mapping.values()):
            raise ValueError("SOURCE_COLUMN_BOTH_MAPPED_AND_IGNORED")
        if not set(self.source_preview_columns) <= set(self.field_mapping.values()):
            raise ValueError("SOURCE_PREVIEW_COLUMN_NOT_MAPPED")
        if not set(self.null_tokens) <= set(self.field_mapping):
            raise ValueError("UNKNOWN_NULL_TOKEN_FIELD")
        try:
            properties = self.contracts[self.domain]["$defs"][self.entity]["properties"]
            if set(self.field_mapping) != {name for name in properties if not name.startswith("_")}:
                raise ValueError("SILVER_BUSINESS_FIELDS_MUST_BE_MAPPED")
            source_columns = {
                column["name"] for column in self.source_schema["entities"][self.entity]["columns"]
            }
            if not set(self.field_mapping) <= source_columns:
                raise ValueError("SOURCE_SCHEMA_FIELDS_REQUIRED")
            build_format_profile(
                self.source_schema,
                domain=self.domain,
                entity=self.entity,
                version=self.rule_version,
                target_to_source=self.field_mapping,
                source_options={
                    name: SourceFormatOptions(**_tuples(options))
                    for name, options in self.format_options.items()
                },
            )
            build_duplicate_profile(
                self.source_schema,
                domain=self.domain,
                entity=self.entity,
                version=self.rule_version,
                target_to_source=self.field_mapping,
                collapse_exact_primary_key_duplicates=self.collapse_exact_duplicates,
            )
            self.services(CrosswalkSnapshot(self.crosswalk_release, ()))
        except (KeyError, TypeError) as exc:
            raise ValueError("INVALID_SILVER_PROFILE") from exc
        return self


def _tuples(options: dict[str, Any]) -> dict[str, Any]:
    def convert(value: Any) -> Any:
        return tuple(convert(item) for item in value) if isinstance(value, list) else value

    return {key: convert(value) for key, value in options.items()}


def load_profile(path: str, profile_id: str) -> SourceProfile:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("SILVER_PROFILE_CONFIGURATION_UNAVAILABLE") from exc
    if not isinstance(payload, dict) or profile_id not in payload:
        raise ValueError("SILVER_SOURCE_PROFILE_NOT_FOUND")
    try:
        return SourceProfile.model_validate(payload[profile_id])
    except ValidationError as exc:
        raise ValueError("SILVER_SOURCE_PROFILE_INVALID") from exc
