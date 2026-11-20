from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import (
    ApprovalStatus, AssignmentStatus, AvailabilityType, Brand, BrandProcessRule,
    BrandProcessSchedule, Employee, EmployeeEligibility, EmployeePodMembership,
    PodSchedule, PreferenceType, PublicHoliday,
    RosterAssignment, RosterRun,
)
from .scheduling import _windows_overlap


def validate_roster(session: Session, roster_run_id: int) -> dict:
    """Independently validate saved roster rows against current configured inputs."""
    run = session.scalar(select(RosterRun).where(RosterRun.id == roster_run_id))
    if run is None:
        raise LookupError("Roster run not found")

    employees = session.scalars(select(Employee).options(
        selectinload(Employee.skills), selectinload(Employee.weekly_offs),
        selectinload(Employee.work_contract), selectinload(Employee.availability_exceptions),
        selectinload(Employee.preferences), selectinload(Employee.eligibility),
    )).all()
    employee_by_id = {employee.id: employee for employee in employees}
    rules = session.scalars(select(BrandProcessRule).options(
        selectinload(BrandProcessRule.brand), selectinload(BrandProcessRule.process),
        selectinload(BrandProcessRule.required_skill),
        selectinload(BrandProcessRule.coverage_requirements),
    ).join(BrandProcessRule.brand).where(Brand.is_active.is_(True))).all()
    rule_by_id = {rule.id: rule for rule in rules}
    schedule_by_rule = {item.brand_process_rule_id: item.pod_id
                        for item in session.scalars(select(BrandProcessSchedule)).all()}
    pod_schedules = {item.pod_id: item for item in session.scalars(select(PodSchedule)).all()}
    memberships = session.scalars(select(EmployeePodMembership)).all()
    pod_ids_by_employee: dict[int, set[int]] = defaultdict(set)
    if memberships:
        for item in memberships:
            pod_ids_by_employee[item.employee_id].add(item.pod_id)
    else:
        pod_ids_by_employee = {employee.id: {employee.primary_pod_id} for employee in employees}
    holidays = {item.holiday_date for item in session.scalars(select(PublicHoliday)).all()}
    assignments = session.scalars(select(RosterAssignment).where(
        RosterAssignment.roster_run_id == run.id,
    )).all()

    issues: list[dict] = []

    def add_issue(severity: str, code: str, message: str, *, day: date | None = None,
                  employee_id: int | None = None, rule_id: int | None = None,
                  required: int | None = None, assigned: int | None = None) -> None:
        issue = {"severity": severity, "code": code, "message": message}
        if day is not None:
            issue["date"] = day.isoformat()
        if employee_id is not None and employee_id in employee_by_id:
            issue["employee_code"] = employee_by_id[employee_id].employee_code
        if rule_id is not None and rule_id in rule_by_id:
            rule = rule_by_id[rule_id]
            issue["brand_code"] = rule.brand.code
            issue["process_code"] = rule.process.code
        if required is not None:
            issue["required"] = required
        if assigned is not None:
            issue["assigned"] = assigned
        issues.append(issue)

    assigned_by_rule_day: dict[tuple[int, date], list[RosterAssignment]] = defaultdict(list)
    assigned_by_employee_day: dict[tuple[int, date], list[RosterAssignment]] = defaultdict(list)
    assigned_by_employee_week: dict[tuple[int, int, int], list[RosterAssignment]] = defaultdict(list)
    process_employee_day_counts: Counter[tuple[int, date, int]] = Counter()
    uncovered_reasons: dict[tuple[int, date], list[str]] = defaultdict(list)

    for assignment in assignments:
        if assignment.status == AssignmentStatus.UNCOVERED:
            uncovered_reasons[(assignment.brand_process_rule_id, assignment.assignment_date)].append(
                assignment.explanation
            )
        if assignment.status != AssignmentStatus.WORKING:
            continue
        if assignment.employee_id is None:
            add_issue("error", "working_without_employee",
                      "A working assignment has no employee record.", day=assignment.assignment_date,
                      rule_id=assignment.brand_process_rule_id)
            continue
        employee = employee_by_id.get(assignment.employee_id)
        rule = rule_by_id.get(assignment.brand_process_rule_id)
        if employee is None or rule is None:
            add_issue("error", "missing_reference",
                      "An assignment references an employee or brand/process that is no longer configured.",
                      day=assignment.assignment_date, employee_id=assignment.employee_id,
                      rule_id=assignment.brand_process_rule_id)
            continue

        day = assignment.assignment_date
        assigned_by_employee_day[(employee.id, day)].append(assignment)
        iso = day.isocalendar()
        assigned_by_employee_week[(employee.id, iso.year, iso.week)].append(assignment)
        process_employee_day_counts[(rule.id, day, employee.id)] += 1

        valid_for_coverage = True
        if employee.status != "active":
            valid_for_coverage = False
            add_issue("error", "inactive_employee", "An inactive employee is assigned.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        eligibility = next((item for item in employee.eligibility
                           if item.active and item.brand_process_rule_id == rule.id), None)
        if eligibility is None:
            valid_for_coverage = False
            add_issue("error", "not_eligible", "Employee has no active eligibility for this brand/process.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        required_pod_id = schedule_by_rule.get(rule.id)
        if required_pod_id is not None and required_pod_id not in pod_ids_by_employee.get(employee.id, set()):
            valid_for_coverage = False
            add_issue("error", "outside_pod_window",
                      "Employee is not a member of the pod assigned to this brand/process work window.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        if rule.required_skill_id not in {skill.id for skill in employee.skills}:
            valid_for_coverage = False
            add_issue("error", "missing_skill", "Employee lacks the required skill.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        if day.weekday() in {item.weekday for item in employee.weekly_offs}:
            valid_for_coverage = False
            add_issue("error", "weekly_off_conflict", "Employee is assigned on a configured weekly off.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        absence = next((item for item in employee.availability_exceptions
                        if item.approval_status == ApprovalStatus.APPROVED
                        and item.exception_type in {AvailabilityType.LEAVE, AvailabilityType.COMP_OFF,
                                                    AvailabilityType.UNAVAILABLE}
                        and item.start_date <= day <= item.end_date), None)
        if absence is not None:
            valid_for_coverage = False
            add_issue("error", "approved_absence_conflict",
                      f"Employee is assigned during approved {absence.exception_type.value.replace('_', ' ')}.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        if day.weekday() not in rule.operating_weekdays:
            valid_for_coverage = False
            add_issue("error", "closed_day_assignment", "Brand/process is closed on this weekday.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        if day in holidays and not rule.operate_on_public_holidays:
            valid_for_coverage = False
            add_issue("error", "holiday_assignment", "Brand/process is closed on this public holiday.",
                      day=day, employee_id=employee.id, rule_id=rule.id)
        if valid_for_coverage:
            assigned_by_rule_day[(rule.id, day)].append(assignment)

    # Compare all required dates with the saved assignments, including missing rows.
    _, last_day = calendar.monthrange(run.year, run.month)
    for rule in rules:
        minimum_by_weekday = {item.weekday: item.minimum_staff for item in rule.coverage_requirements}
        maximum_by_weekday = {item.weekday: item.maximum_staff for item in rule.coverage_requirements}
        for day_number in range(1, last_day + 1):
            day = date(run.year, run.month, day_number)
            if day.weekday() not in rule.operating_weekdays:
                continue
            if day in holidays and not rule.operate_on_public_holidays:
                continue
            required = minimum_by_weekday.get(day.weekday(), 0)
            actual = len(assigned_by_rule_day[(rule.id, day)])
            if actual < required:
                reasons = list(dict.fromkeys(
                    reason for reason in uncovered_reasons[(rule.id, day)] if reason
                ))
                reason_text = f" Reason: {' '.join(reasons)}" if reasons else ""
                add_issue("error", "insufficient_coverage",
                          f"{rule.brand.code} {rule.process.code} has {actual} assigned of {required} required."
                          f"{reason_text}",
                          day=day, rule_id=rule.id, required=required, assigned=actual)
            maximum = maximum_by_weekday.get(day.weekday())
            if maximum is not None and actual > maximum:
                add_issue("error", "maximum_staff_exceeded",
                          f"{rule.brand.code} {rule.process.code} has {actual} assigned, above its maximum of {maximum}.",
                          day=day, rule_id=rule.id, required=maximum, assigned=actual)

        if not rule.can_share_same_day:
            for (rule_id, day), rows in assigned_by_rule_day.items():
                if rule_id == rule.id and len(rows) > 1:
                    add_issue("error", "same_process_sharing_conflict",
                              f"{rule.brand.code} {rule.process.code} has {len(rows)} employees assigned on one day, "
                              "but its rule does not allow same-day sharing.",
                              day=day, rule_id=rule.id, required=1, assigned=len(rows))

    for (rule_id, day, employee_id), count in process_employee_day_counts.items():
        if count > 1:
            add_issue("error", "duplicate_process_assignment",
                      "The same employee is counted more than once for one process on one date.",
                      day=day, employee_id=employee_id, rule_id=rule_id, assigned=count)

    for (employee_id, day), rows in assigned_by_employee_day.items():
        employee = employee_by_id[employee_id]
        hours = sum(row.effort_hours for row in rows)
        shifted_rows = [row for row in rows if schedule_by_rule.get(row.brand_process_rule_id) is not None]
        capacity = min(employee.work_contract.daily_capacity_hours, 6.0) if employee.work_contract and shifted_rows else (
            employee.work_contract.daily_capacity_hours if employee.work_contract else 8.0
        )
        if hours > capacity:
            add_issue("error", "daily_capacity_exceeded",
                      f"Employee has {hours:g} assigned hours, above daily capacity of {capacity:g}.",
                      day=day, employee_id=employee_id)

        hours_by_pod: dict[int, float] = defaultdict(float)
        rows_by_pod: dict[int, list[RosterAssignment]] = defaultdict(list)
        for row in rows:
            pod_id = schedule_by_rule.get(row.brand_process_rule_id)
            if pod_id is not None:
                hours_by_pod[pod_id] += row.effort_hours
                rows_by_pod[pod_id].append(row)
        for pod_id, pod_hours in hours_by_pod.items():
            pod_capacity = pod_schedules.get(pod_id).allocation_capacity_hours if pod_id in pod_schedules else 6.0
            if pod_hours > pod_capacity:
                add_issue("error", "pod_window_capacity_exceeded",
                          f"Employee has {pod_hours:g} assigned hours in a pod window, above its {pod_capacity:g}-hour allocation cap.",
                          day=day, employee_id=employee_id)
        pod_ids = list(rows_by_pod)
        for index, first_pod_id in enumerate(pod_ids):
            for second_pod_id in pod_ids[index + 1:]:
                if not _windows_overlap(pod_schedules.get(first_pod_id), pod_schedules.get(second_pod_id)):
                    continue
                add_issue("error", "overlapping_pod_windows",
                          "Employee is assigned to two pod work windows whose active periods overlap.",
                          day=day, employee_id=employee_id)

    for (employee_id, year, week), rows in assigned_by_employee_week.items():
        employee = employee_by_id[employee_id]
        contract = employee.work_contract
        if contract is None:
            continue
        hours = sum(row.effort_hours for row in rows)
        days = len({row.assignment_date for row in rows})
        if hours > contract.max_hours_per_week:
            add_issue("error", "weekly_hours_exceeded",
                      f"Employee has {hours:g} assigned hours in ISO week {year}-W{week:02d}, above the {contract.max_hours_per_week:g}-hour limit.",
                      employee_id=employee_id)
        if days > contract.max_days_per_week:
            add_issue("error", "weekly_days_exceeded",
                      f"Employee is assigned on {days} days in ISO week {year}-W{week:02d}, above the {contract.max_days_per_week}-day limit.",
                      employee_id=employee_id)

    for employee in employees:
        for exception in employee.availability_exceptions:
            if (exception.approval_status == ApprovalStatus.PENDING
                    and exception.exception_type in {AvailabilityType.LEAVE, AvailabilityType.COMP_OFF,
                                                     AvailabilityType.UNAVAILABLE}
                    and exception.end_date >= date(run.year, run.month, 1)
                    and exception.start_date <= date(run.year, run.month, last_day)):
                add_issue("warning", "pending_availability",
                          f"Employee has pending {exception.exception_type.value.replace('_', ' ')} "
                          f"from {exception.start_date.isoformat()} through {exception.end_date.isoformat()}; "
                          "it does not block this roster.", employee_id=employee.id)
        for preference in employee.preferences:
            if preference.preference_type != PreferenceType.UNPREFERRED:
                continue
            for (employee_id, day), rows in assigned_by_employee_day.items():
                if employee_id == employee.id and day.weekday() == preference.weekday:
                    add_issue("warning", "unpreferred_day_assigned",
                              "Employee is scheduled on a weekday marked unpreferred; coverage rules take priority.",
                              day=day, employee_id=employee.id)

    counts = Counter(issue["severity"] for issue in issues)
    if counts["error"]:
        status = "needs_attention"
    elif counts["warning"]:
        status = "valid_with_warnings"
    else:
        status = "valid"
    return {
        "roster_run_id": run.id,
        "year": run.year,
        "month": run.month,
        "status": status,
        "issue_count": len(issues),
        "error_count": counts["error"],
        "warning_count": counts["warning"],
        "issues": issues,
    }
