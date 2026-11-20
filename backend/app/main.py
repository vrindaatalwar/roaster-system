from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, timedelta
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .database import Base, engine, get_session
from .models import (
    ApprovalStatus, AvailabilityException, AvailabilityType, Brand,
    BrandProcessRule, Employee, EmployeeEligibility,
    EligibilityType,
    Pod, Process, PublicHoliday, RosterAssignment, RosterEmployeeDay, RosterRun,
)
from .scheduling import generate_month
from .validation import validate_roster


app = FastAPI(title="DataStraw Smart Roster", version="0.1.0")
allowed_origins = os.getenv(
    "FRONTEND_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


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


class BrandProcessRuleChangeRequest(BaseModel):
    operating_weekdays: list[StrictInt] | None = None
    operate_on_public_holidays: StrictBool | None = None
    can_share_same_day: StrictBool | None = None
    weekday: StrictInt | None = Field(default=None, ge=0, le=6)
    minimum_staff: StrictInt | None = Field(default=None, ge=0)
    maximum_staff: StrictInt | None = Field(default=None, ge=0)

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
        }
        if not self.model_fields_set.intersection(change_fields):
            raise ValueError("Provide at least one brand/process setting to change.")
        for field_name in ("operating_weekdays", "operate_on_public_holidays", "can_share_same_day"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null.")
        if "weekday" in self.model_fields_set and self.weekday is None:
            raise ValueError("weekday cannot be null when provided.")
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
    Base.metadata.create_all(engine)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "stage": "3 - validation and change handling"}


@app.get("/api/v1/seed-summary")
def seed_summary(session: Session = Depends(get_session)) -> dict:
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
                "can_share_same_day": rule.can_share_same_day,
                "continuous_presence": rule.continuous_presence,
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
        brand_details.append({"code": brand.code, "name": brand.name,
                              "active": brand.is_active, "demo_data": brand.is_demo_data,
                              "processes": process_details})

    return {
        "demo_data_notice": "All names, calendars, effort, eligibility, and availability are fictional assessment assumptions.",
        "counts": {"employees": len(employees), "pods": len(pods), "brands": len(brands),
                   "processes": len(process_catalog)},
        "employees": [{
            "code": employee.employee_code,
            "name": employee.full_name,
            "role": employee.role_title,
            "status": employee.status,
            "pod": employee.primary_pod.name,
            "is_pod_lead": employee.is_pod_lead,
            "skills": sorted(skill.code for skill in employee.skills),
            "weekly_off_weekdays": sorted(off.weekday for off in employee.weekly_offs),
            "preferences": [{"weekday": preference.weekday,
                             "type": preference.preference_type.value,
                             "note": preference.note}
                            for preference in employee.preferences],
            "availability_exceptions": [{
                "type": item.exception_type.value,
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
        "public_holidays": [{"name": holiday.name, "date": holiday.holiday_date.isoformat()}
                            for holiday in session.scalars(select(PublicHoliday).order_by(PublicHoliday.holiday_date))],
        "pods": [{"name": pod.name, "specialization": pod.specialization,
                  "lead_employee_code": pod.lead.employee_code if pod.lead else None,
                  "members": sorted(member.employee_code for member in pod.members)} for pod in pods],
        "process_catalog": [{"code": process.code, "name": process.name,
                             "required_skill": process.required_skill.code,
                             "default_effort_hours": process.default_effort_hours,
                             "enabled": process.enabled} for process in process_catalog],
        "brands": brand_details,
    }


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
            "validation": validate_roster(session, run.id),
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

    if "operating_weekdays" in changes:
        rule.operating_weekdays = changes["operating_weekdays"]
    if "operate_on_public_holidays" in changes:
        rule.operate_on_public_holidays = changes["operate_on_public_holidays"]
    if "can_share_same_day" in changes:
        rule.can_share_same_day = changes["can_share_same_day"]
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
            staffing_changed = (
                ("minimum_staff" in changes and payload.minimum_staff != old_minimum)
                or ("maximum_staff" in changes and payload.maximum_staff != old_maximum)
            )
            coverage_changed = (
                coverage is not None and staffing_changed and day.weekday() == requested_weekday
                and day.weekday() in set(old_weekdays) | set(rule.operating_weekdays)
                and (day not in holidays or old_holiday_behavior or rule.operate_on_public_holidays)
            )
            if calendar_changed or coverage_changed or sharing_changed:
                dates.append(day.isoformat())
        if dates:
            affected_runs.append({
                "roster_run_id": run.id,
                "affected_dates": dates,
                "validation": validate_roster(session, run.id),
            })
    return {
        "rule_id": rule.id,
        "brand_code": rule.brand.code,
        "process_code": rule.process.code,
        "previous": {"operating_weekdays": old_weekdays,
                     "operate_on_public_holidays": old_holiday_behavior,
                     "can_share_same_day": old_share_behavior,
                     "minimum_staff": old_minimum, "maximum_staff": old_maximum},
        "current": {"operating_weekdays": rule.operating_weekdays,
                    "operate_on_public_holidays": rule.operate_on_public_holidays,
                    "can_share_same_day": rule.can_share_same_day,
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


@app.get("/api/v1/rosters/{roster_run_id}")
def roster_detail(roster_run_id: int, session: Session = Depends(get_session)) -> dict:
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
    report = run.generation_report
    return {
        "roster_run_id": run.id,
        "year": run.year,
        "month": run.month,
        "status": run.status.value,
        "generation": None if report is None else {
            "result_status": report.result_status,
            "solver_status": report.solver_status,
            "required_slots": report.required_slots,
            "filled_slots": report.filled_slots,
            "uncovered_slots": report.uncovered_slots,
            "explanation": report.explanation,
        },
        "assignments": [{
            "date": item.assignment_date.isoformat(),
            "employee_code": item.employee.employee_code if item.employee else None,
            "brand_code": item.brand_process.brand.code,
            "process_code": item.brand_process.process.code,
            "effort_hours": item.effort_hours,
            "status": item.status.value,
            "explanation": item.explanation,
        } for item in run.assignments],
        "employee_days": [{
            "date": item.schedule_date.isoformat(),
            "employee_code": item.employee.employee_code,
            "status": item.status.value,
            "assigned_effort_hours": item.assigned_effort_hours,
            "explanation": item.explanation,
        } for item in run.employee_days],
    }


@app.get("/api/v1/rosters/{roster_run_id}/validation")
def roster_validation(roster_run_id: int, session: Session = Depends(get_session)) -> dict:
    """Validate persisted assignments independently against current configuration."""
    try:
        return validate_roster(session, roster_run_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


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
    old_unaffected = Counter((row.assignment_date, row.brand_process_rule_id,
                              row.employee_id, row.status.value)
                             for row in old_rows if row.assignment_date not in affected)
    new_unaffected = Counter((row.assignment_date, row.brand_process_rule_id,
                              row.employee_id, row.status.value)
                             for row in new_rows if row.assignment_date not in affected)
    result["before_validation"] = before_validation
    result["after_validation"] = after_validation
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
    iso = assignment_date.isocalendar()
    for row in month_assignments:
        if row.employee_id == (excluded_employee.id if excluded_employee else None):
            continue
        if row.assignment_date == assignment_date:
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
        if daily_hours[employee.id] + effort > contract.daily_capacity_hours:
            excluded_reasons["daily capacity"] += 1
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
