"""Diễn tập Bronze → Silver thủ công bằng MinIO và metadata PostgreSQL thật.

Chế độ trực tiếp dùng mã nguồn giả lập. Chế độ job kiểm chứng job từ catalog demo đã công bố.
Cả hai dùng profile chuẩn hóa giả lập cố định; không chế độ nào là API production.
Không tạo bucket/bảng hay bật production; áp dụng migration riêng.
"""

from __future__ import annotations

# Script chạy trực tiếp cần thiết lập sys.path và .env trước khi import ứng dụng.
# ruff: noqa: E402
import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# Cấu hình dùng đường dẫn .env tương đối, kể cả máy khách MinIO của reader.
os.chdir(ROOT)

from minio import Minio
from sqlalchemy import URL, create_engine, inspect, select, text

from app.core.config import settings
from app.modules.ingestion.contracts import MappingBinding
from app.modules.ingestion.reader_batch import reader_to_batch
from app.modules.ingestion.readers.contract import ReadRequest
from app.modules.ingestion.readers.factory import SourceReaderFactory
from app.modules.ingestion.readers.file_snapshot import read_file_snapshot
from app.modules.pipeline.identity_repository import RELEASES, CrosswalkRepository
from app.modules.pipeline.identity_standardization import (
    IdentityStandardizationService,
    ValueDictionary,
)
from app.modules.pipeline.mapping_profiles import build_profile
from app.modules.pipeline.schema_mapping import (
    MappingKey,
    MappingRegistry,
    SchemaMappingService,
)
from app.modules.pipeline.silver_publication import SilverPublicationService
from app.modules.pipeline.silver_repository import SilverRepository
from app.modules.pipeline.silver_storage import MinioSilverStore, SilverError
from app.modules.pipeline.type_standardization import (
    FieldOptions,
    TypeStandardizationService,
)

DOMAINS = ("admissions", "academic", "hr")
RELEASE = "manual-live-demo-crosswalk-v1"
WHEN = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
JOB_RELEASE = "manual-ingestion-demo-v1"


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def profiles():
    return [
        c
        for c in read_json("data/standardization/s2/examples.json")
        if c["id"].endswith("-flat")
    ]


def normalize(domain, location, snapshot, *, version=None, job_context=None):
    case = next(c for c in profiles() if c["domain"] == domain)
    contract = read_json(f"docs/standardization/silver-schemas/{domain}.json")
    source_type = "EXCEL" if location.lower().endswith(".xlsx") else "CSV"
    key = MappingKey(
        str(job_context["source_system_id"]) if job_context else f"DEMO_live_{domain}",
        domain,
        case["entity"],
        "1",
        f"manual-{domain}-{source_type.lower()}",
        "1",
        contract["x-version"],
    )
    config = (
        {"sheet_name": "Data", "header_row": 1, "skip_rows": 0}
        if source_type == "EXCEL"
        else {"delimiter": ",", "encoding": "utf-8", "has_header": True}
    )
    request = ReadRequest(source_type, location, config, Path(location).name, version)
    raw = read_file_snapshot(
        request,
        SourceReaderFactory.get_reader(source_type),
        source_system_id=key.source_system_id,
        data_source_id=str(job_context["data_source_id"])
        if job_context
        else f"manual-{domain}",
        dataset_id=str(job_context["dataset_id"]) if job_context else case["entity"],
    )
    if job_context:
        verify_job_capture(raw, job_context)
    binding = MappingBinding(
        key.source_system_id,
        key.entity,
        key.source_schema_version,
        key.mapping_id,
        key.mapping_version,
        key.target_schema_version,
    )
    batch = reader_to_batch(
        raw,
        batch_id=str(uuid4()),
        domain_id=domain,
        source_type=source_type,
        binding=binding,
    )
    fields = tuple(contract["$defs"][key.entity]["properties"])
    profile = build_profile(
        key=key,
        source_type=source_type,
        target_fields=fields,
        source_columns=tuple(case["source_paths"]),
        field_paths={k: tuple(v) for k, v in case["field_paths"].items()},
        ignored_sources=tuple(case["ignored_sources"]),
        deferred_fields=tuple(n for n in fields if n not in case["field_paths"]),
    )
    mapped = SchemaMappingService(MappingRegistry((profile,))).map_batch(batch)
    options = {}
    if domain == "admissions":
        options = {
            "max_score": FieldOptions(decimal_separator=","),
            "is_active": FieldOptions(true_tokens=("Có",), false_tokens=("Không",)),
        }
    if domain == "academic":
        options = {
            f: FieldOptions(date_format="%d/%m/%Y") for f in ("start_date", "end_date")
        }
    types = TypeStandardizationService(
        key=key, conversion_version="manual-types-1", contract=contract, options=options
    )
    aliases = read_json("data/standardization/s4/preview-profiles.json")["profiles"][
        key.entity
    ]["dictionaries"]
    identity = IdentityStandardizationService(
        types=types,
        version="manual-identity-1",
        crosswalk=snapshot,
        dictionaries={
            f: ValueDictionary("manual-dict-1", tuple(a.items()))
            for f, a in aliases.items()
        },
        reference_namespaces={},
        effective_at=WHEN,
    )
    return identity.standardize(types.standardize(mapped))


def validate_job_input(row, bucket):
    """Chỉ catalog dữ liệu mẫu chuyên dụng mới được dùng các profile giả lập này."""
    if not row or row["status"] != "SUCCESS" or row["job_type"] != "EXECUTION_V1":
        raise ValueError("SUCCESSFUL_BRONZE_JOB_REQUIRED")
    domain = row["domain"]
    if (
        domain not in DOMAINS
        or row["source_code"] != f"DEMO_FLOW_{domain}"
        or row["dataset_code"] != f"DEMO_FLOW_{domain}_BRONZE"
        or row["dataset_source_id"] != row["source_system_id"]
        or row["layer_code"] != "BRONZE"
    ):
        raise ValueError("DEDICATED_DEMO_CATALOG_REQUIRED")
    output = row["output"] or {}
    uri = urlsplit(output.get("uri", ""))
    parts = uri.path.lstrip("/").split("/")
    if (
        uri.scheme != "s3"
        or uri.netloc != bucket
        or uri.query
        or uri.fragment
        or len(parts) != 6
        or parts[0] != domain
        or parts[4] != f"job-{row['ingestion_job_id']}"
        or parts[-1] != row["source_table"]
        or not parts[-1].endswith((".xlsx", ".csv"))
    ):
        raise ValueError("BRONZE_JOB_OUTPUT_INVALID_OR_UNSUPPORTED")
    if (
        not output.get("version_id")
        or output["version_id"] == "null"
        or not isinstance(output.get("sha256"), str)
        or len(output["sha256"]) != 64
        or not isinstance(output.get("size_bytes"), int)
    ):
        raise ValueError("BRONZE_JOB_EVIDENCE_REQUIRED")
    return {**dict(row), "object": "/".join(parts)}


def read_job_input(engine, job_id):
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("""
            SELECT j.ingestion_job_id,j.status,j.job_type,j.output,j.source_table,
                   j.rows_read,j.data_source_id,j.dataset_id,s.source_system_id,
                   s.source_code,d.domain,d.dataset_code,
                   d.source_system_id AS dataset_source_id,l.layer_code
            FROM app.ingestion_job j
            JOIN app.data_source ds ON ds.data_source_id=j.data_source_id
            JOIN app.source_system s ON s.source_system_id=ds.source_system_id
            JOIN app.dataset d ON d.dataset_id=j.dataset_id
            JOIN app.data_layer l ON l.data_layer_id=j.target_layer_id
            WHERE j.ingestion_job_id=:job_id
        """),
                {"job_id": job_id},
            )
            .mappings()
            .first()
        )
    return validate_job_input(row, settings.minio_bucket_bronze)


def verify_job_capture(raw, context):
    if (
        raw.provenance.content_sha256 != context["output"]["sha256"]
        or raw.provenance.object_version_id != context["output"]["version_id"]
        or raw.rows_read != context["rows_read"]
    ):
        raise ValueError("BRONZE_JOB_CAPTURE_MISMATCH")


def initialize_job_crosswalk(engine):
    with engine.begin() as conn:
        repo = CrosswalkRepository(conn)
        if conn.execute(
            select(RELEASES.c.version).where(RELEASES.c.version == JOB_RELEASE)
        ).first():
            return repo.load(JOB_RELEASE)
        for c in profiles():
            source_id = conn.execute(
                text(
                    "SELECT source_system_id FROM app.source_system WHERE source_code=:code"
                ),
                {"code": f"DEMO_FLOW_{c['domain']}"},
            ).scalar_one()
            repo.register(
                namespace=str(source_id),
                entity=c["entity"],
                source=(c["records"][0]["id"],),
                evidence="Synthetic ingestion demo v1",
                valid_from=WHEN,
            )
        return repo.freeze(JOB_RELEASE)


def initialize_crosswalk(engine):
    with engine.begin() as conn:
        repo = CrosswalkRepository(conn)
        if conn.execute(
            select(RELEASES.c.version).where(RELEASES.c.version == RELEASE)
        ).first():
            return repo.load(RELEASE)
        for c in profiles():
            repo.register(
                namespace=f"DEMO_live_{c['domain']}",
                entity=c["entity"],
                source=(c["records"][0]["id"],),
                evidence="Synthetic manual demo v1; not a production match",
                valid_from=WHEN,
            )
        return repo.freeze(RELEASE)


def main():
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("check", "init-crosswalk", "init-job-crosswalk", "run", "inspect"),
    )
    parser.add_argument("--domain", choices=DOMAINS)
    parser.add_argument("--object", help="Object key inside configured Bronze bucket")
    parser.add_argument("--version", help="Pin a specific Bronze object version")
    parser.add_argument(
        "--ingestion-job",
        type=int,
        help="SUCCESS job from the dedicated DEMO_FLOW catalog",
    )
    parser.add_argument("--session", default="buoi1")
    parser.add_argument(
        "--base", default="none", help="none, active, or exact parent snapshot UUID"
    )
    args = parser.parse_args()
    if (
        not args.session.isascii()
        or not args.session.replace("-", "").isalnum()
        or len(args.session) > 40
    ):
        parser.error("session: use 1-40 ASCII letters/digits/hyphens")
    if args.ingestion_job is not None and (
        args.ingestion_job <= 0 or args.object or args.version or args.domain
    ):
        parser.error(
            "--ingestion-job must be positive and cannot combine with domain/object/version"
        )
    if (
        args.action == "run"
        and args.ingestion_job is None
        and (not args.domain or not args.object)
    ):
        parser.error("run requires --domain and --object")
    if (
        args.action == "run"
        and args.ingestion_job is None
        and (
            not args.object.startswith("demo/")
            or not args.object.lower().endswith((".csv", ".xlsx"))
        )
    ):
        parser.error("Demo only accepts .csv/.xlsx objects under demo/")
    engine = create_engine(
        URL.create(
            "postgresql+psycopg",
            username=settings.db_user,
            password=settings.db_password,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
        ),
        connect_args={"connect_timeout": 5},
    )
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    try:
        if args.action == "check":
            required = {
                "canonical_identity",
                "identity_crosswalk",
                "crosswalk_release",
                "silver_run",
                "silver_snapshot",
                "silver_head",
            }
            missing = required - set(inspect(engine).get_table_names(schema="app"))
            buckets = {
                b: client.bucket_exists(b)
                for b in (settings.minio_bucket_bronze, settings.minio_bucket_silver)
            }
            versioning = (
                client.get_bucket_versioning(settings.minio_bucket_bronze).status
                if buckets[settings.minio_bucket_bronze]
                else None
            )
            print(
                json.dumps(
                    {
                        "missing_tables": sorted(missing),
                        "buckets": buckets,
                        "bronze_versioning": versioning,
                    },
                    indent=2,
                )
            )
            return (
                0
                if not missing and all(buckets.values()) and versioning == "Enabled"
                else 1
            )
        if args.action in {"init-crosswalk", "init-job-crosswalk"}:
            snapshot = (
                initialize_job_crosswalk(engine)
                if args.action == "init-job-crosswalk"
                else initialize_crosswalk(engine)
            )
            print(
                json.dumps(
                    {
                        "release": snapshot.version,
                        "demo_entries": [
                            asdict(e)
                            for e in snapshot.entries
                            if (
                                args.action == "init-crosswalk"
                                and e.namespace.startswith("DEMO_live_")
                            )
                            or (
                                args.action == "init-job-crosswalk"
                                and e.evidence == "Synthetic ingestion demo v1"
                            )
                        ],
                    },
                    default=str,
                    indent=2,
                )
            )
            return 0
        repo = SilverRepository(engine)
        publisher = SilverPublicationService(
            repository=repo,
            storage=MinioSilverStore(client, settings.minio_bucket_silver),
            contracts={
                d: read_json(f"docs/standardization/silver-schemas/{d}.json")
                for d in DOMAINS
            },
            scope=f"manual-demo-{args.session}",
            mode="rehearsal",
        )
        before = repo.head(publisher.scope)
        if args.action == "inspect":
            print(
                json.dumps(
                    {
                        "active_snapshot": before,
                        "tables": publisher.read_snapshot(before) if before else {},
                    },
                    default=str,
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        job_context = (
            read_job_input(engine, args.ingestion_job) if args.ingestion_job else None
        )
        if job_context:
            args.domain = job_context["domain"]
            args.object = job_context["object"]
            args.version = job_context["output"]["version_id"]
        with engine.begin() as conn:
            crosswalk = CrosswalkRepository(conn).load(
                JOB_RELEASE if job_context else RELEASE
            )
        stat = client.stat_object(
            settings.minio_bucket_bronze, args.object, version_id=args.version
        )
        if not stat.version_id or stat.version_id == "null":
            raise ValueError(
                "BRONZE_VERSIONING_REQUIRED: upload again after enabling versioning"
            )
        if job_context and stat.size != job_context["output"]["size_bytes"]:
            raise ValueError("BRONZE_JOB_SIZE_MISMATCH")
        result = normalize(
            args.domain,
            f"s3://{settings.minio_bucket_bronze}/{args.object}",
            crosswalk,
            version=stat.version_id,
            job_context=job_context,
        )
        base = (
            before
            if args.base == "active"
            else None
            if args.base == "none"
            else args.base
        )
        report = {
            "ingestion_job_id": args.ingestion_job,
            "scope": publisher.scope,
            "source": args.object,
            "source_version": stat.version_id,
            "base_snapshot": base,
            "head_before": before,
            "input_rows": result.total_count,
            "accepted_rows": len(result.accepted),
            "rejected_locators": result.rejected_locators,
            "issues": [
                asdict(i)
                for i in (*result.mapping_issues, *result.type_issues, *result.issues)
            ],
        }
        try:
            receipt = publisher.publish((result,), base_snapshot=base)
            report.update(
                status="PUBLISHED",
                receipt=asdict(receipt),
                tables=publisher.read_snapshot(receipt.snapshot_id),
            )
        except SilverError as exc:
            report.update(status="FAILED", error=str(exc))
        report["head_after"] = repo.head(publisher.scope)
        output = ROOT / ".local/live-demo/reports" / f"{uuid4()}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(report, default=str, ensure_ascii=False, indent=2)
        output.write_text(encoded, encoding="utf-8")
        print(encoded)
        print(f"Report: {output}")
        return 0 if report["status"] == "PUBLISHED" else 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
