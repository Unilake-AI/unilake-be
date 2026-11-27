from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.core.deps import get_current_user
from app.core.exceptions import AppException, app_exception_handler
from app.modules.ingestion.router import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(AppException, app_exception_handler)
    app.dependency_overrides[get_current_user] = lambda: {
        "sub": "1",
        "roles": ["HR_STAFF", "ACADEMIC_STAFF", "ADMISSIONS_STAFF"],
    }
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("domain", "kind", "columns"),
    [
        ("hr", "hr_employee", ["Mã NV", "Họ Tên", "Là giảng viên", "Trạng thái nhân viên"]),
        ("hr", "hr_assignment", ["Mã phân công", "Mã NV", "Mã phòng", "Mã chức vụ"]),
        ("hr", "hr_qualification", ["Mã NV", "Tên bằng cấp"]),
        ("hr", "hr_position_catalog", ["Mã chức vụ", "Tên chức vụ", "Cấp chức vụ"]),
        ("hr", "hr_qualification_catalog", ["Tên văn bằng", "Loại văn bằng"]),
        ("academic", "academic_curriculum", ["Mã chương trình", "Phiên bản", "Mã học phần"]),
        ("academic", "academic_student", ["MSSV", "Mã chương trình", "Năm nhập học"]),
        ("academic", "academic_class", ["Mã lớp học phần", "Mã môn", "Mã học kỳ"]),
        ("academic", "academic_enrollment", ["Mã SV", "Mã lớp", "Trạng thái đăng ký"]),
        ("academic", "academic_enrollment_grade", ["Mã SV", "Mã lớp", "Điểm hệ 10"]),
        (
            "admissions",
            "admissions_year_catalog",
            ["Năm tuyển sinh", "Ngày bắt đầu", "Ngày kết thúc", "Trạng thái"],
        ),
        (
            "admissions",
            "admissions_method_catalog",
            ["Mã phương thức", "Tên phương thức", "Thang điểm", "Đang áp dụng"],
        ),
        (
            "admissions",
            "admissions_registration",
            ["Mã thí sinh", "Mã chương trình", "Năm tuyển sinh", "Mã phương thức"],
        ),
        (
            "admissions",
            "admissions_results",
            [
                "Mã thí sinh",
                "Mã chương trình",
                "Năm tuyển sinh",
                "Mã phương thức",
                "Phiên bản hiện tại",
                "Điểm xét tuyển",
                "Kết quả xét tuyển",
            ],
        ),
        (
            "admissions",
            "admissions_enrollment",
            [
                "Mã thí sinh",
                "Mã chương trình",
                "Năm tuyển sinh",
                "Mã phương thức",
                "Phiên bản hiện tại",
                "Trạng thái nhập học",
            ],
        ),
    ],
)
def test_staff_can_read_csv_headers_and_analyze_each_supported_workflow(
    client, domain, kind, columns
):
    read = client.post(
        "/ingestion/imports/file-layout",
        data={"domain": domain, "parser_options": '{"delimiter":";","skip_rows":1}'},
        files={
            "file": (
                "input.csv",
                ("Thông tin tệp\n" + ";".join(columns) + "\n").encode("utf-8-sig"),
            )
        },
    )
    assert read.status_code == 200, read.text
    assert read.json()["columns"] == columns
    analysis = client.post(
        "/ingestion/imports/analyze",
        json={"domain": domain, "columns": columns, "options": {"import_type": kind}},
    )
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["contract"]["import_type"] == kind


def test_workbook_requires_sheet_selection_and_reads_the_configured_header(client):
    wb = Workbook()
    wb.active.title = "Huong dan"
    sheet = wb.create_sheet("Ket qua")
    sheet.append(["Tiêu đề tài liệu"])
    sheet.append(["Mã thí sinh", "Điểm xét tuyển"])
    content = BytesIO()
    wb.save(content)
    wb.close()
    files = {"file": ("admissions.xlsx", content.getvalue())}
    response = client.post(
        "/ingestion/imports/file-layout", data={"domain": "admissions"}, files=files
    )
    assert response.json() == {
        "sheet_names": ["Huong dan", "Ket qua"],
        "sheet_name": None,
        "columns": [],
    }
    selected = client.post(
        "/ingestion/imports/file-layout",
        data={"domain": "admissions", "parser_options": '{"sheet_name":"Ket qua","header_row":2}'},
        files=files,
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["columns"] == ["Mã thí sinh", "Điểm xét tuyển"]


def test_analysis_suggests_results_for_a_file_labeled_as_registration(client):
    columns = [
        "Mã thí sinh",
        "Mã chương trình",
        "Năm tuyển sinh",
        "Mã phương thức",
        "Phiên bản hiện tại",
        "Điểm xét tuyển",
        "Kết quả xét tuyển",
    ]
    response = client.post(
        "/ingestion/imports/analyze",
        json={
            "domain": "admissions",
            "columns": columns,
            "options": {"import_type": "admissions_registration"},
        },
    )
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert details["suggested_workflow"] == "Cập nhật kết quả xét tuyển"
    assert {item["column"] for item in details["issues"]} == {"Điểm xét tuyển", "Kết quả xét tuyển"}


def test_analysis_can_keep_one_alias_and_explicitly_ignore_the_other(client):
    request = {
        "domain": "hr",
        "columns": ["Mã NV", "Họ Tên", "Họ Và Tên", "Là giảng viên"],
        "options": {"import_type": "hr_employee"},
    }
    response = client.post("/ingestion/imports/analyze", json=request)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IMPORT_MAPPING_AMBIGUOUS"
    request["options"]["ignored_columns"] = ["Họ Tên"]
    response = client.post("/ingestion/imports/analyze", json=request)
    assert response.status_code == 200, response.text
    assert response.json()["field_mapping"]["full_name"] == "Họ Và Tên"
