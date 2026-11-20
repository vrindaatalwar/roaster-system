import unittest
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import (
    ReplacementSelection,
    ReplanRosterRequest,
    BrandProcessRuleChangeRequest,
    change_brand_process_rule,
    preview_replan,
    replacement_candidates,
    replan_roster,
)
from app.models import (
    ApprovalStatus,
    AssignmentStatus,
    AvailabilityException,
    AvailabilityType,
    Brand,
    BrandProcessRule,
    CoverageRequirement,
    Employee,
    EmployeeEligibility,
    EmployeeSkill,
    EmployeeWeeklyOff,
    EmployeeWorkContract,
    EligibilityType,
    Pod,
    Process,
    RosterAssignment,
    Skill,
)
from app.scheduling import generate_month
from app.validation import validate_roster


def first_monday_after_today() -> date:
    first_next_month = (date.today().replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next_month + timedelta(days=(7 - first_next_month.weekday()) % 7)


class StageThreeCoreFlowTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.day = first_monday_after_today()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def make_roster_inputs(self, eligible_count=3):
        skill = Skill(code="CS", name="Customer support")
        pod = Pod(name="Pod Alpha", specialization="Support")
        brand = Brand(code="BRD001", name="Test Brand")
        process = Process(code="CS", name="Customer support", description="Support coverage",
                          required_skill=skill, default_effort_hours=4)
        self.session.add_all([skill, pod, brand, process])
        self.session.flush()

        employees = []
        for index in range(3):
            employee = Employee(
                employee_code=f"EMP{index + 1:03d}", full_name=f"Employee {index + 1}",
                role_title="Operations Associate", primary_pod_id=pod.id,
                status="active", is_pod_lead=index == 0,
            )
            self.session.add(employee)
            self.session.flush()
            employees.append(employee)
            self.session.add_all([
                EmployeeSkill(employee_id=employee.id, skill_id=skill.id),
                EmployeeWorkContract(employee_id=employee.id, daily_capacity_hours=8,
                                     max_days_per_week=5, max_hours_per_week=40,
                                     minimum_days_off_per_week=2),
                EmployeeWeeklyOff(employee_id=employee.id, weekday=5),
                EmployeeWeeklyOff(employee_id=employee.id, weekday=6),
            ])

        rule = BrandProcessRule(
            brand_id=brand.id, process_id=process.id, required_skill_id=skill.id,
            operating_weekdays=[0], operate_on_public_holidays=False,
            effort_hours_per_employee=4, can_share_same_day=False, continuous_presence=False,
        )
        self.session.add(rule)
        self.session.flush()
        self.session.add_all([
            CoverageRequirement(brand_process_rule_id=rule.id, weekday=0,
                                minimum_staff=1, maximum_staff=None),
            CoverageRequirement(brand_process_rule_id=rule.id, weekday=1,
                                minimum_staff=1, maximum_staff=None),
        ])
        for index, employee in enumerate(employees[:eligible_count]):
            self.session.add(EmployeeEligibility(
                employee_id=employee.id, brand_process_rule_id=rule.id,
                eligibility_type=EligibilityType.PRIMARY if index == 0 else EligibilityType.BACKUP,
                active=True,
            ))
        self.session.commit()
        return employees, rule

    def generate_test_month(self):
        return generate_month(self.session, self.day.year, self.day.month)

    def test_one_of_three_absence_finds_backup_and_targeted_replan_preserves_other_dates(self):
        self.make_roster_inputs(eligible_count=3)
        generated = self.generate_test_month()
        source_id = generated["roster_run_id"]
        source_assignment = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == source_id,
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        ))
        unavailable_employee = source_assignment.employee
        self.session.add(AvailabilityException(
            employee_id=unavailable_employee.id,
            exception_type=AvailabilityType.LEAVE,
            approval_status=ApprovalStatus.APPROVED,
            start_date=self.day,
            end_date=self.day,
        ))
        self.session.commit()

        before_replan = validate_roster(self.session, source_id)
        affected_codes = {issue["code"] for issue in before_replan["issues"]
                          if issue.get("date") == self.day.isoformat()}
        self.assertIn("approved_absence_conflict", affected_codes)
        self.assertIn("insufficient_coverage", affected_codes)

        candidates = replacement_candidates(
            source_id, assignment_date=self.day, brand_code="BRD001", process_code="CS",
            excluding_employee_code=unavailable_employee.employee_code, session=self.session,
        )
        self.assertEqual(candidates["candidate_count"], 2)
        selected = candidates["candidates"][0]["employee_code"]
        result = replan_roster(
            source_id,
            ReplanRosterRequest(
                affected_dates=[self.day],
                replacement=ReplacementSelection(
                    date=self.day, brand_code="BRD001", process_code="CS", employee_code=selected,
                ),
            ),
            session=self.session,
        )

        self.assertEqual(result["after_validation"]["status"], "valid")
        self.assertTrue(result["unaffected_assignments_preserved"])
        final_assignment = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == result["roster_run_id"],
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        ))
        self.assertEqual(final_assignment.employee.employee_code, selected)

    def test_replan_preview_recommends_backup_without_saving_a_new_roster(self):
        self.make_roster_inputs(eligible_count=3)
        generated = self.generate_test_month()
        source_id = generated["roster_run_id"]
        source_assignment = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == source_id,
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        ))
        employee = source_assignment.employee
        self.session.add(AvailabilityException(
            employee_id=employee.id,
            exception_type=AvailabilityType.LEAVE,
            approval_status=ApprovalStatus.APPROVED,
            start_date=self.day,
            end_date=self.day,
        ))
        self.session.commit()

        preview = preview_replan(
            source_id, ReplanRosterRequest(affected_dates=[self.day]), session=self.session,
        )
        recommendation = next(item for item in preview["recommendations"]
                              if item["date"] == self.day.isoformat())
        self.assertEqual(recommendation["current_employee_code"], employee.employee_code)
        self.assertNotEqual(recommendation["proposed_employee_code"], employee.employee_code)
        self.assertEqual(recommendation["action"], "reassigned")
        self.assertEqual(preview["uncovered_slots"], 0)
        self.assertEqual(len(self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id != source_id,
        )).all()), 0)

    def test_sole_eligible_absence_has_a_clear_uncovered_reason(self):
        self.make_roster_inputs(eligible_count=1)
        generated = self.generate_test_month()
        source_id = generated["roster_run_id"]
        employee = self.session.scalar(select(Employee).where(Employee.employee_code == "EMP001"))
        self.session.add(AvailabilityException(
            employee_id=employee.id,
            exception_type=AvailabilityType.LEAVE,
            approval_status=ApprovalStatus.APPROVED,
            start_date=self.day,
            end_date=self.day,
        ))
        self.session.commit()

        candidates = replacement_candidates(
            source_id, assignment_date=self.day, brand_code="BRD001", process_code="CS",
            excluding_employee_code=employee.employee_code, session=self.session,
        )
        self.assertEqual(candidates["candidate_count"], 0)
        self.assertIn("No qualified, available employee", candidates["explanation"])

        result = replan_roster(
            source_id, ReplanRosterRequest(affected_dates=[self.day]), session=self.session,
        )
        self.assertEqual(result["result_status"], "partial")
        self.assertEqual(result["after_validation"]["status"], "needs_attention")
        uncovered = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == result["roster_run_id"],
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.UNCOVERED,
        ))
        self.assertIn("All qualified eligible employees are on weekly off or approved leave", uncovered.explanation)
        gap = next(issue for issue in result["after_validation"]["issues"]
                   if issue["code"] == "insufficient_coverage" and issue.get("date") == self.day.isoformat())
        self.assertIn("All qualified eligible employees", gap["message"])

    def test_brand_calendar_change_identifies_affected_dates_and_replans_to_new_days(self):
        _, rule = self.make_roster_inputs(eligible_count=3)
        generated = self.generate_test_month()
        source_id = generated["roster_run_id"]

        change = change_brand_process_rule(
            rule.id,
            BrandProcessRuleChangeRequest(operating_weekdays=[1]),
            session=self.session,
        )
        affected_run = next(item for item in change["affected_runs"]
                            if item["roster_run_id"] == source_id)
        affected_dates = [date.fromisoformat(item) for item in affected_run["affected_dates"]]
        self.assertTrue(affected_dates)
        self.assertTrue(all(day.weekday() in {0, 1} for day in affected_dates))

        preview = preview_replan(
            source_id, ReplanRosterRequest(affected_dates=affected_dates), session=self.session,
        )
        self.assertTrue(any(item["action"] == "demand_removed" for item in preview["recommendations"]))
        self.assertTrue(any(item["action"] == "added" and date.fromisoformat(item["date"]).weekday() == 1
                            for item in preview["recommendations"]))
        self.assertEqual(len(self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id != source_id,
        )).all()), 0)

        result = replan_roster(
            source_id, ReplanRosterRequest(affected_dates=affected_dates), session=self.session,
        )
        self.assertEqual(result["after_validation"]["status"], "valid")
        self.assertTrue(result["unaffected_assignments_preserved"])
        new_assignments = self.session.scalars(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == result["roster_run_id"],
            RosterAssignment.status == AssignmentStatus.WORKING,
        )).all()
        self.assertTrue(new_assignments)
        self.assertTrue(all(assignment.assignment_date.weekday() == 1 for assignment in new_assignments))

    def test_independent_validator_detects_disallowed_same_day_sharing(self):
        employees, rule = self.make_roster_inputs(eligible_count=3)
        generated = self.generate_test_month()
        run_id = generated["roster_run_id"]
        existing = self.session.scalar(select(RosterAssignment).where(
            RosterAssignment.roster_run_id == run_id,
            RosterAssignment.assignment_date == self.day,
            RosterAssignment.status == AssignmentStatus.WORKING,
        ))
        extra_employee = next(employee for employee in employees if employee.id != existing.employee_id)
        self.session.add(RosterAssignment(
            roster_run_id=run_id, employee_id=extra_employee.id,
            brand_process_rule_id=rule.id, assignment_date=self.day,
            effort_hours=4, status=AssignmentStatus.WORKING,
            explanation="Intentional validator fixture.",
        ))
        self.session.commit()

        result = validate_roster(self.session, run_id)
        self.assertIn("same_process_sharing_conflict", {issue["code"] for issue in result["issues"]})

    def test_replacement_candidates_respect_rule_that_forbids_same_day_sharing(self):
        _, rule = self.make_roster_inputs(eligible_count=3)
        monday_requirement = self.session.scalar(select(CoverageRequirement).where(
            CoverageRequirement.brand_process_rule_id == rule.id,
            CoverageRequirement.weekday == 0,
        ))
        monday_requirement.minimum_staff = 2
        self.session.commit()
        generated = self.generate_test_month()

        result = replacement_candidates(
            generated["roster_run_id"], assignment_date=self.day,
            brand_code="BRD001", process_code="CS", session=self.session,
        )
        self.assertEqual(result["candidate_count"], 0)
        self.assertIn("does not allow multiple employees", result["explanation"])


if __name__ == "__main__":
    unittest.main()
