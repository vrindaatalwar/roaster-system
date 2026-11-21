from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
import os

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from .database import Base, SessionLocal, engine, get_session
from .migrations import upgrade_schema
from .models import (
    AllocationSetting, ApprovalStatus, AssignmentStatus, AvailabilityException, AvailabilityType, Brand,
    BrandPriority, BrandProcessRule, BrandProcessSchedule, Employee, EmployeeEligibility,
    EmployeePodMembership,
    EmployeeRosterDayStatus, EmployeeSkill, EmployeeWeeklyOff, EmployeeWorkContract,
    EligibilityType, CoverageRequirement, Pod, PodSchedule, Process, PublicHoliday, RosterAssignment,
    PriorityOverride, RosterEmployeeDay, RosterRun, RosterStatus, Skill,
)
from .scheduling import _windows_overlap, generate_month
from .validation import validate_roster
from starlette.responses import JSONResponse


app = FastAPI(title="DataStraw Smart Roster", version="0.1.0")
allowed_origins = os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-App-Role", "X-Employee-Code"],
)


def openapi_with_demo_role_headers() -> dict:
    """Make the unauthenticated demo role inputs visible in Swagger UI."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    for path, operations in schema.get("paths", {}).items():
        if not path.startswith("/api/v1/") or path == "/api/v1/demo-identities":
            continue
        for operation in operations.values():
            if not isinstance(operation, dict):
                continue
            parameters = operation.setdefault("parameters", [])
            parameters.extend([
                {"name": "X-App-Role", "in": "header", "required": True,
                 "description": "Demo role: manager, pod_lead, or employee. This is not authentication.",
                 "schema": {"type": "string", "enum": ["manager", "pod_lead", "employee"]}},
                {"name": "X-Employee-Code", "in": "header", "required": False,
                 "description": "Required for pod_lead and employee roles; ignored for manager.",
                 "schema": {"type": "string"}},
            ])
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = openapi_with_demo_role_headers


@app.middleware("http")
async def enforce_demo_role_access(request: Request, call_next):
    """Apply role permissions using a selectable, unauthenticated demo identity.

    The role and employee headers are intentionally not authentication. They let
    the UI demonstrate role-specific access until a trusted identity provider is
    added; callers can currently change these headers themselves.
    """
    if (request.method == "OPTIONS" or not request.url.path.startswith("/api/v1/")
            or request.url.path == "/api/v1/demo-identities"):
        return await call_next(request)

    role = request.headers.get("X-App-Role", "").strip().lower()
    if role not in {"manager", "pod_lead", "employee"}:
        return JSONResponse(status_code=401, content={
            "detail": "Select a demo role using the X-App-Role header.",
        })

    request.state.actor_role = role
    request.state.actor_employee_id = None
    request.state.actor_employee_code = None
    request.state.actor_pod_ids = set()
    request.state.actor_member_ids = set()

    if role != "manager":
        employee_code = request.headers.get("X-Employee-Code", "").strip()
        if not employee_code:
            return JSONResponse(status_code=401, content={
                "detail": "Select an employee identity using the X-Employee-Code header.",
            })
        with SessionLocal() as session:
            employee = session.scalar(select(Employee).where(
                Employee.employee_code == employee_code,
                Employee.status == "active",
            ))
            if employee is None:
                return JSONResponse(status_code=403, content={
                    "detail": "The selected demo identity is not an active employee.",
                })
            if role == "pod_lead" and not employee.is_pod_lead:
                return JSONResponse(status_code=403, content={
                    "detail": "Pod lead access requires a configured pod lead identity.",
                })
            pod_ids = set(session.scalars(select(Pod.id).where(
                Pod.lead_employee_id == employee.id,
            )).all()) if role == "pod_lead" else set()
            if role == "pod_lead" and not pod_ids:
                return JSONResponse(status_code=403, content={
                    "detail": "This employee is not assigned as a lead for an active pod.",
                })
            member_ids = {employee.id}
            if pod_ids:
                member_ids.update(session.scalars(select(Employee.id).where(
                    Employee.primary_pod_id.in_(pod_ids),
                )).all())
                member_ids.update(session.scalars(select(EmployeePodMembership.employee_id).where(
                    EmployeePodMembership.pod_id.in_(pod_ids),
                )).all())
            request.state.actor_employee_id = employee.id
            request.state.actor_employee_code = employee.employee_code
            request.state.actor_pod_ids = pod_ids
            request.state.actor_member_ids = member_ids

    if role != "manager":
        path = request.url.path
        allowed_read = request.method == "GET" and (
            path == "/api/v1/seed-summary"
            or path == "/api/v1/rosters"
            or (path.startswith("/api/v1/rosters/") and (
                path.count("/") == 4
                or path.endswith("/validation")
            ))
        )
        if not allowed_read:
            return JSONResponse(status_code=403, content={
                "detail": "This action is available to managers only.",
            })

    return await call_next(request)


class GenerateRosterRequest(BaseModel):
    year: int = Field(strict=True, ge=date.today().year, le=date.today().year + 5)
    month: int = Field(strict=True, ge=1, le=12)


class ReplacementSelection(BaseModel):
    date: date
    brand_code: str
    process_code: str
    employee_code: str


class ReplanRosterRequest(BaseModel):
    affected_dates: list[date] = Field(default_factory=list)
    full_month: bool = False
    replacement: ReplacementSelection | None = None


class AvailabilityChangeRequest(BaseModel):
    employee_code: str = Field(min_length=1, max_length=20, strip_whitespace=True)
    exception_type: AvailabilityType
    approval_status: ApprovalStatus
    start_date: date
    end_date: date
    note: str = Field(default="", max_length=200)

    @field_validator("employee_code", mode="before")
    @classmethod
    def reject_blank_employee_code(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("Employee code cannot be blank.")
        return value

    @model_validator(mode="after")
    def validate_date_range(self) -> "AvailabilityChangeRequest":
        if self.end_date < self.start_date:
            raise ValueError("End date must be on or after start date.")
        return self


class AvailabilityStatusRequest(BaseModel):
    approval_status: ApprovalStatus


class EmployeeConfigurationRequest(BaseModel):
    employee_code: str = Field(min_length=1, max_length=20, strip_whitespace=True)
    full_name: str = Field(min_length=1, max_length=120, strip_whitespace=True)
    role_title: str = Field(min_length=1, max_length=80, strip_whitespace=True)
    pod_name: str = Field(min_length=1, max_length=80, strip_whitespace=True)
    pod_names: list[str] = Field(default_factory=list)
    is_pod_lead: bool = False
    status: str = Field(default="active", pattern="^(active|inactive)$")
    skills: list[str] = Field(default_factory=list)
    weekly_off_weekdays: list[StrictInt] = Field(default_factory=list)
    daily_capacity_hours: float = Field(default=6, gt=0, le=8)
    max_days_per_week: StrictInt = Field(default=5, ge=1, le=5)
    max_hours_per_week: float = Field(default=40, gt=0, le=45)
    minimum_days_off_per_week: StrictInt = Field(default=2, ge=0, le=6)

    @field_validator("employee_code", "full_name", "role_title", "pod_name", mode="before")
    @classmethod
    def reject_blank_values(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("This value cannot be blank.")
        return value

    @field_validator("weekly_off_weekdays")
    @classmethod
    def validate_weekly_offs(cls, values: list[int]) -> list[int]:
        if len(values) > 6 or any(value not in range(7) for value in values) or len(set(values)) != len(values):
            raise ValueError("Weekly offs must be unique weekdays from 0 through 6.")
        return values

    @field_validator("skills")
    @classmethod
    def validate_skills(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().upper() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Skill codes must be unique.")
        return normalized

    @field_validator("pod_names")
    @classmethod
    def validate_pod_memberships(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Pod memberships must be unique.")
        return normalized

    @model_validator(mode="after")
    def validate_cs_weekly_cap(self) -> "EmployeeConfigurationRequest":
        if self.max_hours_per_week > 40 and "CS" not in self.skills:
            raise ValueError("A weekly cap above 40 hours is allowed only for employees with the CS skill.")
        return self


class PodConfigurationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80, strip_whitespace=True)
    specialization: str = Field(default="", max_length=120)
    lead_employee_code: str | None = None
    member_employee_codes: list[str] | None = None
    shift_name: str = Field(default="Work window", min_length=1, max_length=80)
    shift_start: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    break_start: str = Field(default="13:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    break_end: str = Field(default="14:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    allocation_capacity_hours: float = Field(default=6, gt=0, le=8)

    @model_validator(mode="after")
    def validate_shift_window(self) -> "PodConfigurationRequest":
        start, end = int(self.shift_start[:2] + self.shift_start[3:]), int(self.shift_end[:2] + self.shift_end[3:])
        break_start = int(self.break_start[:2] + self.break_start[3:])
        break_end = int(self.break_end[:2] + self.break_end[3:])
        if not start < break_start < break_end < end:
            raise ValueError("The lunch break must fall inside the pod work window.")
        if end - start != 900:
            raise ValueError("Demo pod windows must span 8 clock hours; configure lunch and allocation separately.")
        return self

    @field_validator("member_employee_codes")
    @classmethod
    def validate_member_codes(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Pod members must be unique employee codes.")
        return normalized


class BrandConfigurationRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20, strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100, strip_whitespace=True)
    active: bool = True
    deadline_priority: StrictInt = Field(default=3, ge=1, le=5)
    deadline_note: str = Field(default="", max_length=200)
    client_tier: StrictInt = Field(default=3, ge=1, le=5)
    deadline_date: date | None = None


class AllocationSettingsRequest(BaseModel):
    capacity_units: StrictInt = Field(default=12, ge=1, le=16)
    min_chunk_units: StrictInt = Field(default=1, ge=1, le=16)
    max_brands_per_day: StrictInt = Field(default=2, ge=1, le=15)
    hours_layer_enabled: StrictBool = False
    solver_time_limit_seconds: StrictInt = Field(default=60, ge=1, le=600)

    @model_validator(mode="after")
    def validate_units(self) -> "AllocationSettingsRequest":
        if self.min_chunk_units > self.capacity_units:
            raise ValueError("Minimum allocation chunk cannot exceed daily capacity.")
        return self


class PriorityOverrideRequest(BaseModel):
    brand_code: str = Field(min_length=1, max_length=20)
    start_date: date
    end_date: date
    multiplier: float = Field(gt=0, le=10)
    note: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def validate_dates(self) -> "PriorityOverrideRequest":
        if self.end_date < self.start_date:
            raise ValueError("End date must be on or after start date.")
        return self


class ProcessConfigurationRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20, strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80, strip_whitespace=True)
    description: str = Field(default="", max_length=2000)
    required_skill: str = Field(min_length=1, max_length=30, strip_whitespace=True)
    default_effort_hours: float = Field(default=6, gt=0, le=6)
    enabled: bool = True


class BrandProcessConfigurationRequest(BaseModel):
    brand_code: str
    process_code: str
    operating_weekdays: list[StrictInt]
    operate_on_public_holidays: bool = False
    effort_hours_per_employee: float = Field(gt=0, le=6)
    demand_units: StrictInt | None = Field(default=None, ge=0, le=10000)
    pod_name: str | None = None
    can_share_same_day: bool = False
    minimum_by_weekday: list[StrictInt] = Field(min_length=7, max_length=7)
    maximum_by_weekday: list[StrictInt | None] = Field(default_factory=lambda: [None] * 7, min_length=7, max_length=7)

    @field_validator("operating_weekdays")
    @classmethod
    def validate_days(cls, values: list[int]) -> list[int]:
        if not values or len(values) != len(set(values)) or any(value not in range(7) for value in values):
            raise ValueError("Select unique operating weekdays from 0 through 6.")
        return values

    @model_validator(mode="after")
    def validate_coverage(self) -> "BrandProcessConfigurationRequest":
        for minimum, maximum in zip(self.minimum_by_weekday, self.maximum_by_weekday):
            if minimum < 0 or (maximum is not None and maximum < minimum):
                raise ValueError("Daily coverage minimums and maximums are inconsistent.")
        return self


class EligibilityChangeRequest(BaseModel):
    employee_code: str
    rule_id: int
    eligibility_type: EligibilityType
    active: bool = True


class PublicHolidayConfigurationRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, strip_whitespace=True)
    holiday_date: date = Field(alias="date")


class AssignmentEditRequest(BaseModel):
    employee_code: str | None


class BrandProcessRuleChangeRequest(BaseModel):
    operating_weekdays: list[StrictInt] | None = None
    operate_on_public_holidays: StrictBool | None = None
    can_share_same_day: StrictBool | None = None
    effort_hours_per_employee: float | None = Field(default=None, gt=0, le=6)
    demand_units: StrictInt | None = Field(default=None, ge=0, le=10000)
    weekday: StrictInt | None = Field(default=None, ge=0, le=6)
    minimum_staff: StrictInt | None = Field(default=None, ge=0)
    maximum_staff: StrictInt | None = Field(default=None, ge=0)
    pod_name: str | None = None

    @field_validator("operating_weekdays")
    @classmethod
    def validate_operating_weekdays(cls, days: list[int] | None) -> list[int] | None:
        if days is None:
            return days
        if not days:
            raise ValueError("Select at least one operating weekday.")
        if any(day not in range(7) for day in days) or len(set(days)) != len(days):
            raise ValueError("Operating weekdays must be unique integers from 0 through 6.")
        return days

    @model_validator(mode="after")
    def require_an_actual_change(self) -> "BrandProcessRuleChangeRequest":
        change_fields = {
            "operating_weekdays", "operate_on_public_holidays", "can_share_same_day",
            "minimum_staff", "maximum_staff",
            "pod_name",
            "effort_hours_per_employee",
            "demand_units",
        }
        if not self.model_fields_set.intersection(change_fields):
            raise ValueError("Provide at least one brand/process setting to change.")
        for field_name in ("operating_weekdays", "operate_on_public_holidays", "can_share_same_day",
                           "effort_hours_per_employee"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "weekday" in self.model_fields_set and self.weekday is None:
            raise ValueError("weekday cannot be null when provided.")
        if "pod_name" in self.model_fields_set and not self.pod_name:
            raise ValueError("pod_name cannot be blank when provided.")
        return self


def validate_staffing_limit(minimum_staff: int | None, maximum_staff: int | None,
                            active_employee_count: int) -> None:
    """Reject staffing targets that cannot fit within the active team size."""
    if any(value is not None and value > active_employee_count
           for value in (minimum_staff, maximum_staff)):
        raise HTTPException(
            status_code=422,
            detail=f"Staffing limits cannot exceed the active team size ({active_employee_count}).",
        )


@app.on_event("startup")
def create_schema() -> None:
    """Create the assessment demo schema when the API starts."""
    upgrade_schema(engine)


def allocation_settings_payload(session: Session) -> dict:
    setting = session.get(AllocationSetting, 1)
    if setting is None:
        return {"capacity_units": 12, "min_chunk_units": 1, "max_brands_per_day": 2,
                "hours_layer_enabled": False, "solver_time_limit_seconds": 60}
    return {"capacity_units": setting.capacity_units, "min_chunk_units": setting.min_chunk_units,
            "max_brands_per_day": setting.max_brands_per_day,
            "hours_layer_enabled": setting.hours_layer_enabled,
            "solver_time_limit_seconds": setting.solver_time_limit_seconds}


def priority_overrides_payload(session: Session) -> list[dict]:
    return [{"id": row.id, "brand_code": row.brand.code, "start_date": row.start_date.isoformat(),
             "end_date": row.end_date.isoformat(), "multiplier": row.multiplier, "note": row.note}
            for row in session.scalars(select(PriorityOverride).options(
                selectinload(PriorityOverride.brand)).order_by(PriorityOverride.start_date)).all()]


@app.get("/api/v1/allocation-settings")
def get_allocation_settings(session: Session = Depends(get_session)) -> dict:
    return allocation_settings_payload(session)


@app.put("/api/v1/allocation-settings")
def put_allocation_settings(payload: AllocationSettingsRequest,
                            session: Session = Depends(get_session)) -> dict:
    setting = session.get(AllocationSetting, 1)
    if setting is None:
        setting = AllocationSetting(id=1)
        session.add(setting)
    for key, value in payload.model_dump().items():
        setattr(setting, key, value)
    session.commit()
    return allocation_settings_payload(session)


@app.get("/api/v1/priority-overrides")
def get_priority_overrides(session: Session = Depends(get_session)) -> list[dict]:
    return priority_overrides_payload(session)


@app.post("/api/v1/priority-overrides")
def create_priority_override(payload: PriorityOverrideRequest,
                             session: Session = Depends(get_session)) -> dict:
    brand = session.scalar(select(Brand).where(Brand.code == payload.brand_code.strip().upper()))
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    row = PriorityOverride(brand_id=brand.id, start_date=payload.start_date,
                           end_date=payload.end_date, multiplier=payload.multiplier,
                           note=payload.note.strip())
    session.add(row)
    session.commit()
    session.refresh(row)
    return {"id": row.id, "brand_code": brand.code, "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(), "multiplier": row.multiplier, "note": row.note}


@app.patch("/api/v1/priority-overrides/{override_id}")
def update_priority_override(override_id: int, payload: PriorityOverrideRequest,
                             session: Session = Depends(get_session)) -> dict:
    row = session.get(PriorityOverride, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Priority override not found.")
    brand = session.scalar(select(Brand).where(Brand.code == payload.brand_code.strip().upper()))
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    row.brand_id = brand.id
    row.start_date, row.end_date = payload.start_date, payload.end_date
    row.multiplier, row.note = payload.multiplier, payload.note.strip()
    session.commit()
    return {"id": row.id, "brand_code": brand.code, "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(), "multiplier": row.multiplier, "note": row.note}


@app.delete("/api/v1/priority-overrides/{override_id}")
def delete_priority_override(override_id: int, session: Session = Depends(get_session)) -> dict:
    row = session.get(PriorityOverride, override_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Priority override not found.")
    session.delete(row)
    session.commit()
    return {"status": "removed"}


@app.get("/api/v1/demo-identities")
def demo_identities(session: Session = Depends(get_session)) -> list[dict]:
    """List selectable demo identities; this endpoint contains no schedule data."""
    employees = session.scalars(select(Employee).where(
        Employee.status == "active",
    ).order_by(Employee.employee_code)).all()
    return [{"employee_code": item.employee_code, "full_name": item.full_name,
             "is_pod_lead": item.is_pod_lead} for item in employees]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "stage": "4 - management, roster views, and editing"}


@app.get("/api/v1/seed-summary")
def seed_summary(request: Request, session: Session = Depends(get_session)) -> dict:
    """Read-only inventory so the manager can inspect demo inputs."""
    employees = session.scalars(
        select(Employee).options(
            selectinload(Employee.primary_pod), selectinload(Employee.skills),
            selectinload(Employee.weekly_offs), selectinload(Employee.work_contract),
            selectinload(Employee.availability_exceptions), selectinload(Employee.preferences),
        ).order_by(Employee.employee_code)
    ).all()
    pods = session.scalars(
        select(Pod).options(selectinload(Pod.members), selectinload(Pod.lead)).order_by(Pod.name)
    ).all()
    brands = session.scalars(
        select(Brand).options(
            selectinload(Brand.process_rules).selectinload(BrandProcessRule.process),
            selectinload(Brand.process_rules).selectinload(BrandProcessRule.required_skill),
            selectinload(Brand.process_rules).selectinload(BrandProcessRule.coverage_requirements),
            selectinload(Brand.process_rules).selectinload(BrandProcessRule.eligibility)
                .selectinload(EmployeeEligibility.employee),
        ).order_by(Brand.code)
    ).all()
    process_catalog = session.scalars(select(Process).options(
        selectinload(Process.required_skill)
    ).order_by(Process.code)).all()
    memberships = session.scalars(select(EmployeePodMembership)).all()
    pod_by_id = {pod.id: pod for pod in pods}
    pod_ids_by_employee: dict[int, set[int]] = {}
    for membership in memberships:
        pod_ids_by_employee.setdefault(membership.employee_id, set()).add(membership.pod_id)
    for employee in employees:
        pod_ids_by_employee.setdefault(employee.id, set()).add(employee.primary_pod_id)
    priorities = {item.brand_id: item for item in session.scalars(select(BrandPriority)).all()}
    pod_schedules = {item.pod_id: item for item in session.scalars(select(PodSchedule)).all()}
    process_schedules = {item.brand_process_rule_id: item for item in session.scalars(select(BrandProcessSchedule)).all()}

    brand_details = []
    for brand in brands:
        process_details = []
        for rule in brand.process_rules:
            process_details.append({
                "rule_id": rule.id,
                "code": rule.process.code,
                "name": rule.process.name,
                "required_skill": rule.required_skill.code,
                "operating_weekdays": rule.operating_weekdays,
                "operate_on_public_holidays": rule.operate_on_public_holidays,
                "effort_hours_per_employee": rule.effort_hours_per_employee,
                "demand_units": rule.demand_units,
                "can_share_same_day": rule.can_share_same_day,
                "continuous_presence": rule.continuous_presence,
                "pod_name": pod_by_id[process_schedules[rule.id].pod_id].name
                    if rule.id in process_schedules and process_schedules[rule.id].pod_id in pod_by_id else None,
                "deadline_priority": priorities[brand.id].priority if brand.id in priorities else 3,
                "deadline_note": priorities[brand.id].deadline_note if brand.id in priorities else "",
                "client_tier": priorities[brand.id].client_tier if brand.id in priorities else 3,
                "deadline_date": priorities[brand.id].deadline_date.isoformat()
                    if brand.id in priorities and priorities[brand.id].deadline_date else None,
                "coverage_by_weekday": [
                    {"weekday": item.weekday, "minimum_staff": item.minimum_staff,
                     "maximum_staff": item.maximum_staff}
                    for item in sorted(rule.coverage_requirements, key=lambda row: row.weekday)
                ],
                "eligible_employees": [
                    {"employee_code": eligibility.employee.employee_code,
                     "eligibility_type": eligibility.eligibility_type.value,
                     "active": eligibility.active}
                    for eligibility in rule.eligibility
                ],
            })
        brand_details.append({"id": brand.id, "code": brand.code, "name": brand.name,
                              "active": brand.is_active, "demo_data": brand.is_demo_data,
                              "deadline_priority": priorities[brand.id].priority if brand.id in priorities else 3,
                              "deadline_note": priorities[brand.id].deadline_note if brand.id in priorities else "",
                              "client_tier": priorities[brand.id].client_tier if brand.id in priorities else 3,
                              "deadline_date": priorities[brand.id].deadline_date.isoformat()
                                  if brand.id in priorities and priorities[brand.id].deadline_date else None,
                              "processes": process_details})

    result = {
        "demo_data_notice": "All names, calendars, effort, eligibility, and availability are fictional assessment assumptions.",
        "counts": {"employees": len(employees), "pods": len(pods), "brands": len(brands),
                   "processes": len(process_catalog)},
        "employees": [{
            "id": employee.id,
            "code": employee.employee_code,
            "name": employee.full_name,
            "role": employee.role_title,
            "status": employee.status,
            "pod": employee.primary_pod.name,
            "pods": sorted(pod_by_id[pod_id].name for pod_id in
                           pod_ids_by_employee.get(employee.id, {employee.primary_pod_id}) if pod_id in pod_by_id),
            "is_pod_lead": employee.is_pod_lead,
            "skills": sorted(skill.code for skill in employee.skills),
            "weekly_off_weekdays": sorted(off.weekday for off in employee.weekly_offs),
            "preferences": [{"weekday": preference.weekday,
                             "type": preference.preference_type.value,
                             "note": preference.note}
                            for preference in employee.preferences],
            "availability_exceptions": [{
                "type": item.exception_type.value,
                "id": item.id,
                "approval_status": item.approval_status.value,
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
                "note": item.note,
            } for item in employee.availability_exceptions],
            "contract": {
                "daily_capacity_hours": employee.work_contract.daily_capacity_hours,
                "max_days_per_week": employee.work_contract.max_days_per_week,
                "max_hours_per_week": employee.work_contract.max_hours_per_week,
                "minimum_days_off_per_week": employee.work_contract.minimum_days_off_per_week,
            },
        } for employee in employees],
        "public_holidays": [{"id": holiday.id, "name": holiday.name, "date": holiday.holiday_date.isoformat()}
                            for holiday in session.scalars(select(PublicHoliday).order_by(PublicHoliday.holiday_date))],
        "pods": [{"id": pod.id, "name": pod.name, "specialization": pod.specialization,
                  "lead_employee_code": pod.lead.employee_code if pod.lead else None,
                  "members": sorted({member.employee_code for member in pod.members} |
                                    {employee.employee_code for employee in employees
                                     if pod.id in pod_ids_by_employee.get(employee.id, set())}),
                  "shift": None if pod.id not in pod_schedules else {
                      "name": pod_schedules[pod.id].shift_name,
                      "start": pod_schedules[pod.id].shift_start,
                      "end": pod_schedules[pod.id].shift_end,
                      "break_start": pod_schedules[pod.id].break_start,
                      "break_end": pod_schedules[pod.id].break_end,
                      "allocation_capacity_hours": pod_schedules[pod.id].allocation_capacity_hours,
                  }} for pod in pods],
        "process_catalog": [{"code": process.code, "name": process.name,
                             "required_skill": process.required_skill.code,
                             "default_effort_hours": process.default_effort_hours,
                             "enabled": process.enabled} for process in process_catalog],
        "brands": brand_details,
        "allocation_settings": allocation_settings_payload(session),
        "priority_overrides": priority_overrides_payload(session),
    }

    role = request.state.actor_role
    if role == "manager":
        return result

    actor_employee_id = request.state.actor_employee_id
    actor_employee_code = request.state.actor_employee_code
    visible_pod_ids = request.state.actor_pod_ids
    visible_member_ids = request.state.actor_member_ids
    if role == "employee":
        visible_employees = [item for item in result["employees"] if item["id"] == actor_employee_id]
        visible_pods = [item for item in result["pods"]
                        if actor_employee_code in item["members"] or item["lead_employee_code"] == actor_employee_code]
        result["employees"] = visible_employees
        result["pods"] = visible_pods
        result["brands"] = []
        result["process_catalog"] = []
    else:
        visible_pod_names = {pod_by_id[pod_id].name for pod_id in visible_pod_ids if pod_id in pod_by_id}
        visible_employees = [item for item in result["employees"] if item["id"] in visible_member_ids]
        result["employees"] = visible_employees
        result["pods"] = [item for item in result["pods"] if item["id"] in visible_pod_ids]
        scoped_brands = []
        for brand in result["brands"]:
            scoped_processes = [item for item in brand["processes"] if item["pod_name"] in visible_pod_names]
            if scoped_processes:
                for process in scoped_processes:
                    process["eligible_employees"] = [
                        eligible for eligible in process["eligible_employees"]
                        if any(employee["code"] == eligible["employee_code"] for employee in visible_employees)
                    ]
                brand["processes"] = scoped_processes
                scoped_brands.append(brand)
        result["brands"] = scoped_brands
        visible_process_codes = {item["code"] for brand in scoped_brands for item in brand["processes"]}
        result["process_catalog"] = [item for item in result["process_catalog"]
                                      if item["code"] in visible_process_codes]

    result["counts"] = {
        "employees": len(result["employees"]), "pods": len(result["pods"]),
        "brands": len(result["brands"]),
        "processes": len(result["process_catalog"]),
    }
    return result


def _find_employee(session: Session, code: str) -> Employee | None:
    return session.scalar(select(Employee).where(Employee.employee_code == code))


def _sync_pod_members(session: Session, pod: Pod, employee_codes: list[str],
                       lead_employee_code: str | None = None) -> None:
    """Set additional pod memberships while preserving employees' primary pods."""
    selected_codes = set(employee_codes)
    if lead_employee_code:
        selected_codes.add(lead_employee_code)
    selected = session.scalars(select(Employee).where(
        Employee.employee_code.in_(selected_codes), Employee.status == "active",
    )).all() if selected_codes else []
    found_codes = {employee.employee_code for employee in selected}
    if found_codes != selected_codes:
        missing = sorted(selected_codes - found_codes)
        raise HTTPException(status_code=422, detail=f"Unknown or inactive pod member(s): {', '.join(missing)}.")
    selected_ids = {employee.id for employee in selected}
    current_links = session.scalars(select(EmployeePodMembership).where(
        EmployeePodMembership.pod_id == pod.id,
    )).all()
    for link in current_links:
        employee = session.get(Employee, link.employee_id)
        if link.employee_id not in selected_ids and employee and employee.primary_pod_id != pod.id:
            session.delete(link)
    existing_ids = {link.employee_id for link in current_links}
    for employee in selected:
        if employee.id not in existing_ids:
            session.add(EmployeePodMembership(employee_id=employee.id, pod_id=pod.id))
    session.flush()


@app.post("/api/v1/employees")
def create_employee(payload: EmployeeConfigurationRequest,
                    session: Session = Depends(get_session)) -> dict:
    if payload.is_pod_lead and payload.status != "active":
        raise HTTPException(status_code=422, detail="A pod lead must be an active employee.")
    if _find_employee(session, payload.employee_code):
        raise HTTPException(status_code=409, detail="Employee code is already in use.")
    pod = session.scalar(select(Pod).where(Pod.name == payload.pod_name))
    if pod is None:
        raise HTTPException(status_code=404, detail="Choose an existing pod before adding an employee.")
    employee = Employee(employee_code=payload.employee_code, full_name=payload.full_name,
                        role_title=payload.role_title, primary_pod=pod,
                        is_pod_lead=payload.is_pod_lead, status=payload.status)
    session.add(employee)
    session.flush()
    _save_employee_configuration(session, employee, payload)
    if payload.is_pod_lead:
        if pod.lead and pod.lead.id != employee.id:
            pod.lead.is_pod_lead = False
        pod.lead = employee
    session.commit()
    return {"employee_code": employee.employee_code, "status": "saved"}


@app.patch("/api/v1/employees/{employee_code}")
def update_employee(employee_code: str, payload: EmployeeConfigurationRequest,
                    session: Session = Depends(get_session)) -> dict:
    if payload.is_pod_lead and payload.status != "active":
        raise HTTPException(status_code=422, detail="A pod lead must be an active employee.")
    employee = _find_employee(session, employee_code)
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    if payload.employee_code != employee_code and _find_employee(session, payload.employee_code):
        raise HTTPException(status_code=409, detail="Employee code is already in use.")
    pod = session.scalar(select(Pod).where(Pod.name == payload.pod_name))
    if pod is None:
        raise HTTPException(status_code=404, detail="Choose an existing pod before updating an employee.")
    previous_pod = employee.primary_pod
    if previous_pod.lead_employee_id == employee.id:
        previous_pod.lead = None
    employee.employee_code = payload.employee_code
    employee.full_name = payload.full_name
    employee.role_title = payload.role_title
    employee.primary_pod = pod
    employee.is_pod_lead = payload.is_pod_lead
    employee.status = payload.status
    _save_employee_configuration(session, employee, payload)
    if payload.is_pod_lead:
        if pod.lead and pod.lead.id != employee.id:
            pod.lead.is_pod_lead = False
        pod.lead = employee
    elif pod.lead_employee_id == employee.id:
        pod.lead = None
    session.commit()
    return {"employee_code": employee.employee_code, "status": "saved"}


def _save_employee_configuration(session: Session, employee: Employee,
                                 payload: EmployeeConfigurationRequest) -> None:
    pod_names = set(payload.pod_names) | {payload.pod_name}
    pods = session.scalars(select(Pod).where(Pod.name.in_(pod_names))).all()
    if {pod.name for pod in pods} != pod_names:
        missing = sorted(pod_names - {pod.name for pod in pods})
        raise HTTPException(status_code=404, detail=f"Unknown pod(s): {', '.join(missing)}.")
    session.execute(delete(EmployeePodMembership).where(EmployeePodMembership.employee_id == employee.id))
    session.add_all(EmployeePodMembership(employee_id=employee.id, pod_id=pod.id) for pod in pods)
    employee.weekly_offs.clear()
    employee.weekly_offs.extend(EmployeeWeeklyOff(weekday=day) for day in payload.weekly_off_weekdays)
    contract = employee.work_contract
    if contract is None:
        contract = EmployeeWorkContract(employee=employee)
    contract.daily_capacity_hours = payload.daily_capacity_hours
    contract.max_days_per_week = payload.max_days_per_week
    contract.max_hours_per_week = payload.max_hours_per_week
    contract.minimum_days_off_per_week = payload.minimum_days_off_per_week
    session.add(contract)
    known = {skill.code: skill for skill in session.scalars(select(Skill)).all()}
    missing = sorted(set(payload.skills) - set(known))
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown skill code(s): {', '.join(missing)}.")
    employee.skills = [known[code] for code in payload.skills]


@app.post("/api/v1/pods")
def create_pod(payload: PodConfigurationRequest, session: Session = Depends(get_session)) -> dict:
    if session.scalar(select(Pod.id).where(Pod.name == payload.name)) is not None:
        raise HTTPException(status_code=409, detail="Pod name is already in use.")
    pod = Pod(name=payload.name, specialization=payload.specialization)
    session.add(pod)
    session.flush()
    session.add(PodSchedule(
        pod_id=pod.id, shift_name=payload.shift_name,
        shift_start=payload.shift_start, shift_end=payload.shift_end,
        break_start=payload.break_start, break_end=payload.break_end,
        allocation_capacity_hours=payload.allocation_capacity_hours,
    ))
    if payload.lead_employee_code:
        lead = _find_employee(session, payload.lead_employee_code)
        if lead is None:
            raise HTTPException(status_code=404, detail="Pod lead employee not found.")
        if lead.status != "active":
            raise HTTPException(status_code=422, detail="A pod lead must be an active employee.")
        if lead.primary_pod and lead.primary_pod.lead_employee_id == lead.id:
            lead.primary_pod.lead = None
        if pod.lead and pod.lead.id != lead.id:
            pod.lead.is_pod_lead = False
        lead.primary_pod = pod
        if session.get(EmployeePodMembership, (lead.id, pod.id)) is None:
            session.add(EmployeePodMembership(employee_id=lead.id, pod_id=pod.id))
        lead.is_pod_lead = True
        pod.lead = lead
    if payload.member_employee_codes is not None or payload.lead_employee_code:
        _sync_pod_members(session, pod, payload.member_employee_codes or [], payload.lead_employee_code)
    session.commit()
    return {"pod_id": pod.id, "status": "saved"}


@app.patch("/api/v1/pods/{pod_id}")
def update_pod(pod_id: int, payload: PodConfigurationRequest,
               session: Session = Depends(get_session)) -> dict:
    pod = session.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found.")
    duplicate = session.scalar(select(Pod.id).where(Pod.name == payload.name, Pod.id != pod_id))
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Pod name is already in use.")
    pod.name = payload.name
    pod.specialization = payload.specialization
    schedule = session.scalar(select(PodSchedule).where(PodSchedule.pod_id == pod.id))
    if schedule is None:
        schedule = PodSchedule(pod_id=pod.id)
        session.add(schedule)
    schedule.shift_name = payload.shift_name
    schedule.shift_start = payload.shift_start
    schedule.shift_end = payload.shift_end
    schedule.break_start = payload.break_start
    schedule.break_end = payload.break_end
    schedule.allocation_capacity_hours = payload.allocation_capacity_hours
    if payload.member_employee_codes is not None:
        _sync_pod_members(session, pod, payload.member_employee_codes, payload.lead_employee_code)
    if payload.lead_employee_code:
        lead = _find_employee(session, payload.lead_employee_code)
        if lead is None:
            raise HTTPException(status_code=404, detail="Pod lead employee not found.")
        if lead.status != "active":
            raise HTTPException(status_code=422, detail="A pod lead must be an active employee.")
        if lead.primary_pod_id != pod.id and session.get(EmployeePodMembership, (lead.id, pod.id)) is None:
            raise HTTPException(status_code=422, detail="The pod lead must be a member of this pod.")
        if pod.lead and pod.lead.id != lead.id:
            pod.lead.is_pod_lead = False
        pod.lead = lead
        lead.is_pod_lead = True
    else:
        if pod.lead:
            pod.lead.is_pod_lead = False
        pod.lead = None
    session.commit()
    return {"pod_id": pod.id, "status": "saved"}


@app.delete("/api/v1/pods/{pod_id}")
def delete_pod(pod_id: int, session: Session = Depends(get_session)) -> dict:
    pod = session.get(Pod, pod_id)
    if pod is None:
        raise HTTPException(status_code=404, detail="Pod not found.")
    if pod.members or session.scalar(select(EmployeePodMembership.employee_id).where(
        EmployeePodMembership.pod_id == pod.id).limit(1)):
        raise HTTPException(status_code=409, detail="Move pod members before removing this pod.")
    if session.scalar(select(BrandProcessSchedule.brand_process_rule_id).where(
        BrandProcessSchedule.pod_id == pod.id).limit(1)):
        raise HTTPException(status_code=409, detail="Move brand/process work to another pod before removing this pod.")
    pod.lead = None
    session.delete(pod)
    session.commit()
    return {"status": "removed"}


@app.post("/api/v1/brands")
def create_brand(payload: BrandConfigurationRequest, session: Session = Depends(get_session)) -> dict:
    code = payload.code.upper()
    if session.scalar(select(Brand.id).where(Brand.code == code)) is not None:
        raise HTTPException(status_code=409, detail="Brand code is already in use.")
    brand = Brand(code=code, name=payload.name, is_active=payload.active, is_demo_data=False)
    session.add(brand)
    session.flush()
    session.add(BrandPriority(brand_id=brand.id, priority=payload.deadline_priority,
                              deadline_note=payload.deadline_note.strip(), client_tier=payload.client_tier,
                              deadline_date=payload.deadline_date))
    session.commit()
    return {"brand_code": brand.code, "status": "saved"}


@app.patch("/api/v1/brands/{brand_code}")
def update_brand(brand_code: str, payload: BrandConfigurationRequest,
                 session: Session = Depends(get_session)) -> dict:
    brand = session.scalar(select(Brand).where(Brand.code == brand_code))
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    if payload.code.upper() != brand_code and session.scalar(select(Brand.id).where(Brand.code == payload.code.upper())):
        raise HTTPException(status_code=409, detail="Brand code is already in use.")
    brand.code, brand.name, brand.is_active = payload.code.upper(), payload.name, payload.active
    priority = session.get(BrandPriority, brand.id)
    if priority is None:
        priority = BrandPriority(brand_id=brand.id)
        session.add(priority)
    priority.priority = payload.deadline_priority
    priority.deadline_note = payload.deadline_note.strip()
    priority.client_tier = payload.client_tier
    priority.deadline_date = payload.deadline_date
    session.commit()
    return {"brand_code": brand.code, "status": "saved"}


@app.delete("/api/v1/brands/{brand_code}")
def delete_brand(brand_code: str, session: Session = Depends(get_session)) -> dict:
    brand = session.scalar(select(Brand).where(Brand.code == brand_code))
    if brand is None:
        raise HTTPException(status_code=404, detail="Brand not found.")
    if brand.process_rules or session.scalar(select(RosterAssignment.id).join(RosterAssignment.brand_process)
                                             .where(BrandProcessRule.brand_id == brand.id).limit(1)):
        raise HTTPException(status_code=409, detail="Remove unused process rules first, or archive this brand.")
    session.delete(brand)
    session.commit()
    return {"status": "removed"}


@app.post("/api/v1/processes")
def create_process(payload: ProcessConfigurationRequest,
                   session: Session = Depends(get_session)) -> dict:
    code = payload.code.upper()
    if session.scalar(select(Process.id).where(Process.code == code)) is not None:
        raise HTTPException(status_code=409, detail="Process code is already in use.")
    skill_code = payload.required_skill.upper()
    skill = session.scalar(select(Skill).where(Skill.code == skill_code))
    if skill is None:
        skill = Skill(code=skill_code, name=skill_code.replace("_", " ").title())
        session.add(skill)
        session.flush()
    process = Process(code=code, name=payload.name, description=payload.description,
                      required_skill=skill, default_effort_hours=payload.default_effort_hours,
                      enabled=payload.enabled)
    session.add(process)
    session.commit()
    return {"process_code": process.code, "status": "saved"}


@app.patch("/api/v1/processes/{process_code}")
def update_process(process_code: str, payload: ProcessConfigurationRequest,
                   session: Session = Depends(get_session)) -> dict:
    process = session.scalar(select(Process).where(Process.code == process_code.upper()))
    if process is None:
        raise HTTPException(status_code=404, detail="Process not found.")
    if not payload.enabled and process.brand_rules:
        raise HTTPException(status_code=409, detail="Remove the process from its brands before disabling it.")
    new_code = payload.code.upper()
    if new_code != process.code and session.scalar(select(Process.id).where(Process.code == new_code)):
        raise HTTPException(status_code=409, detail="Process code is already in use.")
    skill_code = payload.required_skill.upper()
    skill = session.scalar(select(Skill).where(Skill.code == skill_code))
    if skill is None:
        skill = Skill(code=skill_code, name=skill_code.replace("_", " ").title())
        session.add(skill)
        session.flush()
    process.code, process.name, process.description = new_code, payload.name, payload.description
    process.required_skill = skill
    process.default_effort_hours = payload.default_effort_hours
    process.enabled = payload.enabled
    for rule in process.brand_rules:
        rule.required_skill = skill
    session.commit()
    return {"process_code": process.code, "status": "saved"}


@app.delete("/api/v1/processes/{process_code}")
def delete_process(process_code: str, session: Session = Depends(get_session)) -> dict:
    process = session.scalar(select(Process).where(Process.code == process_code.upper()))
    if process is None:
        raise HTTPException(status_code=404, detail="Process not found.")
    if process.brand_rules or session.scalar(select(RosterAssignment.id).join(RosterAssignment.brand_process)
                                              .where(BrandProcessRule.process_id == process.id).limit(1)):
        raise HTTPException(status_code=409, detail="Remove unused brand/process rules first.")
    session.delete(process)
    session.commit()
    return {"status": "removed"}


@app.post("/api/v1/brand-process-rules")
def create_brand_process_rule(payload: BrandProcessConfigurationRequest,
                              session: Session = Depends(get_session)) -> dict:
    brand = session.scalar(select(Brand).where(Brand.code == payload.brand_code.upper()))
    process = session.scalar(select(Process).where(Process.code == payload.process_code.upper()))
    if brand is None or process is None:
        raise HTTPException(status_code=404, detail="Choose an existing brand and process.")
    if not process.enabled:
        raise HTTPException(status_code=422, detail="Enable this process before adding it to a brand.")
    if payload.effort_hours_per_employee > 6:
        raise HTTPException(status_code=422, detail="Brand work blocks cannot exceed six allocatable hours.")
    if session.scalar(select(BrandProcessRule.id).where(
        BrandProcessRule.brand_id == brand.id, BrandProcessRule.process_id == process.id
    )):
        raise HTTPException(status_code=409, detail="This brand already has that process.")
    rule = BrandProcessRule(
        brand=brand, process=process, required_skill=process.required_skill,
        operating_weekdays=payload.operating_weekdays,
        operate_on_public_holidays=payload.operate_on_public_holidays,
        effort_hours_per_employee=payload.effort_hours_per_employee,
        demand_units=payload.demand_units,
        can_share_same_day=payload.can_share_same_day, continuous_presence=False,
        notes="Manager-configured assessment rule.",
    )
    session.add(rule)
    session.flush()
    if payload.pod_name:
        pod = session.scalar(select(Pod).where(Pod.name == payload.pod_name))
        if pod is None or session.scalar(select(PodSchedule.id).where(PodSchedule.pod_id == pod.id)) is None:
            raise HTTPException(status_code=422, detail="Choose a pod with a configured work window.")
        session.add(BrandProcessSchedule(brand_process_rule_id=rule.id, pod_id=pod.id))
    if session.get(BrandPriority, brand.id) is None:
        session.add(BrandPriority(brand_id=brand.id, priority=3, deadline_note=""))
    session.add_all(CoverageRequirement(
        brand_process=rule, weekday=weekday,
        minimum_staff=payload.minimum_by_weekday[weekday],
        maximum_staff=payload.maximum_by_weekday[weekday],
    ) for weekday in range(7))
    session.commit()
    return {"rule_id": rule.id, "status": "saved"}


@app.delete("/api/v1/brand-process-rules/{rule_id}")
def delete_brand_process_rule(rule_id: int, session: Session = Depends(get_session)) -> dict:
    rule = session.get(BrandProcessRule, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Brand/process rule not found.")
    if session.scalar(select(RosterAssignment.id).where(RosterAssignment.brand_process_rule_id == rule.id).limit(1)):
        raise HTTPException(status_code=409, detail="This rule appears in a saved roster and cannot be removed. Archive its brand instead.")
    session.delete(rule)
    session.commit()
    return {"status": "removed"}


@app.put("/api/v1/eligibility")
def change_eligibility(payload: EligibilityChangeRequest,
                       session: Session = Depends(get_session)) -> dict:
    employee = _find_employee(session, payload.employee_code)
    rule = session.get(BrandProcessRule, payload.rule_id)
    if employee is None or rule is None:
        raise HTTPException(status_code=404, detail="Employee or brand/process rule not found.")
    if payload.active and employee.status != "active":
        raise HTTPException(status_code=422, detail="Only active employees can be marked eligible.")
    if payload.active and rule.required_skill_id not in {skill.id for skill in employee.skills}:
        raise HTTPException(status_code=422, detail="The employee must have the process skill before being marked eligible.")
    record = session.scalar(select(EmployeeEligibility).where(
        EmployeeEligibility.employee_id == employee.id,
        EmployeeEligibility.brand_process_rule_id == rule.id,
    ))
    if record is None:
        record = EmployeeEligibility(employee=employee, brand_process=rule)
        session.add(record)
    record.eligibility_type = payload.eligibility_type
    record.active = payload.active
    session.commit()
    return {"employee_code": employee.employee_code, "rule_id": rule.id, "status": "saved"}


@app.post("/api/v1/public-holidays")
def create_public_holiday(payload: PublicHolidayConfigurationRequest,
                          session: Session = Depends(get_session)) -> dict:
    if session.scalar(select(PublicHoliday.id).where(
        PublicHoliday.name == payload.name, PublicHoliday.holiday_date == payload.holiday_date,
    )):
        raise HTTPException(status_code=409, detail="This named holiday is already recorded for that date.")
    record = PublicHoliday(name=payload.name, holiday_date=payload.holiday_date, is_demo_data=False)
    session.add(record)
    session.commit()
    return {"holiday_id": record.id, "status": "saved"}


@app.patch("/api/v1/availability-exceptions/{exception_id}")
def update_availability_status(exception_id: int, payload: AvailabilityStatusRequest,
                               session: Session = Depends(get_session)) -> dict:
    record = session.get(AvailabilityException, exception_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Availability request not found.")
    record.approval_status = payload.approval_status
    session.commit()
    month_runs = session.scalars(select(RosterRun).order_by(RosterRun.id.desc())).all()
    affected = []
    for run in month_runs:
        first = max(date(run.year, run.month, 1), record.start_date)
        last = min(date(run.year, run.month, calendar.monthrange(run.year, run.month)[1]), record.end_date)
        if first <= last:
            affected.append({"roster_run_id": run.id, "affected_dates": [
                (first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)
            ]})
    return {"availability_exception_id": record.id,
            "employee_code": record.employee.employee_code,
            "exception_type": record.exception_type.value,
            "approval_status": record.approval_status.value,
            "affected_runs": affected}


@app.delete("/api/v1/public-holidays/{holiday_id}")
def delete_public_holiday(holiday_id: int, session: Session = Depends(get_session)) -> dict:
    record = session.get(PublicHoliday, holiday_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Public holiday not found.")
    session.delete(record)
    session.commit()
    return {"status": "removed"}


@app.post("/api/v1/availability-exceptions")
def create_availability_exception(payload: AvailabilityChangeRequest,
                                  session: Session = Depends(get_session)) -> dict:
    """Save manager-entered leave, comp-off, or unavailability and report affected runs."""
    if payload.end_date < payload.start_date:
        raise HTTPException(status_code=422, detail="End date must be on or after start date.")
    employee = session.scalar(select(Employee).where(Employee.employee_code == payload.employee_code))
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found.")
    if employee.status != "active":
        raise HTTPException(status_code=422, detail="Availability changes can only be recorded for active employees.")
    record = AvailabilityException(
        employee_id=employee.id,
        exception_type=payload.exception_type,
        approval_status=payload.approval_status,
        start_date=payload.start_date,
        end_date=payload.end_date,
        note=payload.note,
    )
    session.add(record)
    session.commit()
    session.expire_all()

    affected_runs = []
    runs = session.scalars(select(RosterRun).order_by(RosterRun.id.desc())).all()
    for run in runs:
        month_start = date(run.year, run.month, 1)
        month_end = date(run.year, run.month, calendar.monthrange(run.year, run.month)[1])
        first = max(month_start, payload.start_date)
        last = min(month_end, payload.end_date)
        if first > last:
            continue
        affected_dates = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
        affected_runs.append({
            "roster_run_id": run.id,
            "affected_dates": [day.isoformat() for day in affected_dates],
        })
    return {
        "availability_exception_id": record.id,
        "employee_code": employee.employee_code,
        "exception_type": record.exception_type.value,
        "approval_status": record.approval_status.value,
        "start_date": record.start_date.isoformat(),
        "end_date": record.end_date.isoformat(),
        "affected_runs": affected_runs,
        "next_action": "Review validation, then request a targeted replan for the listed dates.",
    }


@app.patch("/api/v1/brand-process-rules/{rule_id}")
def change_brand_process_rule(rule_id: int, payload: BrandProcessRuleChangeRequest,
                              session: Session = Depends(get_session)) -> dict:
    """Save a manager-selected calendar or daily minimum change and list affected dates."""
    rule = session.scalar(select(BrandProcessRule).options(
        selectinload(BrandProcessRule.coverage_requirements),
        selectinload(BrandProcessRule.brand), selectinload(BrandProcessRule.process),
    ).where(BrandProcessRule.id == rule_id))
    if rule is None:
        raise HTTPException(status_code=404, detail="Brand/process rule not found.")
    changes = payload.model_dump(exclude_unset=True)
    if "operating_weekdays" in changes:
        days = changes["operating_weekdays"]
        if (days is None or not days or any(day not in range(7) for day in days)
                or len(set(days)) != len(days)):
            raise HTTPException(
                status_code=422,
                detail="Select at least one operating weekday; values must be unique integers from 0 through 6.",
            )
    if any(key in changes for key in {"minimum_staff", "maximum_staff"}) and payload.weekday is None:
        raise HTTPException(status_code=422, detail="weekday is required when changing minimum or maximum staffing.")
    if "minimum_staff" in changes and payload.minimum_staff is None:
        raise HTTPException(status_code=422, detail="minimum_staff cannot be null.")

    if "minimum_staff" in changes or "maximum_staff" in changes:
        active_employee_count = session.scalar(
            select(func.count()).select_from(Employee).where(Employee.status == "active")
        ) or 0
        validate_staffing_limit(payload.minimum_staff if "minimum_staff" in changes else None,
                                payload.maximum_staff if "maximum_staff" in changes else None,
                                active_employee_count)

    old_weekdays = list(rule.operating_weekdays)
    old_holiday_behavior = rule.operate_on_public_holidays
    old_share_behavior = rule.can_share_same_day
    old_effort = rule.effort_hours_per_employee
    old_demand_units = rule.demand_units
    requested_weekday = payload.weekday
    old_minimum = None
    old_maximum = None
    coverage = next((item for item in rule.coverage_requirements
                     if item.weekday == requested_weekday), None) if requested_weekday is not None else None
    if requested_weekday is not None and coverage is None:
        raise HTTPException(status_code=404, detail="No coverage rule exists for that weekday.")
    if coverage is not None:
        old_minimum, old_maximum = coverage.minimum_staff, coverage.maximum_staff
        new_minimum = payload.minimum_staff if "minimum_staff" in changes else old_minimum
        new_maximum = payload.maximum_staff if "maximum_staff" in changes else old_maximum
        if new_maximum is not None and new_minimum is not None and new_maximum < new_minimum:
            raise HTTPException(status_code=422, detail="Maximum staff cannot be lower than minimum staff.")

    old_schedule = session.get(BrandProcessSchedule, rule.id)
    old_pod_id = old_schedule.pod_id if old_schedule else None
    new_pod = None
    if "pod_name" in changes:
        new_pod = session.scalar(select(Pod).where(Pod.name == payload.pod_name))
        if new_pod is None or session.scalar(select(PodSchedule.id).where(PodSchedule.pod_id == new_pod.id)) is None:
            raise HTTPException(status_code=422, detail="Choose a pod with a configured work window.")
        if old_schedule is None:
            old_schedule = BrandProcessSchedule(brand_process_rule_id=rule.id, pod_id=new_pod.id)
            session.add(old_schedule)
        old_schedule.pod_id = new_pod.id

    if "operating_weekdays" in changes:
        rule.operating_weekdays = changes["operating_weekdays"]
    if "operate_on_public_holidays" in changes:
        rule.operate_on_public_holidays = changes["operate_on_public_holidays"]
    if "can_share_same_day" in changes:
        rule.can_share_same_day = changes["can_share_same_day"]
    if "effort_hours_per_employee" in changes:
        rule.effort_hours_per_employee = payload.effort_hours_per_employee
    if "demand_units" in changes:
        rule.demand_units = payload.demand_units
    if coverage is not None:
        if "minimum_staff" in changes:
            coverage.minimum_staff = payload.minimum_staff
        if "maximum_staff" in changes:
            coverage.maximum_staff = payload.maximum_staff
    session.commit()
    session.expire_all()

    affected_runs = []
    holidays = {item.holiday_date for item in session.scalars(select(PublicHoliday)).all()}
    for run in session.scalars(select(RosterRun).order_by(RosterRun.id.desc())).all():
        dates = []
        for day_number in range(1, calendar.monthrange(run.year, run.month)[1] + 1):
            day = date(run.year, run.month, day_number)
            calendar_changed = (
                (day.weekday() in old_weekdays) != (day.weekday() in rule.operating_weekdays)
                or (day in holidays and old_holiday_behavior != rule.operate_on_public_holidays
                    and day.weekday() in set(old_weekdays) | set(rule.operating_weekdays))
            )
            sharing_changed = (old_share_behavior != rule.can_share_same_day
                               and day.weekday() in set(old_weekdays) | set(rule.operating_weekdays)
                               and (day not in holidays or old_holiday_behavior or rule.operate_on_public_holidays))
            effort_changed = (old_effort != rule.effort_hours_per_employee
                              and day.weekday() in set(old_weekdays) | set(rule.operating_weekdays)
                              and (day not in holidays or old_holiday_behavior or rule.operate_on_public_holidays))
            staffing_changed = (
                ("minimum_staff" in changes and payload.minimum_staff != old_minimum)
                or ("maximum_staff" in changes and payload.maximum_staff != old_maximum)
            )
            coverage_changed = (
                coverage is not None and staffing_changed and day.weekday() == requested_weekday
                and day.weekday() in set(old_weekdays) | set(rule.operating_weekdays)
                and (day not in holidays or old_holiday_behavior or rule.operate_on_public_holidays)
            )
            pod_changed = old_pod_id != (new_pod.id if new_pod else old_pod_id)
            demand_changed = ("demand_units" in changes and payload.demand_units != old_demand_units
                             and day.weekday() in set(old_weekdays) | set(rule.operating_weekdays)
                             and (day not in holidays or old_holiday_behavior or rule.operate_on_public_holidays))
            if calendar_changed or coverage_changed or sharing_changed or pod_changed or effort_changed or demand_changed:
                dates.append(day.isoformat())
        if dates:
            affected_runs.append({
                "roster_run_id": run.id,
                "affected_dates": dates,
            })
    return {
        "rule_id": rule.id,
        "brand_code": rule.brand.code,
        "process_code": rule.process.code,
        "previous": {"operating_weekdays": old_weekdays,
                     "operate_on_public_holidays": old_holiday_behavior,
                     "can_share_same_day": old_share_behavior,
                     "effort_hours_per_employee": old_effort,
                     "demand_units": old_demand_units,
                     "pod_id": old_pod_id,
                     "minimum_staff": old_minimum, "maximum_staff": old_maximum},
        "current": {"operating_weekdays": rule.operating_weekdays,
                    "operate_on_public_holidays": rule.operate_on_public_holidays,
                    "can_share_same_day": rule.can_share_same_day,
                    "effort_hours_per_employee": rule.effort_hours_per_employee,
                    "demand_units": rule.demand_units,
                    "pod_id": new_pod.id if new_pod else old_pod_id,
                    "weekday": requested_weekday,
                    "minimum_staff": coverage.minimum_staff if coverage else None,
                    "maximum_staff": coverage.maximum_staff if coverage else None},
        "affected_runs": affected_runs,
        "next_action": "Review validation, then request a targeted replan for the listed dates.",
    }


@app.post("/api/v1/rosters/generate")
def generate_roster(payload: GenerateRosterRequest,
                    session: Session = Depends(get_session)) -> dict:
    """Generate and save a draft roster for the requested calendar month."""
    current_year = date.today().year
    if not current_year <= payload.year <= current_year + 5:
        raise HTTPException(
            status_code=422,
            detail=f"Year must be between {current_year} and {current_year + 5}.",
        )
    if not 1 <= payload.month <= 12:
        raise HTTPException(status_code=422, detail="Month must be between 1 and 12.")
    return generate_month(session, payload.year, payload.month)


@app.get("/api/v1/rosters")
def list_rosters(request: Request, year: int | None = None, month: int | None = None,
                 session: Session = Depends(get_session)) -> list[dict]:
    if month is not None and not 1 <= month <= 12:
        raise HTTPException(status_code=422, detail="Month must be between 1 and 12.")
    query = select(RosterRun).options(selectinload(RosterRun.generation_report))
    if year is not None:
        query = query.where(RosterRun.year == year)
    if month is not None:
        query = query.where(RosterRun.month == month)
    if request.state.actor_role != "manager":
        query = query.where(RosterRun.status == RosterStatus.PUBLISHED)
    runs = session.scalars(query.order_by(RosterRun.id.desc())).all()
    result = []
    for run in runs:
        item = {"roster_run_id": run.id, "year": run.year, "month": run.month,
                "status": run.status.value,
                "result_status": run.generation_report.result_status if run.generation_report else "unknown",
                "required_slots": run.generation_report.required_slots if run.generation_report else 0,
                "filled_slots": run.generation_report.filled_slots if run.generation_report else 0,
                "uncovered_slots": run.generation_report.uncovered_slots if run.generation_report else 0}
        if request.state.actor_role == "employee":
            item.update({"result_status": "personal_view", "required_slots": 0,
                         "filled_slots": 0, "uncovered_slots": 0})
        elif request.state.actor_role == "pod_lead":
            scoped_assignments = session.scalars(select(RosterAssignment).where(
                RosterAssignment.roster_run_id == run.id,
            )).all()
            pod_by_rule = {row.brand_process_rule_id: row.pod_id
                           for row in session.scalars(select(BrandProcessSchedule)).all()}
            scoped_assignments = [row for row in scoped_assignments
                                  if pod_by_rule.get(row.brand_process_rule_id)
                                  in request.state.actor_pod_ids]
            filled = sum(row.status == AssignmentStatus.WORKING for row in scoped_assignments)
            uncovered = sum(row.status == AssignmentStatus.UNCOVERED for row in scoped_assignments)
            item.update({"result_status": "partial" if uncovered else "complete",
                         "required_slots": len(scoped_assignments),
                         "filled_slots": filled, "uncovered_slots": uncovered})
        result.append(item)
    return result


@app.get("/api/v1/rosters/{roster_run_id}")
def roster_detail(roster_run_id: int, request: Request,
                  session: Session = Depends(get_session)) -> dict:
    # Keep direct service calls used by small unit fixtures convenient; HTTP requests
    # still receive the authenticated-by-demo role scope from middleware.
    if isinstance(request, Session):
        session, actor_role, actor_employee_id, actor_member_ids, actor_pod_ids = request, "manager", None, set(), set()
    else:
        actor_role = request.state.actor_role
        actor_employee_id = request.state.actor_employee_id
        actor_member_ids = request.state.actor_member_ids
        actor_pod_ids = request.state.actor_pod_ids
    run = session.scalar(select(RosterRun).options(
        selectinload(RosterRun.assignments).selectinload(RosterAssignment.employee),
        selectinload(RosterRun.assignments).selectinload(RosterAssignment.brand_process)
            .selectinload(BrandProcessRule.brand),
        selectinload(RosterRun.assignments).selectinload(RosterAssignment.brand_process)
            .selectinload(BrandProcessRule.process),
        selectinload(RosterRun.employee_days).selectinload(RosterEmployeeDay.employee),
        selectinload(RosterRun.generation_report),
    ).where(RosterRun.id == roster_run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Roster run not found")
    if actor_role != "manager" and run.status != RosterStatus.PUBLISHED:
        raise HTTPException(status_code=404, detail="Published roster not found.")
    report = run.generation_report
    schedules_by_rule = {item.brand_process_rule_id: item.pod_id
                         for item in session.scalars(select(BrandProcessSchedule)).all()}
    pod_by_id = {item.id: item for item in session.scalars(select(Pod)).all()}
    shift_by_pod = {item.pod_id: item for item in session.scalars(select(PodSchedule)).all()}
    priorities_by_brand = {item.brand_id: item.priority for item in session.scalars(select(BrandPriority)).all()}
    role = actor_role
    visible_member_ids = actor_member_ids
    visible_assignments = [item for item in run.assignments if (
        role == "manager"
        or (role == "employee" and item.employee_id == actor_employee_id)
        or (role == "pod_lead" and schedules_by_rule.get(item.brand_process_rule_id)
            in actor_pod_ids)
    )]
    visible_employee_days = [item for item in run.employee_days if (
        role == "manager"
        or (role == "employee" and item.employee_id == actor_employee_id)
        or (role == "pod_lead" and item.employee_id in visible_member_ids)
    )]
    priority_snapshot = (run.allocation_snapshot or {}).get("priority_scores", {})
    process_day_totals: dict[tuple[int, date], int] = defaultdict(int)
    for assignment in visible_assignments:
        if assignment.status == AssignmentStatus.WORKING:
            process_day_totals[(assignment.brand_process_rule_id, assignment.assignment_date)] += (
                assignment.hours_units or int(round(assignment.effort_hours * 2))
            )
    process_day_allocation = []
    if role in {"manager", "pod_lead"}:
        _, month_last_day = calendar.monthrange(run.year, run.month)
        holidays_for_view = {row.holiday_date for row in session.scalars(select(PublicHoliday)).all()}
        rules_for_view = session.scalars(select(BrandProcessRule).options(
            selectinload(BrandProcessRule.brand), selectinload(BrandProcessRule.process),
            selectinload(BrandProcessRule.coverage_requirements),
        ).join(BrandProcessRule.brand).where(Brand.is_active.is_(True))).all()
        for rule in rules_for_view:
            pod_id = schedules_by_rule.get(rule.id)
            if role == "pod_lead" and pod_id not in actor_pod_ids:
                continue
            minimum_by_weekday = {req.weekday: req.minimum_staff for req in rule.coverage_requirements}
            for day_number in range(1, month_last_day + 1):
                day = date(run.year, run.month, day_number)
                if day.weekday() not in rule.operating_weekdays or (
                    day in holidays_for_view and not rule.operate_on_public_holidays
                ):
                    continue
                minimum = minimum_by_weekday.get(day.weekday(), 0)
                demand = rule.demand_units if rule.demand_units is not None else minimum * int(
                    (run.allocation_snapshot or {}).get("capacity_units", 12)
                )
                if not minimum and not demand:
                    continue
                process_day_allocation.append({
                    "brand_code": rule.brand.code, "process_code": rule.process.code,
                    "date": day.isoformat(), "served_units": process_day_totals.get((rule.id, day), 0),
                    "demand_units": demand,
                    "priority_score": priority_snapshot.get(f"{rule.brand.code}:{rule.process.code}:{day.isoformat()}"),
                })
    generation = None
    if report is not None and role == "manager":
        generation = {
            "result_status": report.result_status,
            "solver_status": report.solver_status,
            "required_slots": report.required_slots,
            "filled_slots": report.filled_slots,
            "uncovered_slots": report.uncovered_slots,
            "explanation": report.explanation,
            "hours_layer_status": report.hours_layer_status,
            "solve_time_seconds": report.solve_time_seconds,
        }
    elif report is not None and role == "pod_lead":
        scoped_filled = sum(item.status == AssignmentStatus.WORKING for item in visible_assignments)
        scoped_uncovered = sum(item.status == AssignmentStatus.UNCOVERED for item in visible_assignments)
        generation = {
            "result_status": "partial" if scoped_uncovered else "complete",
            "solver_status": report.solver_status,
            "required_slots": len(visible_assignments),
            "filled_slots": scoped_filled,
            "uncovered_slots": scoped_uncovered,
            "explanation": f"This view covers {len(visible_assignments)} required slot(s) in your pod(s).",
            "hours_layer_status": report.hours_layer_status,
            "solve_time_seconds": report.solve_time_seconds,
        }
    return {
        "roster_run_id": run.id,
        "year": run.year,
        "month": run.month,
        "status": run.status.value,
        "generation": generation,
        "assignments": [{
            "assignment_id": item.id,
            "date": item.assignment_date.isoformat(),
            "employee_code": item.employee.employee_code if item.employee else None,
            "brand_code": item.brand_process.brand.code,
            "process_code": item.brand_process.process.code,
            "effort_hours": item.effort_hours,
            "hours_units": item.hours_units or int(round(item.effort_hours * 2)),
            "priority_score": priority_snapshot.get(
                f"{item.brand_process.brand.code}:{item.brand_process.process.code}:{item.assignment_date.isoformat()}"
            ),
            "pod_name": (pod_by_id[schedules_by_rule[item.brand_process_rule_id]].name
                         if item.brand_process_rule_id in schedules_by_rule
                         and schedules_by_rule[item.brand_process_rule_id] in pod_by_id else None),
            "shift": (None if item.brand_process_rule_id not in schedules_by_rule
                      or schedules_by_rule[item.brand_process_rule_id] not in shift_by_pod else {
                          "name": shift_by_pod[schedules_by_rule[item.brand_process_rule_id]].shift_name,
                          "start": shift_by_pod[schedules_by_rule[item.brand_process_rule_id]].shift_start,
                          "end": shift_by_pod[schedules_by_rule[item.brand_process_rule_id]].shift_end,
                          "break_start": shift_by_pod[schedules_by_rule[item.brand_process_rule_id]].break_start,
                          "break_end": shift_by_pod[schedules_by_rule[item.brand_process_rule_id]].break_end,
                      }),
            "deadline_priority": priorities_by_brand.get(item.brand_process.brand_id, 3),
            "available_candidates": (_assignment_candidates(session, run, item)
                                      if role == "manager" else []),
            "status": item.status.value,
            "explanation": item.explanation,
        } for item in sorted(visible_assignments, key=lambda row: (
            row.assignment_date, row.brand_process.brand.code, row.brand_process.process.code,
        ))],
        "process_day_allocation": process_day_allocation,
        "allocation_snapshot": run.allocation_snapshot if role == "manager" else None,
        "employee_days": [{
            "date": item.schedule_date.isoformat(),
            "employee_code": item.employee.employee_code,
            "status": item.status.value,
            "assigned_effort_hours": item.assigned_effort_hours,
            "explanation": item.explanation,
        } for item in sorted(visible_employee_days, key=lambda row: (
            row.employee.employee_code, row.schedule_date,
        ))],
    }


def _assignment_candidates(session: Session, run: RosterRun,
                           target: RosterAssignment) -> list[dict]:
    rule = target.brand_process
    target_pod_id = session.scalar(select(BrandProcessSchedule.pod_id).where(
        BrandProcessSchedule.brand_process_rule_id == rule.id
    ))
    target_window = session.scalar(select(PodSchedule).where(PodSchedule.pod_id == target_pod_id)) if target_pod_id else None
    membership_rows = session.scalars(select(EmployeePodMembership)).all()
    membership_by_employee: dict[int, set[int]] = defaultdict(set)
    for item in membership_rows:
        membership_by_employee[item.employee_id].add(item.pod_id)
    day_assignments = session.scalars(select(RosterAssignment).options(
        selectinload(RosterAssignment.brand_process),
    ).where(RosterAssignment.roster_run_id == run.id,
            RosterAssignment.assignment_date == target.assignment_date,
            RosterAssignment.status == AssignmentStatus.WORKING,
            RosterAssignment.id != target.id)).all()
    weekly_assignments = session.scalars(select(RosterAssignment).where(
        RosterAssignment.roster_run_id == run.id,
        RosterAssignment.status == AssignmentStatus.WORKING,
        RosterAssignment.assignment_date >= target.assignment_date - timedelta(days=target.assignment_date.weekday()),
        RosterAssignment.assignment_date <= target.assignment_date + timedelta(days=6 - target.assignment_date.weekday()),
        RosterAssignment.id != target.id,
    )).all()
    schedules_by_rule = {item.brand_process_rule_id: item.pod_id
                         for item in session.scalars(select(BrandProcessSchedule)).all()}
    shifts_by_pod = {item.pod_id: item for item in session.scalars(select(PodSchedule)).all()}
    employees = session.scalars(select(Employee).options(
        selectinload(Employee.skills), selectinload(Employee.weekly_offs),
        selectinload(Employee.work_contract), selectinload(Employee.availability_exceptions),
        selectinload(Employee.eligibility),
    ).where(Employee.status == "active").order_by(Employee.employee_code)).all()
    for employee in employees:
        membership_by_employee[employee.id].add(employee.primary_pod_id)
    options = []
    for employee in employees:
        eligibility = next((item for item in employee.eligibility
                            if item.active and item.brand_process_rule_id == rule.id), None)
        if eligibility is None or rule.required_skill_id not in {skill.id for skill in employee.skills}:
            continue
        member_pods = membership_by_employee.get(employee.id, {employee.primary_pod_id})
        if target_pod_id is not None and target_pod_id not in member_pods:
            continue
        if (target.assignment_date.weekday() in {item.weekday for item in employee.weekly_offs}
                or any(item.approval_status == ApprovalStatus.APPROVED
                       and item.start_date <= target.assignment_date <= item.end_date
                       for item in employee.availability_exceptions)):
            continue
        contract = employee.work_contract
        daily_total = sum(row.effort_hours for row in day_assignments if row.employee_id == employee.id)
        weekly_total = sum(row.effort_hours for row in weekly_assignments if row.employee_id == employee.id)
        weekly_days = {row.assignment_date for row in weekly_assignments if row.employee_id == employee.id}
        if contract and target.assignment_date not in weekly_days and len(weekly_days) >= contract.max_days_per_week:
            continue
        other_shift_ids = [schedules_by_rule.get(row.brand_process_rule_id)
                           for row in day_assignments if row.employee_id == employee.id]
        has_shifted_work = target_pod_id is not None or any(item is not None for item in other_shift_ids)
        daily_capacity = min(contract.daily_capacity_hours, 6.0) if contract and has_shifted_work else (
            contract.daily_capacity_hours if contract else 8.0
        )
        if daily_total + target.effort_hours > daily_capacity + 1e-6:
            continue
        if contract and weekly_total + target.effort_hours > contract.max_hours_per_week + 1e-6:
            continue
        target_pod_total = sum(row.effort_hours for row in day_assignments
                               if row.employee_id == employee.id
                               and schedules_by_rule.get(row.brand_process_rule_id) == target_pod_id)
        if target_window and target_pod_total + target.effort_hours > target_window.allocation_capacity_hours + 1e-6:
            continue
        blocked_by_overlap = False
        for row, other_pod_id in zip([row for row in day_assignments if row.employee_id == employee.id],
                                     other_shift_ids):
            if (target_pod_id is not None and other_pod_id is not None
                    and target_pod_id != other_pod_id
                    and _windows_overlap(target_window, shifts_by_pod.get(other_pod_id))):
                blocked_by_overlap = True
                break
        if blocked_by_overlap:
            continue
        if not rule.can_share_same_day and any(
            row.employee_id == employee.id and row.brand_process_rule_id == rule.id for row in day_assignments
        ):
            continue
        priority = session.get(BrandPriority, rule.brand_id)
        reason = (f"Required skill {rule.required_skill.code} matches; {eligibility.eligibility_type.value}-eligible, "
                  "available, and within remaining daily/weekly capacity.")
        if target_window:
            reason += f" Member of {target_window.shift_name} ({target_window.shift_start}–{target_window.shift_end})."
        reason += f" Brand deadline priority {priority.priority if priority else 3}/5."
        options.append({"employee_code": employee.employee_code, "name": employee.full_name,
                        "eligibility_type": eligibility.eligibility_type.value, "reason": reason})
    return options


@app.patch("/api/v1/rosters/{roster_run_id}/assignments/{assignment_id}")
def edit_roster_assignment(roster_run_id: int, assignment_id: int,
                           payload: AssignmentEditRequest,
                           session: Session = Depends(get_session)) -> dict:
    """Apply a manager edit to a draft, then immediately validate the full roster."""
    run = session.get(RosterRun, roster_run_id)
    assignment = session.get(RosterAssignment, assignment_id)
    if run is None or assignment is None or assignment.roster_run_id != roster_run_id:
        raise HTTPException(status_code=404, detail="Roster assignment not found.")
    if run.status.value != "draft":
        raise HTTPException(status_code=409, detail="Only draft rosters can be edited.")
    previous_employee_id = assignment.employee_id
    employee = _find_employee(session, payload.employee_code) if payload.employee_code else None
    if payload.employee_code and (employee is None or employee.status != "active"):
        raise HTTPException(status_code=404, detail="Choose an active employee.")
    assignment.employee = employee
    assignment.status = AssignmentStatus.WORKING if employee else AssignmentStatus.UNCOVERED
    assignment.explanation = (
        "Manager-edited assignment. Review the validation results for eligibility, availability, and capacity."
        if employee else "Manager left this required coverage slot unassigned."
    )
    session.flush()
    for employee_id in {previous_employee_id, employee.id if employee else None} - {None}:
        _refresh_employee_day(session, run, employee_id, assignment.assignment_date)
    if run.generation_report:
        rows = session.scalars(select(RosterAssignment).where(RosterAssignment.roster_run_id == run.id)).all()
        run.generation_report.filled_slots = sum(row.status == AssignmentStatus.WORKING for row in rows)
        run.generation_report.uncovered_slots = sum(row.status == AssignmentStatus.UNCOVERED for row in rows)
        run.generation_report.result_status = "complete" if run.generation_report.uncovered_slots == 0 else "partial"
    session.commit()
    checks = validate_roster(session, run.id)
    return {"roster_run_id": run.id, "assignment_id": assignment.id,
            "employee_code": employee.employee_code if employee else None,
            "validation": checks}


def _refresh_employee_day(session: Session, run: RosterRun, employee_id: int, day: date) -> None:
    day_row = session.scalar(select(RosterEmployeeDay).where(
        RosterEmployeeDay.roster_run_id == run.id,
        RosterEmployeeDay.employee_id == employee_id,
        RosterEmployeeDay.schedule_date == day,
    ))
    if day_row is None:
        return
    assignments = session.scalars(select(RosterAssignment).where(
        RosterAssignment.roster_run_id == run.id,
        RosterAssignment.employee_id == employee_id,
        RosterAssignment.assignment_date == day,
        RosterAssignment.status == AssignmentStatus.WORKING,
    )).all()
    effort = sum(row.effort_hours for row in assignments)
    employee = session.get(Employee, employee_id)
    absence = next((item for item in employee.availability_exceptions
                    if item.approval_status == ApprovalStatus.APPROVED
                    and item.start_date <= day <= item.end_date), None)
    day_row.assigned_effort_hours = effort
    if absence:
        day_row.status = EmployeeRosterDayStatus.LEAVE
        day_row.explanation = f"Unavailable due to approved {absence.exception_type.value.replace('_', ' ')}."
    elif day.weekday() in {item.weekday for item in employee.weekly_offs}:
        day_row.status = EmployeeRosterDayStatus.OFF
        day_row.explanation = "Configured weekly day off."
    elif effort:
        day_row.status = EmployeeRosterDayStatus.WORKING
        day_row.explanation = f"Assigned {effort:g} effort hours across eligible brand/process work."
    else:
        day_row.status = EmployeeRosterDayStatus.UNSCHEDULED
        day_row.explanation = "Available but not assigned to a required coverage slot on this date."


@app.get("/api/v1/rosters/{roster_run_id}/validation")
def roster_validation(roster_run_id: int, request: Request,
                      session: Session = Depends(get_session)) -> dict:
    """Validate persisted assignments independently against current configuration."""
    if request.state.actor_role != "manager":
        run = session.get(RosterRun, roster_run_id)
        if run is None or run.status != RosterStatus.PUBLISHED:
            raise HTTPException(status_code=404, detail="Published roster not found.")
    try:
        result = validate_roster(session, roster_run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    role = request.state.actor_role
    if role == "manager":
        return result
    if role == "employee":
        visible_issues = [issue for issue in result["issues"]
                          if issue.get("employee_code") == request.state.actor_employee_code]
    else:
        visible_rules = set(session.execute(
            select(Brand.code, Process.code)
            .join(BrandProcessRule, BrandProcessRule.brand_id == Brand.id)
            .join(Process, BrandProcessRule.process_id == Process.id)
            .join(BrandProcessSchedule,
                  BrandProcessSchedule.brand_process_rule_id == BrandProcessRule.id)
            .where(BrandProcessSchedule.pod_id.in_(request.state.actor_pod_ids))
        ).all())
        visible_employee_codes = set(session.scalars(select(Employee.employee_code).where(
            Employee.id.in_(request.state.actor_member_ids),
        )).all())
        visible_issues = [issue for issue in result["issues"] if (
            issue.get("employee_code") in visible_employee_codes
            or (issue.get("brand_code"), issue.get("process_code")) in visible_rules
        )]
    counts = Counter(issue["severity"] for issue in visible_issues)
    result.update({
        "status": "needs_attention" if counts["error"] or counts["gap"] else
                  "valid_with_warnings" if counts["warning"] else "valid",
        "issue_count": len(visible_issues),
        "error_count": counts["error"],
        "warning_count": counts["warning"],
        "gap_count": counts["gap"],
        "hard_violation_count": counts["error"],
        "issues": visible_issues,
    })
    return result


@app.post("/api/v1/rosters/{roster_run_id}/publish")
def publish_roster(roster_run_id: int, session: Session = Depends(get_session)) -> dict:
    run = session.get(RosterRun, roster_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Roster run not found.")
    checks = validate_roster(session, run.id)
    if checks["error_count"] or checks.get("gap_count", 0):
        raise HTTPException(status_code=409, detail={
            "message": "This roster has mandatory coverage gaps or hard-rule conflicts and must remain a draft.",
            "validation": checks,
        })
    run.status = RosterStatus.PUBLISHED
    session.commit()
    return {"roster_run_id": run.id, "status": run.status.value, "validation": checks}


@app.post("/api/v1/rosters/{roster_run_id}/replan-preview")
def preview_replan(roster_run_id: int, payload: ReplanRosterRequest,
                   session: Session = Depends(get_session)) -> dict:
    """Solve a proposed targeted roster without saving a new roster run."""
    source = session.get(RosterRun, roster_run_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Roster run not found")
    if not payload.full_month and not payload.affected_dates:
        raise HTTPException(status_code=422, detail="Provide affected dates or request a full-month replan.")
    if len(set(payload.affected_dates)) != len(payload.affected_dates):
        raise HTTPException(status_code=422, detail="Affected dates must not contain duplicates.")
    month_start = date(source.year, source.month, 1)
    month_end = date(source.year, source.month, calendar.monthrange(source.year, source.month)[1])
    if payload.full_month:
        affected = {month_start + timedelta(days=offset)
                    for offset in range((month_end - month_start).days + 1)}
    else:
        affected = set(payload.affected_dates)
        if any(day < month_start or day > month_end for day in affected):
            raise HTTPException(status_code=422, detail="Affected dates must fall inside the source roster month.")

    forced_assignments: list[tuple[date, int, int]] = []
    if payload.replacement is not None:
        selection = payload.replacement
        if selection.date not in affected:
            raise HTTPException(status_code=422, detail="The selected replacement date must be affected by this replan.")
        rule = session.scalar(select(BrandProcessRule).join(BrandProcessRule.brand)
                              .join(BrandProcessRule.process)
                              .where(Brand.code == selection.brand_code,
                                     Process.code == selection.process_code))
        employee = session.scalar(select(Employee).where(Employee.employee_code == selection.employee_code))
        if rule is None or employee is None:
            raise HTTPException(status_code=404, detail="Selected brand/process or employee was not found.")
        forced_assignments.append((selection.date, rule.id, employee.id))

    try:
        result = generate_month(
            session, source.year, source.month,
            preserve_from_run_id=roster_run_id, affected_dates=affected,
            forced_assignments=forced_assignments, commit=False,
        )
        validation = validate_roster(session, result["roster_run_id"])
        load_rows = lambda run_id: session.scalars(select(RosterAssignment).options(
            selectinload(RosterAssignment.employee),
            selectinload(RosterAssignment.brand_process).selectinload(BrandProcessRule.brand),
            selectinload(RosterAssignment.brand_process).selectinload(BrandProcessRule.process),
        ).where(RosterAssignment.roster_run_id == run_id)).all()
        previous_rows = load_rows(roster_run_id)
        proposed_rows = load_rows(result["roster_run_id"])

        def grouped(rows):
            grouped_rows: dict[tuple[date, str, str], list[RosterAssignment]] = defaultdict(list)
            for row in rows:
                if row.assignment_date in affected:
                    grouped_rows[(row.assignment_date, row.brand_process.brand.code,
                                  row.brand_process.process.code)].append(row)
            for key in grouped_rows:
                grouped_rows[key].sort(key=lambda row: (
                    row.employee.employee_code if row.employee else "~",
                    row.status.value,
                ))
            return grouped_rows

        previous_by_slot = grouped(previous_rows)
        proposed_by_slot = grouped(proposed_rows)
        recommendations = []
        for key in sorted(set(previous_by_slot) | set(proposed_by_slot)):
            old_slots = previous_by_slot.get(key, [])
            new_slots = proposed_by_slot.get(key, [])
            for index in range(max(len(old_slots), len(new_slots))):
                old = old_slots[index] if index < len(old_slots) else None
                new = new_slots[index] if index < len(new_slots) else None
                old_employee = old.employee.employee_code if old and old.employee else None
                new_employee = new.employee.employee_code if new and new.employee else None
                if new is None:
                    action = "demand_removed"
                    explanation = "This assignment is no longer required by the updated calendar or staffing rule."
                    proposed_status = "not_required"
                elif new.status == AssignmentStatus.UNCOVERED:
                    action = "unfilled"
                    explanation = new.explanation
                    proposed_status = "uncovered"
                elif old is None:
                    action = "added"
                    explanation = new.explanation
                    proposed_status = "working"
                elif old_employee == new_employee:
                    action = "retained"
                    explanation = new.explanation
                    proposed_status = "working"
                else:
                    action = "reassigned"
                    explanation = new.explanation
                    proposed_status = "working"
                recommendations.append({
                    "date": key[0].isoformat(),
                    "brand_code": key[1],
                    "process_code": key[2],
                    "current_employee_code": old_employee,
                    "current_status": old.status.value if old else None,
                    "proposed_employee_code": new_employee,
                    "proposed_status": proposed_status,
                    "action": action,
                    "effort_hours": new.effort_hours if new else (old.effort_hours if old else 0),
                    "explanation": explanation,
                })

        required, filled = _coverage_totals(session, source.year, source.month, affected, proposed_rows)
        uncovered = required - filled
        response = {
            "source_roster_run_id": roster_run_id,
            "year": source.year,
            "month": source.month,
            "affected_dates": sorted(day.isoformat() for day in affected),
            "result_status": "complete" if uncovered == 0 else "partial",
            "required_slots": required,
            "filled_slots": filled,
            "uncovered_slots": uncovered,
            "validation_status": validation["status"],
            "unaffected_assignments_preserved": True,
            "recommendations": recommendations,
        }
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        session.rollback()
    return response


def _coverage_totals(session: Session, year: int, month: int, affected: set[date],
                     rows: list[RosterAssignment]) -> tuple[int, int]:
    """Count minimum staffing slots, independent of how many hour chunks are saved."""
    rules = session.scalars(select(BrandProcessRule).options(
        selectinload(BrandProcessRule.coverage_requirements),
    ).join(BrandProcessRule.brand).where(Brand.is_active.is_(True))).all()
    holidays = {row.holiday_date for row in session.scalars(select(PublicHoliday)).all()}
    assigned = Counter((row.brand_process_rule_id, row.assignment_date)
                       for row in rows if row.status == AssignmentStatus.WORKING)
    required_total = filled_total = 0
    for rule in rules:
        minimum_by_weekday = {item.weekday: item.minimum_staff for item in rule.coverage_requirements}
        for day in affected:
            if day.year != year or day.month != month or day.weekday() not in rule.operating_weekdays:
                continue
            if day in holidays and not rule.operate_on_public_holidays:
                continue
            required = minimum_by_weekday.get(day.weekday(), 0)
            if required:
                required_total += required
                filled_total += min(required, assigned[(rule.id, day)])
    return required_total, filled_total


@app.post("/api/v1/rosters/{roster_run_id}/replan")
def replan_roster(roster_run_id: int, payload: ReplanRosterRequest,
                  session: Session = Depends(get_session)) -> dict:
    """Create a draft replan, preserving unaffected assignments unless coverage needs a move."""
    source = session.get(RosterRun, roster_run_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Roster run not found")
    if not payload.full_month and not payload.affected_dates:
        raise HTTPException(status_code=422, detail="Provide affected dates or request a full-month replan.")
    if len(set(payload.affected_dates)) != len(payload.affected_dates):
        raise HTTPException(status_code=422, detail="Affected dates must not contain duplicates.")
    month_start = date(source.year, source.month, 1)
    month_end = date(source.year, source.month, calendar.monthrange(source.year, source.month)[1])
    if payload.full_month:
        affected = {month_start + timedelta(days=offset)
                    for offset in range((month_end - month_start).days + 1)}
    else:
        affected = set(payload.affected_dates)
        if any(day < month_start or day > month_end for day in affected):
            raise HTTPException(status_code=422, detail="Affected dates must fall inside the source roster month.")

    forced_assignments: list[tuple[date, int, int]] = []
    if payload.replacement is not None:
        selection = payload.replacement
        if selection.date not in affected:
            raise HTTPException(status_code=422, detail="The selected replacement date must be affected by this replan.")
        rule = session.scalar(select(BrandProcessRule).join(BrandProcessRule.brand)
                              .join(BrandProcessRule.process)
                              .where(Brand.code == selection.brand_code,
                                     Process.code == selection.process_code))
        employee = session.scalar(select(Employee).where(Employee.employee_code == selection.employee_code))
        if rule is None or employee is None:
            raise HTTPException(status_code=404, detail="Selected brand/process or employee was not found.")
        forced_assignments.append((selection.date, rule.id, employee.id))

    before_validation = validate_roster(session, roster_run_id)
    try:
        result = generate_month(session, source.year, source.month,
                                preserve_from_run_id=roster_run_id, affected_dates=affected,
                                forced_assignments=forced_assignments)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    after_validation = validate_roster(session, result["roster_run_id"])
    old_rows = session.scalars(select(RosterAssignment).where(
        RosterAssignment.roster_run_id == roster_run_id,
    )).all()
    new_rows = session.scalars(select(RosterAssignment).where(
        RosterAssignment.roster_run_id == result["roster_run_id"],
    )).all()
    affected_required, affected_filled = _coverage_totals(
        session, source.year, source.month, affected, new_rows,
    )
    old_unaffected = Counter((row.assignment_date, row.brand_process_rule_id,
                              row.employee_id, row.status.value)
                             for row in old_rows if row.assignment_date not in affected)
    new_unaffected = Counter((row.assignment_date, row.brand_process_rule_id,
                              row.employee_id, row.status.value)
                             for row in new_rows if row.assignment_date not in affected)
    result["before_validation"] = before_validation
    result["after_validation"] = after_validation
    result["affected_required_slots"] = affected_required
    result["affected_filled_slots"] = affected_filled
    result["affected_uncovered_slots"] = affected_required - affected_filled
    result["unaffected_assignments_preserved"] = old_unaffected == new_unaffected
    result["affected_dates"] = sorted(day.isoformat() for day in affected)
    return result


@app.get("/api/v1/rosters/{roster_run_id}/replacement-candidates")
def replacement_candidates(roster_run_id: int, assignment_date: date,
                           brand_code: str, process_code: str,
                           excluding_employee_code: str | None = None,
                           session: Session = Depends(get_session)) -> dict:
    """List every qualified, available, capacity-safe replacement for one coverage slot."""
    run = session.get(RosterRun, roster_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Roster run not found.")
    if assignment_date.year != run.year or assignment_date.month != run.month:
        raise HTTPException(status_code=422, detail="Assignment date must fall inside the roster month.")
    rule = session.scalar(select(BrandProcessRule).join(BrandProcessRule.brand)
                          .join(BrandProcessRule.process)
                          .where(Brand.code == brand_code, Process.code == process_code))
    if rule is None:
        raise HTTPException(status_code=404, detail="Brand/process rule not found.")
    if assignment_date.weekday() not in rule.operating_weekdays:
        return {"candidates": [], "candidate_count": 0,
                "explanation": "This brand/process is closed on the selected date."}
    holidays = {item.holiday_date for item in session.scalars(select(PublicHoliday)).all()}
    if assignment_date in holidays and not rule.operate_on_public_holidays:
        return {"candidates": [], "candidate_count": 0,
                "explanation": "This brand/process is closed on the selected public holiday."}
    required_staff = next((item.minimum_staff for item in rule.coverage_requirements
                           if item.weekday == assignment_date.weekday()), 0)
    if required_staff > 1 and not rule.can_share_same_day:
        return {
            "candidates": [],
            "candidate_count": 0,
            "explanation": (
                f"This date requires {required_staff} distinct employees, but the brand/process rule does not "
                "allow multiple employees to cover it on the same day. Change the sharing rule or staffing "
                "requirement before selecting a replacement."
            ),
        }

    target_pod_id = session.scalar(select(BrandProcessSchedule.pod_id).where(
        BrandProcessSchedule.brand_process_rule_id == rule.id,
    ))
    target_window = session.scalar(select(PodSchedule).where(PodSchedule.pod_id == target_pod_id)) \
        if target_pod_id is not None else None
    pod_windows = {item.pod_id: item for item in session.scalars(select(PodSchedule)).all()}
    pod_by_rule = {item.brand_process_rule_id: item.pod_id
                   for item in session.scalars(select(BrandProcessSchedule)).all()}
    memberships: dict[int, set[int]] = defaultdict(set)
    for link in session.scalars(select(EmployeePodMembership)).all():
        memberships[link.employee_id].add(link.pod_id)
    for employee in session.scalars(select(Employee)).all():
        memberships[employee.id].add(employee.primary_pod_id)

    excluded_employee = None
    if excluding_employee_code:
        excluded_employee = session.scalar(select(Employee).where(
            Employee.employee_code == excluding_employee_code,
        ))
    employees = session.scalars(select(Employee).options(
        selectinload(Employee.skills), selectinload(Employee.weekly_offs),
        selectinload(Employee.work_contract), selectinload(Employee.availability_exceptions),
        selectinload(Employee.eligibility),
    ).where(Employee.status == "active")).all()
    month_assignments = session.scalars(select(RosterAssignment).where(
        RosterAssignment.roster_run_id == run.id,
        RosterAssignment.status == "working",
        RosterAssignment.employee_id.is_not(None),
    )).all()
    daily_hours: Counter[int] = Counter()
    weekly_hours: Counter[int] = Counter()
    weekly_days: dict[int, set[date]] = defaultdict(set)
    same_process_employees = set()
    day_rows = []
    iso = assignment_date.isocalendar()
    for row in month_assignments:
        if row.employee_id == (excluded_employee.id if excluded_employee else None):
            continue
        if row.assignment_date == assignment_date:
            day_rows.append(row)
            daily_hours[row.employee_id] += row.effort_hours
            if row.brand_process_rule_id == rule.id:
                same_process_employees.add(row.employee_id)
        row_iso = row.assignment_date.isocalendar()
        if row_iso[:2] == iso[:2]:
            weekly_hours[row.employee_id] += row.effort_hours
            weekly_days[row.employee_id].add(row.assignment_date)

    candidates = []
    excluded_reasons = Counter()
    for employee in employees:
        if employee.employee_code == excluding_employee_code:
            continue
        eligibility = next((item for item in employee.eligibility
                            if item.active and item.brand_process_rule_id == rule.id), None)
        if eligibility is None:
            excluded_reasons["not eligible"] += 1
            continue
        if rule.required_skill_id not in {skill.id for skill in employee.skills}:
            excluded_reasons["missing required skill"] += 1
            continue
        if target_pod_id is not None and target_pod_id not in memberships.get(employee.id, set()):
            excluded_reasons["not a member of the required pod"] += 1
            continue
        if assignment_date.weekday() in {off.weekday for off in employee.weekly_offs}:
            excluded_reasons["weekly off"] += 1
            continue
        if any(item.approval_status == ApprovalStatus.APPROVED
               and item.exception_type in {AvailabilityType.LEAVE, AvailabilityType.COMP_OFF,
                                           AvailabilityType.UNAVAILABLE}
               and item.start_date <= assignment_date <= item.end_date
               for item in employee.availability_exceptions):
            excluded_reasons["approved absence"] += 1
            continue
        if employee.id in same_process_employees:
            excluded_reasons["already assigned to this process"] += 1
            continue
        contract = employee.work_contract
        if contract is None:
            excluded_reasons["missing capacity configuration"] += 1
            continue
        effort = rule.effort_hours_per_employee
        other_pod_ids = [pod_by_rule.get(row.brand_process_rule_id)
                         for row in day_rows if row.employee_id == employee.id]
        has_window_work = target_pod_id is not None or any(pod_id is not None for pod_id in other_pod_ids)
        daily_capacity = min(contract.daily_capacity_hours, 6.0) if has_window_work else contract.daily_capacity_hours
        if daily_hours[employee.id] + effort > daily_capacity:
            excluded_reasons["daily capacity"] += 1
            continue
        target_pod_hours = sum(
            row.effort_hours for row in day_rows
            if row.employee_id == employee.id and pod_by_rule.get(row.brand_process_rule_id) == target_pod_id
        )
        target_pod_capacity = target_window.allocation_capacity_hours if target_window else 6.0
        if target_pod_id is not None and target_pod_hours + effort > target_pod_capacity:
            excluded_reasons["pod window capacity"] += 1
            continue
        if any(
            target_pod_id is not None and other_pod_id is not None and other_pod_id != target_pod_id
            and _windows_overlap(target_window, pod_windows.get(other_pod_id))
            for other_pod_id in other_pod_ids
        ):
            excluded_reasons["overlapping pod window"] += 1
            continue
        if weekly_hours[employee.id] + effort > contract.max_hours_per_week:
            excluded_reasons["weekly hours"] += 1
            continue
        projected_days = len(weekly_days[employee.id] | {assignment_date})
        if projected_days > contract.max_days_per_week:
            excluded_reasons["weekly workdays"] += 1
            continue
        candidates.append({
            "employee_code": employee.employee_code,
            "name": employee.full_name,
            "eligibility_type": eligibility.eligibility_type.value,
            "projected_daily_hours": daily_hours[employee.id] + effort,
            "projected_weekly_hours": weekly_hours[employee.id] + effort,
            "reason": (f"Qualified and eligible as {eligibility.eligibility_type.value}; available on this date "
                       "and within configured daily and weekly capacity."),
        })
    candidates.sort(key=lambda item: (
        item["projected_weekly_hours"],
        item["eligibility_type"] != EligibilityType.PRIMARY.value,
        item["projected_daily_hours"], item["employee_code"],
    ))
    return {
        "roster_run_id": run.id,
        "date": assignment_date.isoformat(),
        "brand_code": brand_code,
        "process_code": process_code,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "explanation": ("Ranked by projected weekly workload, then primary eligibility and daily workload."
                        if candidates else "No qualified, available employee has enough capacity for this slot."),
        "common_blockers": dict(excluded_reasons),
    }
