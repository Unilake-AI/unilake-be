"""Manual Bronze→Silver runner for the three synthetic live-demo profiles."""

from __future__ import annotations

import json
import logging
import re
from contextlib import suppress
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import URL, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.modules.ingestion.contracts import MappingBinding
from app.modules.ingestion.contracts.readers import (
    ReadRequest,
    SourceReaderFactory,
    read_file_snapshot,
    reader_to_batch,
)
from app.modules.pipeline.cleaning_pipeline import (
    CleaningPipeline,
    CleaningTask,
)
from app.modules.pipeline.cleaning_runs import CleaningRunRepository
from app.modules.pipeline.duplicate_records import build_duplicate_profile
from app.modules.pipeline.format_validation import SourceFormatOptions, build_format_profile
from app.modules.pipeline.identity_repository import CrosswalkRepository
from app.modules.pipeline.identity_standardization import (
    IdentityStandardizationService,
    ValueDictionary,
)
from app.modules.pipeline.mapping_profiles import build_profile
from app.modules.pipeline.missing_values import MissingValueProfile, MissingValueRule
from app.modules.pipeline.quarantine_records import (
    file_parser_options,
    quarantine_source,
    save_quarantine_records,
)
from app.modules.pipeline.schema_mapping import (
    MappingKey,
    MappingRegistry,
    SchemaMappingService,
)
from app.modules.pipeline.silver_pipeline import NormalizationTask, SilverPipeline
from app.modules.pipeline.silver_publication import SilverPublicationService
from app.modules.pipeline.silver_repository import SilverRepository
from app.modules.pipeline.silver_storage import MinioSilverStore, SilverError, checksum
from app.modules.pipeline.type_standardization import TypeStandardizationService
from app.providers import silver_writer_client, source_reader_client

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parents[3]
DEMO_DOMAINS = ("admissions", "academic", "hr")
DEMO_CROSSWALK_RELEASE = "manual-ingestion-demo-v1"
PROFILE_VERSION = "manual-cleaning-demo-v1"
TYPE_VERSION = "manual-cleaning-types-v1"
IDENTITY_VERSION = "manual-cleaning-identity-v1"
# Máy chủ quy định chính sách chạy thử. Các bộ dữ liệu demo khác yêu cầu batch đầy đủ.
PARTIAL_POSITION_POLICY = "hr-position-partial-v1"
MAX_POSITION_QUARANTINED = 10
MAX_POSITION_QUARANTINED_PERCENT = 10
logger = logging.getLogger(__name__)


def _read_json(relative_path: str) -> Any:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def _case(domain: str) -> dict[str, Any]:
    cases = _read_json("data/standardization/s2/examples.json")
    selected = next(
        (
            item
            for item in cases
            if item.get("id") == f"{domain}-flat" and item.get("domain") == domain
        ),
        None,
    )
    if not isinstance(selected, dict):
        raise ValueError("DEMO_PROFILE_NOT_FOUND")
    return selected


def _job_context(engine: Any, ingestion_job_id: int) -> dict[str, Any]:
    with engine.begin() as connection:
        row = (
            connection.execute(
                text("""
                SELECT j.ingestion_job_id,j.status,j.job_type,j.output,j.source_table,
                       j.rows_read,j.data_source_id,j.dataset_id,ds.source_type,
                       ds.configuration,ds.location,s.source_system_id,s.source_code,
                       d.domain,d.dataset_code,d.source_system_id AS dataset_source_id,
                       l.layer_code
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
    _validate_job_context(context)
    return context


def _validate_job_context(context: dict[str, Any]) -> None:
    if context["status"] != "SUCCESS" or context["job_type"] != "EXECUTION_V1":
        raise ValueError("SUCCESSFUL_BRONZE_JOB_REQUIRED")
    domain = context["domain"]
    if (
        domain not in DEMO_DOMAINS
        or context["source_code"] != f"DEMO_FLOW_{domain}"
        or context["dataset_code"] != f"DEMO_FLOW_{domain}_BRONZE"
        or context["dataset_source_id"] != context["source_system_id"]
        or context["layer_code"] != "BRONZE"
    ):
        raise ValueError("DEDICATED_DEMO_CATALOG_REQUIRED")
    if (
        not isinstance(context["configuration"], dict)
        or not isinstance(context["location"], str)
        or not context["location"].strip()
    ):
        raise ValueError("DEMO_FILE_SOURCE_CONFIGURATION_REQUIRED")
    if context["source_type"] not in {"CSV", "EXCEL"}:
        raise ValueError("UNSUPPORTED_BRONZE_FILE_TYPE")
    source_table = context["source_table"]
    extension = ".xlsx" if context["source_type"] == "EXCEL" else ".csv"
    if not isinstance(source_table, str) or not source_table.lower().endswith(extension):
        raise ValueError("BRONZE_SOURCE_FILE_TYPE_MISMATCH")
    # Object phải có phiên bản và nằm trong bucket Bronze theo đường dẫn của job này.
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
        or object_parts[4] != f"job-{context['ingestion_job_id']}"
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
) -> tuple[
    CleaningTask,
    SchemaMappingService,
    MappingKey,
    dict[str, Any],
    dict[str, Any],
]:
    output = context["output"]
    source_type = context["source_type"]
    # Đọc đúng phiên bản object mà ingestion đã ghi nhận, kể cả khi đã có phiên bản mới hơn.
    request = ReadRequest(
        source_type=source_type,
        location=output["uri"],
        configuration=file_parser_options(source_type, context["configuration"]),
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
    # Đối chiếu checksum và số dòng đọc được với thông tin do ingestion job lưu lại.
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

    case = _case(context["domain"])
    expected_columns = tuple(path.removeprefix("/") for path in case["source_paths"])
    if any("/" in column for column in expected_columns) or set(parsed.columns) != set(
        expected_columns
    ):
        raise ValueError("DEMO_SOURCE_LAYOUT_MISMATCH")
    contract = _read_json(f"docs/standardization/silver-schemas/{context['domain']}.json")
    key = MappingKey(
        str(context["source_system_id"]),
        context["domain"],
        case["entity"],
        "1",
        f"manual-{context['domain']}-{source_type.lower()}",
        "1",
        contract["x-version"],
    )
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
        domain_id=context["domain"],
        source_type=source_type,
        binding=binding,
    )
    schema = _read_json(f"docs/data-schema/{context['domain']}_v1.schema.json")
    entity_schema = schema["entities"][case["entity"]]
    # Cleaning chạy trước mapping, nên áp dụng quy tắc schema theo tên cột nguồn.
    target_to_source = {
        target: pointer_paths[0].removeprefix("/")
        for target, pointer_paths in case["field_paths"].items()
    }
    options = _format_options(context["domain"], case)
    missing = MissingValueProfile(
        context["domain"],
        case["entity"],
        PROFILE_VERSION,
        tuple(target_to_source.values()),
        tuple(
            MissingValueRule(
                target_to_source[column["name"]],
                nullable=column["nullable"],
            )
            for column in entity_schema["columns"]
            if column["name"] in target_to_source
        ),
    )
    cleaning = CleaningTask(
        batch=batch,
        missing_profile=missing,
        format_profile=build_format_profile(
            schema,
            domain=context["domain"],
            entity=case["entity"],
            version=PROFILE_VERSION,
            target_to_source=target_to_source,
            source_options=options,
        ),
        duplicate_profile=build_duplicate_profile(
            schema,
            domain=context["domain"],
            entity=case["entity"],
            version=PROFILE_VERSION,
            target_to_source=target_to_source,
        ),
    )
    mapping_profile = build_profile(
        key=key,
        source_type=source_type,
        target_fields=tuple(contract["$defs"][key.entity]["properties"]),
        source_columns=batch.source_paths,
        field_paths={name: tuple(paths) for name, paths in case["field_paths"].items()},
        ignored_sources=tuple(case["ignored_sources"]),
        # Để các bước sau xử lý những trường Silver chưa được ánh xạ trong profile demo.
        deferred_fields=tuple(
            name
            for name in contract["$defs"][key.entity]["properties"]
            if name not in case["field_paths"]
        ),
    )
    mapping = SchemaMappingService(MappingRegistry((mapping_profile,)))
    return cleaning, mapping, key, contract, case


def _format_options(domain: str, case: dict[str, Any]) -> dict[str, SourceFormatOptions]:
    aliases = _read_json("data/standardization/s4/preview-profiles.json")["profiles"][
        case["entity"]
    ]["dictionaries"]
    options = {
        field: SourceFormatOptions(aliases=tuple(values.items()), trim=True)
        for field, values in aliases.items()
    }
    if domain == "admissions":
        options["max_score"] = SourceFormatOptions(decimal_separator=",", trim=True)
        options["is_active"] = SourceFormatOptions(
            true_tokens=("Có", "true"),
            false_tokens=("Không", "false"),
            trim=True,
        )
    elif domain == "academic":
        for field in ("start_date", "end_date"):
            options[field] = SourceFormatOptions(date_formats=("%d/%m/%Y",), trim=True)
    return options


def _identity_service(
    engine: Any, key: MappingKey, contract: dict[str, Any]
) -> IdentityStandardizationService:
    # Dùng bản crosswalk demo cố định và thời điểm hiệu lực trong profile để chuẩn hóa định danh.
    with engine.begin() as connection:
        crosswalk = CrosswalkRepository(connection).load(DEMO_CROSSWALK_RELEASE)
    profile = _read_json("data/standardization/s4/preview-profiles.json")
    aliases = profile["profiles"][key.entity]["dictionaries"]
    types = TypeStandardizationService(
        key=key,
        conversion_version=TYPE_VERSION,
        contract=contract,
    )
    return IdentityStandardizationService(
        types=types,
        version=IDENTITY_VERSION,
        crosswalk=crosswalk,
        dictionaries={
            name: ValueDictionary("manual-demo-dictionary-v1", tuple(entries.items()))
            for name, entries in aliases.items()
        },
        reference_namespaces={},
        effective_at=datetime.fromisoformat(profile["effective_at"]),
    )


def run_manual_cleaning(
    ingestion_job_id: int,
    *,
    requested_by: str,
    session: str = "default",
    base_snapshot: str | None = "active",
) -> dict[str, Any]:
    """Run one successful synthetic demo Bronze job through cleaning and Silver."""
    if (
        not isinstance(session, str)
        or re.fullmatch(r"[A-Za-z0-9-]{1,40}", session) is None
        or (
            base_snapshot is not None
            and (not isinstance(base_snapshot, str) or not base_snapshot.strip())
        )
    ):
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
        context = _job_context(engine, ingestion_job_id)
        run_id = repository.start(
            context, entity=_case(context["domain"])["entity"], actor=requested_by, session=session
        )
        cleaning, mapping, key, contract, case = _read_bronze(context)
        cleaned = CleaningPipeline().clean(cleaning)
        report = cleaned.report
        counts = {
            "records_read": report.records_read,
            "records_passed": report.records_passed,
            "records_quarantined": report.records_quarantined,
            "records_collapsed": report.records_collapsed,
        }
        # Giữ số liệu cleaning kể cả khi chuẩn hóa hoặc xuất bản thất bại ở bước sau.
        with engine.begin() as connection:
            # Ghi hồ sơ và số liệu trong cùng giao dịch trước mọi thao tác xuất bản Silver.
            evidence = None
            if report.records_quarantined:
                evidence = quarantine_source(context, cleaning.batch)
                save_quarantine_records(connection, run_id, cleaning.batch, cleaned)
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
            "issues": [asdict(issue) for issue in report.issues],
            "publication": None,
        }
        # Mỗi session có lịch sử snapshot chạy thử riêng.
        scope = f"manual-cleaning-{session}"
        storage = None
        quarantine_report = None
        if report.records_quarantined:
            # Chỉ cho phép publish một phần với batch hr.position có dòng đạt và đáp ứng cả hai ngưỡng.
            # So sánh bằng số nguyên để kiểm tra ngưỡng phần trăm mà không cần làm tròn.
            partial_allowed = (
                (report.domain, report.entity) == ("hr", "position")
                and report.records_passed > 0
                and report.records_quarantined <= MAX_POSITION_QUARANTINED
                and report.records_quarantined * 100
                <= report.records_read * MAX_POSITION_QUARANTINED_PERCENT
            )
            storage = MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver)
            output = context["output"]
            # Lưu vị trí dữ liệu và lỗi kiểm tra. Giá trị gốc của các ô vẫn nằm ở Bronze.
            data = json.dumps(
                {
                    **{
                        key: value
                        for key, value in result.items()
                        if key not in {"status", "run_id", "requested_by", "publication"}
                    },
                    "scope": f"rehearsal:{scope}",
                    "bronze": {key: output[key] for key in ("uri", "version_id", "sha256")},
                    "policy": PARTIAL_POSITION_POLICY if report.entity == "position" else "strict",
                    "decision": "ALLOW_PARTIAL" if partial_allowed else "BLOCK",
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
            quarantine_report = storage.put(
                f"silver/rehearsal/quarantine/job-{ingestion_job_id}/report-{checksum(data)}.json",
                data,
            )
            # Đọc lại để xác minh báo cáo, kể cả khi batch bị chặn và không tạo manifest.
            storage.get(quarantine_report)
            result["quarantine_report"] = {
                "uri": f"s3://{settings.minio_bucket_silver}/{quarantine_report.key}",
                **asdict(quarantine_report),
            }
            repository.update(run_id, report_ref=asdict(quarantine_report))
            if not partial_allowed:
                repository.update(
                    run_id, publication_status="BLOCKED", finished_at=datetime.now(UTC)
                )
                result["status"] = "QUARANTINED"
                _log_report(requested_by, result)
                return result
        if not cleaned.batch.records:
            repository.update(run_id, publication_status="SKIPPED", finished_at=datetime.now(UTC))
            result["status"] = "EMPTY"
            _log_report(requested_by, result)
            return result

        repository.update(run_id, publication_status="RUNNING")
        identity = _identity_service(engine, key, contract)
        contracts = {
            domain: _read_json(f"docs/standardization/silver-schemas/{domain}.json")
            for domain in DEMO_DOMAINS
        }
        published_status = (
            "PUBLISHED_WITH_QUARANTINE" if quarantine_report is not None else "PUBLISHED"
        )
        cleaning_run_id = run_id
        publication_recorded = False

        def record_publication(connection: Connection | None, snapshot_id: str) -> None:
            nonlocal publication_recorded
            repository.update(
                cleaning_run_id,
                connection=connection,
                publication_status=published_status,
                records_published=report.records_passed,
                silver_snapshot_id=snapshot_id,
                finished_at=datetime.now(UTC),
            )
            publication_recorded = True

        publisher = SilverPublicationService(
            repository=SilverRepository(engine, on_published=record_publication),
            storage=storage
            or MinioSilverStore(silver_writer_client(), settings.minio_bucket_silver),
            contracts=contracts,
            scope=scope,
            mode="rehearsal",
        )
        # Với "active", lấy snapshot hiện hành của session. Với ID cụ thể, dùng trực tiếp ID đó.
        # Giá trị None yêu cầu publish mà không có snapshot cha.
        base = (
            publisher.repository.head(publisher.scope)
            if base_snapshot == "active"
            else base_snapshot
        )
        # Chuyển đúng tập dòng đã qua cleaning và được thống kê trong báo cáo.
        publication = SilverPipeline(publisher).run(
            (
                NormalizationTask(
                    batch=cleaned.batch,
                    mapping=mapping,
                    identity=identity,
                ),
            ),
            base_snapshot=base,
            quarantine_report=quarantine_report,
        )
        publication_committed = True
        # Khi dùng lại snapshot đã tồn tại, không có giao dịch đổi snapshot để ghi kèm.
        if not publication_recorded:
            record_publication(None, publication.snapshot_id)
        result.update(
            status=published_status,
            publication={
                "snapshot_id": publication.snapshot_id,
                "scope": publication.scope,
                "mode": publication.mode,
                "reused": publication.reused,
                "records_published": report.records_passed,
                "base_snapshot": base,
                "entity": case["entity"],
            },
        )
        _log_report(requested_by, result)
        return result
    except Exception as exc:
        # Không ghi FAILED nếu Silver đã công bố nhưng lần cập nhật metadata cuối bị lỗi.
        if run_id is not None and not publication_committed:
            error_code = (
                "PIPELINE_METADATA_UNAVAILABLE"
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
