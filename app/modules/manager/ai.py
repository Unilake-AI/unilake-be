"""Text-to-SQL trên bản thống kê Gold đã lọc quyền; không cho LLM truy cập DB."""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime
from typing import TYPE_CHECKING, Any

import duckdb
import pyarrow as pa
import sqlglot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from app.core.config import settings
from app.core.exceptions import AppException
from app.modules.pipeline.contracts.gold import DIMENSIONS
from app.repositories.manager_report_repo import dataset_scope

if TYPE_CHECKING:
    from app.modules.manager.access import ManagerAccess
    from app.modules.manager.schemas import ReportFilters

COLUMNS = (
    "dataset_id",
    "dataset_name",
    "domain",
    "scope",
    "entity",
    "metric",
    "group_field",
    "group_value",
    "value",
    "finished_at",
)
MAX_INPUT_ROWS = 50_000
MAX_RESULT_ROWS = 200


def validate_sql(sql: str) -> str:
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        raise AppException(
            "Không thể phân tích câu truy vấn AI", code="INVALID_SQL", status_code=422
        ) from None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise AppException(
            "Chỉ chấp nhận một truy vấn thống kê SELECT", code="INVALID_SQL", status_code=422
        )
    tree = statements[0]
    allowed = {
        exp.Select,
        exp.Column,
        exp.Identifier,
        exp.Table,
        exp.From,
        exp.Where,
        exp.And,
        exp.Or,
        exp.Not,
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Like,
        exp.ILike,
        exp.In,
        exp.Between,
        exp.Is,
        exp.Null,
        exp.Literal,
        exp.Boolean,
        exp.Star,
        exp.Alias,
        exp.Order,
        exp.Ordered,
        exp.Group,
        exp.Limit,
        exp.Paren,
        exp.Count,
        exp.Sum,
        exp.Avg,
        exp.Min,
        exp.Max,
        exp.Round,
        exp.Div,
        exp.Mul,
        exp.Add,
        exp.Sub,
        exp.Neg,
        exp.Distinct,
        exp.TableAlias,
    }
    tables = list(tree.find_all(exp.Table))
    aliases = {alias.alias for alias in tree.find_all(exp.Alias)}
    if (
        any(type(node) not in allowed for node in tree.walk())
        or len(list(tree.find_all(exp.Select))) != 1
        or len(tables) != 1
        or tables[0].name != "manager_metrics"
        or tables[0].db
        or tables[0].catalog
        or any(column.name not in set(COLUMNS) | aliases for column in tree.find_all(exp.Column))
    ):
        raise AppException(
            "Truy vấn vượt phạm vi thống kê được hỗ trợ", code="INVALID_SQL", status_code=422
        )
    # Không gộp nhiều nguồn thành một số người duy nhất.
    if any(tree.find_all(exp.AggFunc)):
        group = tree.args.get("group")
        grouped = {column.name for column in group.find_all(exp.Column)} if group else set()
        if not {"scope", "dataset_id"} <= grouped:
            raise AppException(
                "Thống kê phải giữ riêng từng bộ dữ liệu và nguồn",
                code="INVALID_SQL",
                status_code=422,
            )
    return tree.limit(MAX_RESULT_ROWS + 1).sql(dialect="duckdb")


async def generate_sql(question: str, domains: list[str]) -> str:
    if (
        settings.llm_provider.lower() != "openai"
        or not settings.llm_api_key
        or not settings.llm_model
    ):
        raise AppException(
            "Cần cấu hình nhà cung cấp và model AI", code="NLQ_UNAVAILABLE", status_code=503
        )
    instructions = (
        "You translate Vietnamese analytics questions into ONE DuckDB SELECT, return SQL only, no markdown. "
        "Only table manager_metrics with columns: dataset_id BIGINT,dataset_name VARCHAR,domain VARCHAR,scope VARCHAR,"
        "entity VARCHAR,metric VARCHAR,group_field VARCHAR,group_value VARCHAR,value BIGINT,finished_at VARCHAR. "
        "Rows are already authorized current Gold aggregates, not individual persons. "
        "record_count rows have null group_field/group_value. Grouped counts use metric record_count_by_<field>. "
        "Never mix total and grouped metrics, never sum across different sources. Always preserve dataset_id and scope. "
        "For person count select value WHERE entity='student' or employee and metric='record_count'; do not COUNT rows. "
        "Only supported entities/dimensions: "
        + json.dumps({d: DIMENSIONS[d] for d in domains}, ensure_ascii=False)
        + ". "
        "No GPA, pass rate, causal conclusions, business-period joins, file access or other tables. "
        "If unsupported return UNSUPPORTED. group_value is text. Limit results to 200. "
        "Allowed functions: count,sum,avg,min,max,round; aggregate group must include dataset_id and scope."
    )

    def call() -> str:
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(
                {
                    "model": settings.llm_model,
                    "instructions": instructions,
                    "input": question,
                    "store": False,
                    "max_output_tokens": 2000,
                }
            ).encode(),
            headers={
                "Authorization": "Bearer " + settings.llm_api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=settings.llm_timeout_seconds) as response:
                payload = json.load(response)
            output = "".join(
                part.get("text", "")
                for item in payload.get("output", [])
                if item.get("type") == "message"
                for part in item.get("content", [])
                if part.get("type") == "output_text"
            ).strip()
            if not output or output == "UNSUPPORTED":
                raise AppException(
                    "Câu hỏi cần dữ liệu hoặc công thức chưa được hỗ trợ",
                    code="NLQ_UNSUPPORTED",
                    status_code=422,
                )
            return output
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            raise AppException(
                "Không thể kết nối dịch vụ AI", code="NLQ_UNAVAILABLE", status_code=503
            ) from None

    return await asyncio.to_thread(call)


async def input_metrics(
    db: AsyncSession, manager: ManagerAccess, filters: ReportFilters
) -> tuple[list[dict[str, Any]], list[int]]:
    clause, params = dataset_scope(manager, filters)
    rows = (
        (
            await db.execute(
                text(
                    "SELECT m.*,d.dataset_name FROM gold.current_metrics m JOIN app.dataset d ON d.dataset_id=m.dataset_id AND d.domain=m.domain WHERE "
                    + clause
                    + " LIMIT :maximum"
                ),
                {**params, "maximum": MAX_INPUT_ROWS + 1},
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > MAX_INPUT_ROWS:
        raise AppException(
            "Phạm vi quá rộng; hãy chọn một bộ dữ liệu", code="NLQ_SCOPE_TOO_LARGE", status_code=422
        )
    result = []
    for row in rows:
        dimensions = row["dimensions"] or {}
        field, value = next(iter(dimensions.items()), (None, None))
        result.append(
            {
                **{
                    key: row[key]
                    for key in COLUMNS
                    if key not in {"group_field", "group_value", "finished_at"}
                },
                "group_field": field,
                "group_value": None if value is None else str(value),
                "finished_at": row["finished_at"].isoformat()
                if isinstance(row["finished_at"], datetime)
                else str(row["finished_at"]),
            }
        )
    return result, sorted({int(row["dataset_id"]) for row in result})


def execute(sql: str, rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    schema = pa.schema(
        [(key, pa.int64() if key in {"dataset_id", "value"} else pa.string()) for key in COLUMNS]
    )
    with duckdb.connect(
        ":memory:", config={"threads": 1, "memory_limit": "256MB", "enable_external_access": False}
    ) as connection:
        connection.register("manager_metrics", pa.Table.from_pylist(rows, schema=schema))
        timer = threading.Timer(5, connection.interrupt)
        try:
            timer.start()
            cursor = connection.execute(sql)
            names = [column[0] for column in cursor.description]
            values = cursor.fetchmany(MAX_RESULT_ROWS + 1)
            return [dict(zip(names, row, strict=True)) for row in values[:MAX_RESULT_ROWS]], len(
                values
            ) > MAX_RESULT_ROWS
        except duckdb.Error:
            raise AppException(
                "Không thể thực thi truy vấn thống kê này", code="INVALID_SQL", status_code=422
            ) from None
        finally:
            timer.cancel()
