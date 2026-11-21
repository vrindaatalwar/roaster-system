import unittest
import random
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.migrations import upgrade_schema
from app.models import (
    AllocationSetting, AssignmentStatus, Brand, BrandPriority, BrandProcessRule,
    CoverageRequirement, Employee, EmployeeEligibility, EmployeeSkill,
    EmployeeWeeklyOff, EmployeeWorkContract, EligibilityType, Pod, Process,
    RosterAssignment, RosterRun, Skill,
)
from app.scheduling import generate_month
from app.validation import validate_roster


class HoursAllocationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.day = date.today() + timedelta(days=(7 - date.today().weekday()) % 7)
        self.skill = Skill(code="CS", name="Customer support")
        self.pod = Pod(name="Pod", specialization="Support")
        self.session.add_all([self.skill, self.pod])
        self.session.flush()
        self.employees = []
        for index in range(3):
            employee = Employee(employee_code=f"EMP{index + 1:03d}", full_name=f"Employee {index + 1}",
                                role_title="Associate", primary_pod_id=self.pod.id, status="active")
            self.session.add(employee)
            self.session.flush()
            self.session.add_all([
                EmployeeSkill(employee_id=employee.id, skill_id=self.skill.id),
                EmployeeWorkContract(employee_id=employee.id, daily_capacity_hours=6,
                                     max_days_per_week=5, max_hours_per_week=40,
                                     minimum_days_off_per_week=2),
                EmployeeWeeklyOff(employee_id=employee.id, weekday=5),
                EmployeeWeeklyOff(employee_id=employee.id, weekday=6),
            ])
            self.employees.append(employee)
        self.rules = []
        for code, tier, days_until_deadline in [("HIGH", 5, 2), ("LOW", 1, 100)]:
            brand = Brand(code=code, name=code)
            process = Process(code=f"P{code}", name=code, description="", required_skill=self.skill,
                              default_effort_hours=3, enabled=True)
            self.session.add_all([brand, process])
            self.session.flush()
            rule = BrandProcessRule(
                brand_id=brand.id, process_id=process.id, required_skill_id=self.skill.id,
                operating_weekdays=[self.day.weekday()], operate_on_public_holidays=False,
                effort_hours_per_employee=3, demand_units=24, can_share_same_day=True,
                continuous_presence=False,
            )
            self.session.add(rule)
            self.session.flush()
            self.session.add_all([
                CoverageRequirement(brand_process_rule_id=rule.id, weekday=weekday,
                                    minimum_staff=1 if weekday == self.day.weekday() else 0,
                                    maximum_staff=None)
                for weekday in range(7)
            ])
            self.session.add(BrandPriority(
                brand_id=brand.id, priority=tier, client_tier=tier,
                deadline_date=self.day + timedelta(days=days_until_deadline),
            ))
            for employee in self.employees:
                self.session.add(EmployeeEligibility(
                    employee_id=employee.id, brand_process_rule_id=rule.id,
                    eligibility_type=EligibilityType.PRIMARY if employee.id == self.employees[0].id
                    else EligibilityType.BACKUP, active=True,
                ))
            self.rules.append(rule)
        self.session.add(AllocationSetting(id=1, capacity_units=12, min_chunk_units=1,
                                          max_brands_per_day=2, hours_layer_enabled=True,
                                          solver_time_limit_seconds=10))
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_hours_layer_respects_shared_capacity_floor_and_deadline_weighted_surplus(self):
        generated = generate_month(self.session, self.day.year, self.day.month)
        self.assertNotEqual(generated["hours_layer"]["status"], "fallback_layer_1", generated["hours_layer"])
        rows = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        for employee in self.employees:
            self.assertLessEqual(sum(row.hours_units for row in rows if row.employee_id == employee.id), 12)
        served = {rule.brand.code: sum(row.hours_units for row in rows
                                      if row.brand_process_rule_id == rule.id)
                  for rule in self.rules}
        self.assertTrue(all(units <= 24 for units in served.values()))
        self.assertGreaterEqual(served["HIGH"], 24)
        self.assertEqual(served["LOW"], 12)
        self.assertEqual(validate_roster(self.session, generated["roster_run_id"])["error_count"], 0)

    def test_deadline_urgency_shifts_surplus_with_equal_client_tier(self):
        for priority in self.session.scalars(select(BrandPriority)).all():
            priority.client_tier = 3
            priority.deadline_date = self.day + timedelta(days=2 if priority.brand_id == self.rules[0].brand_id else 100)
        self.session.commit()
        generated = generate_month(self.session, self.day.year, self.day.month)
        rows = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        served = {rule.brand.code: sum(row.hours_units for row in rows
                                      if row.brand_process_rule_id == rule.id)
                  for rule in self.rules}
        self.assertGreater(served["HIGH"], served["LOW"])

    def test_disabled_hours_layer_keeps_layer_one_and_records_snapshot(self):
        setting = self.session.get(AllocationSetting, 1)
        setting.hours_layer_enabled = False
        self.session.commit()
        generated = generate_month(self.session, self.day.year, self.day.month)
        self.assertEqual(generated["hours_layer"]["status"], "disabled")
        working = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == generated["roster_run_id"],
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        self.assertEqual(len(working), 2)

    def test_no_available_candidate_creates_gaps_without_layer_two_failure(self):
        for employee in self.employees:
            self.session.add(EmployeeWeeklyOff(employee_id=employee.id, weekday=self.day.weekday()))
        self.session.commit()
        generated = generate_month(self.session, self.day.year, self.day.month)
        self.assertNotEqual(generated["hours_layer"]["status"], "fallback_layer_1",
                            generated["hours_layer"])
        checked = validate_roster(self.session, generated["roster_run_id"])
        self.assertEqual(checked["error_count"], 0, checked["issues"])
        self.assertGreaterEqual(checked["gap_count"], 2)

    def test_infeasible_hours_inputs_fall_back_to_layer_one(self):
        for rule in self.rules:
            rule.demand_units = 0
        self.session.commit()
        generated = generate_month(self.session, self.day.year, self.day.month)
        self.assertEqual(generated["hours_layer"]["status"], "fallback_layer_1")
        self.assertTrue(generated["hours_layer"]["fallback"])
        self.assertEqual(validate_roster(self.session, generated["roster_run_id"])["error_count"], 0)

    def test_validator_rejects_sub_minimum_chunk_and_excess_brand_count(self):
        run = RosterRun(year=self.day.year, month=self.day.month, allocation_snapshot={
            "hours_layer_enabled": True, "capacity_units": 12,
            "min_chunk_units": 2, "max_brands_per_day": 1,
        })
        self.session.add(run)
        self.session.flush()
        for index, rule in enumerate(self.rules):
            self.session.add(RosterAssignment(
                roster_run_id=run.id, employee_id=self.employees[0].id,
                brand_process_rule_id=rule.id, assignment_date=self.day,
                effort_hours=0.5 if index == 0 else 2,
                hours_units=1 if index == 0 else 4,
                status=AssignmentStatus.WORKING, explanation="manual negative fixture",
            ))
        self.session.commit()
        checked = validate_roster(self.session, run.id)
        codes = {issue["code"] for issue in checked["issues"]}
        self.assertIn("assignment_below_minimum_chunk", codes)
        self.assertIn("maximum_brands_per_day_exceeded", codes)

    def test_fifty_deterministic_hours_configurations_have_no_hard_violations(self):
        rng = random.Random(17)
        for _ in range(50):
            setting = self.session.get(AllocationSetting, 1)
            setting.capacity_units = rng.choice([8, 10, 12])
            setting.min_chunk_units = rng.choice([1, 2])
            setting.max_brands_per_day = rng.choice([1, 2])
            setting.solver_time_limit_seconds = 10
            self.session.commit()
            generated = generate_month(self.session, self.day.year, self.day.month)
            self.assertNotEqual(generated["hours_layer"]["status"], "fallback_layer_1",
                                generated["hours_layer"])
            checked = validate_roster(self.session, generated["roster_run_id"])
            self.assertEqual(checked["error_count"], 0, checked["issues"])

    def test_additive_schema_upgrade_is_idempotent(self):
        upgrade_schema(self.engine)
        upgrade_schema(self.engine)


if __name__ == "__main__":
    unittest.main()
