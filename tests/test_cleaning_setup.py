"""Kiểm tra gợi ý từ file thật và việc lưu đúng lựa chọn quản trị viên đã xem trước."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.modules.ingestion.contracts.readers import (
    ReadRequest,
    SourceReaderFactory,
    read_file_snapshot,
)
from app.modules.ingestion.readers.file_snapshot import fingerprint
from app.modules.pipeline.models.profiles import SilverSourceProfile
from app.modules.pipeline.repositories.identity import IDENTITY_TABLES, CrosswalkRepository
from app.modules.pipeline.routers.cleaning_setup import router
from app.modules.pipeline.services import cleaning_setup as service
from tests.test_silver_management_api import environment  # noqa: F401

BASE = "/pipeline/cleaning/ingestion-jobs/42/setup"


@pytest.fixture
def setup_environment(environment, tmp_path, monkeypatch):  # noqa: F811
    client, engine, user = environment
    client.app.include_router(router)
    for table in IDENTITY_TABLES:
        table.create(engine)
    path = tmp_path / "positions.csv"
    path.write_text(
        "position_id,position_code,position_name,position_level\n007,NV,Chuyên viên,Staff\n",
        encoding="utf-8",
    )
    parsed = read_file_snapshot(
        ReadRequest(
            "CSV", str(path), {"has_header": True, "delimiter": ",", "encoding": "utf-8"}, path.name
        ),
        SourceReaderFactory.get_reader("CSV"),
        source_system_id="17",
        data_source_id="21",
        dataset_id="31",
    )
    assert parsed.provenance is not None
    parsed = replace(
        parsed,
        provenance=replace(
            parsed.provenance,
            source_ref="s3://bronze/hr/2026/11/15/job-42/positions.csv",
            object_version_id="pinned",
            ingestion_job_id=42,
        ),
    )
    context = {
        "ingestion_job_id": 42,
        "data_source_id": 21,
        "dataset_id": 31,
        "source_system_id": 17,
        "source_type": "CSV",
        "source_table": path.name,
        "domain": "hr",
        "created_at": datetime(2026, 11, 15, tzinfo=UTC),
        "output": {
            "source_type": "CSV",
            "parser_options": {"has_header": True, "delimiter": ",", "encoding": "utf-8"},
            "sha256": "a" * 64,
        },
    }
    monkeypatch.setattr(service, "_job_context", lambda *_: dict(context))
    monkeypatch.setattr(service, "read_bronze_snapshot", lambda *_: parsed)
    with engine.begin() as connection:
        repository = CrosswalkRepository(connection)
        repository.register(
            namespace="17",
            entity="position",
            source=("007",),
            evidence="Giữ nguyên mã nguồn trong kiểm thử",
            valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        )
        repository.freeze("source-17-v1")
    analysis = client.get(BASE)
    assert analysis.status_code == 200, analysis.text
    proposal = analysis.json()
    entity = next(item for item in proposal["entities"] if item["entity"] == "position")
    choices = {
        "analysis_fingerprint": proposal["analysis_fingerprint"],
        "entity": "position",
        "field_mapping": entity["field_mapping"],
        "crosswalk_release": "source-17-v1",
        "identity_mode": "canonical",
    }
    return client, engine, user, context, parsed, proposal, choices


def test_suggestions_do_not_save_or_cast_source_ids(setup_environment):
    client, engine, _, _, _, proposal, choices = setup_environment
    assert proposal["suggested_entity"] == "position"
    assert proposal["columns"][0]["leading_zero_count"] == 1
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == 1
    assert preview["cleaned_sample"][0]["position_id"] == "007"
    assert preview["normalized_sample"][0]["position_id"] == 1
    with engine.connect() as connection:
        assert connection.execute(select(SilverSourceProfile)).first() is None
    request = {"choices": choices, "confirmation_token": preview["confirmation_token"]}
    saved = client.post(f"{BASE}/confirm", json=request)
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1
    assert saved.json()["definition"]["data_source_id"] == 21
    assert saved.json()["definition"]["collapse_exact_duplicates"] is False
    assert saved.json()["approval_status"] == "NEEDS_DOMAIN_OWNER_APPROVAL"
    assert client.post(f"{BASE}/confirm", json=request).status_code == 409
    assert client.get("/pipeline/profiles").json()[0]["profile_id"] == saved.json()["profile_id"]


def test_no_identity_allows_cleaning_preview_but_never_saves_placeholder(setup_environment):
    client, engine, _, _, _, _, choices = setup_environment
    choices["crosswalk_release"] = None
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    assert response.json()["records_passed_cleaning"] == 1
    assert response.json()["blocking_reasons"]
    assert response.json()["confirmation_token"] is None
    with engine.connect() as connection:
        assert connection.execute(select(SilverSourceProfile)).first() is None


@pytest.mark.parametrize("change", ["actor", "choices", "file", "token", "expired", "revision"])
def test_confirm_must_match_preview_actor_rules_and_bronze(setup_environment, monkeypatch, change):
    client, engine, user, context, _, _, choices = setup_environment
    preview = client.post(f"{BASE}/preview", json=choices).json()
    token = preview["confirmation_token"]
    if change == "actor":
        user["sub"] = "2"
    elif change == "choices":
        choices["collapse_exact_duplicates"] = True
    elif change == "file":
        context["output"]["sha256"] = "b" * 64
    elif change == "token":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif change == "expired":
        now = service.time.time()
        monkeypatch.setattr(service.time, "time", lambda: now + 1801)
    else:
        assert (
            client.post(
                f"{BASE}/confirm", json={"choices": choices, "confirmation_token": token}
            ).status_code
            == 200
        )
    response = client.post(
        f"{BASE}/confirm", json={"choices": choices, "confirmation_token": token}
    )
    assert response.status_code == 409, response.text
    with engine.connect() as connection:
        assert len(connection.execute(select(SilverSourceProfile)).all()) == (
            1 if change == "revision" else 0
        )


@pytest.mark.parametrize("role", ["HR_STAFF", "HR_MANAGER", "VIEWER"])
def test_only_data_admin_can_read_or_confirm(setup_environment, role):
    client, _, user, _, _, _, choices = setup_environment
    user["roles"] = [role]
    assert client.get(BASE).status_code == 403
    assert client.post(f"{BASE}/preview", json=choices).status_code == 403
    assert (
        client.post(
            f"{BASE}/confirm", json={"choices": choices, "confirmation_token": "invalid"}
        ).status_code
        == 403
    )


def test_ambiguous_dates_are_not_guessed():
    assert service._date_options(["01/02/2026"]) == ["DMY", "MDY"]
    assert service._date_options(["25/02/2026"]) == ["DMY"]
    assert service._date_options(["2026-02-25"]) == ["YMD"]
    assert service._date_options(["not-a-date"]) == []


def test_source_mode_confirms_without_crosswalk_and_keeps_007_distinct_from_7(
    setup_environment, monkeypatch
):
    client, _, _, _, parsed, _, old = setup_environment
    choices = {**old, "identity_mode": "source", "crosswalk_release": None}
    parsed = replace(
        parsed,
        rows=[parsed.rows[0], ("7", "CV", "Chuyên viên khác", "Staff")],
        record_locators=("record:1", "record:2"),
    )
    monkeypatch.setattr(service, "read_bronze_snapshot", lambda *_: parsed)
    monkeypatch.setattr(
        CrosswalkRepository,
        "load",
        lambda *_: pytest.fail("Chế độ giữ nguồn không được đọc đối chiếu mã"),
    )
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == 2, preview["normalization_issues"]
    assert [row["position_id"] for row in preview["normalized_sample"]] == ["007", "7"]
    assert preview["blocking_reasons"] == []
    saved = client.post(
        f"{BASE}/confirm",
        json={"choices": choices, "confirmation_token": preview["confirmation_token"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["definition"]["identity_mode"] == "source"
    assert saved.json()["definition"]["crosswalk_release"] is None


@pytest.mark.parametrize("collapse", [False, True])
def test_unrecognized_file_uses_real_columns_without_inventing_primary_key(
    setup_environment, monkeypatch, collapse
):
    client, _, _, _, parsed, _, _ = setup_environment
    parsed = replace(
        parsed,
        columns=["tracking_code", "note"],
        rows=[("007", ""), ("007", ""), ("7", "khác")],
        record_locators=("record:1", "record:2", "record:3"),
    )
    assert parsed.provenance is not None
    parsed = replace(
        parsed,
        provenance=replace(parsed.provenance, columns_fingerprint=fingerprint(parsed.columns)),
    )
    monkeypatch.setattr(service, "read_bronze_snapshot", lambda *_: parsed)
    analysis = client.get(BASE).json()
    assert analysis["suggested_entity"] == "file_31"
    entity = next(item for item in analysis["entities"] if item["entity"] == "file_31")
    choices = {
        "analysis_fingerprint": analysis["analysis_fingerprint"],
        "entity": "file_31",
        "field_mapping": entity["field_mapping"],
        "identifier_fields": ["tracking_code"],
        "collapse_exact_duplicates": collapse,
    }
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == (2 if collapse else 3), preview
    assert preview["records_collapsed"] == (1 if collapse else 0)
    assert preview["records_quarantined"] == 0
    saved = client.post(
        f"{BASE}/confirm",
        json={"choices": choices, "confirmation_token": preview["confirmation_token"]},
    )
    assert saved.status_code == 200, saved.text
    definition = saved.json()["definition"]
    assert definition["source_schema"]["entities"]["file_31"]["primary_key"] == []
    assert definition["contracts"]["hr"]["$defs"]["file_31"]["x-primary-key"] == [
        "_source_record_id"
    ]


def test_generic_requires_date_choice_and_rejects_identity_integer_cast(
    setup_environment, monkeypatch
):
    client, _, _, _, parsed, _, _ = setup_environment
    parsed = replace(parsed, columns=["code", "day"], rows=[("007", "01/02/2026")])
    assert parsed.provenance is not None
    parsed = replace(
        parsed,
        provenance=replace(parsed.provenance, columns_fingerprint=fingerprint(parsed.columns)),
    )
    monkeypatch.setattr(service, "read_bronze_snapshot", lambda *_: parsed)
    analysis = client.get(BASE).json()
    choices = {
        "analysis_fingerprint": analysis["analysis_fingerprint"],
        "entity": "file_31",
        "field_mapping": {"code": "code", "day": "day"},
        "identifier_fields": ["code"],
        "field_types": {"code": "string", "day": "date"},
    }
    assert client.post(f"{BASE}/preview", json=choices).status_code == 422
    choices["date_formats"] = {"day": "DMY"}
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    assert response.json()["normalized_sample"][0] == {"code": "007", "day": "2026-02-01"}
    choices["field_types"]["code"] = "integer"
    assert client.post(f"{BASE}/preview", json=choices).status_code == 422


@pytest.mark.parametrize(
    "field", ["field_mapping", "ignored_columns", "analysis_fingerprint", "data_source_id"]
)
def test_cannot_skip_columns_or_replace_source(setup_environment, field):
    client, _, _, _, _, _, choices = setup_environment
    if field == "field_mapping":
        choices[field].pop("position_id")
    elif field == "ignored_columns":
        choices[field] = ["position_name"]
    elif field == "analysis_fingerprint":
        choices[field] = "0" * 64
    else:
        choices[field] = 999
    assert client.post(f"{BASE}/preview", json=choices).status_code in {409, 422}


@pytest.mark.parametrize("domain", ["hr", "academic", "admissions"])
def test_can_build_choices_for_every_supported_entity(setup_environment, monkeypatch, domain):
    _, engine, _, context, parsed, _, _ = setup_environment
    context["domain"] = domain
    source, contracts = service._templates(domain)
    for entity, schema in source["entities"].items():
        if entity not in contracts[domain]["$defs"]:
            continue
        columns = [column["name"] for column in schema["columns"]]
        empty = replace(parsed, columns=columns, rows=[])
        monkeypatch.setattr(service, "_capture", lambda *_, captured=empty: (context, captured))
        choices = service.CleaningChoices(
            analysis_fingerprint=service._analysis_fingerprint(context, empty, source, contracts),
            entity=entity,
            identity_mode="canonical",
            field_mapping={name: name for name in columns},
            reference_namespaces={
                name: "17"
                for name, spec in contracts[domain]["$defs"][entity]["properties"].items()
                if "x-references" in spec
            },
            date_formats={
                column["name"]: "YMD" for column in schema["columns"] if column["db_type"] == "date"
            },
        )
        _, _, profile, _ = service._definition(engine, 42, choices)
        assert profile.entity == entity
        assert profile.collapse_exact_duplicates is False


def test_excel_source_ids_keep_zero_prefix(setup_environment, monkeypatch, tmp_path):
    from openpyxl import Workbook

    client, engine, _, context, _, _, _ = setup_environment
    path = tmp_path / "positions.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["position_id", "position_code", "position_name", "position_level"])
    sheet.append(["007", "NV", "Chuyên viên", "Staff"])
    sheet.append([7, "CV", "Chuyên viên khác", "Staff"])
    workbook.save(path)
    parsed = read_file_snapshot(
        ReadRequest(
            "EXCEL",
            str(path),
            {"sheet_name": sheet.title, "header_row": 1, "skip_rows": 0},
            path.name,
        ),
        SourceReaderFactory.get_reader("EXCEL"),
        source_system_id="17",
        data_source_id="21",
        dataset_id="31",
    )
    assert parsed.provenance is not None
    parsed = replace(
        parsed,
        provenance=replace(
            parsed.provenance,
            source_ref="s3://bronze/hr/2026/11/15/job-42/positions.xlsx",
            object_version_id="pinned",
            ingestion_job_id=42,
        ),
    )
    context.update(source_type="EXCEL", source_table=path.name)
    context["output"].update(source_type="EXCEL")
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE data_source SET source_type='EXCEL' WHERE data_source_id=21")
        )
    monkeypatch.setattr(service, "read_bronze_snapshot", lambda *_: parsed)
    analysis = client.get(BASE).json()
    proposal = next(item for item in analysis["entities"] if item["entity"] == "position")
    choices = {
        "analysis_fingerprint": analysis["analysis_fingerprint"],
        "entity": "position",
        "field_mapping": proposal["field_mapping"],
    }
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == 2, preview
    assert [row["position_id"] for row in preview["normalized_sample"]] == ["007", "7"]
    assert (
        client.post(
            f"{BASE}/confirm",
            json={"choices": choices, "confirmation_token": preview["confirmation_token"]},
        ).status_code
        == 200
    )


def test_employee_preview_derives_birth_year_without_publishing_personal_fields(
    setup_environment, monkeypatch, tmp_path
):
    client, engine, _, context, _, _, _ = setup_environment
    path = tmp_path / "employees.csv"
    path.write_text(
        "employee_id,employee_code,full_name,date_of_birth,gender,email,phone,hire_date,employee_status\n007,NV0007,Người kiểm thử,1990-02-03,Male,person@example.org,0901234567,,Active\n",
        encoding="utf-8",
    )
    parsed = read_file_snapshot(
        ReadRequest(
            "CSV", str(path), {"delimiter": ",", "encoding": "utf-8", "has_header": True}, path.name
        ),
        SourceReaderFactory.get_reader("CSV"),
        source_system_id="17",
        data_source_id="21",
        dataset_id="31",
    )
    assert parsed.provenance is not None
    parsed = replace(
        parsed,
        provenance=replace(
            parsed.provenance,
            source_ref="s3://bronze/hr/2026/11/15/job-42/employees.csv",
            object_version_id="pinned",
            ingestion_job_id=42,
        ),
    )
    context["source_table"] = path.name
    monkeypatch.setattr(service, "read_bronze_snapshot", lambda *_: parsed)
    with engine.begin() as connection:
        repo = CrosswalkRepository(connection)
        repo.register(
            namespace="17",
            entity="employee",
            source=("007",),
            evidence="Đã đối chiếu mã nhân viên trong kiểm thử",
            valid_from=datetime(2020, 1, 1, tzinfo=UTC),
        )
        repo.freeze("employee-v1")
    analysis = client.get(BASE).json()
    entity = next(item for item in analysis["entities"] if item["entity"] == "employee")
    assert analysis["suggested_entity"] == "employee"
    choices = {
        "analysis_fingerprint": analysis["analysis_fingerprint"],
        "entity": "employee",
        "field_mapping": entity["field_mapping"],
        "ignored_columns": ["full_name", "email", "phone"],
        "date_formats": {"birth_year": "YMD", "hire_date": "YMD"},
        "crosswalk_release": "employee-v1",
        "identity_mode": "canonical",
    }
    response = client.post(f"{BASE}/preview", json=choices)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["records_normalized"] == 1, preview["normalization_issues"]
    assert preview["normalized_sample"][0]["birth_year"] == 1990
    assert not {"full_name", "date_of_birth", "email", "phone"} & set(
        preview["normalized_sample"][0]
    )
    assert preview["cleaned_sample"][0]["employee_id"] == "007"
