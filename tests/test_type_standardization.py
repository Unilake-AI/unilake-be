from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.pipeline.schema_mapping import MappedRow, MappingKey, MappingResult
from app.modules.pipeline.type_standardization import (
    ConversionConfigurationError,
    ConversionError,
    FieldOptions,
    TypeStandardizationService,
    convert_value,
)

ROOT = Path(__file__).resolve().parents[1]


def spec(physical, **extras):
    return {"type": "string", "x-physical-type": physical, **extras}


DEC = spec("decimal(6,2)", **{"x-precision": 6, "x-scale": 2})


@pytest.mark.parametrize(
    ("value", "target", "options", "expected"),
    [
        ("007", spec("varchar(30)"), FieldOptions(), "007"),
        ("  A\u0301  ", spec("utf8"), FieldOptions(trim=True), "Á"),
        ("-2147483648", spec("int"), FieldOptions(), -2147483648),
        ("9223372036854775807", spec("bigint"), FieldOptions(), 9223372036854775807),
        (
            "1.234,50",
            DEC,
            FieldOptions(decimal_separator=",", thousands_separator="."),
            Decimal("1234.50"),
        ),
        (Decimal("0.1"), DEC, FieldOptions(), Decimal("0.10")),
        ("Có", spec("boolean"), FieldOptions(true_tokens=("Có",), false_tokens=("Không",)), True),
        ("false", spec("boolean"), FieldOptions(), False),
        ("31/12/2026", spec("date32"), FieldOptions(date_format="%d/%m/%Y"), date(2026, 12, 31)),
        (61, spec("date32"), FieldOptions(excel_epoch="1900"), date(1900, 3, 1)),
        (0, spec("date32"), FieldOptions(excel_epoch="1904"), date(1904, 1, 1)),
        (
            "2026-01-01T07:00:00+07:00",
            spec("timestamp[us,UTC]"),
            FieldOptions(),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
        ("", spec("int", type=["integer", "null"]), FieldOptions(null_tokens=("",)), None),
        ("0", spec("int", type=["integer", "null"]), FieldOptions(null_tokens=("",)), 0),
    ],
)
def test_values(value, target, options, expected):
    result = convert_value(value, target, options)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize(
    ("value", "target", "options", "code"),
    [
        ("2147483648", spec("int"), FieldOptions(), "INTEGER_OVERFLOW"),
        ("12.7", spec("int"), FieldOptions(), "INTEGER_PARSE_ERROR"),
        (True, spec("bigint"), FieldOptions(), "INTEGER_PARSE_ERROR"),
        (1.1, DEC, FieldOptions(), "DECIMAL_PARSE_ERROR"),
        ("1e2", DEC, FieldOptions(), "DECIMAL_PARSE_ERROR"),
        ("12,34.00", DEC, FieldOptions(thousands_separator=","), "DECIMAL_PARSE_ERROR"),
        ("1.234", DEC, FieldOptions(), "DECIMAL_SCALE_ERROR"),
        ("10000", DEC, FieldOptions(), "DECIMAL_OVERFLOW"),
        (Decimal("NaN"), DEC, FieldOptions(), "DECIMAL_PARSE_ERROR"),
        ("31/02/2026", spec("date32"), FieldOptions(date_format="%d/%m/%Y"), "DATE_PARSE_ERROR"),
        ("3/4/2026", spec("date32"), FieldOptions(date_format="%d/%m/%Y"), "DATE_PARSE_ERROR"),
        (60, spec("date32"), FieldOptions(excel_epoch="1900"), "EXCEL_FICTITIOUS_DATE"),
        (1.5, spec("date32"), FieldOptions(excel_epoch="1900"), "EXCEL_DATE_ERROR"),
        (45200, spec("date32"), FieldOptions(), "EXCEL_EPOCH_REQUIRED"),
        (datetime(2026, 1, 1), spec("date32"), FieldOptions(), "DATE_TIME_LOSS"),
        ("2026-01-01T00:00:00", spec("timestamp[us,UTC]"), FieldOptions(), "TIMEZONE_REQUIRED"),
        (
            "2026-01-01T00:00:00.1234567Z",
            spec("timestamp[us,UTC]"),
            FieldOptions(),
            "TIMESTAMP_PRECISION_ERROR",
        ),
        ("2026-01-01", spec("timestamp[us,UTC]"), FieldOptions(), "TIMESTAMP_PARSE_ERROR"),
        ("maybe", spec("boolean"), FieldOptions(), "INVALID_BOOLEAN"),
        (0, spec("boolean"), FieldOptions(), "INVALID_BOOLEAN"),
        (7, spec("varchar(30)"), FieldOptions(), "STRING_TYPE_ERROR"),
        ("abc", spec("utf8", maxLength=2), FieldOptions(), "STRING_LENGTH_ERROR"),
        ("x", spec("utf8", pattern="^[0-9]+$"), FieldOptions(), "PATTERN_ERROR"),
        (None, spec("int"), FieldOptions(), "REQUIRED_VALUE_MISSING"),
        ("4.01", {**DEC, "x-maximum": 4}, FieldOptions(), "VALUE_RANGE_ERROR"),
    ],
)
def test_invalid_values(value, target, options, code):
    with pytest.raises(ConversionError, match=code):
        convert_value(value, target, options)


def make_service(domain, entity, options=None):
    contract = json.loads(
        (ROOT / f"docs/standardization/silver-schemas/{domain}.json").read_text(encoding="utf-8")
    )
    key = MappingKey("SYNTH", domain, entity, "1", "mapping", "1", contract["x-version"])
    return key, TypeStandardizationService(
        key=key, conversion_version="types-1", contract=contract, options=options
    )


def result(key, values):
    return MappingResult(key, "batch", "v1", (MappedRow("line:2", values),), (), (), 1, 1, False)


def test_s2_to_s3_decimal_boolean_and_id_preservation():
    key, service = make_service(
        "admissions",
        "admission_method",
        {
            "max_score": FieldOptions(decimal_separator=","),
            "is_active": FieldOptions(true_tokens=("Có",), false_tokens=("Không",)),
        },
    )
    mapped = result(
        key,
        {
            "admission_method_id": "007",
            "method_code": "THPT",
            "method_name": "Thi",
            "max_score": "30,00",
            "is_active": "Có",
        },
    )
    typed = service.standardize(mapped)
    assert typed.accepted[0].values["max_score"] == Decimal("30.00")
    assert typed.accepted[0].values["is_active"] is True
    assert typed.accepted[0].pending_s4 == {"admission_method_id": "007"}
    assert not typed.silver_ready
    assert mapped.accepted[0].values["max_score"] == "30,00"


def test_all_22_entities_typed_and_birth_year():
    fixtures = json.loads(
        (ROOT / "data/standardization/s1/expected-business-records.json").read_text(
            encoding="utf-8"
        )
    )
    count = 0
    for domain in ("admissions", "academic", "hr"):
        contract = json.loads(
            (ROOT / f"docs/standardization/silver-schemas/{domain}.json").read_text(
                encoding="utf-8"
            )
        )
        for entity in contract["$defs"]:
            key, service = make_service(domain, entity)
            raw = fixtures[entity][0].copy()
            inputs = None
            if "birth_year" in raw:
                year = raw.pop("birth_year")
                inputs = {"line:2": {"date_of_birth": f"{year}-01-01"}}
            typed = service.standardize(
                result(key, raw),
                derivation_inputs=inputs,
                derivation_input_version="v1" if inputs else None,
            )
            assert not typed.issues, (entity, typed.issues)
            assert len(typed.accepted) == 1
            if inputs:
                assert typed.accepted[0].values["birth_year"] == fixtures[entity][0]["birth_year"]
                assert "date_of_birth" not in typed.accepted[0].values
            count += 1
    assert count == 22


def test_optional_failure_does_not_disappear():
    raw = {
        "program_id": "1",
        "program_code": "CNTT-CQ",
        "program_name": "Test",
        "major_id": "2",
        "degree_level": "Bachelor",
        "duration_years": "bad",
        "tuition_fee_per_credit": None,
        "status": "Active",
    }
    key, strict = make_service("admissions", "program")
    failed = strict.standardize(result(key, raw))
    assert failed.rejected_locators == ("line:2",)
    assert "bad" not in repr(failed.issues)
    _, permissive = make_service(
        "admissions", "program", {"duration_years": FieldOptions(invalid_optional_to_null=True)}
    )
    typed = permissive.standardize(result(key, raw))
    assert typed.accepted[0].values["duration_years"] is None
    assert typed.issues[0].severity == "warning"
    del raw["duration_years"]
    assert permissive.standardize(result(key, raw)).rejected_locators


def test_bad_configuration():
    with pytest.raises(ConversionConfigurationError, match="BOOLEAN_TOKEN_CONFLICT"):
        convert_value("x", spec("boolean"), FieldOptions(true_tokens=("x",), false_tokens=("x",)))
    with pytest.raises(ConversionConfigurationError, match="REQUIRED_FIELD"):
        make_service(
            "hr", "position", {"position_name": FieldOptions(invalid_optional_to_null=True)}
        )
    key, service = make_service("hr", "employee")
    with pytest.raises(ConversionConfigurationError, match="DERIVATION_VERSION_MISMATCH"):
        service.standardize(result(key, {}), derivation_inputs={}, derivation_input_version="wrong")


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("2026-03-08T02:30:00", "NONEXISTENT_LOCAL_TIME"),
        ("2026-11-01T01:30:00", "AMBIGUOUS_LOCAL_TIME"),
    ],
)
def test_dst_rejects_ambiguous_or_missing_wall_time(text, code):
    with pytest.raises(ConversionError, match=code):
        convert_value(
            text, spec("timestamp[us,UTC]"), FieldOptions(source_timezone="America/New_York")
        )


def test_timezone_date_and_high_precision():
    assert convert_value(
        "2026-01-01T07:00:00",
        spec("timestamp[us,UTC]"),
        FieldOptions(source_timezone="Asia/Ho_Chi_Minh"),
    ) == datetime(2026, 1, 1, tzinfo=UTC)
    value = "9" * 36 + ".99"
    assert convert_value(
        value, spec("decimal(38,2)", **{"x-precision": 38, "x-scale": 2})
    ) == Decimal(value)


@pytest.mark.parametrize(
    "case",
    [
        "admissions-flat",
        "admissions-nested",
        "academic-flat",
        "academic-nested",
        "hr-flat",
        "hr-nested",
    ],
)
def test_s2_s3_cli(case):
    process = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "scripts/standardization/preview_s3.py"),
            "--case",
            case,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    payload = json.loads(process.stdout)
    assert payload["rejected_locators"] == []
    assert payload["silver_ready"] is False
    row = payload["accepted"][0]
    if case.startswith("admissions"):
        assert row["values"]["max_score"] == {"type": "decimal", "value": "30.00"}
        assert row["values"]["is_active"] is True
        assert row["pending_s4"]["admission_method_id"] == "007"
    elif case.startswith("academic"):
        assert row["values"]["start_date"] == {"type": "date", "value": "2026-09-01"}
        assert row["pending_s4"]["status"] == "Đang diễn ra"
    else:
        assert row["values"]["position_code"] == "GV"
        assert row["pending_s4"]["position_level"] == "Nhân viên"


def test_mapping_errors_and_preview_counts_survive():
    key, service = make_service("hr", "position")
    mapped = replace(
        result(key, {}), accepted=(), rejected_locators=("line:2",), preview=True, total_count=50
    )
    typed = service.standardize(mapped)
    assert typed.rejected_locators == ("line:2",)
    assert typed.preview
    assert typed.total_count == 50


def test_missing_dob_and_bad_dob_do_not_create_fake_year():
    fixtures = json.loads(
        (ROOT / "data/standardization/s1/expected-business-records.json").read_text(
            encoding="utf-8"
        )
    )
    key, service = make_service("hr", "employee")
    mapped = result(key, fixtures["employee"][0])
    assert service.standardize(mapped).issues[0].code == "DERIVATION_INPUT_REQUIRED"
    typed = service.standardize(
        mapped,
        derivation_inputs={"line:2": {"date_of_birth": "bad-private-date"}},
        derivation_input_version="v1",
    )
    assert typed.rejected_locators == ("line:2",)
    assert "bad-private" not in repr(typed)


def test_s4_identity_policy_and_enum_string_type():
    with pytest.raises(ConversionConfigurationError, match="IDENTITY_CANNOT_NULL_ON_ERROR"):
        make_service(
            "academic", "student", {"applicant_id": FieldOptions(invalid_optional_to_null=True)}
        )
    key, service = make_service("hr", "position")
    typed = service.standardize(
        result(
            key,
            {
                "position_id": True,
                "position_code": "GV",
                "position_name": "Teacher",
                "position_level": 123,
            },
        )
    )
    assert {i.code for i in typed.issues} == {"SOURCE_ID_TYPE_ERROR", "STRING_TYPE_ERROR"}


@pytest.mark.parametrize(
    "fmt",
    [
        "%m-%d",
        "%Y-%m",
        "%d",
        "%y-%m-%d",
        "%%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%j",
        "%x",
        "%Y-%m-%d %",
        "%Y-%m-%d %d",
        "",
    ],
)
def test_date_format_rejected_before_any_row(fmt):
    options = FieldOptions(date_format=fmt)
    with pytest.raises(ConversionConfigurationError, match="INVALID_DATE_FORMAT"):
        make_service("academic", "semester", {"start_date": options})
    # Không được che giấu cấu hình không hợp lệ bằng giá trị cho phép null.
    with pytest.raises(ConversionConfigurationError, match="INVALID_DATE_FORMAT"):
        convert_value(None, spec("date32", type=["string", "null"]), options)


@pytest.mark.parametrize(
    "fmt",
    [
        "%Y-%m-%d",
        "%H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m-%d %H:%M:%S",
        "%Y-%m-%d %%H:%M:%S",
        "%Y-%m-%d %I:%M:%S",
        "%Y-%m-%d %I:%M:%S %p",
        "%c",
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S %Q",
        "",
    ],
)
def test_timestamp_format_rejected_before_any_row(fmt):
    options = FieldOptions(timestamp_format=fmt, source_timezone="UTC")
    with pytest.raises(ConversionConfigurationError, match="INVALID_TIMESTAMP_FORMAT"):
        make_service("admissions", "applicant", {"created_at": options})
    with pytest.raises(ConversionConfigurationError, match="INVALID_TIMESTAMP_FORMAT"):
        convert_value("2026-10-15", spec("timestamp[us,UTC]"), options)


@pytest.mark.parametrize(
    ("value", "fmt", "expected"),
    [
        (
            "15/10/2026 12:34:56",
            "%d/%m/%Y %H:%M:%S",
            datetime(2026, 10, 15, 12, 34, 56, tzinfo=UTC),
        ),
        ("20261015123456", "%Y%m%d%H%M%S", datetime(2026, 10, 15, 12, 34, 56, tzinfo=UTC)),
        (
            "2026-10-15 12:34:56.12+0700",
            "%Y-%m-%d %H:%M:%S.%f%z",
            datetime(2026, 10, 15, 5, 34, 56, 120000, tzinfo=UTC),
        ),
        ("2026-10-15 00:00:00", "%Y-%m-%d %H:%M:%S", datetime(2026, 10, 15, tzinfo=UTC)),
    ],
)
def test_complete_timestamp_formats_still_work(value, fmt, expected):
    assert (
        convert_value(
            value,
            spec("timestamp[us,UTC]"),
            FieldOptions(timestamp_format=fmt, source_timezone="UTC"),
        )
        == expected
    )


@pytest.mark.parametrize("value", ["2026-10-15", "2026-10-15 12:34", "2026-10-15 1:2:3"])
def test_complete_format_does_not_accept_partial_input(value):
    with pytest.raises(ConversionError, match="TIMESTAMP_PARSE_ERROR"):
        convert_value(
            value,
            spec("timestamp[us,UTC]"),
            FieldOptions(timestamp_format="%Y-%m-%d %H:%M:%S", source_timezone="UTC"),
        )


def test_short_compact_timestamp_cannot_be_reinterpreted():
    with pytest.raises(ConversionError, match="TIMESTAMP_PARSE_ERROR"):
        convert_value(
            "20261015123",
            spec("timestamp[us,UTC]"),
            FieldOptions(timestamp_format="%Y%m%d%H%M%S", source_timezone="UTC"),
        )


def test_literal_percent_does_not_count_as_directive():
    assert convert_value(
        "2026-10-15 %Y", spec("date32"), FieldOptions(date_format="%Y-%m-%d %%Y")
    ) == date(2026, 10, 15)
    assert convert_value("0001-01-01", spec("date32")) == date(1, 1, 1)


def service_inputs():
    contract = json.loads(
        (ROOT / "docs/standardization/silver-schemas/admissions.json").read_text(encoding="utf-8")
    )
    key = MappingKey(
        "sys", "admissions", "admission_method", "1", "mapping", "1", contract["x-version"]
    )
    return key, contract


def test_configuration_deeply_immutable_and_detached():
    key, contract = service_inputs()
    options = {"max_score": FieldOptions(decimal_separator=",")}
    service = TypeStandardizationService(
        key=key, conversion_version="1", contract=contract, options=options
    )
    original = service.configuration_fingerprint
    contract["$defs"]["admission_method"]["properties"]["max_score"]["x-scale"] = 0
    options["max_score"] = FieldOptions()
    assert service.spec["properties"]["max_score"]["x-scale"] == 2
    assert service.options["max_score"].decimal_separator == ","
    with pytest.raises(TypeError):
        service.options["max_score"] = FieldOptions()  # pyright: ignore[reportIndexIssue] -- kiểm tra ánh xạ bất biến
    with pytest.raises(TypeError):
        service.spec["properties"]["max_score"]["x-scale"] = 0
    with pytest.raises(TypeError):
        service.spec["required"][0] = "other"
    for name, value in [
        ("version", "other"),
        ("spec", {}),
        ("options", {}),
        ("configuration_fingerprint", "fake"),
        ("key", replace(key, entity="other")),
    ]:
        with pytest.raises(FrozenInstanceError):
            setattr(service, name, value)
    typed = service.standardize(
        result(
            key,
            {
                "admission_method_id": "007",
                "method_code": "THPT",
                "method_name": "Test",
                "max_score": "30,00",
                "is_active": True,
            },
        )
    )
    assert typed.accepted[0].values["max_score"] == Decimal("30.00")
    assert typed.configuration_fingerprint == original


def test_fingerprint_stable_for_equivalent_config_and_sensitive_to_content():
    key, contract = service_inputs()

    def build(c=contract, opts=None, version="1"):
        return TypeStandardizationService(
            key=key, conversion_version=version, contract=c, options=opts
        )

    first = build()
    reordered = json.loads(json.dumps(contract, sort_keys=True))
    assert (
        first.configuration_fingerprint
        == build(reordered, {"max_score": FieldOptions()}).configuration_fingerprint
    )
    assert (
        first.configuration_fingerprint
        != build(opts={"max_score": FieldOptions(decimal_separator=",")}).configuration_fingerprint
    )
    changed = copy.deepcopy(contract)
    changed["$defs"]["admission_method"]["properties"]["max_score"]["x-minimum"] = 1
    assert first.configuration_fingerprint != build(changed).configuration_fingerprint
    assert first.configuration_fingerprint != build(version="2").configuration_fingerprint


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (["$defs"], {}, "ENTITY_NOT_FOUND"),
        (["x-version"], "wrong", "CONVERSION_VERSION_MISMATCH"),
        (["$defs", "admission_method", "x-owner"], "hr", "DOMAIN_MISMATCH"),
        (["$defs", "admission_method", "required"], [], "INVALID_REQUIRED_FIELDS"),
        (["$defs", "admission_method", "x-primary-key"], ["missing"], "INVALID_PRIMARY_KEY"),
        (
            ["$defs", "admission_method", "x-unique-together"],
            [["missing"]],
            "INVALID_UNIQUE_CONSTRAINT",
        ),
        (
            ["$defs", "admission_method", "properties", "max_score", "x-physical-type"],
            "money",
            "UNSUPPORTED_PHYSICAL_TYPE",
        ),
        (
            ["$defs", "admission_method", "properties", "max_score", "x-precision"],
            7,
            "INVALID_DECIMAL_CONTRACT",
        ),
        (
            ["$defs", "admission_method", "properties", "max_score", "x-scale"],
            True,
            "INVALID_DECIMAL_CONTRACT",
        ),
        (
            ["$defs", "admission_method", "properties", "max_score", "type"],
            "number",
            "PHYSICAL_LOGICAL_TYPE_MISMATCH",
        ),
        (
            ["$defs", "admission_method", "properties", "max_score", "x-maximum"],
            -1,
            "INVALID_RANGE",
        ),
        (
            ["$defs", "admission_method", "properties", "method_code", "pattern"],
            "[",
            "INVALID_PATTERN",
        ),
        (
            ["$defs", "admission_method", "properties", "method_code", "maxLength"],
            5,
            "INVALID_STRING_CONTRACT",
        ),
        (
            ["$defs", "admission_method", "properties", "method_code", "enum"],
            [False],
            "INVALID_ENUM",
        ),
        (
            ["$defs", "admission_method", "properties", "admission_method_id", "x-references"],
            {"entity": "x"},
            "INVALID_REFERENCE",
        ),
        (
            ["$defs", "admission_method", "properties", "admission_method_id", "type"],
            ["integer", "null"],
            "INVALID_PRIMARY_KEY",
        ),
        (
            ["$defs", "admission_method", "properties", "max_score", "x-derived-from"],
            "anything",
            "UNSUPPORTED_DERIVATION",
        ),
    ],
)
def test_invalid_contract_fails_at_construction(path, value, code):
    key, contract = service_inputs()
    node = contract
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    with pytest.raises(ConversionConfigurationError, match=code):
        TypeStandardizationService(key=key, conversion_version="1", contract=contract)


@pytest.mark.parametrize(
    "option",
    [
        FieldOptions(null_tokens=[""]),  # pyright: ignore[reportArgumentType] -- kiểm tra đầu vào không hợp lệ
        FieldOptions(true_tokens=("true", True)),  # pyright: ignore[reportArgumentType] -- kiểm tra đầu vào không hợp lệ
        FieldOptions(null_tokens=("", "")),
        FieldOptions(trim="yes"),  # pyright: ignore[reportArgumentType] -- kiểm tra đầu vào không hợp lệ
        FieldOptions(decimal_separator=[]),  # pyright: ignore[reportArgumentType] -- kiểm tra đầu vào không hợp lệ
        FieldOptions(excel_epoch=[]),  # pyright: ignore[reportArgumentType] -- kiểm tra đầu vào không hợp lệ
    ],
)
def test_mutable_or_invalid_options_rejected(option):
    key, contract = service_inputs()
    with pytest.raises(ConversionConfigurationError):
        TypeStandardizationService(
            key=key, conversion_version="1", contract=contract, options={"max_score": option}
        )


def test_mapping_key_cannot_hold_mutable_values():
    key, contract = service_inputs()
    with pytest.raises(ConversionConfigurationError, match="INVALID_MAPPING_KEY"):
        TypeStandardizationService(
            key=replace(key, source_system_id=["sys"]), conversion_version="1", contract=contract
        )


def test_missing_physical_type_and_nonfinite_bound_are_config_errors():
    key, contract = service_inputs()
    del contract["$defs"]["admission_method"]["properties"]["max_score"]["x-physical-type"]
    with pytest.raises(ConversionConfigurationError, match="PHYSICAL_TYPE_REQUIRED"):
        TypeStandardizationService(key=key, conversion_version="1", contract=contract)
    key, contract = service_inputs()
    contract["$defs"]["admission_method"]["properties"]["max_score"]["x-minimum"] = float("nan")
    with pytest.raises(ConversionConfigurationError, match="INVALID_CONTRACT"):
        TypeStandardizationService(key=key, conversion_version="1", contract=contract)
