import unittest
from datetime import date

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import (
    AssignmentEditRequest,
    AvailabilityStatusRequest,
    BrandConfigurationRequest,
    EmployeeConfigurationRequest,
    EligibilityChangeRequest,
    ProcessConfigurationRequest,
    change_eligibility,
    create_brand,
    create_employee,
    edit_roster_assignment,
    publish_roster,
    update_process,
    update_availability_status,
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
    EmployeeRosterDayStatus,
    EmployeeSkill,
    EmployeeWeeklyOff,
    EmployeeWorkContract,
    EligibilityType,
    Pod,
    Process,
    RosterAssignment,
    RosterEmployeeDay,
    RosterRun,
    Skill,
)


class AssessmentManagementTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.pod = Pod(name="Pod A", specialization="Support")
        self.skill = Skill(code="CS", name="Customer support")
        self.brand = Brand(code="BRD001", name="Test brand")
        self.process = Process(code="CS", name="Customer support", description="Support",
                               required_skill=self.skill, default_effort_hours=4)
        self.session.add_all([self.pod, self.skill, self.brand, self.process])
        self.session.flush()
        self.rule = BrandProcessRule(
            brand=self.brand, process=self.process, required_skill=self.skill,
            operating_weekdays=[0], operate_on_public_holidays=False,
            effort_hours_per_employee=4, can_share_same_day=False,
        )
        self.session.add(self.rule)
        self.session.flush()
        self.session.add(CoverageRequirement(
            brand_process=self.rule, weekday=0, minimum_staff=1,
        ))
        self.employees = []
        for index in range(2):
            employee = Employee(
                employee_code=f"EMP{index + 1:03d}", full_name=f"Employee {index + 1}",
                role_title="Operations Associate", primary_pod=self.pod,
                status="active", is_pod_lead=index == 0,
            )
            self.session.add(employee)
            self.session.flush()
            self.session.add_all([
                EmployeeSkill(employee_id=employee.id, skill_id=self.skill.id),
                EmployeeWorkContract(employee_id=employee.id, daily_capacity_hours=8,
                                     max_days_per_week=5, max_hours_per_week=40,
                                     minimum_days_off_per_week=2),
                EmployeeEligibility(employee_id=employee.id, brand_process_rule_id=self.rule.id,
                                    eligibility_type=EligibilityType.PRIMARY if index == 0 else EligibilityType.BACKUP),
            ])
            self.employees.append(employee)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_manager_can_add_employee_with_configured_skills_and_capacity(self):
        payload = EmployeeConfigurationRequest(
            employee_code="EMP003", full_name="New Employee", role_title="Specialist",
            pod_name="Pod A", skills=["CS"], weekly_off_weekdays=[5, 6],
            daily_capacity_hours=8, max_days_per_week=5, max_hours_per_week=40,
        )
        response = create_employee(payload, session=self.session)
        employee = self.session.scalar(select(Employee).where(Employee.employee_code == "EMP003"))
        self.assertEqual(response["status"], "saved")
        self.assertEqual({skill.code for skill in employee.skills}, {"CS"})
        self.assertEqual({off.weekday for off in employee.weekly_offs}, {5, 6})
        self.assertEqual(employee.work_contract.max_hours_per_week, 40)

    def test_existing_availability_request_can_be_approved_and_reports_affected_dates(self):
        request = AvailabilityException(
            employee=self.employees[0], exception_type=AvailabilityType.LEAVE,
            approval_status=ApprovalStatus.PENDING,
            start_date=date(2027, 1, 4), end_date=date(2027, 1, 5),
        )
        run = RosterRun(year=2027, month=1)
        self.session.add_all([request, run])
        self.session.commit()

        response = update_availability_status(
            request.id, AvailabilityStatusRequest(approval_status=ApprovalStatus.APPROVED),
            session=self.session,
        )

        self.assertEqual(request.approval_status, ApprovalStatus.APPROVED)
        self.assertEqual(response["affected_runs"], [{
            "roster_run_id": run.id, "affected_dates": ["2027-01-04", "2027-01-05"],
        }])

    def test_eligibility_cannot_be_added_without_required_skill(self):
        other_skill = Skill(code="NDR", name="Delivery resolution")
        self.session.add(other_skill)
        self.session.flush()
        self.employees[1].skills = [other_skill]
        self.session.commit()

        with self.assertRaises(HTTPException) as caught:
            change_eligibility(EligibilityChangeRequest(
                employee_code="EMP002", rule_id=self.rule.id,
                eligibility_type=EligibilityType.BACKUP, active=True,
            ), session=self.session)
        self.assertEqual(caught.exception.status_code, 422)

    def test_eligibility_can_be_removed_after_skill_is_changed(self):
        other_skill = Skill(code="NDR", name="Delivery resolution")
        self.session.add(other_skill)
        self.session.flush()
        self.employees[0].skills = [other_skill]
        self.session.commit()

        response = change_eligibility(EligibilityChangeRequest(
            employee_code="EMP001", rule_id=self.rule.id,
            eligibility_type=EligibilityType.PRIMARY, active=False,
        ), session=self.session)

        record = self.session.scalar(select(EmployeeEligibility).where(
            EmployeeEligibility.employee_id == self.employees[0].id,
            EmployeeEligibility.brand_process_rule_id == self.rule.id,
        ))
        self.assertEqual(response["status"], "saved")
        self.assertFalse(record.active)

    def test_brand_codes_are_compared_case_insensitively(self):
        with self.assertRaises(HTTPException) as caught:
            create_brand(BrandConfigurationRequest(code="brd001", name="Duplicate"), session=self.session)
        self.assertEqual(caught.exception.status_code, 409)

    def test_process_with_brand_rules_cannot_be_disabled(self):
        with self.assertRaises(HTTPException) as caught:
            update_process("CS", ProcessConfigurationRequest(
                code="CS", name="Customer support", description="Support",
                required_skill="CS", default_effort_hours=4, enabled=False,
            ), session=self.session)
        self.assertEqual(caught.exception.status_code, 409)

    def test_manual_assignment_edit_is_validated_and_updates_employee_day(self):
        run = RosterRun(year=2027, month=1)
        self.session.add(run)
        self.session.flush()
        work_day = date(2027, 1, 4)  # Monday
        assignment = RosterAssignment(
            roster_run=run, employee=self.employees[0], brand_process=self.rule,
            assignment_date=work_day, effort_hours=4, status=AssignmentStatus.WORKING,
        )
        self.session.add(assignment)
        for employee in self.employees:
            self.session.add(RosterEmployeeDay(
                roster_run=run, employee=employee, schedule_date=work_day,
                status=EmployeeRosterDayStatus.WORKING if employee.id == self.employees[0].id else EmployeeRosterDayStatus.UNSCHEDULED,
                assigned_effort_hours=4 if employee.id == self.employees[0].id else 0,
            ))
        self.session.add(EmployeeWeeklyOff(employee=self.employees[1], weekday=0))
        self.session.commit()

        result = edit_roster_assignment(
            run.id, assignment.id, AssignmentEditRequest(employee_code="EMP002"),
            session=self.session,
        )

        self.assertEqual(assignment.employee.employee_code, "EMP002")
        self.assertIn("weekly_off_conflict", {issue["code"] for issue in result["validation"]["issues"]})
        employee_day = self.session.scalar(select(RosterEmployeeDay).where(
            RosterEmployeeDay.roster_run_id == run.id,
            RosterEmployeeDay.employee_id == self.employees[1].id,
            RosterEmployeeDay.schedule_date == work_day,
        ))
        self.assertEqual(employee_day.assigned_effort_hours, 4)
        self.assertEqual(employee_day.status, EmployeeRosterDayStatus.OFF)

    def test_publish_rejects_uncovered_roster(self):
        run = RosterRun(year=2027, month=1)
        self.session.add(run)
        self.session.commit()

        with self.assertRaises(HTTPException) as caught:
            publish_roster(run.id, session=self.session)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(run.status.value, "draft")

    def test_publish_accepts_roster_after_independent_validation(self):
        run = RosterRun(year=2027, month=1)
        self.session.add(run)
        self.session.flush()
        self.session.add_all(RosterAssignment(
            roster_run=run, employee=self.employees[0], brand_process=self.rule,
            assignment_date=date(2027, 1, day), effort_hours=4,
            status=AssignmentStatus.WORKING,
        ) for day in (4, 11, 18, 25))
        self.session.commit()

        result = publish_roster(run.id, session=self.session)
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["validation"]["error_count"], 0)


if __name__ == "__main__":
    unittest.main()
