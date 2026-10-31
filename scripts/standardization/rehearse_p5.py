"""Chạy S2/S3/S4 giả lập → Parquet cục bộ và metadata SQLite, không dùng production."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from preview_s2 import ROOT
from preview_s4 import normalize_case
from sqlalchemy import create_engine, event

from app.infra.db.base import Base
from app.modules.pipeline.silver_publication import SilverPublicationService
from app.modules.pipeline.silver_repository import SILVER_TABLES, SilverRepository
from app.modules.pipeline.silver_storage import LocalSilverStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, required=True, help="Dedicated local rehearsal folder"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{output / 'silver-rehearsal.sqlite'}",
        execution_options={"schema_translate_map": {"app": None}},
    )

    @event.listens_for(engine, "connect")
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    try:
        Base.metadata.create_all(engine, tables=SILVER_TABLES)
        contracts = {
            d: json.loads(
                (ROOT / f"docs/standardization/silver-schemas/{d}.json").read_text(
                    encoding="utf-8"
                )
            )
            for d in ("admissions", "academic", "hr")
        }
        publisher = SilverPublicationService(
            repository=SilverRepository(engine),
            storage=LocalSilverStore(output / "objects"),
            contracts=contracts,
            scope="synthetic-p5",
            mode="rehearsal",
        )
        results = tuple(
            normalize_case(case, preview=False)
            for case in ("admissions-flat", "academic-flat", "hr-flat")
        )
        receipt = publisher.publish(results)
        tables = publisher.read_snapshot(receipt.snapshot_id)
        summary = {
            **asdict(receipt),
            "row_counts": {name: len(rows) for name, rows in tables.items()},
            "production_activated": False,
        }
        (output / "receipt.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        print(json.dumps(summary, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
