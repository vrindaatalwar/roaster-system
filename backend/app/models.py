from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _enum_values(enum_class: type) -> list[str]:
    return [member.value for member in enum_class]


class EligibilityType(StrEnum):
    PRIMARY = "primary"
    BACKUP = "backup"


class AvailabilityType(StrEnum):
    LEAVE = "leave"
    COMP_OFF = "comp_off"
    UNAVAILABLE = "unavailable"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"


class PreferenceType(StrEnum):
    PREFERRED = "preferred"
    UNPREFERRED = "unpreferred"


class RosterStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class AssignmentStatus(StrEnum):
    WORKING = "working"
    OFF = "off"
    LEAVE = "leave"
    UNCOVERED = "uncovered"


class EmployeeRosterDayStatus(StrEnum):
    WORKING = "working"
    OFF = "off"
    LEAVE = "leave"
    UNSCHEDULED = "unscheduled"


class EmployeeSkill(Base):
    __tablename__ = "employee_skills"
    __table_args__ = (UniqueConstraint("employee_id", "skill_id"),)

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="CASCADE"), primary_key=True)
    proficiency: Mapped[str] = mapped_column(String(30), default="qualified")


class EmployeePodMembership(Base):
    """An employee may be rostered in more than one operational pod."""
    __tablename__ = "employee_pod_memberships"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    pod_id: Mapped[int] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"), primary_key=True)


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("employee_code"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_employee_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(20), nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role_title: Mapped[str] = mapped_column(String(80), nullable=False)
    primary_pod_id: Mapped[int] = mapped_column(ForeignKey("pods.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    is_pod_lead: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    primary_pod: Mapped["Pod"] = relationship(back_populates="members", foreign_keys=[primary_pod_id])
    skills: Mapped[list["Skill"]] = relationship(secondary="employee_skills", back_populates="employees")
    work_contract: Mapped["EmployeeWorkContract"] = relationship(back_populates="employee", uselist=False)
    weekly_offs: Mapped[list["EmployeeWeeklyOff"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    availability_exceptions: Mapped[list["AvailabilityException"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    preferences: Mapped[list["EmployeePreference"]] = relationship(back_populates="employee", cascade="all, delete-orphan")
    eligibility: Mapped[list["EmployeeEligibility"]] = relationship(back_populates="employee", cascade="all, delete-orphan")


class Pod(Base):
    __tablename__ = "pods"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    specialization: Mapped[str] = mapped_column(String(120), nullable=False)
    lead_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id", use_alter=True, name="fk_pod_lead"), nullable=True
    )

    members: Mapped[list[Employee]] = relationship(back_populates="primary_pod", foreign_keys="Employee.primary_pod_id")
    lead: Mapped[Employee | None] = relationship(foreign_keys=[lead_employee_id], post_update=True)


class PodSchedule(Base):
    """Configurable shift window and allocatable work capacity for a pod."""
    __tablename__ = "pod_schedules"
    __table_args__ = (
        UniqueConstraint("pod_id"),
        CheckConstraint("allocation_capacity_hours > 0 AND allocation_capacity_hours <= 8",
                        name="ck_pod_allocation_capacity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pod_id: Mapped[int] = mapped_column(ForeignKey("pods.id", ondelete="CASCADE"), nullable=False)
    shift_name: Mapped[str] = mapped_column(String(80), default="Work window", nullable=False)
    shift_start: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    shift_end: Mapped[str] = mapped_column(String(5), default="18:00", nullable=False)
    break_start: Mapped[str] = mapped_column(String(5), default="13:00", nullable=False)
    break_end: Mapped[str] = mapped_column(String(5), default="14:00", nullable=False)
    allocation_capacity_hours: Mapped[float] = mapped_column(Float, default=6.0, nullable=False)


class BrandProcessSchedule(Base):
    """Maps a brand/process requirement to the pod's shift window."""
    __tablename__ = "brand_process_schedules"

    brand_process_rule_id: Mapped[int] = mapped_column(
        ForeignKey("brand_process_rules.id", ondelete="CASCADE"), primary_key=True
    )
    pod_id: Mapped[int] = mapped_column(ForeignKey("pods.id", ondelete="RESTRICT"), nullable=False)


class BrandPriority(Base):
    """Manager-set deadline urgency; a soft tie-breaker after total coverage."""
    __tablename__ = "brand_priorities"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 5", name="ck_brand_priority_range"),
    )

    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    deadline_note: Mapped[str] = mapped_column(String(200), default="", nullable=False)


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)

    employees: Mapped[list[Employee]] = relationship(secondary="employee_skills", back_populates="skills")
    processes: Mapped[list["Process"]] = relationship(back_populates="required_skill")


class EmployeeWorkContract(Base):
    __tablename__ = "employee_work_contracts"
    __table_args__ = (
        UniqueConstraint("employee_id"),
        CheckConstraint("daily_capacity_hours > 0 AND daily_capacity_hours <= 8", name="ck_daily_capacity"),
        CheckConstraint("max_days_per_week BETWEEN 1 AND 5", name="ck_max_days_per_week"),
        CheckConstraint("max_hours_per_week BETWEEN 1 AND 40", name="ck_max_hours_per_week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    daily_capacity_hours: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    max_days_per_week: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_hours_per_week: Mapped[float] = mapped_column(Float, default=40.0, nullable=False)
    minimum_days_off_per_week: Mapped[int] = mapped_column(Integer, default=2, nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="work_contract")


class EmployeeWeeklyOff(Base):
    __tablename__ = "employee_weekly_offs"
    __table_args__ = (
        UniqueConstraint("employee_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_weekly_off_weekday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)  # Monday=0 ... Sunday=6

    employee: Mapped[Employee] = relationship(back_populates="weekly_offs")


class AvailabilityException(Base):
    __tablename__ = "availability_exceptions"
    __table_args__ = (CheckConstraint("end_date >= start_date", name="ck_availability_date_range"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    exception_type: Mapped[AvailabilityType] = mapped_column(
        Enum(AvailabilityType, native_enum=False, values_callable=_enum_values), nullable=False
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False, values_callable=_enum_values),
        default=ApprovalStatus.APPROVED, nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="availability_exceptions")


class EmployeePreference(Base):
    __tablename__ = "employee_preferences"
    __table_args__ = (
        UniqueConstraint("employee_id", "weekday", "preference_type"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_preference_weekday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    preference_type: Mapped[PreferenceType] = mapped_column(
        Enum(PreferenceType, native_enum=False, values_callable=_enum_values), nullable=False
    )
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="preferences")


class PublicHoliday(Base):
    __tablename__ = "public_holidays"
    __table_args__ = (UniqueConstraint("holiday_date", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Process(Base):
    __tablename__ = "processes"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    required_skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False)
    default_effort_hours: Mapped[float] = mapped_column(Float, default=8.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    required_skill: Mapped[Skill] = relationship(back_populates="processes")
    brand_rules: Mapped[list["BrandProcessRule"]] = relationship(back_populates="process", cascade="all, delete-orphan")


class Brand(Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo_data: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    process_rules: Mapped[list["BrandProcessRule"]] = relationship(back_populates="brand", cascade="all, delete-orphan")


class BrandProcessRule(Base):
    __tablename__ = "brand_process_rules"
    __table_args__ = (
        UniqueConstraint("brand_id", "process_id"),
        CheckConstraint("effort_hours_per_employee > 0 AND effort_hours_per_employee <= 8", name="ck_brand_process_effort"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), nullable=False)
    process_id: Mapped[int] = mapped_column(ForeignKey("processes.id", ondelete="RESTRICT"), nullable=False)
    required_skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False)
    operating_weekdays: Mapped[list[int]] = mapped_column(JSON, nullable=False)  # Monday=0 ... Sunday=6
    operate_on_public_holidays: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    effort_hours_per_employee: Mapped[float] = mapped_column(Float, nullable=False)
    can_share_same_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    continuous_presence: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    brand: Mapped[Brand] = relationship(back_populates="process_rules")
    process: Mapped[Process] = relationship(back_populates="brand_rules")
    required_skill: Mapped[Skill] = relationship()
    coverage_requirements: Mapped[list["CoverageRequirement"]] = relationship(back_populates="brand_process", cascade="all, delete-orphan")
    eligibility: Mapped[list["EmployeeEligibility"]] = relationship(back_populates="brand_process", cascade="all, delete-orphan")


class CoverageRequirement(Base):
    __tablename__ = "coverage_requirements"
    __table_args__ = (
        UniqueConstraint("brand_process_rule_id", "weekday"),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_coverage_weekday"),
        CheckConstraint("minimum_staff >= 0", name="ck_coverage_minimum"),
        CheckConstraint("maximum_staff IS NULL OR maximum_staff >= minimum_staff", name="ck_coverage_maximum"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    brand_process_rule_id: Mapped[int] = mapped_column(ForeignKey("brand_process_rules.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_staff: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    maximum_staff: Mapped[int | None] = mapped_column(Integer)

    brand_process: Mapped[BrandProcessRule] = relationship(back_populates="coverage_requirements")


class EmployeeEligibility(Base):
    __tablename__ = "employee_eligibility"
    __table_args__ = (UniqueConstraint("employee_id", "brand_process_rule_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), nullable=False)
    brand_process_rule_id: Mapped[int] = mapped_column(ForeignKey("brand_process_rules.id", ondelete="CASCADE"), nullable=False)
    eligibility_type: Mapped[EligibilityType] = mapped_column(
        Enum(EligibilityType, native_enum=False, values_callable=_enum_values), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    employee: Mapped[Employee] = relationship(back_populates="eligibility")
    brand_process: Mapped[BrandProcessRule] = relationship(back_populates="eligibility")


class RosterRun(Base):
    __tablename__ = "roster_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RosterStatus] = mapped_column(
        Enum(RosterStatus, native_enum=False, values_callable=_enum_values),
        default=RosterStatus.DRAFT, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    assignments: Mapped[list["RosterAssignment"]] = relationship(back_populates="roster_run", cascade="all, delete-orphan")
    employee_days: Mapped[list["RosterEmployeeDay"]] = relationship(
        back_populates="roster_run", cascade="all, delete-orphan"
    )
    generation_report: Mapped["RosterGenerationReport | None"] = relationship(
        back_populates="roster_run", cascade="all, delete-orphan", uselist=False
    )


class RosterGenerationReport(Base):
    __tablename__ = "roster_generation_reports"

    roster_run_id: Mapped[int] = mapped_column(
        ForeignKey("roster_runs.id", ondelete="CASCADE"), primary_key=True
    )
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)
    solver_status: Mapped[str] = mapped_column(String(30), nullable=False)
    required_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    uncovered_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    roster_run: Mapped[RosterRun] = relationship(back_populates="generation_report")


class RosterEmployeeDay(Base):
    __tablename__ = "roster_employee_days"
    __table_args__ = (
        UniqueConstraint("roster_run_id", "employee_id", "schedule_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    roster_run_id: Mapped[int] = mapped_column(ForeignKey("roster_runs.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"), nullable=False)
    schedule_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[EmployeeRosterDayStatus] = mapped_column(
        Enum(EmployeeRosterDayStatus, native_enum=False, values_callable=_enum_values), nullable=False
    )
    assigned_effort_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    roster_run: Mapped[RosterRun] = relationship(back_populates="employee_days")
    employee: Mapped[Employee] = relationship()


class RosterAssignment(Base):
    __tablename__ = "roster_assignments"
    __table_args__ = (
        CheckConstraint("effort_hours >= 0 AND effort_hours <= 8", name="ck_roster_effort_hours"),
        CheckConstraint("status != 'working' OR employee_id IS NOT NULL", name="ck_working_assignment_employee"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    roster_run_id: Mapped[int] = mapped_column(ForeignKey("roster_runs.id", ondelete="CASCADE"), nullable=False)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="RESTRICT"))
    brand_process_rule_id: Mapped[int] = mapped_column(ForeignKey("brand_process_rules.id", ondelete="RESTRICT"), nullable=False)
    assignment_date: Mapped[date] = mapped_column(Date, nullable=False)
    effort_hours: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(AssignmentStatus, native_enum=False, values_callable=_enum_values),
        default=AssignmentStatus.WORKING, nullable=False,
    )
    explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    roster_run: Mapped[RosterRun] = relationship(back_populates="assignments")
    employee: Mapped[Employee | None] = relationship()
    brand_process: Mapped[BrandProcessRule] = relationship()
