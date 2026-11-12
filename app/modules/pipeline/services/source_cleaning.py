"""Làm sạch và lưu dữ liệu Bronze vào Silver theo cấu hình của nguồn thực tế."""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.ingestion.contracts import MappingBinding
from app.modules.ingestion.contracts.readers import (
    ReadRequest,
    SourceReaderFactory,
    read_file_snapshot,
    reader_to_batch,
)
from app.modules.pipeline.cleaning.duplicate_records import build_duplicate_profile
from app.modules.pipeline.cleaning.format_validation import (
    SourceFormatOptions,
    build_format_profile,
)
from app.modules.pipeline.cleaning.missing_values import MissingValueProfile, MissingValueRule
from app.modules.pipeline.cleaning.partial_safety import partial_key_evidence
from app.modules.pipeline.cleaning.pipeline import (
    CleaningPipeline,
    CleaningTask,
)
from app.modules.pipeline.cleaning.quarantine import (
    collect_standardization_issues,
    quarantine_source,
    save_quarantine_records,
)
from app.modules.pipeline.repositories.cleaning_runs import CleaningRunRepository
from app.modules.pipeline.repositories.identity import CrosswalkRepository
from app.modules.pipeline.repositories.silver import SilverRepository
from app.modules.pipeline.repositories.silver_storage import MinioSilverStore, SilverError, checksum
from app.modules.pipeline.services.source_profiles import SourceProfile, _tuples
from app.modules.pipeline.silver.lineage import record_pipeline_lineage_sync
from app.modules.pipeline.silver.pipeline import NormalizationTask, SilverPipeline
from app.modules.pipeline.silver.publication import SilverPublicationService
from app.providers import silver_writer_client, source_reader_client
from app.repositories.silver_governance_repo import (
    approval_required,
    approved_evidence,
    recheck_approvals,
    reject,
)
from app.repositories.silver_profile_repo import load_source_profile

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

    from app.modules.pipeline.standardization.identity_standardization import (
        IdentityResult,
        IdentityStandardizationService,
    )
    from app.modules.pipeline.standardization.schema_mapping import SchemaMappingService

logger = logging.getLogger(__name__)


def _job_context(engine: Any, ingestion_job_id: int) -> dict[str, Any]:
    with engine.begin() as connection:
        row = (
            connection.execute(
                text("""
                SELECT j.ingestion_job_id,j.status,j.job_type,j.output,j.source_table,
                       j.rows_read,j.data_source_id,j.dataset_id,j.root_job_id,j.upload_input,
                       ds.source_type,
                       ds.configuration,ds.location,s.source_system_id,s.source_code,
                       d.domain,d.dataset_code,d.source_system_id AS dataset_source_id,
                       l.layer_code, ds.is_active AS source_active,
                       s.is_active AS system_active, d.status AS dataset_status
                FROM app.ingestion_job j
                JOIN app.data_source ds ON ds.data_source_id=j.data_source_id
                JOIN app.source_system s ON s.source_system_id=ds.source_system_id
                JOIN app.dataset d ON d.dataset_id=j.dataset_id
                JOIN app.data_layer l ON l.data_layer_id=j.target_layer_id
                WHERE j.ingestion_job_id=:job_id
                """),
                {"job_id": ingestion_job_id},
            )
            .mappings()
            .first()
        )
    if row is None:
        raise LookupError("INGESTION_JOB_NOT_FOUND")
    context = dict(row)
    if (
        not context["source_active"]
        or not context["system_active"]
        or context["dataset_status"] != "ACTIVE"
    ):
        raise ValueError("SOURCE_OR_DATASET_INACTIVE")
    output = context["output"]
    if isinstance(output, dict) and output.get("reused_bronze"):
        parts = urlsplit(output.get("uri", "")).path.lstrip("/").split("/")
        match = re.fullmatch(r"job-([1-9][0-9]*)", parts[4]) if len(parts) == 6 else None
        if match is None:
            raise ValueError("BRONZE_REUSE_ORIGIN_REQUIRED")
        with engine.begin() as connection:
            original = (
                connection.execute(
                    text("""
                    SELECT ingestion_job_id,root_job_id,job_type,data_source_id,dataset_id,
                           source_table,upload_input,bronze_output
                    FROM app.ingestion_job WHERE ingestion_job_id=:job_id
                    """),
                    {"job_id": int(match.group(1))},
                )
                .mappings()
                .first()
            )
        context["bronze_ingestion_job_id"] = _validate_reused_bronze(
            context, dict(original) if original is not None else None
        )
    _validate_job_context(context)
    return context


def _validate_reused_bronze(context: dict[str, Any], original: dict[str, Any] | None) -> int:
    """Chỉ nhận lại file từ một lần chạy cùng chuỗi, cùng bộ dữ liệu và cùng file đầu vào."""
    if (
        original is None
        or context["job_type"] != "UPLOAD_V1"
        or original["job_type"] != "UPLOAD_V1"
        or (original["root_job_id"] or original["ingestion_job_id"])
        != (context["root_job_id"] or context["ingestion_job_id"])
        or any(
            original[field] != context[field]
            for field in ("data_source_id", "dataset_id", "source_table", "upload_input")
        )
        or not isinstance(original["upload_input"], dict)
        or not isinstance(original["bronze_output"], dict)
        or any(
            original["bronze_output"].get(field) != context["output"].get(field)
            for field in ("uri", "version_id", "sha256", "size_bytes", "source_version_id")
        )
    ):
        raise ValueError("BRONZE_REUSE_ORIGIN_MISMATCH")
    return int(original["ingestion_job_id"])


def _validate_job_context(context: dict[str, Any]) -> None:
    if context["status"] != "SUCCESS" or context["job_type"] not in {"EXECUTION_V1", "UPLOAD_V1"}:
        raise ValueError("SUCCESSFUL_BRONZE_JOB_REQUIRED")
    domain = context["domain"]
    if (
        context["dataset_source_id"] != context["source_system_id"]
        or context["layer_code"] != "BRONZE"
    ):
        raise ValueError("BRONZE_DATASET_SOURCE_MISMATCH")
    if context["source_type"] not in {"CSV", "EXCEL"}:
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    source_table = context["source_table"]
    extensions = (".xlsx", ".xlsm") if context["source_type"] == "EXCEL" else (".csv",)
    if not isinstance(source_table, str) or not source_table.lower().endswith(extensions):
        raise ValueError("BRONZE_SOURCE_FILE_TYPE_MISMATCH")
    # File thuộc lần nạp này hoặc lần trước đã được đối chiếu trong cùng chuỗi chạy lại.
    output = context["output"]
    if not isinstance(output, dict):
        raise ValueError("BRONZE_JOB_OUTPUT_REQUIRED")
    uri = urlsplit(output.get("uri", ""))
    object_parts = uri.path.lstrip("/").split("/")
    if (
        uri.scheme != "s3"
        or uri.netloc != settings.minio_bucket_bronze
        or uri.query
        or uri.fragment
        or len(object_parts) != 6
        or object_parts[0] != domain
        or object_parts[4]
        != f"job-{context.get('bronze_ingestion_job_id', context['ingestion_job_id'])}"
        or object_parts[-1] != source_table
    ):
        raise ValueError("BRONZE_JOB_OUTPUT_INVALID_OR_UNSUPPORTED")
    if (
        not isinstance(output.get("version_id"), str)
        or not output["version_id"]
        or output["version_id"] == "null"
        or not isinstance(output.get("sha256"), str)
        or re.fullmatch(r"[0-9a-fA-F]{64}", output["sha256"]) is None
        or type(output.get("size_bytes")) is not int
        or output["size_bytes"] < 0
        or type(context["rows_read"]) is not int
        or context["rows_read"] < 0
    ):
        raise ValueError("BRONZE_JOB_EVIDENCE_REQUIRED")
    context["object_key"] = "/".join(object_parts)


def _read_bronze(
    context: dict[str, Any],
    profile: SourceProfile,
) -> CleaningTask:
    output = context["output"]
    source_type = context["source_type"]
    # Đọc đúng phiên bản tệp được ghi nhận khi thu thập, kể cả khi đã có phiên bản mới hơn.
    request = ReadRequest(
        source_type=source_type,
        location=output["uri"],
        configuration=context["parser_options"],
        source_table=context["source_table"],
        object_version_id=output["version_id"],
    )
    object_stat = source_reader_client(settings.minio_bucket_bronze).stat_object(
        settings.minio_bucket_bronze,
        context["object_key"],
        version_id=output["version_id"],
    )
    if object_stat.version_id != output["version_id"] or object_stat.size != output["size_bytes"]:
        raise ValueError("BRONZE_JOB_OBJECT_EVIDENCE_MISMATCH")
    parsed = read_file_snapshot(
        request,
        SourceReaderFactory.get_reader(source_type),
        source_system_id=str(context["source_system_id"]),
        data_source_id=str(context["data_source_id"]),
        dataset_id=str(context["dataset_id"]),
    )
    # Đối chiếu mã kiểm tra nội dung và số dòng với thông tin của lần thu thập.
    provenance = parsed.provenance
    if (
        provenance is None
        or provenance.content_sha256.lower() != output["sha256"].lower()
        or provenance.object_version_id != output["version_id"]
        or parsed.rows_read != context["rows_read"]
    ):
        raise ValueError("BRONZE_JOB_CAPTURE_MISMATCH")

    parsed = replace(
        parsed,
        provenance=replace(provenance, ingestion_job_id=context["ingestion_job_id"]),
    )

    key = profile.key()
    binding = MappingBinding(
        key.source_system_id,
        key.entity,
        key.source_schema_version,
        key.mapping_id,
        key.mapping_version,
        key.target_schema_version,
    )
    batch = reader_to_batch(
        parsed,
        batch_id=f"job-{context['ingestion_job_id']}-{uuid4().hex}",
        domain_id=profile.domain,
        source_type=source_type,
        binding=binding,
    )
    expected = set(profile.field_mapping.values()) | set(profile.ignored_columns)
    if set(parsed.columns) != expected:
        raise ValueError("SOURCE_LAYOUT_MISMATCH")
    entity_schema = profile.source_schema["entities"][profile.entity]
    options = {
        name: SourceFormatOptions(**_tuples(values))
        for name, values in profile.format_options.items()
    }
    missing = MissingValueProfile(
        profile.domain,
        profile.entity,
        profile.rule_version,
        tuple(profile.field_mapping.values()),
        tuple(
            MissingValueRule(
                profile.field_mapping[column["name"]],
                nullable=column["nullable"],
                null_tokens=tuple(profile.null_tokens.get(column["name"], [""])),
            )
            for column in entity_schema["columns"]
            if column["name"] in profile.field_mapping
        ),
    )
    cleaning = CleaningTask(
        batch=batch,
        missing_profile=missing,
        format_profile=build_format_profile(
            profile.source_schema,
            domain=profile.domain,
            entity=profile.entity,
            version=profile.rule_version,
            target_to_source=profile.field_mapping,
            source_options=options,
        ),
        duplicate_profile=build_duplicate_profile(
            profile.source_schema,
            domain=profile.domain,
            entity=profile.entity,
            version=profile.rule_version,
            target_to_source=profile.field_mapping,
            collapse_exact_primary_key_duplicates=profile.collapse_exact_duplicates,
        ),
    )
    return cleaning


def _identity_service(
    engine: Any, profile: SourceProfile
) -> tuple[SchemaMappingService, IdentityStandardizationService]:
    with engine.begin() as connection:
        crosswalk = CrosswalkRepository(connection).load(profile.crosswalk_release)
    return profile.services(crosswalk)


def _bind_profile(context: dict[str, Any], profile: SourceProfile) -> None:
    for field in (
        "data_source_id",
        "dataset_id",
        "source_system_id",
        "source_type",
        "source_table",
        "domain",
    ):
        if context[field] != getattr(profile, field):
            raise ValueError("SILVER_PROFILE_SOURCE_MISMATCH")
    output = context["output"]
    if output.get("source_type") != profile.source_type or not isinstance(
        output.get("parser_options"), dict
    ):
        raise ValueError("BRONZE_PARSER_EVIDENCE_REQUIRED")
    context["parser_options"] = output["parser_options"]
    context["configuration"] = output["parser_options"]


def run_manual_cleaning(
    ingestion_job_id: int,
    *,
    requested_by: str,
    profile_id: str,
    base_snapshot: str | None = "active",
    # [SỬA - Vấn đề 1] Cho phép xuất bản riêng các dòng sạch khi tệp vẫn còn dòng bị cách ly.
    allow_partial_publication: bool = True,
) -> dict[str, Any]:
    """Làm sạch và lưu vào Silver từ một lần thu thập thành công, dùng quy tắc của nguồn đã khai báo."""
    if base_snapshot is not None and (
        not isinstance(base_snapshot, str) or not base_snapshot.strip()
    ):
        raise ValueError("INVALID_MANUAL_CLEANING_OPTIONS")
    if type(allow_partial_publication) is not bool:
        raise ValueError("INVALID_MANUAL_CLEANING_OPTIONS")
    if type(ingestion_job_id) is not int or ingestion_job_id <= 0:
        raise ValueError("INVALID_INGESTION_JOB_ID")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        ),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
    repository = CleaningRunRepository(engine)
    run_id = None
    cleaning_completed = False
    publication_committed = False
    try:
        profile = load_source_profile(engine, settings.silver_profiles_path, profile_id)
        approvals: list[dict[str, Any]] = []
        if approval_required():
            with engine.begin() as connection:
                approvals.append(approved_evidence(connection, profile_id, profile.fingerprint))
        context = _job_context(engine, ingestion_job_id)
        _bind_profile(context, profile)
        mapping, identity = _identity_service(engine, profile)
        run_id = repository.start(
            context, entity=profile.entity, actor=requested_by, session=profile.scope
        )
        repository.update(
            run_id,
            profile_evidence={
                "profile_id": profile_id,
                "fingerprint": profile.fingerprint,
                "rule_version": profile.rule_version,
                "definition": profile.model_dump(mode="json"),
                "approval": approvals[0]
                if approvals
                else {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"},
            },
        )
        cleaning = _read_bronze(context, profile)
        cleaned = CleaningPipeline().clean(cleaning)
        report = cleaned.report

        # [SỬA - Vấn đề 2] Chạy trước các bước chuẩn hóa (S2 Mapping -> S3 Type -> S4 Identity/Dictionary)
        # ngay tại đây để phát hiện và gom các dòng bị loại ở bước chuẩn hóa vào cùng hồ sơ quarantine.
        standardized: IdentityResult | None = None
        std_rejected: set[str] = set()
        std_issues: list[dict[str, Any]] = []
        if cleaned.batch.records:
            mapped = mapping.map_batch(cleaned.batch)
            typed = identity.types.standardize(mapped)
            standardized = identity.standardize(typed)
            std_rejected = set(standardized.rejected_locators)
            std_issues = [
                item
                for issues in collect_standardization_issues(
                    standardized, field_mapping=profile.field_mapping
                ).values()
                for item in issues
            ]

        total_quarantined = report.records_quarantined + len(std_rejected)
        total_passed = report.records_passed - len(std_rejected)
        counts = {
            "records_read": report.records_read,
            "records_passed": total_passed,
            "records_quarantined": total_quarantined,
            "records_collapsed": report.records_collapsed,
        }
        # Giữ số liệu làm sạch và chuẩn hóa kể cả khi bước công bố Silver thất bại ở bước sau.
        with engine.begin() as connection:
            # Lưu đầy đủ thông tin lần chạy và hồ sơ quarantine của cả Cleaning + Standardization.
            evidence = None
            if total_quarantined:
                evidence = {
                    **quarantine_source(context, cleaning.batch),
                    "allowed_columns": profile.source_preview_columns,
                }
                save_quarantine_records(
                    connection,
                    run_id,
                    cleaning.batch,
                    cleaned,
                    standardized=standardized,
                    field_mapping=profile.field_mapping,
                )
            repository.update(
                run_id,
                connection=connection,
                cleaning_status="COMPLETED",
                input_version=report.input_version,
                quarantine_source=evidence,
                **counts,
            )
        cleaning_completed = True
        result: dict[str, Any] = {
            "status": "CLEANED",
            "run_id": run_id,
            "ingestion_job_id": ingestion_job_id,
            "requested_by": requested_by,
            "domain": report.domain,
            "entity": report.entity,
            "input_version": report.input_version,
            **counts,
            "issues": [*[asdict(issue) for issue in report.issues], *std_issues],
            "publication": None,
            "quarantine_report": None,
            "profile_id": profile_id,
            "profile_fingerprint": profile.fingerprint,
            "profile_approval": approvals[0]
            if approvals
            else {"status": "NEEDS_DOMAIN_OWNER_APPROVAL"},
        }
        if total_quarantined and approval_required():
            result["partial_key_check"] = partial_key_evidence(
                cleaning, set(cleaned.quarantined_locators) | std_rejected
            )
        # Các bảng liên quan dùng chung phạm vi để kiểm tra liên kết trước khi công bố.
        scope = profile.scope
        storage = MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver)
        data = json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
        ref = storage.put(f"silver/reports/{run_id}/report-{checksum(data)}.json", data)
        storage.get(ref)  # Kiểm tra ghi thành công
        repository.update(run_id, report_ref=asdict(ref))  # Lưu reference của report vào database
        if total_quarantined:
            # [SỬA - Vấn đề 1] Trả về tham chiếu báo cáo cách ly để người gọi/manifest đối chiếu được.
            result["quarantine_report"] = asdict(ref)

        # [SỬA - Vấn đề 2] Loại bỏ các dòng đã rớt ở S2/S3/S4 khỏi batch sạch trước khi đưa vào
        # SilverPublicationService, để lớp publication nhận tập 100% hợp lệ và giữ nguyên invariant.
        publishable_batch = (
            replace(
                cleaned.batch,
                records=tuple(
                    record
                    for record, locator in zip(
                        cleaned.batch.records, cleaned.batch.record_locators, strict=True
                    )
                    if locator not in std_rejected
                ),
                record_locators=tuple(
                    locator
                    for locator in cleaned.batch.record_locators
                    if locator not in std_rejected
                ),
            )
            if std_rejected
            else cleaned.batch
        )

        # [SỬA - Vấn đề 1 & 2] Chỉ chặn công bố khi:
        # 1) Có dòng bị cách ly (ở Cleaning hoặc Standardization) VÀ allow_partial_publication=False, HOẶC
        # 2) Có dòng bị cách ly VÀ không còn dòng sạch nào để công bố (toàn bộ tệp đều lỗi).
        key_blocked = result.get("partial_key_check", {}).get("status") == "BLOCKED"
        if total_quarantined and (
            not allow_partial_publication or not publishable_batch.records or key_blocked
        ):
            repository.update(
                run_id,
                publication_status="BLOCKED",
                finished_at=datetime.now(UTC),
                error_code="SILVER_PARTIAL_KEY_SAFETY_BLOCKED" if key_blocked else None,
            )
            result["status"] = "QUARANTINED"
            _log_report(requested_by, result)
            return result
        if not publishable_batch.records:
            repository.update(run_id, publication_status="SKIPPED", finished_at=datetime.now(UTC))
            result["status"] = "EMPTY"
            _log_report(requested_by, result)
            return result

        repository.update(run_id, publication_status="RUNNING")
        contracts = profile.contracts
        # [SỬA - Vấn đề 1 & 2] Phân biệt trạng thái công bố toàn phần ("PUBLISHED")
        # và công bố một phần dữ liệu sạch khi vẫn còn dòng bị cách ly ("PUBLISHED_WITH_QUARANTINE").
        quarantine_ref = ref if total_quarantined else None
        published_status = "PUBLISHED_WITH_QUARANTINE" if total_quarantined else "PUBLISHED"
        cleaning_run_id = run_id
        publication_recorded = False

        def record_publication(connection: Connection | None, snapshot_id: str) -> None:
            nonlocal publication_recorded
            if approval_required():
                if connection is None:
                    with engine.begin() as transaction:
                        record_publication(transaction, snapshot_id)
                    return
                recheck_approvals(connection, approvals)
                for proof in sorted(approvals, key=lambda item: item["profile_id"]):
                    approved_evidence(
                        connection,
                        proof["profile_id"],
                        proof["fingerprint"],
                        partial=bool(total_quarantined) and proof["profile_id"] == profile_id,
                        expected=proof,
                    )
            repository.update(
                cleaning_run_id,
                connection=connection,
                publication_status=published_status,
                records_published=total_passed,
                silver_snapshot_id=snapshot_id,
                finished_at=datetime.now(UTC),
            )
            with suppress(Exception):
                record_pipeline_lineage_sync(
                    connection or engine,
                    source_layer="BRONZE",
                    source_name=str(context["output"]["uri"]),
                    target_layer="SILVER",
                    target_name=profile.entity,
                    metadata={
                        "cleaning_run_id": cleaning_run_id,
                        "silver_snapshot_id": snapshot_id,
                        "ingestion_job_id": ingestion_job_id,
                        "domain": profile.domain,
                        "entity": profile.entity,
                        "records_read": report.records_read,
                        "records_passed": total_passed,
                        "records_quarantined": total_quarantined,
                        "records_collapsed": report.records_collapsed,
                        "status": published_status,
                    },
                )
            publication_recorded = True

        publisher = SilverPublicationService(
            repository=SilverRepository(engine, on_published=record_publication),
            storage=storage,
            contracts=contracts,
            scope=scope,
            mode="production",
            profile_approvals=approvals,
        )
        # Lựa chọn "active" dùng bộ dữ liệu hiện hành; mã cụ thể dùng đúng bộ dữ liệu đó.
        # Nếu không chọn bộ dữ liệu trước đó thì công bố một bộ mới độc lập.
        base = (
            publisher.repository.head(publisher.scope)
            if base_snapshot == "active"
            else base_snapshot
        )
        if approval_required():
            if base:
                inherited = (
                    publisher.describe_snapshot(base).get("governance", {}).get("profile_approvals")
                )
                if not inherited:
                    reject("SILVER_PARENT_APPROVAL_REQUIRED")
                # Keep all contributing evidence; fail closed on mixed revisions.
                for proof in inherited:
                    if proof not in approvals:
                        approvals.append(proof)
            with engine.begin() as connection:
                recheck_approvals(connection, approvals)
                for proof in sorted(approvals, key=lambda item: item["profile_id"]):
                    approved_evidence(
                        connection,
                        proof["profile_id"],
                        proof["fingerprint"],
                        partial=bool(total_quarantined) and proof["profile_id"] == profile_id,
                        expected=proof,
                    )
            # Include the complete evidence in both manifest and idempotency hash.
            publisher = SilverPublicationService(
                repository=publisher.repository,
                storage=storage,
                contracts=contracts,
                scope=scope,
                mode="production",
                profile_approvals=approvals,
            )
        # Chuyển đúng các bản ghi đã qua làm sạch + chuẩn hóa và được thống kê trong báo cáo.
        # [SỬA - Vấn đề 1 & 2] Nếu có dòng bị cách ly, truyền kèm quarantine_report để merge giữ dữ liệu cũ
        # và gắn bằng chứng báo cáo cách ly vào manifest của snapshot mới.
        publication = SilverPipeline(publisher).run(
            (
                NormalizationTask(
                    batch=publishable_batch,
                    mapping=mapping,
                    identity=identity,
                ),
            ),
            base_snapshot=base,
            quarantine_report=quarantine_ref,
        )
        publication_committed = True
        # Khi dùng lại bộ dữ liệu đã công bố, cần lưu riêng kết quả vì không có lần công bố mới.
        if not publication_recorded:
            record_publication(None, publication.snapshot_id)
        result.update(
            status=published_status,
            publication={
                "snapshot_id": publication.snapshot_id,
                "scope": publication.scope,
                "mode": publication.mode,
                "reused": publication.reused,
                "records_published": total_passed,
                "base_snapshot": base,
                "entity": profile.entity,
            },
        )
        _log_report(requested_by, result)
        return result
    except Exception as exc:
        # Không đánh dấu thất bại nếu Silver đã được công bố nhưng bước cập nhật thông tin cuối cùng bị lỗi.
        if run_id is not None and not publication_committed:
            error_code = (
                exc.code
                if isinstance(exc, AppException)
                else "PIPELINE_METADATA_UNAVAILABLE"
                if isinstance(exc, SQLAlchemyError)
                else "SILVER_PUBLICATION_REJECTED"
                if isinstance(exc, SilverError)
                else "CLEANING_PROCESSING_FAILED"
            )
            # Nếu PostgreSQL đang lỗi, giữ nguyên lỗi gốc trả về cho người gọi.
            with suppress(SQLAlchemyError):
                repository.update(
                    run_id,
                    cleaning_status="COMPLETED" if cleaning_completed else "FAILED",
                    publication_status="FAILED" if cleaning_completed else "NOT_STARTED",
                    error_code=error_code,
                    finished_at=datetime.now(UTC),
                )
        if isinstance(exc, SilverError):
            raise ValueError(f"SILVER_PUBLICATION_REJECTED:{exc}") from exc
        raise
    finally:
        engine.dispose()


def _log_report(requested_by: str, report: dict[str, Any]) -> None:
    issue_codes = sorted({issue["code"] for issue in report["issues"]})
    logger.info(
        "Manual cleaning report actor=%s job=%s domain=%s entity=%s status=%s "
        "read=%d passed=%d quarantined=%d collapsed=%d issue_codes=%s snapshot=%s",
        requested_by,
        report["ingestion_job_id"],
        report["domain"],
        report["entity"],
        report["status"],
        report["records_read"],
        report["records_passed"],
        report["records_quarantined"],
        report["records_collapsed"],
        issue_codes,
        (report["publication"] or {}).get("snapshot_id"),
    )


def preview_cleaning(
    engine: Any, ingestion_job_id: int, profile_id: str, limit: int
) -> dict[str, Any]:
    """Đọc và kiểm tra đầy đủ, chỉ trả một số dòng xem trước và không công bố dữ liệu."""
    profile = load_source_profile(engine, settings.silver_profiles_path, profile_id)
    context = _job_context(engine, ingestion_job_id)
    _bind_profile(context, profile)
    mapping, identity = _identity_service(engine, profile)
    cleaned = CleaningPipeline().clean(_read_bronze(context, profile))
    mapped = mapping.map_batch(cleaned.batch)
    typed = identity.types.standardize(mapped)
    normalized = identity.standardize(typed)
    report = cleaned.report
    return {
        "preview": True,
        "ingestion_job_id": ingestion_job_id,
        "profile_id": profile_id,
        "profile_fingerprint": profile.fingerprint,
        "records_read": report.records_read,
        "records_passed_cleaning": report.records_passed,
        "records_quarantined": report.records_quarantined,
        "records_collapsed": report.records_collapsed,
        "records_normalized": len(normalized.accepted),
        "cleaning_issues": [asdict(issue) for issue in report.issues],
        "normalization_issues": [
            *[asdict(issue) for issue in normalized.mapping_issues],
            *[asdict(issue) for issue in normalized.type_issues],
            *[asdict(issue) for issue in normalized.issues],
        ],
        "cleaned_sample": list(cleaned.batch.records[:limit]),
        "normalized_sample": [row.values for row in normalized.accepted[:limit]],
        "publication_checked": False,
        "note": "Bản xem trước chưa kiểm tra liên kết với toàn bộ phiên bản Silver; bước công bố sẽ kiểm tra tiếp.",
    }
