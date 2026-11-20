import unittest
from datetime import date, timedelta

from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import PodConfigurationRequest, roster_detail
from app.models import (
    AssignmentStatus, Brand, BrandPriority, BrandProcessRule, BrandProcessSchedule,
    CoverageRequirement, Employee, EmployeeEligibility, EmployeePodMembership,
    EmployeeSkill, EmployeeWeeklyOff, EmployeeWorkContract, EligibilityType,
    Pod, PodSchedule, Process, RosterAssignment, RosterRun, Skill,
)
from app.scheduling import generate_month
from app.validation import validate_roster


class TimeWindowPriorityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.year = date.today().year
        self.month = date.today().month
        self.skills = {
            "CS": Skill(code="CS", name="Customer support"),
            "ACC": Skill(code="ACC", name="Cart recovery"),
        }
        self.pods = [Pod(name="Pod Alpha", specialization="Support"),
                     Pod(name="Pod Beta", specialization="Operations")]
        self.session.add_all([*self.skills.values(), *self.pods])
        self.session.flush()
        self.shifts = [
            PodSchedule(pod_id=self.pods[0].id, shift_name="Day Window", shift_start="09:00", shift_end="18:00",
                        break_start="13:00", break_end="14:00", allocation_capacity_hours=6),
            PodSchedule(pod_id=self.pods[1].id, shift_name="Late Window", shift_start="13:00", shift_end="22:00",
                        break_start="17:00", break_end="18:00", allocation_capacity_hours=6),
        ]
        self.session.add_all(self.shifts)
        self.employee = Employee(employee_code="EMP001", full_name="Aarav Mehta", role_title="Associate",
                                 primary_pod_id=self.pods[0].id, status="active", is_pod_lead=True)
        self.session.add(self.employee)
        self.session.flush()
        self.session.add_all([
            EmployeePodMembership(employee_id=self.employee.id, pod_id=pod.id) for pod in self.pods
        ])
        self.session.add_all([
            EmployeeSkill(employee_id=self.employee.id, skill_id=skill.id)
            for skill in self.skills.values()
        ])
        self.session.add_all([
            EmployeeWorkContract(employee_id=self.employee.id, daily_capacity_hours=6,
                                 max_days_per_week=5, max_hours_per_week=40,
                                 minimum_days_off_per_week=2),
            EmployeeWeeklyOff(employee_id=self.employee.id, weekday=5),
            EmployeeWeeklyOff(employee_id=self.employee.id, weekday=6),
        ])
        self.rules = []
        for index, (code, skill_code, pod_index, priority) in enumerate([
            ("BRD-HIGH", "CS", 0, 5), ("BRD-LOW", "ACC", 1, 1),
        ]):
            brand = Brand(code=code, name=code)
            process = Process(code=skill_code, name=skill_code, description="Test", required_skill=self.skills[skill_code],
                              default_effort_hours=3, enabled=True)
            self.session.add_all([brand, process])
            self.session.flush()
            rule = BrandProcessRule(
                brand_id=brand.id, process_id=process.id, required_skill_id=self.skills[skill_code].id,
                operating_weekdays=[0], operate_on_public_holidays=False, effort_hours_per_employee=3,
                can_share_same_day=False, continuous_presence=False,
            )
            self.session.add(rule)
            self.session.flush()
            self.session.add_all([
                CoverageRequirement(brand_process_rule_id=rule.id, weekday=weekday,
                                    minimum_staff=1 if weekday == 0 else 0, maximum_staff=None)
                for weekday in range(7)
            ])
            self.session.add_all([
                EmployeeEligibility(employee_id=self.employee.id, brand_process_rule_id=rule.id,
                                    eligibility_type=EligibilityType.PRIMARY, active=True),
                BrandProcessSchedule(brand_process_rule_id=rule.id, pod_id=self.pods[pod_index].id),
                BrandPriority(brand_id=brand.id, priority=priority,
                              deadline_note="Urgent" if priority == 5 else "Routine"),
            ])
            self.rules.append(rule)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _first_monday(self):
        first = date(self.year, self.month, 1)
        return first + timedelta(days=(7 - first.weekday()) % 7)

    def test_overlapping_pod_demand_uses_deadline_priority_without_breaking_specialization(self):
        result = generate_month(self.session, self.year, self.month)
        rows = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == result["roster_run_id"],
            RosterAssignment.assignment_date == self._first_monday(),
        )).all()
        by_rule = {row.brand_process_rule_id: row for row in rows}
        self.assertEqual(by_rule[self.rules[0].id].status, AssignmentStatus.WORKING)
        self.assertEqual(by_rule[self.rules[1].id].status, AssignmentStatus.UNCOVERED)
        self.assertIn("priority is 5/5", by_rule[self.rules[0].id].explanation)
        self.assertEqual(sum(row.status == AssignmentStatus.WORKING for row in rows), 1)

    def test_assignment_picker_only_returns_members_of_the_required_pod_window(self):
        other = Employee(employee_code="EMP002", full_name="Other Employee", role_title="Associate",
                         primary_pod_id=self.pods[1].id, status="active", is_pod_lead=False)
        self.session.add(other)
        self.session.flush()
        self.session.add_all([
            EmployeeSkill(employee_id=other.id, skill_id=self.skills["CS"].id),
            EmployeeWorkContract(employee_id=other.id, daily_capacity_hours=6,
                                 max_days_per_week=5, max_hours_per_week=40,
                                 minimum_days_off_per_week=2),
            EmployeeWeeklyOff(employee_id=other.id, weekday=5),
            EmployeeWeeklyOff(employee_id=other.id, weekday=6),
            EmployeePodMembership(employee_id=other.id, pod_id=self.pods[1].id),
            EmployeeEligibility(employee_id=other.id, brand_process_rule_id=self.rules[0].id,
                                eligibility_type=EligibilityType.BACKUP, active=True),
        ])
        self.session.commit()
        generated = generate_month(self.session, self.year, self.month)
        roster = roster_detail(generated["roster_run_id"], self.session)
        sample = next(row for row in roster["assignments"]
                      if row["brand_code"] == "BRD-HIGH" and date.fromisoformat(row["date"]).weekday() == 0)
        self.assertEqual(sample["pod_name"], "Pod Alpha")
        self.assertIn("EMP001", [candidate["employee_code"] for candidate in sample["available_candidates"]])
        self.assertNotIn("EMP002", [candidate["employee_code"] for candidate in sample["available_candidates"]])

    def test_validator_flags_manual_assignments_in_overlapping_pod_windows(self):
        run = RosterRun(year=self.year, month=self.month)
        self.session.add(run)
        self.session.flush()
        day = self._first_monday()
        for rule in self.rules:
            self.session.add(RosterAssignment(
                roster_run_id=run.id, employee_id=self.employee.id, brand_process_rule_id=rule.id,
                assignment_date=day, effort_hours=3, status=AssignmentStatus.WORKING,
                explanation="Test assignment",
            ))
        self.session.commit()
        checked = validate_roster(self.session, run.id)
        self.assertTrue(any(issue["code"] == "overlapping_pod_windows" for issue in checked["issues"]))

    def test_pod_window_requires_eight_hours_and_a_break_inside_it(self):
        with self.assertRaises(ValidationError):
            PodConfigurationRequest(name="Pod Invalid", shift_start="09:00", shift_end="17:00",
                                    break_start="13:00", break_end="14:00")
        with self.assertRaises(ValidationError):
            PodConfigurationRequest(name="Pod Invalid", shift_start="09:00", shift_end="18:00",
                                    break_start="18:00", break_end="19:00")


if __name__ == "__main__":
    unittest.main()
