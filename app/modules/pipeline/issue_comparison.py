"""Đối chiếu dữ liệu trước và sau khi sửa cho Data Admin theo business key."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zipfile import BadZipFile

from minio.error import MinioException
from starlette.concurrency import run_in_threadpool
from urllib3.exceptions import HTTPError

from app.core.config import settings
from app.modules.pipeline.quarantine_records import SourceReadLimitError, read_bronze_snapshot
from app.modules.pipeline.source_profiles import SourceProfile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.modules.ingestion.contracts.readers import ReadResult

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOMAIN_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
_PUBLISHED_STATUSES = frozenset({"PUBLISHED", "PUBLISHED_WITH_QUARANTINE"})


@dataclass(frozen=True)
class _SnapshotLoad:
    parsed: ReadResult | None = None
    error_reason: str | None = None


@lru_cache(maxsize=16)
def _schema_primary_key(domain: str, entity: str) -> tuple[str, ...]:
    if not _DOMAIN_PATTERN.fullmatch(domain):
        return ()
    data_schema_path = _REPO_ROOT / "docs" / "data-schema" / f"{domain}_v1.schema.json"
    if data_schema_path.is_file():
        payload = json.loads(data_schema_path.read_text(encoding="utf-8"))
        spec = payload.get("entities", {}).get(entity)
        if isinstance(spec, dict) and isinstance(spec.get("primary_key"), list):
            fields = tuple(f for f in spec["primary_key"] if isinstance(f, str) and f.strip())
            if fields:
                return fields

    silver_schema_path = (
        _REPO_ROOT / "docs" / "standardization" / "silver-schemas" / f"{domain}.json"
    )
    if silver_schema_path.is_file():
        payload = json.loads(silver_schema_path.read_text(encoding="utf-8"))
        spec = payload.get("$defs", {}).get(entity)
        if isinstance(spec, dict) and isinstance(spec.get("x-primary-key"), list):
            fields = tuple(f for f in spec["x-primary-key"] if isinstance(f, str) and f.strip())
            if fields:
                return fields
    return ()


def resolve_business_key_fields(
    *,
    domain: str,
    entity: str,
    dataset_id: int,
    data_source_id: int | None = None,
) -> tuple[str, ...]:
    """Xác định danh sách cột business key theo hợp đồng dữ liệu hoặc SourceProfile."""
    profiles_path = Path(settings.silver_profiles_path)
    if profiles_path.is_file():
        try:
            raw_profiles = json.loads(profiles_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw_profiles = {}
        if isinstance(raw_profiles, dict):
            for raw_profile in raw_profiles.values():
                if not isinstance(raw_profile, dict):
                    continue
                if (
                    raw_profile.get("dataset_id") != dataset_id
                    or raw_profile.get("entity") != entity
                    or raw_profile.get("domain") != domain
                    or (
                        data_source_id is not None
                        and raw_profile.get("data_source_id") != data_source_id
                    )
                ):
                    continue
                try:
                    profile = SourceProfile.model_validate(raw_profile)
                except ValueError:
                    continue
                pk = profile.source_schema.get("entities", {}).get(entity, {}).get("primary_key")
                if isinstance(pk, list) and all(
                    isinstance(field, str) and field in profile.field_mapping for field in pk
                ):
                    mapped = tuple(profile.field_mapping[field] for field in pk)
                    if mapped:
                        return mapped
    return _schema_primary_key(domain, entity)


def _normalize_key_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    text = str(value).strip()
    return text if text else None


def _extract_business_key(
    row: Mapping[str, Any], key_fields: tuple[str, ...]
) -> tuple[tuple[str, ...] | None, dict[str, Any] | None]:
    if not key_fields or any(field not in row for field in key_fields):
        return None, None
    normalized: list[str] = []
    display: dict[str, Any] = {}
    for field in key_fields:
        raw_val = row.get(field)
        norm_val = _normalize_key_value(raw_val)
        if norm_val is None:
            return None, None
        normalized.append(norm_val)
        display[field] = raw_val
    return tuple(normalized), display


def _classify_read_error(exc: Exception) -> str:
    if isinstance(exc, SourceReadLimitError):
        return "BRONZE_SOURCE_TOO_LARGE"
    text = str(exc)
    if "VERSION_MISMATCH" in text:
        return "BRONZE_VERSION_MISMATCH"
    if "CHECKSUM_MISMATCH" in text:
        return "BRONZE_CHECKSUM_MISMATCH"
    if "LOCATOR_MISMATCH" in text:
        return "BRONZE_LOCATOR_MISMATCH"
    return "BRONZE_SOURCE_UNAVAILABLE"


def _build_source_from_job(
    *,
    output: dict[str, Any] | None,
    upload_input: dict[str, Any] | None,
    source_table: str | None,
    source_system_id: int | None,
    data_source_id: int | None,
    dataset_id: int,
    fallback_source_type: str | None,
    fallback_configuration: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(output, dict):
        return None
    source_type = output.get("source_type") or fallback_source_type
    parser_options = output.get("parser_options")
    if not isinstance(parser_options, dict) and isinstance(upload_input, dict):
        parser_options = upload_input.get("parser_options")
    if not isinstance(parser_options, dict) and isinstance(fallback_configuration, dict):
        parser_options = fallback_configuration
    if source_system_id is None or data_source_id is None or not source_table:
        return None
    return {
        "uri": output.get("uri"),
        "version_id": output.get("version_id"),
        "sha256": output.get("sha256"),
        "size_bytes": output.get("size_bytes"),
        "source_type": source_type,
        "file_name": source_table,
        "parser_options": parser_options,
        "source_system_id": source_system_id,
        "data_source_id": data_source_id,
        "dataset_id": dataset_id,
    }


async def _load_snapshot(
    source: dict[str, Any] | None,
    *,
    expected_bronze_version: str | None,
    require_fingerprints: bool,
) -> _SnapshotLoad:
    if not isinstance(source, dict):
        return _SnapshotLoad(error_reason="BRONZE_PROVENANCE_MISSING")
    if expected_bronze_version and source.get("version_id") != expected_bronze_version:
        return _SnapshotLoad(error_reason="BRONZE_VERSION_MISMATCH")
    try:
        parsed = await run_in_threadpool(
            read_bronze_snapshot,
            source,
            require_provenance_fingerprints=require_fingerprints,
        )
    except (
        SourceReadLimitError,
        MinioException,
        HTTPError,
        OSError,
        ValueError,
        BadZipFile,
        TimeoutError,
    ) as exc:
        return _SnapshotLoad(error_reason=_classify_read_error(exc))
    return _SnapshotLoad(parsed=parsed)


def _rows_with_locators(parsed: ReadResult) -> list[tuple[int, str, dict[str, Any]]]:
    return [
        (position, locator, dict(zip(parsed.columns, row, strict=True)))
        for position, (locator, row) in enumerate(
            zip(parsed.record_locators, parsed.rows, strict=True), start=1
        )
    ]


def _filter_allowed(
    row: Mapping[str, Any] | None, allowed_columns: set[str] | None
) -> dict[str, Any] | None:
    if row is None:
        return None
    if allowed_columns:
        return {key: value for key, value in row.items() if key in allowed_columns}
    return dict(row)


def _aware_utc(value: Any) -> Any:
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _changed_fields(
    old_values: Mapping[str, Any] | None, new_values: Mapping[str, Any] | None
) -> list[str]:
    if old_values is None or new_values is None:
        return []
    columns = list(dict.fromkeys([*old_values.keys(), *new_values.keys()]))
    return [col for col in columns if old_values.get(col) != new_values.get(col)]


async def build_issue_comparison(
    *,
    issue: dict[str, Any],
    old_records: list[dict[str, Any]],
    new_run: dict[str, Any],
    new_quarantine_records: list[dict[str, Any]],
    source_metadata_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Xây dựng báo cáo đối chiếu trước - sau cho từng hồ sơ lỗi của yêu cầu."""
    entity = str(new_run["entity"])
    domain = str(issue["domain"])
    dataset_id = int(issue["dataset_id"])
    new_data_source_id = new_run.get("data_source_id")
    key_fields = resolve_business_key_fields(
        domain=domain,
        entity=entity,
        dataset_id=dataset_id,
        data_source_id=new_data_source_id if isinstance(new_data_source_id, int) else None,
    )

    # Nhóm bản ghi cũ theo old_cleaning_run_id để chỉ tải và đọc mỗi phiên bản Bronze một lần.
    old_loads: dict[str, _SnapshotLoad] = {}
    allowed_by_old_run: dict[str, set[str] | None] = {}
    for rec in old_records:
        old_run_id = str(rec["cleaning_run_id"])
        if old_run_id in old_loads:
            continue
        source = rec.get("quarantine_source")
        require_fp = isinstance(source, dict)
        if not isinstance(source, dict):
            ds_meta = source_metadata_by_id.get(rec.get("data_source_id", -1), {})
            source = _build_source_from_job(
                output=rec.get("job_output"),
                upload_input=rec.get("job_upload_input"),
                source_table=rec.get("source_table"),
                source_system_id=ds_meta.get("source_system_id"),
                data_source_id=rec.get("data_source_id"),
                dataset_id=dataset_id,
                fallback_source_type=ds_meta.get("source_type"),
                fallback_configuration=ds_meta.get("configuration"),
            )
        raw_allowed = source.get("allowed_columns") if isinstance(source, dict) else None
        allowed_by_old_run[old_run_id] = (
            {str(col) for col in raw_allowed if isinstance(col, str)}
            if isinstance(raw_allowed, list) and raw_allowed
            else None
        )
        old_loads[old_run_id] = await _load_snapshot(
            source,
            expected_bronze_version=rec.get("bronze_version"),
            require_fingerprints=require_fp,
        )

    # Tải Bronze snapshot của lượt Cleaning mới (từ quarantine_source nếu có hoặc từ ingestion_job.output).
    new_source = new_run.get("quarantine_source")
    require_new_fp = isinstance(new_source, dict)
    if not isinstance(new_source, dict):
        ds_meta = source_metadata_by_id.get(new_run.get("data_source_id", -1), {})
        new_source = _build_source_from_job(
            output=new_run.get("job_output"),
            upload_input=new_run.get("job_upload_input"),
            source_table=new_run.get("source_table"),
            source_system_id=ds_meta.get("source_system_id"),
            data_source_id=new_run.get("data_source_id"),
            dataset_id=dataset_id,
            fallback_source_type=ds_meta.get("source_type"),
            fallback_configuration=ds_meta.get("configuration"),
        )
    else:
        job_output = new_run.get("job_output")
        if (
            isinstance(job_output, dict)
            and isinstance(job_output.get("version_id"), str)
            and job_output.get("version_id") != new_run.get("bronze_version")
        ):
            new_source = {**new_source, "version_id": job_output.get("version_id")}

    new_load = await _load_snapshot(
        new_source,
        expected_bronze_version=new_run.get("bronze_version"),
        require_fingerprints=require_new_fp,
    )

    # Chuẩn bị chỉ mục business key cho Bronze cũ và mới (tuyệt đối không dùng số dòng CSV làm khóa).
    old_row_by_locator: dict[str, dict[str, tuple[int, dict[str, Any]]]] = {}
    old_key_counts: dict[str, Counter[tuple[str, ...]]] = {}
    for old_run_id, load in old_loads.items():
        if load.parsed is None:
            continue
        rows_map: dict[str, tuple[int, dict[str, Any]]] = {}
        counts: Counter[tuple[str, ...]] = Counter()
        for position, locator, row_dict in _rows_with_locators(load.parsed):
            rows_map[locator] = (position, row_dict)
            if key_fields and all(field in load.parsed.columns for field in key_fields):
                norm_key, _ = _extract_business_key(row_dict, key_fields)
                if norm_key is not None:
                    counts[norm_key] += 1
        old_row_by_locator[old_run_id] = rows_map
        old_key_counts[old_run_id] = counts

    new_rows_by_key: dict[tuple[str, ...], list[tuple[int, str, dict[str, Any]]]] = defaultdict(
        list
    )
    new_has_key_columns = False
    if new_load.parsed is not None and key_fields:
        new_has_key_columns = all(field in new_load.parsed.columns for field in key_fields)
        if new_has_key_columns:
            for position, locator, row_dict in _rows_with_locators(new_load.parsed):
                norm_key, _ = _extract_business_key(row_dict, key_fields)
                if norm_key is not None:
                    new_rows_by_key[norm_key].append((position, locator, row_dict))

    new_quarantine_by_locator: dict[str, dict[str, Any]] = {
        str(row["locator"]): row for row in new_quarantine_records
    }

    items: list[dict[str, Any]] = []
    counts_by_status: Counter[str] = Counter()
    cleaning_completed = new_run.get("cleaning_status") == "COMPLETED"
    silver_published = new_run.get("publication_status") in _PUBLISHED_STATUSES
    new_bronze_version = new_run.get("bronze_version")

    for rec in old_records:
        old_run_id = str(rec["cleaning_run_id"])
        old_load = old_loads[old_run_id]
        allowed_cols = allowed_by_old_run[old_run_id]
        old_locator = str(rec["locator"])
        old_pos = int(rec["record_position"])
        old_bronze_version = str(rec["bronze_version"])
        orig_codes = list(rec.get("error_codes") or [])
        orig_issues = list(rec.get("issues") or [])

        base_item: dict[str, Any] = {
            "record_id": rec["record_id"],
            "business_key_fields": list(key_fields),
            "business_key": None,
            "old_locator": old_locator,
            "old_record_position": old_pos,
            "old_bronze_version": old_bronze_version,
            "new_locator": None,
            "new_record_position": None,
            "new_bronze_version": new_bronze_version,
            "original_error_codes": orig_codes,
            "original_issues": orig_issues,
            "remaining_error_codes": [],
            "remaining_issues": [],
            "old_values": None,
            "new_values": None,
            "changed_fields": [],
        }

        if old_load.parsed is None:
            status, reason = "MANUAL_REVIEW_REQUIRED", old_load.error_reason
        else:
            located = old_row_by_locator.get(old_run_id, {}).get(old_locator)
            if located is None or located[0] != old_pos:
                status, reason = "MANUAL_REVIEW_REQUIRED", "BRONZE_LOCATOR_MISMATCH"
            else:
                old_row = located[1]
                base_item["old_values"] = _filter_allowed(old_row, allowed_cols)
                if not key_fields or not all(field in old_row for field in key_fields):
                    status, reason = "MANUAL_REVIEW_REQUIRED", "BUSINESS_KEY_UNAVAILABLE"
                else:
                    norm_key, key_display = _extract_business_key(old_row, key_fields)
                    base_item["business_key"] = key_display
                    if norm_key is None:
                        status, reason = "MANUAL_REVIEW_REQUIRED", "BUSINESS_KEY_MISSING"
                    elif old_key_counts[old_run_id][norm_key] > 1:
                        status, reason = (
                            "MANUAL_REVIEW_REQUIRED",
                            "DUPLICATE_BUSINESS_KEY_IN_OLD_BRONZE",
                        )
                    elif new_load.parsed is None:
                        status, reason = "MANUAL_REVIEW_REQUIRED", new_load.error_reason
                    elif not new_has_key_columns:
                        status, reason = "MANUAL_REVIEW_REQUIRED", "BUSINESS_KEY_UNAVAILABLE"
                    else:
                        matches = new_rows_by_key.get(norm_key, [])
                        if not matches:
                            status, reason = "UNMATCHED", "KEY_NOT_FOUND_IN_NEW_BRONZE"
                        elif len(matches) > 1:
                            status = "MANUAL_REVIEW_REQUIRED"
                            reason = "DUPLICATE_BUSINESS_KEY_IN_NEW_BRONZE"
                            dup_codes: set[str] = set()
                            dup_issues: list[dict[str, Any]] = []
                            for _, dup_locator, _ in matches:
                                q_row = new_quarantine_by_locator.get(dup_locator)
                                if q_row is not None:
                                    dup_codes.update(q_row.get("error_codes") or [])
                                    dup_issues.extend(q_row.get("issues") or [])
                            base_item["remaining_error_codes"] = sorted(dup_codes)
                            base_item["remaining_issues"] = dup_issues
                        else:
                            new_pos, new_locator, new_row = matches[0]
                            base_item["new_locator"] = new_locator
                            base_item["new_record_position"] = new_pos
                            filtered_new = _filter_allowed(new_row, allowed_cols)
                            base_item["new_values"] = filtered_new
                            changed = _changed_fields(base_item["old_values"], filtered_new)
                            base_item["changed_fields"] = changed
                            new_q = new_quarantine_by_locator.get(new_locator)
                            if new_q is not None:
                                status = "STILL_INVALID"
                                reason = "NEW_QUARANTINE_ERRORS"
                                base_item["remaining_error_codes"] = list(
                                    new_q.get("error_codes") or []
                                )
                                base_item["remaining_issues"] = list(new_q.get("issues") or [])
                            elif not cleaning_completed:
                                status, reason = "MANUAL_REVIEW_REQUIRED", "CLEANING_NOT_COMPLETED"
                            elif not silver_published:
                                status, reason = "MANUAL_REVIEW_REQUIRED", "SILVER_NOT_PUBLISHED"
                            elif not changed:
                                status = "MANUAL_REVIEW_REQUIRED"
                                reason = "NO_FIELD_VALUE_CHANGED"
                            else:
                                status, reason = "RESOLVED", None

        base_item["status"] = status
        base_item["reason"] = reason
        counts_by_status[status] += 1
        items.append(base_item)

    return {
        "issue_id": issue["issue_id"],
        "dataset_id": dataset_id,
        "domain": domain,
        "entity": entity,
        "issue_status": issue["status"],
        "resolution_job_id": issue.get("resolution_job_id"),
        "cleaning_run": {
            "cleaning_run_id": new_run["run_id"],
            "ingestion_job_id": new_run["ingestion_job_id"],
            "dataset_id": new_run["dataset_id"],
            "domain": new_run["domain"],
            "entity": new_run["entity"],
            "bronze_version": new_run["bronze_version"],
            "cleaning_status": new_run["cleaning_status"],
            "publication_status": new_run["publication_status"],
            "records_read": new_run.get("records_read"),
            "records_passed": new_run.get("records_passed"),
            "records_quarantined": new_run.get("records_quarantined"),
            "records_collapsed": new_run.get("records_collapsed"),
            "records_published": int(new_run.get("records_published") or 0),
            "silver_snapshot_id": new_run.get("silver_snapshot_id"),
            "started_at": _aware_utc(new_run["started_at"]),
            "finished_at": _aware_utc(new_run.get("finished_at")),
        },
        "business_key_fields": list(key_fields),
        "summary": {
            "total": len(items),
            "resolved": counts_by_status["RESOLVED"],
            "still_invalid": counts_by_status["STILL_INVALID"],
            "unmatched": counts_by_status["UNMATCHED"],
            "manual_review_required": counts_by_status["MANUAL_REVIEW_REQUIRED"],
        },
        "records": items,
        "manual_verification_required": True,
        "note": (
            "Bảng đối chiếu chỉ hỗ trợ Data Admin kiểm tra trước khi xác minh thủ công; "
            "trạng thái yêu cầu chỉ chuyển VERIFIED qua bước xác minh."
        ),
    }
