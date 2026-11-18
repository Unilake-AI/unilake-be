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


@dataclass(frozen=True)
class ImportField:
    name: str
    aliases: tuple[str, ...] = ()
    kind: str = "string"
    private: bool = False


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
                    "accepted_values": VALUE_ALIASES.get(f.name, {}),
                }
                for f in self.fields
            ],
        }


F = ImportField
PERSON = (
    F("full_name", ("Họ Và Tên", "Họ Tên", "Họ và tên", "Full Name"), private=True),
    F("date_of_birth", ("Ngày sinh",), "date", True),
    F("gender", ("Giới tính",)),
    F("email", ("Email", "Thư điện tử"), private=True),
    F("phone", ("Số điện thoại", "Điện thoại", "SĐT"), private=True),
)
EMPLOYEE_CODE = F("employee_code", ("Mã NV", "Mã nhân viên"))
APPLICANT_CODE = F("applicant_code", ("Mã thí sinh", "Mã TS"))
PROGRAM_CODE = F("program_code", ("Mã chương trình",))
YEAR = F("year_label", ("Năm tuyển sinh", "Niên khóa"))
METHOD = F("method_code", ("Mã phương thức", "Phương thức xét tuyển"))
REVISION = F(
    "expected_revision", ("Phiên bản hiện tại", "Phiên bản bản ghi", "Revision"), "integer"
)
REGISTRATION_KEYS = (APPLICANT_CODE, PROGRAM_CODE, YEAR, METHOD)
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
                F("position_code", ("Mã chức vụ", "Mã vị trí")),
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
                F("qualification_name", ("Tên bằng cấp", "Tên chứng chỉ")),
                F("qualification_type", ("Loại bằng cấp", "Loại chứng chỉ")),
                F("institution", ("Cơ sở cấp", "Nơi cấp")),
                F("year_obtained", ("Năm cấp",), "integer"),
            ),
            ("employee_code", "qualification_name"),
            ("employee_qualification",),
            ("employee", "qualification"),
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
                F("course_code", ("Mã học phần", "Mã môn", "Mã môn học")),
                F("semester_no", ("Học kỳ", "Học kỳ dự kiến"), "integer"),
                F("course_type", ("Loại học phần",)),
                F("is_required", ("Bắt buộc",), "boolean"),
                F("prerequisite_course_code", ("Mã học phần tiên quyết", "Mã môn tiên quyết")),
            ),
            ("program_code", "version", "course_code"),
            ("curriculum", "curriculum_course"),
            ("program", "course"),
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
    fields = {f.name: f for f in contract.fields}
    mapping = dict(options.field_mapping)
    errors = []
    for name in mapping:
        if name not in fields or mapping[name] not in columns:
            errors.append(issue("IMPORT_MAPPING_INVALID", name, column=mapping[name]))
    for name, field in fields.items():
        if name in mapping:
            continue
        accepted = {header(n) for n in (name, *field.aliases)}
        matches = [c for c in columns if header(c) in accepted]
        if len(matches) > 1:
            errors.append(issue("IMPORT_MAPPING_AMBIGUOUS", name, columns=matches))
        elif matches:
            mapping[name] = matches[0]
    for name in contract.anchors:
        if name not in mapping:
            errors.append(issue("IMPORT_COLUMN_REQUIRED", name))
    covered = list(mapping.values()) + options.ignored_columns
    if len(set(covered)) != len(covered) or not set(covered) <= set(columns):
        errors.append(issue("IMPORT_COLUMN_USED_TWICE_OR_UNKNOWN"))
    errors.extend(issue("IMPORT_MAPPING_REQUIRED", column=c) for c in columns if c not in covered)
    if errors:
        raise ImportValidationError(errors)
    return mapping


def detect_contract(domain: str, columns: list[str]) -> ImportOptions | None:
    candidates = []
    names = {header(c) for c in columns}
    for c in CONTRACTS.values():
        if (
            c.domain != domain
            or not c.enabled
            or c.name in {"admissions_results", "admissions_enrollment"}
        ):
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
        if field.name not in mapping:
            continue
        column = mapping[field.name]
        try:
            result[field.name] = convert(raw.get(column), field.kind, options.date_format)
            if result[field.name] is not None and field.name in VALUE_ALIASES:
                result[field.name] = VALUE_ALIASES[field.name][result[field.name].casefold()]
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
