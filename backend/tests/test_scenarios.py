"""Scenario and property checks transcribed from the supplied guardrail checklist."""
import random
import unittest
import calendar
from datetime import date, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import BrandProcessConfigurationRequest
from app.models import (
    ApprovalStatus, AssignmentStatus, AvailabilityException, AvailabilityType, Brand,
    BrandProcessRule, CoverageRequirement, Employee, EmployeeEligibility, EmployeeSkill,
    EmployeeWeeklyOff, EmployeeWorkContract, EligibilityType, Pod, Process, PublicHoliday,
    RosterAssignment, Skill,
)
from app.scheduling import generate_month
from app.validation import validate_roster
from pydantic import ValidationError


class RequestedGuardrailsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.year, self.month = date.today().year, date.today().month
        self.skill = Skill(code="CS", name="Customer Support")
        self.pod = Pod(name="Pod A", specialization="Support")
        self.brand = Brand(code="BRD-A", name="Brand A")
        self.process = Process(code="CS", name="Support", description="", required_skill=self.skill,
                              default_effort_hours=3, enabled=True)
        self.session.add_all([self.skill, self.pod, self.brand, self.process])
        self.session.flush()
        self.employees = []
        self._eligibility_flags = {}
        self.rule = None

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def add_employee(self, code, *, weekly_offs=(5, 6), eligible=True, skilled=True,
                     active=True, capacity=6):
        employee = Employee(employee_code=code, full_name=code, role_title="Associate",
                            primary_pod_id=self.pod.id, status="active" if active else "inactive",
                            is_pod_lead=not self.employees)
        self.session.add(employee)
        self.session.flush()
        if skilled:
            self.session.add(EmployeeSkill(employee_id=employee.id, skill_id=self.skill.id))
        self.session.add(EmployeeWorkContract(employee_id=employee.id, daily_capacity_hours=capacity,
                                              max_days_per_week=5, max_hours_per_week=40,
                                              minimum_days_off_per_week=2))
        for weekday in weekly_offs:
            self.session.add(EmployeeWeeklyOff(employee_id=employee.id, weekday=weekday))
        self.employees.append(employee)
        self._eligibility_flags[employee.id] = eligible
        if eligible and self.rule is not None:
            self.session.add(EmployeeEligibility(employee_id=employee.id, brand_process_rule_id=self.rule.id,
                                                 eligibility_type=EligibilityType.PRIMARY if len(self.employees) == 1
                                                 else EligibilityType.BACKUP, active=True))
        return employee

    def make_rule(self, *, weekdays=tuple(range(5)), daily_min=1, effort=3,
                  share=False, holidays=False):
        self.rule = BrandProcessRule(
            brand_id=self.brand.id, process_id=self.process.id, required_skill_id=self.skill.id,
            operating_weekdays=list(weekdays), operate_on_public_holidays=holidays,
            effort_hours_per_employee=effort, can_share_same_day=share, continuous_presence=False,
        )
        self.session.add(self.rule)
        self.session.flush()
        self.session.add_all(CoverageRequirement(
            brand_process_rule_id=self.rule.id, weekday=weekday,
            minimum_staff=daily_min, maximum_staff=None,
        ) for weekday in range(7))
        for employee in self.employees:
            if self._eligibility_flags.get(employee.id, True):
                self.session.add(EmployeeEligibility(
                    employee_id=employee.id, brand_process_rule_id=self.rule.id,
                    eligibility_type=EligibilityType.PRIMARY if employee is self.employees[0]
                    else EligibilityType.BACKUP, active=True,
                ))
        self.session.commit()

    def first_weekday(self, weekday):
        first = date(self.year, self.month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7)

    def generate_and_check_hard_rules(self):
        generated = generate_month(self.session, self.year, self.month)
        result = validate_roster(self.session, generated["roster_run_id"])
        hard_issues = [issue for issue in result["issues"]
                       if issue["severity"] == "error" and issue["code"] != "insufficient_coverage"]
        self.assertEqual(hard_issues, [])
        return generated, result

    def test_solo_sunday_off_gets_full_monday_to_friday_coverage(self):
        self.year, self.month = 2027, 3
        self.add_employee("EMP001", weekly_offs=(6, 5))
        self.make_rule(weekdays=(0, 1, 2, 3, 4))
        generated, _ = self.generate_and_check_hard_rules()
        self.assertEqual(generated["uncovered_slots"], 0)

    def test_solo_monday_to_saturday_demand_respects_five_day_cap(self):
        self.year, self.month = 2027, 3
        self.add_employee("EMP001", weekly_offs=(5, 6))
        self.make_rule(weekdays=(0, 1, 2, 3, 4, 5))
        generated, _ = self.generate_and_check_hard_rules()
        self.assertGreater(generated["uncovered_slots"], 0)
        assignments = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        self.assertTrue(all(row.assignment_date.weekday() != 6 for row in assignments))

    def test_three_staggered_employees_cover_two_daily_slots(self):
        self.add_employee("EMP001", weekly_offs=(0, 1))
        self.add_employee("EMP002", weekly_offs=(2, 3))
        self.add_employee("EMP003", weekly_offs=(4, 5))
        self.make_rule(weekdays=tuple(range(7)), daily_min=2, share=True)
        generated, _ = self.generate_and_check_hard_rules()
        self.assertEqual(generated["uncovered_slots"], 0)

    def test_zero_eligible_employees_returns_explained_partial_draft(self):
        self.add_employee("EMP001", eligible=False)
        self.make_rule(weekdays=(0,))
        generated, _ = self.generate_and_check_hard_rules()
        self.assertGreater(generated["uncovered_slots"], 0)
        uncovered = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.status == AssignmentStatus.UNCOVERED,
        )).all()
        self.assertTrue(all("explicitly eligible" in row.explanation for row in uncovered))

    def test_inconsistent_minimum_and_maximum_staff_is_rejected(self):
        minimums = [2] + [0] * 6
        maximums = [1] + [None] * 6
        with self.assertRaises(ValidationError):
            BrandProcessConfigurationRequest(
                brand_code="BRD-A", process_code="CS", operating_weekdays=[0],
                effort_hours_per_employee=3, minimum_by_weekday=minimums,
                maximum_by_weekday=maximums,
            )

    def test_leap_february_and_sunday_month_start_are_handled(self):
        self.year = next(year for year in range(date.today().year, date.today().year + 6)
                         if calendar.isleap(year))
        self.month = 2
        self.add_employee("EMP001", weekly_offs=(5, 6))
        self.make_rule(weekdays=tuple(range(7)))
        leap_month, _ = self.generate_and_check_hard_rules()
        self.assertEqual(leap_month["required_slots"], 29)

        self.tearDown()
        self.setUp()
        self.year, self.month = next(
            (year, month) for year in range(date.today().year, date.today().year + 6)
            for month in range(1, 13) if date(year, month, 1).weekday() == 6
        )
        self.add_employee("EMP001", weekly_offs=(1, 2))
        self.make_rule(weekdays=(6,))
        sunday_start, _ = self.generate_and_check_hard_rules()
        first = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == sunday_start["roster_run_id"],
            RosterAssignment.assignment_date == date(self.year, self.month, 1),
        ))
        self.assertIsNotNone(first)

    def test_identical_inputs_have_the_same_coverage_objective(self):
        self.add_employee("EMP001", weekly_offs=(5, 6))
        self.make_rule(weekdays=tuple(range(7)))
        first, _ = self.generate_and_check_hard_rules()
        second, _ = self.generate_and_check_hard_rules()
        self.assertEqual(first["filled_slots"], second["filled_slots"])
        self.assertEqual(first["uncovered_slots"], second["uncovered_slots"])

    def test_generator_rolls_back_if_independent_validator_finds_hard_conflict(self):
        from app.models import RosterRun
        self.add_employee("EMP001")
        self.make_rule(weekdays=(0,))
        with patch("app.validation.validate_roster", return_value={
            "issues": [{"severity": "error", "code": "forced_test_conflict",
                        "message": "Injected validator conflict"}],
        }):
            with self.assertRaisesRegex(RuntimeError, "independent hard-rule validation"):
                generate_month(self.session, self.year, self.month)
        self.assertEqual(self.session.scalar(select(RosterRun.id)), None)

    def test_daily_demand_with_solo_owner_reports_weekly_off_gaps(self):
        self.year, self.month = 2027, 3
        self.add_employee("EMP001", weekly_offs=(5, 6))
        self.make_rule(weekdays=tuple(range(7)))
        generated, validation = self.generate_and_check_hard_rules()
        self.assertGreaterEqual(generated["uncovered_slots"], 8)
        uncovered = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.status == AssignmentStatus.UNCOVERED,
        )).all()
        self.assertTrue(uncovered)
        self.assertTrue(all(row.explanation for row in uncovered))
        self.assertTrue(any(item["code"] == "insufficient_coverage" for item in validation["issues"]))

    def test_backup_covers_solo_owner_weekly_off(self):
        owner = self.add_employee("EMP001", weekly_offs=(6, 5))
        self.add_employee("EMP002", weekly_offs=(1, 2))
        self.make_rule(weekdays=tuple(range(7)))
        sunday = self.first_weekday(6)
        generated, _ = self.generate_and_check_hard_rules()
        row = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.assignment_date == sunday,
        ))
        self.assertEqual(row.employee_id, self.employees[1].id)
        self.assertNotEqual(row.employee_id, owner.id)

    def test_closed_holiday_has_no_demand_but_open_holiday_does(self):
        employee = self.add_employee("EMP001")
        holiday = self.first_weekday(0)
        self.session.add(PublicHoliday(name="Test holiday", holiday_date=holiday))
        self.make_rule(weekdays=(0,), holidays=False)
        closed, _ = self.generate_and_check_hard_rules()
        row = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == closed["roster_run_id"],
            RosterAssignment.assignment_date == holiday,
        ))
        self.assertIsNone(row)
        self.rule.operate_on_public_holidays = True
        self.session.commit()
        open_run, _ = self.generate_and_check_hard_rules()
        row = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == open_run["roster_run_id"],
            RosterAssignment.assignment_date == holiday,
        ))
        self.assertEqual(row.employee_id, employee.id)

    def test_pending_and_declined_leave_do_not_block_but_pending_warns(self):
        employee = self.add_employee("EMP001")
        self.make_rule(weekdays=(0,))
        day = self.first_weekday(0)
        self.session.add_all([
            AvailabilityException(employee_id=employee.id, exception_type=AvailabilityType.LEAVE,
                                  approval_status=ApprovalStatus.PENDING, start_date=day, end_date=day,
                                  note="Pending request"),
            AvailabilityException(employee_id=employee.id, exception_type=AvailabilityType.LEAVE,
                                  approval_status=ApprovalStatus.DECLINED, start_date=day, end_date=day,
                                  note="Declined request"),
        ])
        self.session.commit()
        generated, validation = self.generate_and_check_hard_rules()
        assignment = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.assignment_date == day,
        ))
        self.assertEqual(assignment.employee_id, employee.id)
        self.assertTrue(any(issue["code"] == "pending_availability" for issue in validation["issues"]))

    def test_approved_leave_for_only_owner_produces_partial_roster(self):
        employee = self.add_employee("EMP001")
        self.make_rule(weekdays=(0,))
        self.session.add(AvailabilityException(
            employee_id=employee.id, exception_type=AvailabilityType.LEAVE,
            approval_status=ApprovalStatus.APPROVED,
            start_date=date(self.year, self.month, 1),
            end_date=date(self.year, self.month, calendar.monthrange(self.year, self.month)[1]),
            note="Approved for month",
        ))
        self.session.commit()
        generated, _ = self.generate_and_check_hard_rules()
        self.assertGreater(generated["uncovered_slots"], 0)
        self.assertEqual(generated["result_status"], "partial")

    def test_hand_built_valid_roster_has_no_issues(self):
        self.add_employee("EMP001")
        self.make_rule(weekdays=(0,))
        generated, validation = self.generate_and_check_hard_rules()
        self.assertEqual(validation["issue_count"], 0)
        self.assertEqual(generated["uncovered_slots"], 0)

    def test_validator_reports_maximum_staff_and_closed_day_assignments(self):
        first = self.add_employee("EMP001")
        second = self.add_employee("EMP002")
        self.make_rule(weekdays=(0,), share=True)
        monday = self.first_weekday(0)
        monday_rule = self.session.scalar(select(CoverageRequirement).where(
            CoverageRequirement.brand_process_rule_id == self.rule.id,
            CoverageRequirement.weekday == 0,
        ))
        monday_rule.maximum_staff = 1
        from app.models import RosterRun
        run = RosterRun(year=self.year, month=self.month)
        self.session.add(run)
        self.session.flush()
        self.session.add_all([
            RosterAssignment(roster_run_id=run.id, employee_id=first.id,
                             brand_process_rule_id=self.rule.id, assignment_date=monday,
                             effort_hours=3, status=AssignmentStatus.WORKING, explanation="fixture"),
            RosterAssignment(roster_run_id=run.id, employee_id=second.id,
                             brand_process_rule_id=self.rule.id, assignment_date=monday,
                             effort_hours=3, status=AssignmentStatus.WORKING, explanation="fixture"),
            RosterAssignment(roster_run_id=run.id, employee_id=first.id,
                             brand_process_rule_id=self.rule.id, assignment_date=self.first_weekday(1),
                             effort_hours=3, status=AssignmentStatus.WORKING, explanation="closed-day fixture"),
        ])
        self.session.commit()
        issues = validate_roster(self.session, run.id)["issues"]
        codes = {issue["code"] for issue in issues}
        self.assertIn("maximum_staff_exceeded", codes)
        self.assertIn("closed_day_assignment", codes)

    def test_validator_reports_ineligible_employee_as_hard_violation(self):
        employee = self.add_employee("EMP001", eligible=False)
        self.make_rule(weekdays=(0,))
        from app.models import RosterRun
        run = RosterRun(year=self.year, month=self.month)
        self.session.add(run)
        self.session.flush()
        self.session.add(RosterAssignment(
            roster_run_id=run.id, employee_id=employee.id, brand_process_rule_id=self.rule.id,
            assignment_date=self.first_weekday(0), effort_hours=3,
            status=AssignmentStatus.WORKING, explanation="invalid fixture",
        ))
        self.session.commit()
        result = validate_roster(self.session, run.id)
        self.assertIn("not_eligible", {issue["code"] for issue in result["issues"]})

    def test_primary_pod_is_retained_when_some_other_memberships_exist(self):
        primary = self.add_employee("EMP001")
        secondary = Pod(name="Pod B", specialization="Operations")
        self.session.add(secondary)
        self.session.flush()
        other = self.add_employee("EMP002", eligible=False)
        from app.models import BrandProcessSchedule, EmployeePodMembership, PodSchedule
        self.session.add_all([
            EmployeePodMembership(employee_id=other.id, pod_id=secondary.id),
            PodSchedule(pod_id=self.pod.id, shift_name="Day", shift_start="09:00", shift_end="18:00",
                        break_start="13:00", break_end="14:00", allocation_capacity_hours=6),
        ])
        self.make_rule(weekdays=(0,))
        self.session.add(BrandProcessSchedule(brand_process_rule_id=self.rule.id, pod_id=self.pod.id))
        self.session.commit()
        generated, _ = self.generate_and_check_hard_rules()
        assignment = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.assignment_date == self.first_weekday(0),
        ))
        self.assertEqual(assignment.employee_id, primary.id)

    def test_randomized_small_worlds_never_generate_hard_rule_violations(self):
        # 50 deterministic, small fixtures exercise the solver/validator contract.
        for seed in range(50):
            with self.subTest(seed=seed):
                self.tearDown()
                self.setUp()
                self.year, self.month = 2027, 3
                rng = random.Random(seed)
                count = rng.randint(1, 4)
                for index in range(count):
                    offs = tuple(rng.sample(range(7), 2))
                    self.add_employee(f"EMP{index + 1:03d}", weekly_offs=offs,
                                      eligible=rng.choice([True, True, False]),
                                      skilled=rng.choice([True, True, False]),
                                      capacity=rng.choice([3, 4, 6]))
                self.make_rule(weekdays=tuple(range(7)), daily_min=rng.randint(1, 2),
                               effort=rng.choice([1.5, 3, 6]), share=True)
                generated, _ = self.generate_and_check_hard_rules()
                self.assertGreaterEqual(generated["required_slots"], generated["filled_slots"])


if __name__ == "__main__":
    unittest.main()
