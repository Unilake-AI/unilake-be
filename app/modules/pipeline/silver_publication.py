"""Kiểm tra, lưu và công bố bộ dữ liệu Silver kèm thông tin nguồn gốc."""

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

from app.modules.governance.contracts import LineageEvidence, SnapshotLineage
from app.modules.pipeline.identity_contracts import digest
from app.modules.pipeline.lineage import silver_lineage
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
        business_gate: BusinessGate | None = None,
    ) -> None:
        if (
            mode not in {"production", "test"}
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
            "policy_json": json.dumps(
                {
                    "scope": "silver-quality-v1",
                    "additional_gate": business_gate.version if business_gate else None,
                },
                sort_keys=True,
            ),
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
        # Silver kiểm tra cấu trúc và chất lượng dữ liệu; phép tính nghiệp vụ được thực hiện ở bước sau.
        if self.business_gate is None:
            return ()
        evidence = self.business_gate.evaluate(deepcopy(tables), deepcopy(manifest))
        if any(i.severity != "warning" for i in evidence.issues):
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

    def _read_manifest_header(self, ref: ObjectRef, snapshot_id: str) -> dict[str, Any]:
        # Kiểm tra nội dung bản kê khi đọc.
        # Chỉ cần đọc thông tin nguồn gốc, không cần tải toàn bộ tệp dữ liệu.
        manifest = json.loads(self.storage.get(ref))
        if (
            manifest["snapshot_id"] != snapshot_id
            or manifest["scope"] != self.scope
            or manifest["mode"] != self.mode
            or manifest["schema_fingerprint"] != self.schema_fingerprint
        ):
            raise SilverError("MANIFEST_CONTRACT_MISMATCH")
        return dict(manifest)

    def _read_manifest(
        self, ref: ObjectRef, snapshot_id: str
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, object]]]]:
        manifest = self._read_manifest_header(ref, snapshot_id)
        if quarantine := manifest.get("quarantine_report"):
            self.storage.get(ObjectRef(**quarantine))
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

    def read_lineage(self, snapshot_id: str, *, max_depth: int = 100) -> SnapshotLineage:
        """Tra cứu nguồn gốc của dữ liệu mới và dữ liệu giữ lại từ các lần công bố trước.

        Chỉ tra cứu các bộ dữ liệu đã công bố, không trả vị trí lưu tệp ra bên ngoài.
        Nếu thiếu thông tin nguồn gốc cũ, ghi nhận là thiếu thay vì tự suy đoán.
        """
        if max_depth < 1:
            raise ValueError("INVALID_LINEAGE_DEPTH")
        current = snapshot_id
        visited: set[str] = set()
        pending: set[str] | None = None
        evidence: list[LineageEvidence] = []
        missing: set[str] = set()
        expected_parent_ref = None
        inherited_objects: dict[str, Any] = {}
        while current:
            if current in visited or len(visited) >= max_depth:
                raise SilverError("LINEAGE_ANCESTRY_LIMIT")
            visited.add(current)
            row = self.repository.snapshot(current, self.scope)
            ref = json.loads(row["manifest_ref"])
            if expected_parent_ref is not None and ref != expected_parent_ref:
                raise SilverError("LINEAGE_PARENT_REFERENCE_MISMATCH")
            manifest = self._read_manifest_header(ObjectRef(**ref), current)
            entities = manifest["entities"]
            if pending is None:
                pending = set(entities)
            for entity, artifact in inherited_objects.items():
                if entity not in entities or entities[entity]["object"] != artifact:
                    raise SilverError("LINEAGE_INHERITED_OBJECT_MISMATCH")
            updated = set(manifest["updated_lineage"])
            selected = pending & updated
            raw = manifest.get("dataset_lineage")
            resolved: set[str] = set()
            if raw is not None:
                item = LineageEvidence.model_validate(raw)
                if (
                    item.run_ref != f"silver:{current}"
                    or item.mode != self.mode
                    or item.parent_snapshot != manifest["parent_snapshot"]
                ):
                    raise SilverError("LINEAGE_RUN_MISMATCH")
                asset_entities = {
                    f"silver:{self.scope}:{self._specs()[entity]['x-owner']}:{entity}": entity
                    for entity in updated
                }
                selected_edges = []
                for edge in item.derivations:
                    output_entity = asset_entities.get(edge.output.asset_id)
                    if (
                        output_entity is None
                        or edge.output.layer != "SILVER"
                        or edge.output.version != current
                        or edge.output.checksum != entities[output_entity]["object"]["sha256"]
                        or edge.output.domain != self._specs()[output_entity]["x-owner"]
                        or edge.output.producer_run_ref not in {None, f"silver:{current}"}
                    ):
                        raise SilverError("LINEAGE_OUTPUT_MISMATCH")
                    if output_entity in selected:
                        resolved.add(output_entity)
                        selected_edges.append(edge)
                if selected_edges:
                    evidence.append(item.model_copy(update={"derivations": tuple(selected_edges)}))
            missing.update(selected - resolved)
            # Bảng được thay mới mà thiếu nguồn gốc không được dùng lại thông tin nguồn gốc cũ.
            pending -= updated
            if not pending:
                break
            current = manifest["parent_snapshot"]
            expected_parent_ref = manifest["parent_manifest"]
            if current and expected_parent_ref is None:
                raise SilverError("LINEAGE_PARENT_REFERENCE_MISSING")
            inherited_objects = {entity: entities[entity]["object"] for entity in pending}
        missing.update(pending or ())
        return SnapshotLineage(
            snapshot_id=snapshot_id,
            evidence=tuple(evidence),
            missing_entities=tuple(sorted(missing)),
        )

    def publish(
        self,
        results: Sequence[IdentityResult],
        *,
        base_snapshot: str | None = None,
        quarantine_report: ObjectRef | None = None,
    ) -> PublicationReceipt:
        if not results:
            raise SilverError("NO_INPUT_BATCHES")
        if quarantine_report is not None:
            if self.mode != "test" or len(results) != 1 or not results[0].accepted:
                raise SilverError("PARTIAL_PUBLICATION_NOT_ALLOWED")
            # Phải đọc được báo cáo cách ly trước khi gắn báo cáo vào bộ dữ liệu.
            self.storage.get(quarantine_report)
        # Không coi thời điểm ghi nhận và mã lô mới là thay đổi của dữ liệu đầu vào.
        inputs: list[dict[str, Any]] = []
        for result in results:
            source = asdict(result.provenance) if result.provenance else None
            if source:
                source.pop("captured_at")
                # Mã lần thu thập chỉ phục vụ tra cứu, không làm thay đổi nội dung dữ liệu.
                # Bỏ thông tin này khi so sánh để vẫn nhận ra các yêu cầu cũ được gửi lại.
                source.pop("ingestion_job_id", None)
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
            # Ghi nhận phiên bản các bước xử lý khi có để nhận biết quy tắc làm sạch đã thay đổi.
            # Dữ liệu cũ chưa qua làm sạch vẫn được nhận diện như trước.
            if result.processing_steps:
                inputs[-1]["processing_steps"] = result.processing_steps
        request = {
            "scope": self.scope,
            "parent": base_snapshot,
            "schema": self.schema_fingerprint,
            "policy": self.policy_json,
            "inputs": sorted(inputs, key=lambda item: checksum(packed(item))),
        }
        if quarantine_report is not None:
            request["quarantine_report_sha256"] = quarantine_report.sha256
        request_hash = checksum(packed(request))
        # Yêu cầu không thay đổi thì dùng lại bộ dữ liệu đã công bố.
        # Không thay bộ dữ liệu hiện hành bằng bộ cũ khi người dùng gửi lại yêu cầu cũ.
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
                # Gộp các lô thuộc cùng một bảng trước khi công bố để giữ đủ dữ liệu của mọi lô.
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
                record_source.pop("ingestion_job_id", None)
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
            if quarantine_report is not None:
                # Gộp các bản ghi đạt yêu cầu và giữ thông tin nguồn gốc của các bản ghi được giữ lại.
                entity = results[0].key.entity
                primary_key = specs[entity]["x-primary-key"]
                incoming_keys = {
                    tuple(row[field] for field in primary_key) for row in updates[entity]
                }
                retained = [
                    row
                    for row in tables.get(entity, ())
                    if tuple(row[field] for field in primary_key) not in incoming_keys
                ]
                retained_ids = {row["_source_record_id"] for row in retained}
                for batch in entities.get(entity, {}).get("lineage", ()):
                    locators = [
                        locator
                        for locator in batch["record_locators"]
                        if locator["source_record_id"] in retained_ids
                    ]
                    if locators:
                        lineage[entity].append({**batch, "record_locators": locators})
                updates[entity] = retained + updates[entity]
            # Kiểm tra toàn bộ dữ liệu sắp công bố, kể cả liên kết với các bảng khác.
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
            if quarantine_report is not None:
                manifest["quarantine_report"] = asdict(quarantine_report)
            # Kiểm tra điều kiện phê duyệt và chất lượng trước khi lưu tệp; nguồn gốc của dữ liệu mới đã được ghi nhận.
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
            manifest["dataset_lineage"] = silver_lineage(
                run_id=run_id,
                scope=self.scope,
                mode=self.mode,
                parent=base_snapshot,
                updated=lineage,
                entities=entities,
            )
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
            # Nếu không lưu được trạng thái, giữ nguyên thông tin lần chạy để đối chiếu sau.
            with suppress(Exception):
                self.repository.fail(run_id, [asdict(i) for i in issues])
            if isinstance(exc, SilverError):
                raise
            raise SilverError("STORAGE_OR_METADATA_FAILURE") from exc

    def resume(self, run_id: str) -> PublicationReceipt:
        # Khi tiếp tục lần chạy dở dang, phải đọc lại tệp và kiểm tra đầy đủ trước khi công bố.
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
