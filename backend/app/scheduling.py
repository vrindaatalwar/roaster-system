from __future__ import annotations

import calendar
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from ortools.sat.python import cp_model
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .models import (
    AllocationSetting, ApprovalStatus, AssignmentStatus, AvailabilityType, Brand, BrandPriority, BrandProcessRule,
    BrandProcessSchedule,
    CoverageRequirement, Employee, EmployeeEligibility, EligibilityType,
    EmployeePodMembership, EmployeeRosterDayStatus, PodSchedule, PreferenceType, PublicHoliday, RosterAssignment,
    RosterEmployeeDay, RosterGenerationReport, RosterRun,
)

HOUR_SCALE = 4  # quarter-hour integer units for CP-SAT
ROSTER_SOLVER_TIME_LIMIT_SECONDS = 12.0
ROSTER_SOLVER_WORKERS = 2
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DemandSlot:
    rule: BrandProcessRule
    day: date
    slot_number: int
    pod_id: int | None = None
    priority: int = 3


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute


def _active_intervals(schedule: PodSchedule) -> list[tuple[int, int]]:
    start, end = _minutes(schedule.shift_start), _minutes(schedule.shift_end)
    break_start, break_end = _minutes(schedule.break_start), _minutes(schedule.break_end)
    return [(start, break_start), (break_end, end)]


def _windows_overlap(first: PodSchedule | None, second: PodSchedule | None) -> bool:
    if first is None or second is None:
        return False
    return any(a_start < b_end and b_start < a_end
               for a_start, a_end in _active_intervals(first)
               for b_start, b_end in _active_intervals(second))


def generate_month(session: Session, year: int, month: int, *,
                   preserve_from_run_id: int | None = None,
                   affected_dates: set[date] | None = None,
                   forced_assignments: list[tuple[date, int, int]] | None = None,
                   commit: bool = True) -> dict:
    generation_started = time.monotonic()
    current_year = date.today().year
    if not isinstance(year, int) or isinstance(year, bool) or not current_year <= year <= current_year + 5:
        raise ValueError(f"Year must be an integer between {current_year} and {current_year + 5}.")
    if not isinstance(month, int) or isinstance(month, bool) or not 1 <= month <= 12:
        raise ValueError("Month must be an integer between 1 and 12.")
    _, last_day = calendar.monthrange(year, month)
    month_dates = [date(year, month, day) for day in range(1, last_day + 1)]
    preserved_assignments: dict[tuple[int, date], list[RosterAssignment]] = defaultdict(list)
    if preserve_from_run_id is not None:
        if affected_dates is None:
            raise ValueError("Affected dates must be supplied when preserving an existing roster.")
        previous = session.get(RosterRun, preserve_from_run_id)
        if previous is None:
            raise LookupError("Source roster run not found.")
        if (previous.year, previous.month) != (year, month):
            raise ValueError("The source roster must use the same year and month.")
        old_assignments = session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == preserve_from_run_id,
        )).all()
        previous_assignments: dict[tuple[int, date], list[RosterAssignment]] = defaultdict(list)
        for assignment in old_assignments:
            previous_assignments[(assignment.brand_process_rule_id, assignment.assignment_date)].append(assignment)
            if assignment.assignment_date not in affected_dates:
                preserved_assignments[(assignment.brand_process_rule_id, assignment.assignment_date)].append(assignment)
    else:
        previous_assignments = defaultdict(list)

    employees = session.scalars(select(Employee).options(
        selectinload(Employee.skills), selectinload(Employee.work_contract),
        selectinload(Employee.weekly_offs), selectinload(Employee.availability_exceptions),
        selectinload(Employee.preferences), selectinload(Employee.eligibility)
            .selectinload(EmployeeEligibility.brand_process),
    ).where(Employee.status == "active").order_by(Employee.employee_code)).all()
    rules = session.scalars(select(BrandProcessRule).options(
        selectinload(BrandProcessRule.brand), selectinload(BrandProcessRule.process),
        selectinload(BrandProcessRule.required_skill),
        selectinload(BrandProcessRule.coverage_requirements),
    ).join(BrandProcessRule.brand).where(Brand.is_active.is_(True))).all()
    holidays = {item.holiday_date for item in session.scalars(select(PublicHoliday)).all()}

    skills_by_employee = {e.id: {s.id for s in e.skills} for e in employees}
    weekly_offs = {e.id: {item.weekday for item in e.weekly_offs} for e in employees}
    contracts = {e.id: e.work_contract for e in employees}
    eligibility: dict[tuple[int, int], EligibilityType] = {}
    for employee in employees:
        for item in employee.eligibility:
            if item.active:
                eligibility[(employee.id, item.brand_process_rule_id)] = item.eligibility_type

    membership_rows = session.scalars(select(EmployeePodMembership)).all()
    pod_ids_by_employee: dict[int, set[int]] = defaultdict(set)
    if membership_rows:
        for membership in membership_rows:
            pod_ids_by_employee[membership.employee_id].add(membership.pod_id)
    # The primary pod remains a membership even when explicit cross-pod rows
    # exist for only some employees in a database upgraded from the old schema.
    for employee in employees:
        pod_ids_by_employee[employee.id].add(employee.primary_pod_id)
    schedule_by_pod = {schedule.pod_id: schedule for schedule in session.scalars(select(PodSchedule)).all()}
    pod_by_rule = {schedule.brand_process_rule_id: schedule.pod_id
                   for schedule in session.scalars(select(BrandProcessSchedule)).all()}
    priorities = {item.brand_id: item.priority for item in session.scalars(select(BrandPriority)).all()}

    blocked_dates: dict[int, set[date]] = defaultdict(set)
    for employee in employees:
        for item in employee.availability_exceptions:
            if item.approval_status != ApprovalStatus.APPROVED:
                continue
            if item.exception_type not in {AvailabilityType.LEAVE, AvailabilityType.COMP_OFF,
                                           AvailabilityType.UNAVAILABLE}:
                continue
            for day in month_dates:
                if item.start_date <= day <= item.end_date:
                    blocked_dates[employee.id].add(day)

    slots: list[DemandSlot] = []
    for rule in rules:
        coverage = {item.weekday: item.minimum_staff for item in rule.coverage_requirements}
        for day in month_dates:
            if day.weekday() not in rule.operating_weekdays:
                continue
            if day in holidays and not rule.operate_on_public_holidays:
                continue
            for slot_no in range(coverage.get(day.weekday(), 0)):
                slots.append(DemandSlot(rule=rule, day=day, slot_number=slot_no,
                                        pod_id=pod_by_rule.get(rule.id),
                                        priority=priorities.get(rule.brand_id, 3)))

    model = cp_model.CpModel()
    assignment_vars: dict[tuple[int, int], cp_model.IntVar] = {}
    uncovered_vars: dict[int, cp_model.IntVar] = {}
    candidates_by_slot: dict[int, list[Employee]] = {}
    slot_indexes_by_rule_day: dict[tuple[int, date], list[int]] = defaultdict(list)
    disruption_penalty_terms: list[cp_model.IntVar] = []
    employee_day_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    employee_day_assignments: dict[tuple[int, date], list[tuple[cp_model.IntVar, float]]] = defaultdict(list)
    employee_week_assignments: dict[tuple[int, int, int], list[tuple[cp_model.IntVar, float]]] = defaultdict(list)
    used_by_process_day: dict[tuple[int, date], list[cp_model.IntVar]] = defaultdict(list)
    used_by_process_day_employee: dict[tuple[int, date, int], list[cp_model.IntVar]] = defaultdict(list)
    employee_shift_assignments: dict[tuple[int, date, int], list[tuple[cp_model.IntVar, float]]] = defaultdict(list)

    for slot_index, slot in enumerate(slots):
        slot_indexes_by_rule_day[(slot.rule.id, slot.day)].append(slot_index)
        candidates = []
        for employee in employees:
            if (employee.id, slot.rule.id) not in eligibility:
                continue
            if slot.rule.required_skill_id not in skills_by_employee[employee.id]:
                continue
            if slot.pod_id is not None and slot.pod_id not in pod_ids_by_employee.get(employee.id, set()):
                continue
            if slot.day.weekday() in weekly_offs[employee.id] or slot.day in blocked_dates[employee.id]:
                continue
            candidates.append(employee)
            variable = model.new_bool_var(f"assign_s{slot_index}_e{employee.id}")
            assignment_vars[(slot_index, employee.id)] = variable
            hours = slot.rule.effort_hours_per_employee
            employee_day_assignments[(employee.id, slot.day)].append((variable, hours))
            if slot.pod_id is not None:
                employee_shift_assignments[(employee.id, slot.day, slot.pod_id)].append((variable, hours))
            iso = slot.day.isocalendar()
            employee_week_assignments[(employee.id, iso.year, iso.week)].append((variable, hours))
            used_by_process_day[(slot.rule.id, slot.day)].append(variable)
            used_by_process_day_employee[(slot.rule.id, slot.day, employee.id)].append(variable)

            day_key = (employee.id, slot.day)
            if day_key not in employee_day_vars:
                employee_day_vars[day_key] = model.new_bool_var(f"work_e{employee.id}_{slot.day.isoformat()}")
            model.add(variable <= employee_day_vars[day_key])

        candidates_by_slot[slot_index] = candidates
        uncovered = model.new_bool_var(f"uncovered_s{slot_index}")
        uncovered_vars[slot_index] = uncovered
        model.add(sum(assignment_vars[(slot_index, e.id)] for e in candidates) + uncovered == 1)

    if preserve_from_run_id is not None:
        current_keys = {key for key in slot_indexes_by_rule_day if key[1] not in affected_dates}
        previous_keys = set(preserved_assignments)
        if current_keys != previous_keys:
            changed_key = next(iter(current_keys.symmetric_difference(previous_keys)))
            raise ValueError(
                f"Roster demand changed on {changed_key[1].isoformat()}; include that date in the affected dates."
            )
        employees_by_id = {employee.id: employee for employee in employees}
        for key, slot_indexes in slot_indexes_by_rule_day.items():
            if key[1] in affected_dates:
                continue
            old_rows = sorted(
                preserved_assignments[key],
                key=lambda row: (row.employee_id is None,
                                 employees_by_id[row.employee_id].employee_code if row.employee_id in employees_by_id else ""),
            )
            if len(old_rows) != len(slot_indexes):
                raise ValueError(
                    f"Coverage changed on {key[1].isoformat()}; include that date in the affected dates."
                )
            for slot_index, old_row in zip(slot_indexes, old_rows):
                if old_row.employee_id is None:
                    # Keep earlier uncovered demand stable outside the selected dates.
                    model.add(uncovered_vars[slot_index] == 1)
                elif (slot_index, old_row.employee_id) in assignment_vars:
                    changed = model.new_bool_var(f"changed_s{slot_index}")
                    model.add(changed + assignment_vars[(slot_index, old_row.employee_id)] == 1)
                    disruption_penalty_terms.append(changed)
                else:
                    employee_code = employees_by_id.get(old_row.employee_id)
                    identifier = employee_code.employee_code if employee_code else str(old_row.employee_id)
                    raise ValueError(
                        f"Cannot preserve {identifier}'s assignment on {key[1].isoformat()}; "
                        "include that date in the affected dates."
                    )

        # On affected dates, keep existing assignments wherever they still satisfy
        # the updated inputs. This makes the proposal focus its changes on the
        # employee absence or brand rule that triggered the replan.
        employees_by_id = {employee.id: employee for employee in employees}
        for key, slot_indexes in slot_indexes_by_rule_day.items():
            if key[1] not in affected_dates:
                continue
            old_rows = sorted(
                previous_assignments.get(key, []),
                key=lambda row: (row.employee_id is None,
                                 employees_by_id[row.employee_id].employee_code if row.employee_id in employees_by_id else ""),
            )
            for slot_index, old_row in zip(slot_indexes, old_rows):
                if old_row.employee_id is None or (slot_index, old_row.employee_id) not in assignment_vars:
                    continue
                changed = model.new_bool_var(f"changed_affected_s{slot_index}")
                model.add(changed + assignment_vars[(slot_index, old_row.employee_id)] == 1)
                disruption_penalty_terms.append(changed)

    for day, rule_id, employee_id in forced_assignments or []:
        indexes = slot_indexes_by_rule_day.get((rule_id, day), [])
        if affected_dates is None or day not in affected_dates:
            raise ValueError("A confirmed replacement must fall on an affected date.")
        if not indexes:
            raise ValueError("The selected brand/process has no required coverage slot on that date.")
        matching = next((index for index in indexes if (index, employee_id) in assignment_vars), None)
        if matching is None:
            raise ValueError("The selected replacement is not qualified and available for that date.")
        model.add(assignment_vars[(matching, employee_id)] == 1)

    # Never count the same employee twice toward one process's daily headcount.
    for variables in used_by_process_day_employee.values():
        model.add(sum(variables) <= 1)
    # If same-day sharing is disabled, one employee is the process's daily resource.
    for (rule_id, _), variables in used_by_process_day.items():
        rule = next(item for item in rules if item.id == rule_id)
        if not rule.can_share_same_day:
            model.add(sum(variables) <= 1)

    shifted_day_keys = {(employee_id, day) for employee_id, day, _ in employee_shift_assignments}
    for (employee_id, day), entries in employee_day_assignments.items():
        contract = contracts[employee_id]
        has_shifted_work = (employee_id, day) in shifted_day_keys
        daily_capacity = min(contract.daily_capacity_hours, 6.0) if has_shifted_work else contract.daily_capacity_hours
        model.add(sum(int(round(hours * HOUR_SCALE)) * variable for variable, hours in entries)
                  <= int(round(daily_capacity * HOUR_SCALE)))
        model.add(sum(variable for variable, _ in entries) >= employee_day_vars[(employee_id, day)])

    shift_use_vars: dict[tuple[int, date, int], cp_model.IntVar] = {}
    for (employee_id, day, pod_id), entries in employee_shift_assignments.items():
        window = schedule_by_pod.get(pod_id)
        capacity = min(window.allocation_capacity_hours, contracts[employee_id].daily_capacity_hours) if window else 6.0
        model.add(sum(int(round(hours * HOUR_SCALE)) * variable for variable, hours in entries)
                  <= int(round(capacity * HOUR_SCALE)))
        shift_use = model.new_bool_var(f"pod_window_e{employee_id}_{day.isoformat()}_p{pod_id}")
        shift_use_vars[(employee_id, day, pod_id)] = shift_use
        for assignment_var, _ in entries:
            model.add(assignment_var <= shift_use)
        model.add(shift_use <= sum(variable for variable, _ in entries))
    # Compare only pod windows for the same employee and date. Scanning every
    # pair of shift variables grows quadratically with the whole month's model.
    shift_keys_by_employee_day: dict[tuple[int, date], list[tuple[int, int, int]]] = defaultdict(list)
    for key in shift_use_vars:
        employee_id, day, _ = key
        shift_keys_by_employee_day[(employee_id, day)].append(key)
    for keys in shift_keys_by_employee_day.values():
        for index, first_key in enumerate(keys):
            first_schedule = schedule_by_pod.get(first_key[2])
            for second_key in keys[index + 1:]:
                if first_key[2] != second_key[2] and _windows_overlap(
                    first_schedule, schedule_by_pod.get(second_key[2])
                ):
                    model.add(shift_use_vars[first_key] + shift_use_vars[second_key] <= 1)

    max_sundays = model.new_int_var(0, len(month_dates), "maximum_sunday_assignments")
    max_holidays = model.new_int_var(0, len(month_dates), "maximum_holiday_assignments")
    for employee in employees:
        sundays = [employee_day_vars[(employee.id, day)] for day in month_dates
                   if day.weekday() == 6 and (employee.id, day) in employee_day_vars]
        holidays_worked = [employee_day_vars[(employee.id, day)] for day in month_dates
                           if day in holidays and (employee.id, day) in employee_day_vars]
        model.add(sum(sundays) <= max_sundays)
        model.add(sum(holidays_worked) <= max_holidays)

    for employee in employees:
        contract = contracts[employee.id]
        for (employee_id, week_year, week_number), entries in employee_week_assignments.items():
            if employee_id != employee.id:
                continue
            model.add(sum(int(round(hours * HOUR_SCALE)) * variable for variable, hours in entries)
                      <= int(round(contract.max_hours_per_week * HOUR_SCALE)))
        weeks = {(y, w) for emp_id, y, w in employee_week_assignments if emp_id == employee.id}
        for week_year, week_number in weeks:
            week_days = [day for (emp_id, day) in employee_day_vars
                         if emp_id == employee.id and day.isocalendar()[:2] == (week_year, week_number)]
            model.add(sum(employee_day_vars[(employee.id, day)] for day in week_days)
                      <= contract.max_days_per_week)

    max_load = model.new_int_var(0, len(slots) * 8 * HOUR_SCALE, "maximum_employee_load")
    for employee in employees:
        employee_vars = [(variable, hours) for (slot_index, employee_id), variable in assignment_vars.items()
                         if employee_id == employee.id for hours in [slots[slot_index].rule.effort_hours_per_employee]]
        effort = sum(int(round(hours * HOUR_SCALE)) * variable for variable, hours in employee_vars)
        model.add(effort <= max_load)

    # Fill demand first, then prefer primary owners, preserve the existing plan, and balance workload.
    uncovered_count = sum(uncovered_vars.values())
    deadline_priority_penalty = sum(
        uncovered_vars[index] * slots[index].priority for index in uncovered_vars
    )
    backup_penalty = sum(
        variable for (slot_index, employee_id), variable in assignment_vars.items()
        if eligibility[(employee_id, slots[slot_index].rule.id)] == EligibilityType.BACKUP
    )
    preference_score_terms = []
    for (slot_index, employee_id), variable in assignment_vars.items():
        slot = slots[slot_index]
        employee = next(e for e in employees if e.id == employee_id)
        day_preferences = [p for p in employee.preferences if p.weekday == slot.day.weekday()]
        if any(p.preference_type == PreferenceType.UNPREFERRED for p in day_preferences):
            preference_score_terms.append(variable * 1)
        elif any(p.preference_type == PreferenceType.PREFERRED for p in day_preferences):
            preference_score_terms.append(variable * -1)
    model.minimize(uncovered_count * 1_000_000_000_000
                   + deadline_priority_penalty * 1_000_000
                   + backup_penalty * 1_000
                   + sum(disruption_penalty_terms) * 100
                   + max_load * 10 + max_sundays * 2 + max_holidays * 2
                   + sum(preference_score_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = ROSTER_SOLVER_TIME_LIMIT_SECONDS
    solver.parameters.num_search_workers = ROSTER_SOLVER_WORKERS
    solver.parameters.random_seed = 0
    model_built_at = time.monotonic()
    status = solver.solve(model)
    solve_finished_at = time.monotonic()
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError(f"CP-SAT did not produce a usable roster (status={solver.status_name(status)}).")

    allocation_settings = session.get(AllocationSetting, 1)
    run = RosterRun(year=year, month=month, allocation_snapshot={
        "capacity_units": allocation_settings.capacity_units if allocation_settings else 12,
        "min_chunk_units": allocation_settings.min_chunk_units if allocation_settings else 1,
        "max_brands_per_day": allocation_settings.max_brands_per_day if allocation_settings else 2,
        "hours_layer_enabled": bool(allocation_settings and allocation_settings.hours_layer_enabled),
        "solver_status": "layer_1",
    })
    session.add(run)
    session.flush()
    filled = 0
    employee_daily_hours: dict[tuple[int, date], float] = defaultdict(float)
    for slot_index, slot in enumerate(slots):
        selected = next((employee for employee in candidates_by_slot[slot_index]
                         if solver.boolean_value(assignment_vars[(slot_index, employee.id)])), None)
        if selected is not None:
            filled += 1
            employee_daily_hours[(selected.id, slot.day)] += slot.rule.effort_hours_per_employee
            kind = eligibility[(selected.id, slot.rule.id)].value
            reason = (f"Required skill {slot.rule.required_skill.code} matches; employee is {kind}-eligible, "
                      "available on this date, and assigned within daily and weekly capacity. "
                      f"Brand deadline priority is {slot.priority}/5.")
            window = schedule_by_pod.get(slot.pod_id) if slot.pod_id is not None else None
            if window is not None:
                reason += (f" Assigned to {window.shift_name} ({window.shift_start}–{window.shift_end}) "
                           f"in pod {slot.pod_id}; lunch {window.break_start}–{window.break_end} is excluded.")
            session.add(RosterAssignment(
                roster_run=run, employee=selected, brand_process=slot.rule,
                assignment_date=slot.day, effort_hours=slot.rule.effort_hours_per_employee,
                hours_units=int(round(slot.rule.effort_hours_per_employee * 2)),
                status=AssignmentStatus.WORKING, explanation=reason,
            ))
        else:
            reason = _uncovered_reason(slot, employees, eligibility, skills_by_employee,
                                       weekly_offs, blocked_dates, pod_ids_by_employee, schedule_by_pod)
            session.add(RosterAssignment(
                roster_run=run, employee=None, brand_process=slot.rule,
                assignment_date=slot.day, effort_hours=slot.rule.effort_hours_per_employee,
                hours_units=0,
                status=AssignmentStatus.UNCOVERED, explanation=reason,
            ))

    for employee in employees:
        for day in month_dates:
            effort_hours = employee_daily_hours[(employee.id, day)]
            approved_absence = next((item for item in employee.availability_exceptions
                                     if item.approval_status == ApprovalStatus.APPROVED
                                     and item.start_date <= day <= item.end_date), None)
            if approved_absence is not None:
                day_status = EmployeeRosterDayStatus.LEAVE
                day_reason = f"Unavailable due to approved {approved_absence.exception_type.value.replace('_', ' ')}."
            elif day.weekday() in weekly_offs[employee.id]:
                day_status = EmployeeRosterDayStatus.OFF
                day_reason = "Configured weekly day off."
            elif effort_hours > 0:
                day_status = EmployeeRosterDayStatus.WORKING
                day_reason = f"Assigned {effort_hours:g} effort hours across eligible brand/process work."
            else:
                day_status = EmployeeRosterDayStatus.UNSCHEDULED
                day_reason = "Available but not assigned to a required coverage slot on this date."
            session.add(RosterEmployeeDay(
                roster_run=run, employee=employee, schedule_date=day,
                status=day_status, assigned_effort_hours=effort_hours, explanation=day_reason,
            ))

    uncovered_count_value = len(slots) - filled
    result_status = "complete" if uncovered_count_value == 0 else "partial"
    pending_warnings = [
        (f"{employee.employee_code} has pending {item.exception_type.value.replace('_', ' ')} "
         f"from {item.start_date.isoformat()} through {item.end_date.isoformat()}; it did not block this roster.")
        for employee in employees
        for item in employee.availability_exceptions
        if item.approval_status == ApprovalStatus.PENDING
        and item.exception_type in {AvailabilityType.LEAVE, AvailabilityType.COMP_OFF,
                                    AvailabilityType.UNAVAILABLE}
        and item.end_date >= month_dates[0] and item.start_date <= month_dates[-1]
    ]
    explanation = ("All required coverage slots were assigned." if not uncovered_count_value
                   else f"{uncovered_count_value} required coverage slot(s) remain uncovered. See each uncovered assignment for blockers.")
    if pending_warnings:
        explanation += f" {len(pending_warnings)} pending availability request(s) overlap this month and did not block generation."
    session.add(RosterGenerationReport(
        roster_run=run,
        result_status=result_status,
        solver_status=solver.status_name(status),
        required_slots=len(slots),
        filled_slots=filled,
        uncovered_slots=uncovered_count_value,
        explanation=explanation,
    ))
    session.flush()
    from .hours_allocation import apply_hours_layer
    hours_result = {"status": "disabled", "solve_time_seconds": 0.0, "fallback": False}
    layer_setting = session.get(AllocationSetting, 1)
    if layer_setting is not None and layer_setting.hours_layer_enabled:
        savepoint = session.begin_nested()
        proposed = None
        try:
            proposed = apply_hours_layer(
                session, run.id, preserve_from_run_id=preserve_from_run_id,
                affected_dates=affected_dates,
            )
            if proposed is None or proposed.get("fallback"):
                raise RuntimeError("Hours model did not return a usable solution")
            from .validation import validate_roster
            proposed_check = validate_roster(session, run.id)
            proposed_hard = [issue for issue in proposed_check["issues"]
                             if issue["severity"] == "error" and issue["code"] != "insufficient_coverage"]
            if proposed_hard:
                raise RuntimeError("Hours model failed independent validation")
            savepoint.commit()
            hours_result = proposed
        except Exception as exc:
            savepoint.rollback()
            hours_result = {
                "status": "fallback_layer_1", "solver_status": proposed.get("status") if proposed else "unknown",
                "failure_stage": proposed.get("failure_stage") if proposed else None,
                "solve_time_seconds": proposed.get("solve_time_seconds", 0.0) if proposed else 0.0,
                "fallback": True, "fallback_reason": str(exc),
            }
            report = session.get(RosterGenerationReport, run.id)
            if report:
                report.hours_layer_status = "fallback_layer_1"
                report.solve_time_seconds = hours_result["solve_time_seconds"]
                report.explanation += " Hours layer failed; retained the valid Layer 1 roster."
            run.allocation_snapshot = {
                "capacity_units": layer_setting.capacity_units,
                "min_chunk_units": layer_setting.min_chunk_units,
                "max_brands_per_day": layer_setting.max_brands_per_day,
                "hours_layer_enabled": True, "fallback": True,
                "solver_status": hours_result["solver_status"],
                "solve_time_seconds": hours_result["solve_time_seconds"],
                "fallback_reason": str(exc),
            }
    if commit:
        session.flush()
        # Do not persist a solver result that violates a hard rule. Uncovered
        # demand is an allowed partial draft; it is reported separately.
        from .validation import validate_roster
        validation = validate_roster(session, run.id)
        hard_issues = [issue for issue in validation["issues"]
                       if issue["severity"] == "error" and issue["code"] != "insufficient_coverage"]
        if hard_issues:
            session.rollback()
            raise RuntimeError(
                "Generated roster failed independent hard-rule validation: "
                + "; ".join(issue["message"] for issue in hard_issues[:5])
            )
        session.commit()
    else:
        session.flush()
    final_report = session.get(RosterGenerationReport, run.id)
    logger.info(
        "Roster generation completed year=%s month=%s required=%s filled=%s solver_status=%s "
        "model_build_seconds=%.3f solve_seconds=%.3f total_seconds=%.3f",
        year, month, len(slots), filled, solver.status_name(status),
        model_built_at - generation_started, solve_finished_at - model_built_at,
        time.monotonic() - generation_started,
    )
    return {
        "roster_run_id": run.id,
        "year": year,
        "month": month,
        "result_status": final_report.result_status if final_report else result_status,
        "solver_status": final_report.solver_status if final_report else solver.status_name(status),
        "required_slots": final_report.required_slots if final_report else len(slots),
        "filled_slots": final_report.filled_slots if final_report else filled,
        "uncovered_slots": final_report.uncovered_slots if final_report else uncovered_count_value,
        "explanation": final_report.explanation if final_report else explanation,
        "warnings": pending_warnings,
        "replanned_from_run_id": preserve_from_run_id,
        "affected_dates": sorted(day.isoformat() for day in affected_dates) if affected_dates is not None else None,
        "hours_layer": hours_result,
    }


def _uncovered_reason(slot, employees, eligibility, skills_by_employee,
                      weekly_offs, blocked_dates, pod_ids_by_employee=None, schedule_by_pod=None) -> str:
    minimum_staff = next((item.minimum_staff for item in slot.rule.coverage_requirements
                          if item.weekday == slot.day.weekday()), 0)
    if minimum_staff > 1 and not slot.rule.can_share_same_day:
        return (f"This brand/process requires {minimum_staff} employees, but its configuration does not "
                "allow multiple employees to cover it on the same day.")
    eligible = [e for e in employees if (e.id, slot.rule.id) in eligibility]
    qualified = [e for e in eligible if slot.rule.required_skill_id in skills_by_employee[e.id]]
    if slot.pod_id is not None and pod_ids_by_employee is not None:
        qualified = [e for e in qualified if slot.pod_id in pod_ids_by_employee.get(e.id, set())]
    available = [e for e in qualified if slot.day.weekday() not in weekly_offs[e.id]
                 and slot.day not in blocked_dates[e.id]]
    if not eligible:
        return "No active employee is explicitly eligible for this brand/process."
    if not qualified:
        if slot.pod_id is not None:
            return "No eligible employee with the required specialization is a member of this pod's work window."
        return "Eligible employees do not hold the required skill."
    if not available:
        return "All qualified eligible employees are on weekly off or approved leave/comp-off/unavailability."
    pod_shift = schedule_by_pod.get(slot.pod_id) if slot.pod_id is not None and schedule_by_pod else None
    window_reason = f" The {pod_shift.shift_name} window may also overlap another pod window." if pod_shift else ""
    return ("Qualified eligible employees exist, but the combined daily/weekly capacity constraints or "
            f"competing higher-priority coverage demand prevented this slot from being assigned. "
            f"Brand deadline priority is {slot.priority}/5.{window_reason}")
