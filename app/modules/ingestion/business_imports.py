"""Versioned input contracts shared by upload, Silver and the Gold database writer."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def packed(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(packed(value).encode()).hexdigest()


def header(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", "", value)


class ImportOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_type: str
    version: Literal["1"] = "1"
    field_mapping: dict[str, str] = Field(default_factory=dict)
    ignored_columns: list[str] = Field(default_factory=list)
    date_format: Literal["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"] = "%Y-%m-%d"
    blank_update: Literal["preserve"] = "preserve"
    context: dict[str, str] = Field(default_factory=dict, max_length=3)

    def metadata(self) -> dict[str, Any]:
        # Keep historical v1 job signatures unchanged when no context was supplied.
        return self.model_dump(mode="json", exclude={"context"} if not self.context else None)


CONTEXT_FIELDS = {
    "admissions_registration": ("program_code", "year_label", "method_code"),
    "academic_curriculum": ("program_code", "version"),
}


@dataclass(frozen=True)
class ImportField:
    name: str
    aliases: tuple[str, ...] = ()
    kind: str = "string"
    private: bool = False
    values: dict[str, str] | None = None

    @property
    def accepted_values(self) -> dict[str, str]:
        return self.values if self.values is not None else VALUE_ALIASES.get(self.name, {})


@dataclass(frozen=True)
class ImportContract:
    name: str
    domain: str
    label: str
    fields: tuple[ImportField, ...]
    anchors: tuple[str, ...]
    writes: tuple[str, ...]
    lookups: tuple[str, ...] = ()
    enabled: bool = True

    @property
    def entity(self) -> str:
        return "import_" + self.name + "_v1"

    def describe(self) -> dict[str, Any]:
        return {
            "import_type": self.name,
            "version": "1",
            "domain": self.domain,
            "label": self.label,
            "enabled": self.enabled,
            "entity": self.entity,
            "write_tables": self.writes,
            "lookup_tables": self.lookups,
            "identity_fields": self.anchors,
            "fields": [
                {
                    "name": f.name,
                    "aliases": f.aliases,
                    "kind": f.kind,
                    "private": f.private,
                    "accepted_values": f.accepted_values,
                }
                for f in self.fields
            ],
        }


F = ImportField
PERSON_DETAILS = (
    F("full_name", ("Họ Và Tên", "Họ Tên", "Họ và tên", "Full Name"), private=True),
    F("date_of_birth", ("Ngày sinh",), "date", True),
    F("gender", ("Giới tính",)),
)
PERSON = (
    *PERSON_DETAILS,
    F("email", ("Email", "Thư điện tử"), private=True),
    F("phone", ("Số điện thoại", "Điện thoại", "SĐT"), private=True),
)
EMPLOYEE_CODE = F("employee_code", ("Mã NV", "Mã nhân viên"))
APPLICANT_CODE = F("applicant_code", ("Mã thí sinh", "Mã TS"))
PROGRAM_CODE = F("program_code", ("Mã chương trình",))
YEAR = F("year_label", ("Năm tuyển sinh", "Niên khóa"))
METHOD = F("method_code", ("Mã phương thức", "Phương thức xét tuyển"))
POSITION_CODE = F("position_code", ("Mã chức vụ", "Mã vị trí"))
QUALIFICATION_NAME = F("qualification_name", ("Tên bằng cấp", "Tên chứng chỉ"))
QUALIFICATION_TYPE = F("qualification_type", ("Loại bằng cấp", "Loại chứng chỉ"))
REVISION = F(
    "expected_revision", ("Phiên bản hiện tại", "Phiên bản bản ghi", "Revision"), "integer"
)
REGISTRATION_KEYS = (APPLICANT_CODE, PROGRAM_CODE, YEAR, METHOD)
STUDENT_CODE = F("student_code", ("Mã sinh viên", "Mã SV", "MSSV"))
CLASS_CODE = F("class_code", ("Mã lớp học phần", "Mã lớp"))
COURSE_CODE = F("course_code", ("Mã học phần", "Mã môn", "Mã môn học"))
SEMESTER_CODE = F("semester_code", ("Mã học kỳ",))
CLASS_REGISTRATION_KEYS = ("student_code", "class_code")
ACADEMIC_RECORD_IMPORTS = frozenset(
    {"academic_student", "academic_class", "academic_enrollment", "academic_enrollment_grade"}
)
CATALOG_IMPORTS = frozenset(
    {
        "hr_position_catalog",
        "hr_qualification_catalog",
        "admissions_year_catalog",
        "admissions_method_catalog",
    }
)
EXPLICIT_IMPORTS = (
    ACADEMIC_RECORD_IMPORTS | CATALOG_IMPORTS | {"admissions_results", "admissions_enrollment"}
)
CONTRACTS = {
    c.name: c
    for c in (
        ImportContract(
            "hr_employee",
            "hr",
            "Hồ sơ nhân viên kèm giảng viên",
            (
                EMPLOYEE_CODE,
                *PERSON,
                F("hire_date", ("Ngày vào làm", "Ngày tuyển dụng"), "date"),
                F("employee_status", ("Trạng thái nhân viên", "Tình trạng nhân viên")),
                F("is_lecturer", ("Là giảng viên",), "boolean"),
                F("academic_degree", ("Học vị",)),
                F("academic_title", ("Học hàm",)),
                F("research_field", ("Lĩnh vực nghiên cứu",)),
            ),
            ("employee_code", "is_lecturer"),
            ("employee", "lecturer"),
        ),
        ImportContract(
            "hr_assignment",
            "hr",
            "Phân công công tác",
            (
                F("assignment_code", ("Mã phân công",)),
                EMPLOYEE_CODE,
                F("department_code", ("Mã phòng", "Mã phòng ban")),
                POSITION_CODE,
                F("start_date", ("Ngày bắt đầu",), "date"),
                F("end_date", ("Ngày kết thúc",), "date"),
                F("is_primary", ("Là phân công chính",), "boolean"),
                F("assignment_status", ("Trạng thái phân công",)),
            ),
            ("assignment_code", "employee_code", "department_code", "position_code"),
            ("assignment",),
            ("employee", "department", "position"),
        ),
        ImportContract(
            "hr_qualification",
            "hr",
            "Bằng cấp nhân viên",
            (
                EMPLOYEE_CODE,
                QUALIFICATION_NAME,
                QUALIFICATION_TYPE,
                F("institution", ("Cơ sở cấp", "Nơi cấp")),
                F("year_obtained", ("Năm cấp",), "integer"),
            ),
            ("employee_code", "qualification_name"),
            ("employee_qualification",),
            ("employee", "qualification"),
        ),
        ImportContract(
            "hr_position_catalog",
            "hr",
            "Danh mục chức vụ",
            (
                POSITION_CODE,
                F("position_name", ("Tên chức vụ", "Tên vị trí")),
                F(
                    "position_level",
                    ("Cấp chức vụ", "Cấp bậc"),
                    values={
                        "leadership": "Leadership",
                        "management": "Management",
                        "staff": "Staff",
                        "lãnh đạo": "Leadership",
                        "quản lý": "Management",
                        "nhân viên": "Staff",
                    },
                ),
            ),
            ("position_code",),
            ("position",),
        ),
        ImportContract(
            "hr_qualification_catalog",
            "hr",
            "Danh mục văn bằng, chứng chỉ",
            (
                F("qualification_name", (*QUALIFICATION_NAME.aliases, "Tên văn bằng")),
                F("qualification_type", (*QUALIFICATION_TYPE.aliases, "Loại văn bằng")),
            ),
            ("qualification_name",),
            ("qualification",),
        ),
        ImportContract(
            "admissions_registration",
            "admissions",
            "Hồ sơ đăng ký tuyển sinh",
            (
                *REGISTRATION_KEYS,
                *PERSON,
                F("national_id", ("CCCD", "CMND"), private=True),
                F("high_school_name", ("Trường THPT",)),
                F("province", ("Tỉnh", "Tỉnh thành")),
                F("preference_rank", ("Thứ tự nguyện vọng", "Nguyện vọng"), "integer"),
                F("application_date", ("Ngày đăng ký",), "date"),
                F("admission_status", ("Trạng thái đăng ký",)),
                REVISION,
            ),
            tuple(f.name for f in REGISTRATION_KEYS),
            ("applicant", "admission"),
            ("program", "admission_year", "admission_method"),
        ),
        ImportContract(
            "admissions_results",
            "admissions",
            "Cập nhật kết quả xét tuyển",
            (
                *REGISTRATION_KEYS,
                F("admission_score", ("Điểm xét tuyển",), "decimal"),
                F("admission_status", ("Kết quả xét tuyển",)),
                REVISION,
            ),
            (*tuple(f.name for f in REGISTRATION_KEYS), "expected_revision", "admission_status"),
            ("admission",),
            ("applicant", "admission"),
        ),
        ImportContract(
            "admissions_enrollment",
            "admissions",
            "Cập nhật trạng thái nhập học",
            (
                *REGISTRATION_KEYS,
                F("enrollment_status", ("Trạng thái nhập học",)),
                F("enrollment_date", ("Ngày xác nhận nhập học", "Ngày nhập học"), "date"),
                REVISION,
            ),
            (*tuple(f.name for f in REGISTRATION_KEYS), "expected_revision", "enrollment_status"),
            ("admission",),
            ("admission",),
        ),
        ImportContract(
            "admissions_year_catalog",
            "admissions",
            "Danh mục năm tuyển sinh",
            (
                YEAR,
                F("start_date", ("Ngày bắt đầu", "Ngày mở tuyển sinh"), "date"),
                F("end_date", ("Ngày kết thúc", "Ngày đóng tuyển sinh"), "date"),
                F(
                    "status",
                    ("Trạng thái năm tuyển sinh", "Trạng thái"),
                    values={
                        "upcoming": "UPCOMING",
                        "open": "OPEN",
                        "closed": "CLOSED",
                        "sắp mở": "UPCOMING",
                        "đang mở": "OPEN",
                        "đã đóng": "CLOSED",
                    },
                ),
            ),
            ("year_label",),
            ("admission_year",),
        ),
        ImportContract(
            "admissions_method_catalog",
            "admissions",
            "Danh mục phương thức xét tuyển",
            (
                METHOD,
                F("method_name", ("Tên phương thức", "Tên phương thức xét tuyển")),
                F("max_score", ("Điểm tối đa", "Thang điểm"), "decimal"),
                F("description", ("Mô tả",)),
                F("is_active", ("Đang áp dụng", "Đang sử dụng", "Hoạt động"), "boolean"),
            ),
            ("method_code",),
            ("admission_method",),
            ("admission",),
        ),
        ImportContract(
            "academic_curriculum",
            "academic",
            "Khung chương trình kèm học phần",
            (
                PROGRAM_CODE,
                F("version", ("Phiên bản",)),
                F("curriculum_code", ("Mã khung", "Mã khung chương trình")),
                F("curriculum_name", ("Tên khung", "Tên khung chương trình")),
                F("effective_from", ("Hiệu lực từ",), "date"),
                F("effective_to", ("Hiệu lực đến",), "date"),
                F("status", ("Trạng thái khung",)),
                COURSE_CODE,
                F("semester_no", ("Học kỳ", "Học kỳ dự kiến"), "integer"),
                F("course_type", ("Loại học phần",)),
                F("is_required", ("Bắt buộc",), "boolean"),
                F("prerequisite_course_code", ("Mã học phần tiên quyết", "Mã môn tiên quyết")),
            ),
            ("program_code", "version", "course_code"),
            ("curriculum", "curriculum_course"),
            ("program", "course"),
        ),
        ImportContract(
            "academic_student",
            "academic",
            "Hồ sơ sinh viên",
            (
                STUDENT_CODE,
                *PERSON_DETAILS,
                PROGRAM_CODE,
                F("year_label", (*YEAR.aliases, "Khóa tuyển sinh", "Năm nhập học")),
                F(
                    "student_status",
                    ("Trạng thái sinh viên", "Tình trạng sinh viên"),
                    values={
                        "active": "Active",
                        "graduated": "Graduated",
                        "suspended": "Suspended",
                        "dropped": "Dropped",
                        "đang học": "Active",
                        "đã tốt nghiệp": "Graduated",
                        "tạm dừng học": "Suspended",
                        "đã thôi học": "Dropped",
                    },
                ),
            ),
            ("student_code", "program_code", "year_label"),
            ("student",),
            ("program", "admission_year"),
        ),
        ImportContract(
            "academic_class",
            "academic",
            "Danh sách lớp học phần",
            (
                CLASS_CODE,
                COURSE_CODE,
                SEMESTER_CODE,
                F("max_capacity", ("Sĩ số tối đa",), "integer"),
                F("room", ("Phòng học",)),
                F(
                    "class_status",
                    ("Trạng thái lớp học phần", "Trạng thái lớp"),
                    values={
                        "open": "Open",
                        "inprogress": "InProgress",
                        "completed": "Completed",
                        "cancelled": "Cancelled",
                        "đang mở": "Open",
                        "đang học": "InProgress",
                        "đã hoàn thành": "Completed",
                        "đã hủy": "Cancelled",
                    },
                ),
            ),
            ("class_code", "course_code", "semester_code"),
            ("class",),
            ("course", "semester"),
        ),
        ImportContract(
            "academic_enrollment",
            "academic",
            "Danh sách đăng ký học phần",
            (
                STUDENT_CODE,
                CLASS_CODE,
                F("enrollment_date", ("Ngày đăng ký học phần", "Ngày đăng ký"), "date"),
                F(
                    "enrollment_status",
                    ("Trạng thái đăng ký học phần", "Trạng thái đăng ký"),
                    values={
                        "registered": "Registered",
                        "withdrawn": "Withdrawn",
                        "completed": "Completed",
                        "đã đăng ký": "Registered",
                        "đã rút": "Withdrawn",
                        "đã hoàn thành": "Completed",
                    },
                ),
            ),
            CLASS_REGISTRATION_KEYS,
            ("enrollment",),
            ("student", "class", "semester"),
        ),
        ImportContract(
            "academic_enrollment_grade",
            "academic",
            "Bảng điểm học phần",
            (
                STUDENT_CODE,
                CLASS_CODE,
                F("midterm_score", ("Điểm giữa kỳ",), "decimal"),
                F("final_score", ("Điểm cuối kỳ",), "decimal"),
                F("total_score", ("Điểm tổng kết", "Điểm hệ 10"), "decimal"),
                F("grade_value", ("Điểm hệ 4",), "decimal"),
                F(
                    "letter_grade",
                    ("Điểm chữ",),
                    values={
                        grade.lower(): grade
                        for grade in ("A", "B+", "B", "C+", "C", "D+", "D", "F")
                    },
                ),
                F("registration_date", ("Ngày đăng ký học phần", "Ngày đăng ký"), "date"),
            ),
            CLASS_REGISTRATION_KEYS,
            ("enrollment_grade",),
            ("student", "class", "enrollment"),
        ),
    )
}


# Explicit dictionaries, scoped by logical field; no fuzzy value matching.
VALUE_ALIASES = {
    "enrollment_status": {
        "pending": "PENDING",
        "confirmed": "CONFIRMED",
        "declined": "DECLINED",
        "withdrawn": "WITHDRAWN",
        "chờ xác nhận": "PENDING",
        "đã xác nhận": "CONFIRMED",
        "từ chối": "DECLINED",
        "đã rút": "WITHDRAWN",
    },
    "employee_status": {"active": "ACTIVE", "resigned": "RESIGNED", "retired": "RETIRED"},
    "assignment_status": {"active": "ACTIVE", "ended": "ENDED"},
    "admission_status": {
        "applied": "APPLIED",
        "accepted": "ACCEPTED",
        "rejected": "REJECTED",
        "waitlisted": "WAITLISTED",
    },
    "gender": {"male": "Male", "female": "Female", "nam": "Male", "nữ": "Female"},
    "academic_degree": {
        "b.sc.": "B.Sc.",
        "m.sc.": "M.Sc.",
        "ph.d.": "Ph.D.",
        "cử nhân": "B.Sc.",
        "thạc sĩ": "M.Sc.",
        "tiến sĩ": "Ph.D.",
    },
    "course_type": {
        "general": "General",
        "foundation": "Foundation",
        "major": "Major",
        "elective": "Elective",
    },
    "qualification_type": {
        "degree": "Degree",
        "certificate": "Certificate",
        "bằng cấp": "Degree",
        "chứng chỉ": "Certificate",
    },
    "status": {"draft": "DRAFT", "active": "ACTIVE", "retired": "RETIRED"},
}


class ImportValidationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        self.issues = issues
        super().__init__(issues[0]["code"] if issues else "IMPORT_INVALID")

    def report(self) -> dict[str, Any]:
        return {"code": str(self), "issues": self.issues[:200], "issue_count": len(self.issues)}


def issue(code: str, field: str | None = None, **location: Any) -> dict[str, Any]:
    return {"code": code, "field": field, **location}


def contract_for(options: ImportOptions, domain: str) -> ImportContract:
    contract = CONTRACTS.get(options.import_type)
    if contract is None or contract.domain != domain:
        raise ImportValidationError([issue("IMPORT_TYPE_OR_DOMAIN_INVALID")])
    if not contract.enabled:
        raise ImportValidationError([issue("IMPORT_BUSINESS_POLICY_REQUIRED")])
    errors = [
        issue("IMPORT_CONTEXT_INVALID", name)
        for name, value in options.context.items()
        if name not in CONTEXT_FIELDS.get(contract.name, ())
        or not value.strip()
        or len(value) > 128
        or "\x00" in value
    ]
    if errors:
        raise ImportValidationError(errors)
    return contract


def supported_contract_fingerprints(contract: ImportContract) -> set[str]:
    definition = contract.describe()
    result = {fingerprint(definition)}
    if contract.name == "admissions_registration":
        # Previously published v1 registration files predate the optional revision column.
        definition["fields"] = [f for f in definition["fields"] if f["name"] != "expected_revision"]
        result.add(fingerprint(definition))
    return result


def resolve_layout(
    contract: ImportContract, columns: list[str], options: ImportOptions
) -> dict[str, str]:
    mapping, errors = inspect_layout(contract, columns, options)
    if errors:
        raise ImportValidationError(errors)
    return mapping


def inspect_layout(
    contract: ImportContract, columns: list[str], options: ImportOptions
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    fields = {f.name: f for f in contract.fields}
    mapping = dict(options.field_mapping)
    errors = []
    for name in mapping:
        if name not in fields or mapping[name] not in columns:
            errors.append(issue("IMPORT_MAPPING_INVALID", name, column=mapping[name]))
    ignored = set(options.ignored_columns)
    for name, field in fields.items():
        if name in mapping:
            continue
        accepted = {header(n) for n in (name, *field.aliases)}
        matches = [c for c in columns if c not in ignored and header(c) in accepted]
        if len(matches) > 1:
            errors.append(issue("IMPORT_MAPPING_AMBIGUOUS", name, columns=matches))
        elif matches:
            mapping[name] = matches[0]
    for name in contract.anchors:
        if name not in mapping and name not in options.context:
            errors.append(issue("IMPORT_COLUMN_REQUIRED", name))
    covered = list(mapping.values()) + options.ignored_columns
    if len(set(covered)) != len(covered) or not set(covered) <= set(columns):
        errors.append(issue("IMPORT_COLUMN_USED_TWICE_OR_UNKNOWN"))
    errors.extend(issue("IMPORT_MAPPING_REQUIRED", column=c) for c in columns if c not in covered)
    return mapping, errors


def detect_contract(domain: str, columns: list[str]) -> ImportOptions | None:
    candidates = []
    names = {header(c) for c in columns}
    for c in CONTRACTS.values():
        if c.domain != domain or not c.enabled or c.name in EXPLICIT_IMPORTS:
            continue
        fields = {f.name: f for f in c.fields}
        if all(names & {header(v) for v in (n, *fields[n].aliases)} for n in c.anchors):
            candidates.append(c)
    if len(candidates) > 1:
        raise ImportValidationError([issue("IMPORT_TYPE_AMBIGUOUS")])
    return ImportOptions(import_type=candidates[0].name) if candidates else None


def convert(value: Any, kind: str, fmt: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if kind == "string":
        # Excel numeric identifiers may already have lost zeros; never guess them back.
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("IMPORT_TEXT_REQUIRED")
        return unicodedata.normalize("NFC", value.strip())
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        variants = {"có": True, "không": False, "true": True, "false": False, "1": True, "0": False}
        return variants[str(value).strip().casefold()]
    if kind == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        result = datetime.strptime(str(value), fmt).date()
        if result.strftime(fmt) != value:
            raise ValueError("IMPORT_DATE_INVALID")
        return result
    number = Decimal(str(value))
    if isinstance(value, bool) or not number.is_finite():
        raise ValueError("IMPORT_NUMBER_INVALID")
    if kind == "integer":
        if number != number.to_integral_value():
            raise ValueError("IMPORT_INTEGER_REQUIRED")
        return int(number)
    return number


def normalize_row(
    contract: ImportContract,
    raw: dict[str, Any],
    mapping: dict[str, str],
    options: ImportOptions,
    locator: str,
) -> dict[str, Any]:
    result, errors = {}, []
    for field in contract.fields:
        column = mapping.get(field.name)
        context_value = options.context.get(field.name)
        if column is None and context_value is None:
            continue
        try:
            value = convert(
                raw.get(column) if column is not None else None, field.kind, options.date_format
            )
            if context_value is not None:
                expected = convert(context_value, field.kind, options.date_format)
                if value is not None and value != expected:
                    errors.append(
                        issue("IMPORT_CONTEXT_MISMATCH", field.name, locator=locator, column=column)
                    )
                value = expected
            if value is not None and (accepted := field.accepted_values):
                value = accepted[value.casefold()]
            result[field.name] = value
        except (ValueError, KeyError, InvalidOperation, OverflowError):
            errors.append(issue("IMPORT_VALUE_INVALID", field.name, locator=locator, column=column))
    for name in contract.anchors:
        if result.get(name) is None:
            errors.append(
                issue("IMPORT_VALUE_REQUIRED", name, locator=locator, column=mapping.get(name))
            )
    if errors:
        raise ImportValidationError(errors)
    return result
