"""Các mô hình SQLAlchemy cho người dùng và các chiều nghiệp vụ cốt lõi."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.infra.db.base import Base

__all__ = [
    "Admission",
    "AdmissionMethod",
    "AdmissionYear",
    "Applicant",
    "Assignment",
    "ClassLecturer",
    "Course",
    "CourseClass",
    "Curriculum",
    "CurriculumCourse",
    "Department",
    "Employee",
    "EmployeeQualification",
    "Enrollment",
    "EnrollmentGrade",
    "Lecturer",
    "Major",
    "Position",
    "Program",
    "Qualification",
    "Semester",
    "Student",
    "User",
]


class Department(Base):
    """Đơn vị tổ chức dùng chung cho dữ liệu đào tạo, nhân sự và quản trị."""

    __tablename__ = "department"
    __table_args__ = (
        Index("ix_department_parent_department_id", "parent_department_id"),
        Index("ix_department_status", "status"),
        {"schema": "app"},
    )

    department_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    department_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    department_name: Mapped[str] = mapped_column(String(200), nullable=False)
    department_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parent_department_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.department.department_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Major(Base):
    __tablename__ = "major"
    __table_args__ = (
        Index("ix_major_department_id", "department_id"),
        Index("ix_major_status", "status"),
        {"schema": "app"},
    )

    major_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    major_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    major_name: Mapped[str] = mapped_column(String(200), nullable=False)
    department_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.department.department_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"), nullable=False)


class Program(Base):
    __tablename__ = "program"
    __table_args__ = (
        Index("ix_program_major_id", "major_id"),
        Index("ix_program_status", "status"),
        {"schema": "app"},
    )

    program_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    program_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    program_name: Mapped[str] = mapped_column(String(200), nullable=False)
    major_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.major.major_id"), nullable=False
    )
    degree_level: Mapped[str] = mapped_column(String(50), nullable=False)
    duration_years: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    tuition_fee_per_credit: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"), nullable=False)


class AdmissionYear(Base):
    __tablename__ = "admission_year"
    __table_args__ = (
        Index("ix_admission_year_status", "status"),
        {"schema": "app"},
    )

    admission_year_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    year_label: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class AdmissionMethod(Base):
    __tablename__ = "admission_method"
    __table_args__ = (
        Index("ix_admission_method_is_active", "is_active"),
        {"schema": "app"},
    )

    admission_method_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    method_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    method_name: Mapped[str] = mapped_column(String(150), nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)


class Applicant(Base):
    __tablename__ = "applicant"
    __table_args__ = (
        Index("ix_applicant_email", "email"),
        Index("ix_applicant_phone", "phone"),
        {"schema": "app"},
    )

    applicant_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    national_id: Mapped[str | None] = mapped_column(String(30), unique=True, nullable=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), nullable=True)
    high_school_name: Mapped[str | None] = mapped_column(String(250), nullable=True)
    province: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Admission(Base):
    __tablename__ = "admission"
    __table_args__ = (
        UniqueConstraint(
            "applicant_id",
            "program_id",
            "admission_year_id",
            "admission_method_id",
            name="uq_admission",
        ),
        Index("ix_admission_applicant_id", "applicant_id"),
        Index("ix_admission_program_id", "program_id"),
        Index("ix_admission_admission_year_id", "admission_year_id"),
        Index("ix_admission_admission_status", "admission_status"),
        {"schema": "app"},
    )

    admission_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    applicant_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.applicant.applicant_id"), nullable=False
    )
    program_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.program.program_id"), nullable=False
    )
    preference_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    admission_method_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.admission_method.admission_method_id"), nullable=False
    )
    admission_year_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.admission_year.admission_year_id"), nullable=False
    )
    application_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    admission_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    admission_status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Student(Base):
    __tablename__ = "student"
    __table_args__ = (
        Index("ix_student_program_id", "program_id"),
        Index("ix_student_admission_year_id", "admission_year_id"),
        Index("ix_student_student_status", "student_status"),
        {"schema": "app"},
    )

    student_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    student_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    applicant_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.applicant.applicant_id"), unique=True, nullable=True
    )
    admission_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.admission.admission_id"), unique=True, nullable=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    program_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.program.program_id"), nullable=False
    )
    admission_year_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.admission_year.admission_year_id"), nullable=False
    )
    student_status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class Curriculum(Base):
    __tablename__ = "curriculum"
    __table_args__ = (
        UniqueConstraint("program_id", "version", name="uq_curriculum_version"),
        Index("ix_curriculum_program_id", "program_id"),
        Index("ix_curriculum_status", "status"),
        {"schema": "app"},
    )

    curriculum_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    program_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.program.program_id"), nullable=False
    )
    curriculum_code: Mapped[str] = mapped_column(String(50), nullable=False)
    curriculum_name: Mapped[str] = mapped_column(String(250), nullable=False)
    version: Mapped[str] = mapped_column(String(30), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"), nullable=False)


class Course(Base):
    __tablename__ = "course"
    __table_args__ = (
        Index("ix_course_status", "status"),
        {"schema": "app"},
    )

    course_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    course_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    course_name: Mapped[str] = mapped_column(String(200), nullable=False)
    credit_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'ACTIVE'"), nullable=False)


class CurriculumCourse(Base):
    __tablename__ = "curriculum_course"
    __table_args__ = (
        UniqueConstraint("curriculum_id", "course_id", name="uq_curriculum_course"),
        Index("ix_curriculum_course_curriculum_id", "curriculum_id"),
        Index("ix_curriculum_course_course_id", "course_id"),
        Index("ix_curriculum_course_prerequisite_course_id", "prerequisite_course_id"),
        {"schema": "app"},
    )

    curriculum_course_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    curriculum_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.curriculum.curriculum_id"), nullable=False
    )
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.course.course_id"), nullable=False
    )
    semester_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    course_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    prerequisite_course_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.course.course_id"), nullable=True
    )


class Semester(Base):
    __tablename__ = "semester"
    __table_args__ = (
        Index("ix_semester_academic_year", "academic_year"),
        Index("ix_semester_status", "status"),
        {"schema": "app"},
    )

    semester_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    semester_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    academic_year: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), server_default=text("'UPCOMING'"), nullable=False
    )


class CourseClass(Base):
    __tablename__ = "class"
    __table_args__ = (
        Index("ix_class_course_id", "course_id"),
        Index("ix_class_semester_id", "semester_id"),
        Index("ix_class_class_status", "class_status"),
        {"schema": "app"},
    )

    class_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    class_code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    course_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.course.course_id"), nullable=False
    )
    semester_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.semester.semester_id"), nullable=False
    )
    max_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    room: Mapped[str | None] = mapped_column(String(50), nullable=True)
    class_status: Mapped[str] = mapped_column(String(30), nullable=False)


class Enrollment(Base):
    __tablename__ = "enrollment"
    __table_args__ = (
        UniqueConstraint("student_id", "class_id", name="uq_enrollment"),
        Index("ix_enrollment_student_id", "student_id"),
        Index("ix_enrollment_class_id", "class_id"),
        Index("ix_enrollment_enrollment_status", "enrollment_status"),
        {"schema": "app"},
    )

    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    student_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.student.student_id"), nullable=False
    )
    class_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.class.class_id"), nullable=False
    )
    enrollment_date: Mapped[date] = mapped_column(Date, nullable=False)
    enrollment_status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class EnrollmentGrade(Base):
    __tablename__ = "enrollment_grade"
    __table_args__ = ({"schema": "app"},)

    enrollment_grade_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    enrollment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.enrollment.enrollment_id"), unique=True, nullable=False
    )
    midterm_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    grade_value: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    letter_grade: Mapped[str | None] = mapped_column(String(5), nullable=True)
    registration_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Employee(Base):
    __tablename__ = "employee"
    __table_args__ = (
        Index("ix_employee_employee_status", "employee_status"),
        {"schema": "app"},
    )

    employee_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    employee_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(150), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    hire_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    employee_status: Mapped[str] = mapped_column(String(30), nullable=False)


class Position(Base):
    __tablename__ = "position"
    __table_args__ = ({"schema": "app"},)

    position_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    position_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    position_name: Mapped[str] = mapped_column(String(150), nullable=False)
    position_level: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Assignment(Base):
    __tablename__ = "assignment"
    __table_args__ = (
        Index("ix_assignment_employee_id", "employee_id"),
        Index("ix_assignment_department_id", "department_id"),
        Index("ix_assignment_position_id", "position_id"),
        Index("ix_assignment_assignment_status", "assignment_status"),
        {"schema": "app"},
    )

    assignment_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.employee.employee_id"), nullable=False
    )
    department_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.department.department_id"), nullable=False
    )
    position_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.position.position_id"), nullable=False
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    assignment_status: Mapped[str] = mapped_column(String(30), nullable=False)


class Lecturer(Base):
    __tablename__ = "lecturer"
    __table_args__ = ({"schema": "app"},)

    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.employee.employee_id"), primary_key=True
    )
    academic_degree: Mapped[str | None] = mapped_column(String(100), nullable=True)
    academic_title: Mapped[str | None] = mapped_column(String(250), nullable=True)
    research_field: Mapped[str | None] = mapped_column(String(250), nullable=True)


class ClassLecturer(Base):
    __tablename__ = "class_lecturer"
    __table_args__ = (
        UniqueConstraint("class_id", "lecturer_id", name="uq_class_lecturer"),
        Index("ix_class_lecturer_class_id", "class_id"),
        Index("ix_class_lecturer_lecturer_id", "lecturer_id"),
        {"schema": "app"},
    )

    class_lecturer_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    class_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.class.class_id"), nullable=False
    )
    lecturer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.lecturer.employee_id"), nullable=False
    )
    teaching_role: Mapped[str | None] = mapped_column(
        String(50), server_default=text("'PRIMARY_LECTURER'"), nullable=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    assigned_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    assigned_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class Qualification(Base):
    __tablename__ = "qualification"
    __table_args__ = ({"schema": "app"},)

    qualification_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    qualification_name: Mapped[str] = mapped_column(String(150), nullable=False)
    qualification_type: Mapped[str | None] = mapped_column(String(100), nullable=True)


class EmployeeQualification(Base):
    __tablename__ = "employee_qualification"
    __table_args__ = (
        Index("ix_employee_qualification_qualification_id", "qualification_id"),
        {"schema": "app"},
    )

    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.employee.employee_id"), primary_key=True
    )
    qualification_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app.qualification.qualification_id"), primary_key=True
    )
    institution: Mapped[str | None] = mapped_column(String(250), nullable=True)
    year_obtained: Mapped[int | None] = mapped_column(Integer, nullable=True)


class User(Base):
    """Thực thể người dùng ánh xạ tới bảng app.app_user."""

    __tablename__ = "app_user"
    __table_args__ = (
        Index("ix_app_user_employee_id", "employee_id"),
        Index("ix_app_user_is_active", "is_active"),
        {"schema": "app"},
    )

    app_user_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, index=True
    )
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("app.employee.employee_id"), unique=True, nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
