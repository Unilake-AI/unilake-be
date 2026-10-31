"""S4 → Parquet đã kiểm tra → bản kê bất biến → con trỏ hiện hành cập nhật qua giao dịch."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from app.modules.pipeline.identity_contracts import digest
from app.modules.pipeline.schema_mapping import MappingKey
from app.modules.pipeline.silver_storage import ObjectRef, SilverError, checksum
from app.modules.pipeline.silver_validation import (
    SilverIssue,
    arrow_schema,
    parquet_bytes,
    read_parquet,
    validate_snapshot,
)
from app.modules.pipeline.type_standardization import TypeStandardizationService

if TYPE_CHECKING:
    from app.modules.pipeline.identity_standardization import IdentityIssue, IdentityResult
    from app.modules.pipeline.schema_mapping import MappingIssue
    from app.modules.pipeline.silver_repository import SilverRepository
    from app.modules.pipeline.silver_storage import SilverObjectStore
    from app.modules.pipeline.type_standardization import TypeIssue

RULE_IDS = frozenset(
    f"{prefix}-DQ-{i:02}"
    for prefix, count in (("ADM", 12), ("ACA", 13), ("HR", 14))
    for i in range(1, count + 1)
)


def upstream_issues(result: IdentityResult) -> tuple[MappingIssue | TypeIssue | IdentityIssue, ...]:
    return (*result.mapping_issues, *result.type_issues, *result.issues)


def encode(value: object) -> object:
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$timestamp": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    raise TypeError("UNSUPPORTED_SILVER_VALUE")


def packed(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=encode,
    ).encode()


@dataclass(frozen=True)
class BusinessEvidence:
    executed_rule_ids: frozenset[str]
    issues: tuple[SilverIssue, ...] = ()


class BusinessGate(Protocol):
    version: str

    def evaluate(
        self, tables: Mapping[str, Sequence[dict[str, object]]], manifest: Mapping[str, Any]
    ) -> BusinessEvidence: ...


@dataclass(frozen=True)
class PublicationReceipt:
    snapshot_id: str
    manifest: ObjectRef
    scope: str
    mode: str
    reused: bool


@dataclass(frozen=True, init=False)
class SilverPublicationService:
    repository: SilverRepository
    storage: SilverObjectStore
    scope: str
    mode: str
    contract_json: str
    policy_json: str
    schema_fingerprint: str
    business_gate: BusinessGate | None

    def __init__(
        self,
        *,
        repository: SilverRepository,
        storage: SilverObjectStore,
        contracts: Mapping[str, Any],
        scope: str,
        mode: str = "production",
        approval: Mapping[str, Any] | None = None,
        business_gate: BusinessGate | None = None,
    ) -> None:
        if (
            mode not in {"production", "rehearsal"}
            or not isinstance(scope, str)
            or not scope.strip()
            or len(scope) > 100
        ):
            raise SilverError("INVALID_PUBLICATION_SCOPE")
        raw = json.loads(json.dumps(contracts, allow_nan=False))
        names: set[str] = set()
        for domain, contract in raw.items():
            for entity, spec in contract["$defs"].items():
                if entity in names:
                    raise SilverError("DUPLICATE_ENTITY_CONTRACT")
                names.add(entity)
                TypeStandardizationService(
                    key=MappingKey(
                        "validate", domain, entity, "1", "validate", "1", contract["x-version"]
                    ),
                    conversion_version="validate",
                    contract=contract,
                )
                arrow_schema(spec)
        if not names:
            raise SilverError("SCHEMA_REQUIRED")
        for contract in raw.values():
            for spec in contract["$defs"].values():
                for field in spec["properties"].values():
                    if "x-references" in field and field["x-references"]["entity"] not in names:
                        raise SilverError("MISSING_REFERENCE_CONTRACT")
        for name, value in {
            "repository": repository,
            "storage": storage,
            "scope": f"{mode}:{scope}",
            "mode": mode,
            "contract_json": json.dumps(raw),
            "schema_fingerprint": digest(raw),
            "policy_json": json.dumps(dict(approval or {}), sort_keys=True, allow_nan=False),
            "business_gate": business_gate,
        }.items():
            object.__setattr__(self, name, value)

    def _specs(self) -> dict[str, Any]:
        return {
            e: s for c in json.loads(self.contract_json).values() for e, s in c["$defs"].items()
        }

    def _gate(
        self, tables: Mapping[str, Sequence[dict[str, object]]], manifest: Mapping[str, Any]
    ) -> tuple[SilverIssue, ...]:
        # Demo bỏ qua cổng phê duyệt nghiệp vụ; không phải bằng chứng đạt production.
        # Production còn yêu cầu schema được duyệt và bằng chứng thực thi bộ DQ rules.
        if self.mode == "rehearsal":
            return ()
        policy = json.loads(self.policy_json)
        contracts = json.loads(self.contract_json)
        if (
            policy.get("activation_allowed") is not True
            or not policy.get("g1_evidence")
            or policy.get("schema_fingerprint") != self.schema_fingerprint
            or any(
                c.get("x-approval") != "APPROVED" or "draft" in c["x-version"]
                for c in contracts.values()
            )
        ):
            raise SilverError("G1_NOT_APPROVED")
        if (
            self.business_gate is None
            or self.business_gate.version != policy.get("dq_version")
            or set(policy.get("dq_rule_ids", [])) != RULE_IDS
        ):
            raise SilverError("BUSINESS_GATE_REQUIRED")
        evidence = self.business_gate.evaluate(deepcopy(tables), deepcopy(manifest))
        if evidence.executed_rule_ids != RULE_IDS or any(
            i.severity != "warning" for i in evidence.issues
        ):
            raise SilverError("BUSINESS_GATE_REJECTED")
        return evidence.issues

    def _receipt(self, row: Mapping[str, Any], reused: bool) -> PublicationReceipt:
        return PublicationReceipt(
            row["snapshot_id"],
            ObjectRef(**json.loads(row["manifest_ref"])),
            self.scope,
            self.mode,
            reused,
        )

    def _read_manifest(
        self, ref: ObjectRef, snapshot_id: str
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, object]]]]:
        manifest = json.loads(self.storage.get(ref))
        if (
            manifest["snapshot_id"] != snapshot_id
            or manifest["scope"] != self.scope
            or manifest["mode"] != self.mode
            or manifest["schema_fingerprint"] != self.schema_fingerprint
        ):
            raise SilverError("MANIFEST_CONTRACT_MISMATCH")
        specs = self._specs()
        tables = {}
        for entity, artifact in manifest["entities"].items():
            if entity not in specs:
                raise SilverError("UNKNOWN_MANIFEST_ENTITY")
            tables[entity] = read_parquet(
                self.storage.get(ObjectRef(**artifact["object"])), specs[entity], artifact["rows"]
            )
        return manifest, tables

    def read_snapshot(self, snapshot_id: str) -> dict[str, list[dict[str, object]]]:
        row = self.repository.snapshot(snapshot_id, self.scope)
        _, tables = self._read_manifest(ObjectRef(**json.loads(row["manifest_ref"])), snapshot_id)
        return tables

    def read_active(self) -> dict[str, list[dict[str, object]]]:
        head = self.repository.head(self.scope)
        return self.read_snapshot(head) if head else {}

    def publish(
        self, results: Sequence[IdentityResult], *, base_snapshot: str | None = None
    ) -> PublicationReceipt:
        if not results:
            raise SilverError("NO_INPUT_BATCHES")
        # Loại thời gian thử/chụp và mã batch khỏi khóa chống xử lý lặp logic.
        inputs = []
        for result in results:
            source = asdict(result.provenance) if result.provenance else None
            if source:
                source.pop("captured_at")
            inputs.append(
                {
                    "key": asdict(result.key),
                    "input_version": result.input_version,
                    "source": source,
                    "conversion": result.conversion_fingerprint,
                    "conversion_version": result.conversion_version,
                    "identity": result.configuration_fingerprint,
                    "identity_version": result.identity_version,
                    "crosswalk": result.crosswalk_fingerprint,
                    "crosswalk_version": result.crosswalk_version,
                    "dictionary_versions": result.dictionary_versions,
                    "effective_at": result.effective_at,
                    "records": [asdict(row) for row in result.accepted],
                    "rejected": result.rejected_locators,
                    "issues": [asdict(i) for i in upstream_issues(result)],
                    "preview": result.preview,
                    "counts": [result.inspected_count, result.total_count],
                }
            )
        request_hash = checksum(
            packed(
                {
                    "scope": self.scope,
                    "parent": base_snapshot,
                    "schema": self.schema_fingerprint,
                    "policy": self.policy_json,
                    "inputs": sorted(inputs, key=lambda item: checksum(packed(item))),
                }
            )
        )
        # Cùng yêu cầu logic thì tái sử dụng snapshot đã công bố (idempotency).
        # Không đưa active head về snapshot cũ khi người dùng gửi lại yêu cầu cũ.
        existing = self.repository.find(self.scope, request_hash)
        if existing:
            manifest, reused_tables = self._read_manifest(
                ObjectRef(**json.loads(existing["manifest_ref"])), existing["snapshot_id"]
            )
            self._gate(reused_tables, manifest)
            return self._receipt(existing, True)
        run_id = str(uuid4())
        self.repository.start(run_id, self.scope, request_hash, base_snapshot)
        issues: list[SilverIssue] = []
        try:
            if self.repository.head(self.scope) != base_snapshot:
                raise SilverError("ACTIVE_SNAPSHOT_CHANGED")
            entities: dict[str, Any] = {}
            tables: dict[str, list[dict[str, object]]] = {}
            parent_ref = None
            if base_snapshot:
                parent = self.repository.snapshot(base_snapshot, self.scope)
                parent_ref = json.loads(parent["manifest_ref"])
                parent_manifest, tables = self._read_manifest(
                    ObjectRef(**parent_ref), base_snapshot
                )
                entities = parent_manifest["entities"]
            specs = self._specs()
            schemas = json.loads(self.contract_json)
            updates: dict[str, list[dict[str, object]]] = {}
            lineage: dict[str, list[dict[str, Any]]] = {}
            processed_at = datetime.now(UTC)
            for result in results:
                entity = result.key.entity
                if (
                    entity not in specs
                    or specs[entity]["x-owner"] != result.key.domain_id
                    or schemas[result.key.domain_id]["x-version"]
                    != result.key.target_schema_version
                ):
                    raise SilverError("S4_SCHEMA_MISMATCH")
                if (
                    result.preview
                    or result.rejected_locators
                    or result.inspected_count != result.total_count
                    or len(result.accepted) != result.total_count
                    or any(i.severity == "error" for i in upstream_issues(result))
                ):
                    issues.append(SilverIssue(entity, "*", "batch", "UPSTREAM_NOT_COMPLETE"))
                    issues.extend(
                        SilverIssue(entity, i.field or "*", i.locator or "batch", i.code)
                        for i in upstream_issues(result)
                        if i.severity == "error"
                    )
                    issues.extend(
                        SilverIssue(entity, "*", locator, "UPSTREAM_ROW_REJECTED")
                        for locator in result.rejected_locators
                    )
                if (
                    not result.input_version
                    or not result.configuration_fingerprint
                    or not result.conversion_fingerprint
                ):
                    raise SilverError("LINEAGE_REQUIRED")
                if result.provenance is not None and (
                    result.provenance.input_version != result.input_version
                    or result.provenance.source_system_id != result.key.source_system_id
                ):
                    raise SilverError("PROVENANCE_MISMATCH")
                if self.mode == "production" and result.provenance is None:
                    raise SilverError("PROVENANCE_REQUIRED")
                if (
                    self.mode == "production"
                    and result.provenance is not None
                    and (
                        not result.provenance.source_ref.startswith("s3://")
                        or not result.provenance.object_version_id
                        or result.provenance.object_version_id == "null"
                    )
                ):
                    raise SilverError("BRONZE_REPLAY_REFERENCE_REQUIRED")
                # Gộp các batch cùng entity trước khi tạo snapshot, tránh batch sau
                # ghi đè mất dữ liệu batch trước ngay trong cùng lần công bố.
                records = updates.setdefault(entity, [])
                meta = asdict(result)
                meta.pop("accepted")
                meta["record_locators"] = []
                lineage.setdefault(entity, []).append(meta)
                record_source = (
                    asdict(result.provenance)
                    if result.provenance
                    else {"namespace": result.key.source_system_id}
                )
                record_source.pop("captured_at", None)
                for row in result.accepted:
                    if not row.locator.strip() or set(row.values) != {
                        n for n in specs[entity]["properties"] if not n.startswith("_")
                    }:
                        raise SilverError("UNEXPECTED_BUSINESS_FIELDS")
                    record_id = "sha256:" + checksum(
                        packed(
                            {
                                "source": record_source,
                                "entity": entity,
                                "input": result.input_version,
                                "locator": row.locator,
                            }
                        )
                    )
                    records.append(
                        {
                            **row.values,
                            "_source_system_id": result.key.source_system_id,
                            "_source_record_id": record_id,
                            "_batch_id": result.batch_id,
                            "_run_id": run_id,
                            "_processed_at": processed_at,
                            "_schema_version": result.key.target_schema_version,
                            "_mapping_version": result.key.mapping_version,
                        }
                    )
                    meta["record_locators"].append(
                        {"source_record_id": record_id, "locator": row.locator}
                    )
            # Đây là thay thế TOÀN BỘ entity được gửi lên, không phải upsert từng dòng.
            # Entity không gửi giữ dữ liệu từ snapshot cha; kiểm tra FK trên toàn bộ
            # snapshot ứng viên để phát hiện tham chiếu bị đứt sau khi thay thế.
            tables.update(updates)
            issues.extend(validate_snapshot(tables, specs))
            if issues:
                raise SilverError("SNAPSHOT_REJECTED")
            manifest = {
                "format": "unilake-silver-v1",
                "snapshot_id": run_id,
                "scope": self.scope,
                "mode": self.mode,
                "activation_allowed": self.mode == "production",
                "request_hash": request_hash,
                "schema_fingerprint": self.schema_fingerprint,
                "policy_fingerprint": checksum(self.policy_json.encode()),
                "governance": json.loads(self.policy_json),
                "parent_snapshot": base_snapshot,
                "parent_manifest": parent_ref,
                "created_at": processed_at.isoformat(),
                "updated_lineage": lineage,
                "entities": entities,
            }
            # Kiểm tra điều kiện quản trị trước mọi thao tác ghi đối tượng. Đã có nguồn gốc cho thực thể mới.
            manifest["business_issues"] = [asdict(issue) for issue in self._gate(tables, manifest)]
            prefix = f"silver/{self.mode}/{checksum(self.scope.encode())}/{run_id}"
            for entity, records in updates.items():
                data = parquet_bytes(records, specs[entity])
                ref = self.storage.put(f"{prefix}/{entity}-{checksum(data)}.parquet", data)
                if read_parquet(self.storage.get(ref), specs[entity], len(records)) != records:
                    raise SilverError("PARQUET_READBACK_MISMATCH")
                entities[entity] = {
                    "object": asdict(ref),
                    "rows": len(records),
                    "lineage": lineage[entity],
                }
            data = packed(manifest)
            ref = self.storage.put(f"{prefix}/manifest-{checksum(data)}.json", data)
            self._read_manifest(ref, run_id)
            self.repository.stage(run_id, ref)
            published = self.repository.publish(run_id)
            return self._receipt(published, published["snapshot_id"] != run_id)
        except Exception as exc:
            if not issues:
                issues = [
                    SilverIssue(
                        "*",
                        "*",
                        "run",
                        str(exc) if isinstance(exc, SilverError) else "STORAGE_OR_METADATA_FAILURE",
                    )
                ]
            # Nếu metadata không khả dụng, giữ lại lần thử để đối soát.
            with suppress(Exception):
                self.repository.fail(run_id, [asdict(i) for i in issues])
            if isinstance(exc, SilverError):
                raise
            raise SilverError("STORAGE_OR_METADATA_FAILURE") from exc

    def resume(self, run_id: str) -> PublicationReceipt:
        # Khôi phục phải đọc và kiểm chứng lại artifact/quy tắc trước khi công bố,
        # không chỉ đổi trạng thái lần chạy dở dang thành thành công.
        run = self.repository.run(run_id)
        if run["scope"] != self.scope or not run["manifest_ref"]:
            raise SilverError("RUN_NOT_STAGED")
        manifest, tables = self._read_manifest(ObjectRef(**json.loads(run["manifest_ref"])), run_id)
        if (
            manifest["request_hash"] != run["request_hash"]
            or manifest["parent_snapshot"] != run["base_snapshot"]
            or manifest["policy_fingerprint"] != checksum(self.policy_json.encode())
        ):
            raise SilverError("RESUME_CONTRACT_MISMATCH")
        if validate_snapshot(tables, self._specs()):
            raise SilverError("SNAPSHOT_REJECTED")
        self._gate(tables, manifest)
        published = self.repository.publish(run_id)
        return self._receipt(
            published, published["snapshot_id"] != run_id or run["status"] == "PUBLISHED"
        )
