"""Optional half-hour allocation model layered over minimum staffing coverage."""
from __future__ import annotations

import calendar
import logging
import time
from collections import defaultdict
from datetime import date

from ortools.sat.python import cp_model
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    AllocationSetting, ApprovalStatus, AssignmentStatus, AvailabilityType, Brand,
    BrandPriority, BrandProcessRule, BrandProcessSchedule, CoverageRequirement,
    Employee, EmployeeEligibility, EmployeePodMembership, EmployeeRosterDayStatus,
    PodSchedule, PriorityOverride, PublicHoliday, RosterAssignment,
    RosterEmployeeDay, RosterGenerationReport, RosterRun,
)
from .scheduling import _windows_overlap


# Objective tiers are solved and fixed in order: floor shortfall, weighted surplus,
# backup usage, then context switching and load balance.
FLOOR_TIER_NAME = "guaranteed_floor"
SURPLUS_TIER_NAME = "priority_weighted_surplus"
PRIORITY_SCORE_SCALE = 100
SURPLUS_DIMINISHING_FACTOR = 0.5
logger = logging.getLogger(__name__)


def default_settings(session: Session) -> AllocationSetting:
    setting = session.get(AllocationSetting, 1)
    if setting is None:
        setting = AllocationSetting(id=1)
        session.add(setting)
        session.flush()
    return setting


def _eligible(employee: Employee, rule: BrandProcessRule, day: date,
              memberships: dict[int, set[int]], pod_by_rule: dict[int, int],
              blocked: dict[int, set[date]]) -> bool:
    eligibility = next((item for item in employee.eligibility
                        if item.active and item.brand_process_rule_id == rule.id), None)
    return bool(
        employee.status == "active" and eligibility
        and rule.required_skill_id in {skill.id for skill in employee.skills}
        and (rule.id not in pod_by_rule or pod_by_rule[rule.id] in memberships.get(employee.id, set()))
        and day.weekday() not in {off.weekday for off in employee.weekly_offs}
        and day not in blocked.get(employee.id, set())
    )


def _priority_components(brand_id: int, day: date, priorities: dict[int, BrandPriority],
                         overrides: list[PriorityOverride], backlog_factor: float) -> dict:
    configured = priorities.get(brand_id)
    tier = configured.client_tier if configured else 3
    deadline = configured.deadline_date if configured else None
    if deadline is None:
        urgency = 1.0
        days_left = None
    else:
        days_left = (deadline - day).days
        urgency = min(5.0, 30.0 / max(days_left, 1))
    matching = [row for row in overrides if row.brand_id == brand_id and row.start_date <= day <= row.end_date]
    override = max(matching, key=lambda row: row.id).multiplier if matching else 1.0
    backlog_factor = max(0.05, min(1.0, backlog_factor))
    return {
        "client_tier": tier, "urgency": round(urgency, 4), "days_to_deadline": days_left,
        "backlog_factor": backlog_factor, "manager_override": override,
        "score": round(tier * urgency * backlog_factor * override, 4),
    }


def apply_hours_layer(session: Session, run_id: int, *, preserve_from_run_id: int | None = None,
                      affected_dates: set[date] | None = None) -> dict | None:
    """Replace the Layer 1 draft only after a usable hours model is solved.

    Returns None when disabled/unusable; caller keeps the already validated Layer 1 draft.
    """
    settings = default_settings(session)
    if not settings.hours_layer_enabled:
        return {"status": "disabled", "solve_time_seconds": 0.0, "fallback": False}

    started = time.monotonic()
    run = session.get(RosterRun, run_id)
    if run is None:
        return None
    _, last_day = calendar.monthrange(run.year, run.month)
    dates = [date(run.year, run.month, n) for n in range(1, last_day + 1)]
    employees = session.scalars(select(Employee).options(
        selectinload(Employee.skills), selectinload(Employee.weekly_offs),
        selectinload(Employee.work_contract), selectinload(Employee.availability_exceptions),
        selectinload(Employee.eligibility),
    ).where(Employee.status == "active").order_by(Employee.employee_code)).all()
    rules = session.scalars(select(BrandProcessRule).options(
        selectinload(BrandProcessRule.brand), selectinload(BrandProcessRule.process),
        selectinload(BrandProcessRule.coverage_requirements),
    ).join(BrandProcessRule.brand).where(Brand.is_active.is_(True))).all()
    holidays = {row.holiday_date for row in session.scalars(select(PublicHoliday)).all()}
    pod_by_rule = {row.brand_process_rule_id: row.pod_id
                   for row in session.scalars(select(BrandProcessSchedule)).all()}
    pod_windows = {row.pod_id: row for row in session.scalars(select(PodSchedule)).all()}
    membership_rows = session.scalars(select(EmployeePodMembership)).all()
    memberships: dict[int, set[int]] = defaultdict(set)
    for row in membership_rows:
        memberships[row.employee_id].add(row.pod_id)
    for employee in employees:
        memberships[employee.id].add(employee.primary_pod_id)
    priorities = {row.brand_id: row for row in session.scalars(select(BrandPriority)).all()}
    overrides = session.scalars(select(PriorityOverride)).all()
    blocked: dict[int, set[date]] = defaultdict(set)
    for employee in employees:
        for exception in employee.availability_exceptions:
            if exception.approval_status == ApprovalStatus.APPROVED and exception.exception_type in {
                AvailabilityType.LEAVE, AvailabilityType.COMP_OFF, AvailabilityType.UNAVAILABLE,
            }:
                blocked[employee.id].update(day for day in dates if exception.start_date <= day <= exception.end_date)

    rows_by_rule_day: dict[tuple[int, date], list[CoverageRequirement]] = defaultdict(list)
    for rule in rules:
        for req in rule.coverage_requirements:
            rows_by_rule_day[(rule.id, date(run.year, run.month, 1))].append(req)
    requirements: dict[tuple[int, date], tuple[int, int | None]] = {}
    for rule in rules:
        by_weekday = {req.weekday: req for req in rule.coverage_requirements}
        for day in dates:
            if day.weekday() not in rule.operating_weekdays or (day in holidays and not rule.operate_on_public_holidays):
                continue
            req = by_weekday.get(day.weekday())
            if req and (req.minimum_staff or (rule.demand_units is not None and rule.demand_units > 0)):
                requirements[(rule.id, day)] = (req.minimum_staff, req.maximum_staff)
    for (rule_id, _), (minimum, _) in requirements.items():
        rule = next(row for row in rules if row.id == rule_id)
        demand = rule.demand_units if rule.demand_units is not None else minimum * settings.capacity_units
        if minimum and demand < minimum * settings.min_chunk_units:
            return {"status": "infeasible", "failure_stage": "demand_below_minimum_chunks",
                    "solve_time_seconds": time.monotonic() - started, "fallback": True}

    # Replans and repeat generations use the preceding draft's still-unserved share.
    # A first generation has no prior served work, so its initial backlog ratio is 1.
    previous_run = session.scalar(select(RosterRun).where(
        RosterRun.year == run.year, RosterRun.month == run.month, RosterRun.id != run.id,
    ).order_by(RosterRun.id.desc()).limit(1))
    total_demand_by_brand: dict[int, int] = defaultdict(int)
    for (rule_id, day), (minimum, _) in requirements.items():
        rule = next(row for row in rules if row.id == rule_id)
        total_demand_by_brand[rule.brand_id] += int(rule.demand_units if rule.demand_units is not None else minimum * settings.capacity_units)
    served_by_brand: dict[int, int] = defaultdict(int)
    if previous_run is not None:
        previous_rows = session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == previous_run.id,
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        rule_by_id_for_backlog = {rule.id: rule for rule in rules}
        for row in previous_rows:
            rule = rule_by_id_for_backlog.get(row.brand_process_rule_id)
            if rule is not None:
                served_by_brand[rule.brand_id] += row.hours_units or int(round(row.effort_hours * 2))
    backlog_by_brand = {
        brand_id: max(0.05, min(1.0, (demand - served_by_brand.get(brand_id, 0)) / demand))
        if demand else 0.05 for brand_id, demand in total_demand_by_brand.items()
    }

    model = cp_model.CpModel()
    h_vars: dict[tuple[int, int, date], cp_model.IntVar] = {}
    a_vars: dict[tuple[int, int, date], cp_model.IntVar] = {}
    work_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    window_vars: dict[tuple[int, int, date], cp_model.IntVar] = {}
    served_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    floor_short_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    floor_staff_short_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    surplus_vars: dict[tuple[int, date], cp_model.IntVar] = {}
    surplus_first_tier: dict[tuple[int, date], cp_model.IntVar] = {}
    surplus_second_tier: dict[tuple[int, date], cp_model.IntVar] = {}
    score_components: dict[tuple[int, date], dict] = {}
    cap = settings.capacity_units
    min_chunk = settings.min_chunk_units
    eligible_triples = []
    employees_by_id = {e.id: e for e in employees}
    rules_by_id = {r.id: r for r in rules}
    for (rule_id, day), (minimum, max_staff) in requirements.items():
        rule = rules_by_id[rule_id]
        demand = rule.demand_units if rule.demand_units is not None else minimum * cap
        demand = max(0, int(demand))
        served = model.new_int_var(0, demand, f"served_r{rule_id}_{day:%Y%m%d}")
        served_vars[(rule_id, day)] = served
        floor = min(demand, minimum * cap)
        short = model.new_int_var(0, floor, f"floor_short_r{rule_id}_{day:%Y%m%d}")
        floor_short_vars[(rule_id, day)] = short
        model.add(served + short >= floor)
        surplus = model.new_int_var(0, demand, f"surplus_r{rule_id}_{day:%Y%m%d}")
        model.add(surplus <= served + short - floor)
        model.add(surplus <= max(0, demand - floor))
        surplus_vars[(rule_id, day)] = surplus
        first_tier = model.new_int_var(0, min(cap, max(0, demand - floor)),
                                       f"surplus_first_r{rule_id}_{day:%Y%m%d}")
        second_tier = model.new_int_var(0, max(0, demand - floor),
                                        f"surplus_second_r{rule_id}_{day:%Y%m%d}")
        model.add(first_tier + second_tier == surplus)
        surplus_first_tier[(rule_id, day)] = first_tier
        surplus_second_tier[(rule_id, day)] = second_tier
        candidates = []
        for employee in employees:
            if not _eligible(employee, rule, day, memberships, pod_by_rule, blocked):
                continue
            eligible_triples.append((employee, rule, day))
            key = (employee.id, rule_id, day)
            h = model.new_int_var(0, min(cap, demand), f"h_e{employee.id}_r{rule_id}_{day:%Y%m%d}")
            a = model.new_bool_var(f"a_e{employee.id}_r{rule_id}_{day:%Y%m%d}")
            h_vars[key], a_vars[key] = h, a
            model.add(h <= cap * a)
            model.add(h >= min_chunk * a)
            candidates.append(a)
            wkey = (employee.id, day)
            if wkey not in work_vars:
                work_vars[wkey] = model.new_bool_var(f"work_e{employee.id}_{day:%Y%m%d}")
            model.add(a <= work_vars[wkey])
            pod_id = pod_by_rule.get(rule_id)
            if pod_id is not None:
                win_key = (employee.id, pod_id, day)
                if win_key not in window_vars:
                    window_vars[win_key] = model.new_bool_var(f"window_e{employee.id}_p{pod_id}_{day:%Y%m%d}")
                model.add(a <= window_vars[win_key])
        model.add(served == sum(h_vars[(employee.id, rule_id, day)] for employee in employees
                                if (employee.id, rule_id, day) in h_vars))
        floor_staff_short = model.new_int_var(0, minimum, f"floor_staff_short_r{rule_id}_{day:%Y%m%d}")
        floor_staff_short_vars[(rule_id, day)] = floor_staff_short
        model.add(sum(candidates) + floor_staff_short >= minimum)
        if not rule.can_share_same_day:
            model.add(sum(candidates) <= 1)
        elif max_staff is not None:
            model.add(sum(candidates) <= max_staff)
        score_components[(rule_id, day)] = _priority_components(
            rule.brand_id, day, priorities, overrides, backlog_by_brand.get(rule.brand_id, 1.0)
        )

    # Shared employee capacity, weekly contract, maximum brand count, and day count.
    triples_by_employee_day: dict[tuple[int, date], list[cp_model.IntVar]] = defaultdict(list)
    assigned_by_employee_day: dict[tuple[int, date], list[cp_model.IntVar]] = defaultdict(list)
    hours_by_employee_week: dict[tuple[int, int, int], list[cp_model.IntVar]] = defaultdict(list)
    works_by_employee_week: dict[tuple[int, int, int], list[cp_model.IntVar]] = defaultdict(list)
    brands_by_employee_day: dict[tuple[int, date], dict[int, list[cp_model.IntVar]]] = defaultdict(lambda: defaultdict(list))
    hours_by_employee_pod_day: dict[tuple[int, int, date], list[cp_model.IntVar]] = defaultdict(list)
    context_terms = []
    employee_total_hours: dict[int, list[cp_model.IntVar]] = defaultdict(list)
    for employee, rule, day in eligible_triples:
        key = (employee.id, rule.id, day)
        triples_by_employee_day[(employee.id, day)].append(h_vars[key])
        assigned_by_employee_day[(employee.id, day)].append(a_vars[key])
        brands_by_employee_day[(employee.id, day)][rule.brand_id].append(a_vars[key])
        employee_total_hours[employee.id].append(h_vars[key])
        pod_id = pod_by_rule.get(rule.id)
        if pod_id is not None:
            hours_by_employee_pod_day[(employee.id, pod_id, day)].append(h_vars[key])
        iso = day.isocalendar()
        hours_by_employee_week[(employee.id, iso.year, iso.week)].append(h_vars[key])
        works_by_employee_week[(employee.id, iso.year, iso.week)].append(work_vars[(employee.id, day)])
    for key, entries in triples_by_employee_day.items():
        employee_id, day = key
        employee = employees_by_id[employee_id]
        contract = employee.work_contract
        daily_cap = min(cap, int(round((contract.daily_capacity_hours if contract else 8) * 2)))
        model.add(sum(entries) <= daily_cap)
        brand_vars = []
        for brand_id, brand_entries in brands_by_employee_day[key].items():
            used = model.new_bool_var(f"brand_e{employee_id}_b{brand_id}_{day:%Y%m%d}")
            for assigned in brand_entries:
                model.add(assigned <= used)
            model.add(used <= sum(brand_entries))
            brand_vars.append(used)
            context_terms.append(used)
        model.add(sum(brand_vars) <= settings.max_brands_per_day)
    for (employee_id, pod_id, day), entries in hours_by_employee_pod_day.items():
        pod_capacity = pod_windows.get(pod_id).allocation_capacity_hours if pod_id in pod_windows else cap / 2
        model.add(sum(entries) <= min(cap, int(round(pod_capacity * 2))))
    # Employees assigned to distinct incompatible windows cannot overlap.
    for employee_id in employees_by_id:
        for day in dates:
            windows = [(key, var) for key, var in window_vars.items() if key[0] == employee_id and key[2] == day]
            for idx, (first_key, first) in enumerate(windows):
                for second_key, second in windows[idx + 1:]:
                    if first_key[1] != second_key[1] and _windows_overlap(
                        pod_windows.get(first_key[1]), pod_windows.get(second_key[1])
                    ):
                        model.add(first + second <= 1)
    for (employee_id, year, week), entries in hours_by_employee_week.items():
        contract = employees_by_id[employee_id].work_contract
        weekly_cap = int(round((contract.max_hours_per_week if contract else 40) * 2))
        model.add(sum(entries) <= weekly_cap)
        distinct_work = {variable.Index(): variable for variable in works_by_employee_week[(employee_id, year, week)]}
        if contract:
            model.add(sum(distinct_work.values()) <= contract.max_days_per_week)

    # Fix unaffected dates from the source roster during targeted replan.
    if preserve_from_run_id is not None and affected_dates is not None:
        old_rows = session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == preserve_from_run_id,
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        for old in old_rows:
            if old.assignment_date in affected_dates:
                continue
            key = (old.employee_id, old.brand_process_rule_id, old.assignment_date)
            if key in a_vars:
                old_units = old.hours_units or int(round(old.effort_hours * 2))
                model.add(a_vars[key] == 1)
                model.add(h_vars[key] == old_units)
            elif old.assignment_date in dates:
                return None

    # One missing staff member is weighted as one full daily capacity of floor shortfall.
    floor_objective = (sum(floor_short_vars.values())
                       + sum(value * cap for value in floor_staff_short_vars.values()))
    weighted_surplus = sum(
        surplus_first_tier[key] * max(1, int(round(score_components[key]["score"] * PRIORITY_SCORE_SCALE)))
        + surplus_second_tier[key] * max(1, int(round(score_components[key]["score"] * PRIORITY_SCORE_SCALE * SURPLUS_DIMINISHING_FACTOR)))
        for key in surplus_vars
    )
    backup_terms = []
    for employee, rule, day in eligible_triples:
        eligibility = next(item for item in employee.eligibility
                           if item.active and item.brand_process_rule_id == rule.id)
        if eligibility.eligibility_type.value == "backup":
            backup_terms.append(a_vars[(employee.id, rule.id, day)])
    model.minimize(floor_objective)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(settings.solver_time_limit_seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    stage_name = "guaranteed_floor"
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": solver.status_name(status).lower(), "solve_time_seconds": time.monotonic() - started,
                "failure_stage": stage_name, "fallback": True}
    best_floor = int(solver.value(floor_objective))
    model.add(floor_objective == best_floor)
    model.maximize(weighted_surplus)
    stage_name = "priority_surplus"
    solver.parameters.max_time_in_seconds = max(0.1, settings.solver_time_limit_seconds - (time.monotonic() - started))
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"status": solver.status_name(status).lower(), "solve_time_seconds": time.monotonic() - started,
                "failure_stage": stage_name, "fallback": True}
    best_surplus = int(solver.value(weighted_surplus))
    model.add(weighted_surplus == best_surplus)
    if backup_terms:
        backup_objective = sum(backup_terms)
        model.minimize(backup_objective)
        stage_name = "backup_preference"
        solver.parameters.max_time_in_seconds = max(0.1, settings.solver_time_limit_seconds - (time.monotonic() - started))
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {"status": solver.status_name(status).lower(), "solve_time_seconds": time.monotonic() - started,
                    "failure_stage": stage_name, "fallback": True}
        model.add(backup_objective == int(solver.value(backup_objective)))
    if context_terms:
        context_objective = sum(context_terms)
        model.minimize(context_objective)
        stage_name = "context_switching"
        solver.parameters.max_time_in_seconds = max(0.1, settings.solver_time_limit_seconds - (time.monotonic() - started))
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {"status": solver.status_name(status).lower(), "solve_time_seconds": time.monotonic() - started,
                    "failure_stage": stage_name, "fallback": True}
        model.add(context_objective == int(solver.value(context_objective)))
    if employee_total_hours:
        max_load = model.new_int_var(0, len(dates) * cap, "maximum_monthly_employee_load")
        for entries in employee_total_hours.values():
            model.add(sum(entries) <= max_load)
        model.minimize(max_load)
        stage_name = "load_balance"
        solver.parameters.max_time_in_seconds = max(0.1, settings.solver_time_limit_seconds - (time.monotonic() - started))
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return {"status": solver.status_name(status).lower(), "solve_time_seconds": time.monotonic() - started,
                    "failure_stage": stage_name, "fallback": True}
    solve_time = time.monotonic() - started
    logger.info("Hours allocation roster=%s eligible_triples=%s variables=%s status=%s solve_time=%.3fs",
                run_id, len(eligible_triples), len(h_vars) + len(a_vars), solver.status_name(status), solve_time)

    old_rows = session.scalars(select(RosterAssignment).where(RosterAssignment.roster_run_id == run.id)).all()
    session.execute(delete(RosterAssignment).where(RosterAssignment.roster_run_id == run.id))
    session.execute(delete(RosterEmployeeDay).where(RosterEmployeeDay.roster_run_id == run.id))
    session.flush()
    hours_by_employee_day: dict[tuple[int, date], int] = defaultdict(int)
    assigned_by_rule_day: dict[tuple[int, date], int] = defaultdict(int)
    score_snapshot = {
        f"{rules_by_id[rule_id].brand.code}:{rules_by_id[rule_id].process.code}:{day.isoformat()}": score
        for (rule_id, day), score in score_components.items()
    }
    for key, var in a_vars.items():
        if not solver.boolean_value(var):
            continue
        employee_id, rule_id, day = key
        units = int(solver.value(h_vars[key]))
        rule = rules_by_id[rule_id]
        eligibility = next(item for item in employees_by_id[employee_id].eligibility
                           if item.active and item.brand_process_rule_id == rule_id)
        score = score_components[(rule_id, day)]
        window = pod_windows.get(pod_by_rule.get(rule_id))
        explanation = (f"Allocated {units / 2:g} hours ({units} half-hour units); {eligibility.eligibility_type.value} eligible. "
                       f"Priority {score['score']:g} = tier {score['client_tier']} × urgency {score['urgency']:g} × "
                       f"backlog {score['backlog_factor']:g} × override {score['manager_override']:g}.")
        if window:
            explanation += f" Window: {window.shift_name}."
        session.add(RosterAssignment(
            roster_run_id=run.id, employee_id=employee_id, brand_process_rule_id=rule_id,
            assignment_date=day, effort_hours=units / 2, hours_units=units,
            status=AssignmentStatus.WORKING, explanation=explanation,
        ))
        hours_by_employee_day[(employee_id, day)] += units
        assigned_by_rule_day[(rule_id, day)] += 1

    for rule_id, day in requirements:
        if assigned_by_rule_day[(rule_id, day)] < requirements[(rule_id, day)][0]:
            rule = rules_by_id[rule_id]
            shortage = requirements[(rule_id, day)][0] - assigned_by_rule_day[(rule_id, day)]
            session.add(RosterAssignment(
                roster_run_id=run.id, employee_id=None, brand_process_rule_id=rule_id,
                assignment_date=day, effort_hours=0, hours_units=0,
                status=AssignmentStatus.UNCOVERED,
                explanation=f"Guaranteed staffing floor is short by {shortage}; all eligible candidates conflict with capacity, availability, skills, or pod windows.",
            ))
    for employee in employees:
        for day in dates:
            units = hours_by_employee_day[(employee.id, day)]
            status_value = (EmployeeRosterDayStatus.LEAVE if day in blocked[employee.id]
                            else EmployeeRosterDayStatus.OFF if day.weekday() in {off.weekday for off in employee.weekly_offs}
                            else EmployeeRosterDayStatus.WORKING if units
                            else EmployeeRosterDayStatus.UNSCHEDULED)
            session.add(RosterEmployeeDay(
                roster_run_id=run.id, employee_id=employee.id, schedule_date=day,
                status=status_value, assigned_effort_hours=units / 2,
                explanation=f"Allocated {units / 2:g} hours across eligible brand processes." if units else "No hours allocated.",
            ))

    report = session.get(RosterGenerationReport, run.id)
    if report:
        report.required_slots = sum(minimum for minimum, _ in requirements.values())
        report.filled_slots = sum(min(assigned_by_rule_day[key], requirements[key][0]) for key in requirements)
        report.uncovered_slots = report.required_slots - report.filled_slots
        report.result_status = "complete" if report.uncovered_slots == 0 else "partial"
        report.hours_layer_status = solver.status_name(status).lower()
        report.solve_time_seconds = solve_time
        floor_text = ("All guaranteed staffing floors were met." if report.uncovered_slots == 0
                      else f"{report.uncovered_slots} guaranteed staffing floor slot(s) remain uncovered.")
        report.explanation = f"{floor_text} Hours layer {solver.status_name(status).lower()} in {solve_time:.2f}s."
    run.allocation_snapshot = {
        "capacity_units": settings.capacity_units, "min_chunk_units": settings.min_chunk_units,
        "max_brands_per_day": settings.max_brands_per_day, "hours_layer_enabled": True,
        "solver_status": solver.status_name(status).lower(), "solve_time_seconds": round(solve_time, 4),
        "priority_scores": score_snapshot,
    }
    session.flush()
    return {"status": solver.status_name(status).lower(), "solve_time_seconds": solve_time,
            "fallback": False, "model_variables": len(h_vars) + len(a_vars), "floor_short_units": best_floor}
