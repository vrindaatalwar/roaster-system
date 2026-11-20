import unittest
from datetime import date

from pydantic import ValidationError

from fastapi import HTTPException

from app.main import (
    AvailabilityChangeRequest,
    BrandProcessRuleChangeRequest,
    validate_staffing_limit,
)


class ManagerChangeGuardrailTests(unittest.TestCase):
    def test_valid_availability_change_is_accepted(self):
        request = AvailabilityChangeRequest(
            employee_code="EMP001",
            exception_type="leave",
            approval_status="approved",
            start_date=date(2026, 11, 14),
            end_date=date(2026, 11, 15),
        )
        self.assertEqual(request.employee_code, "EMP001")

    def test_reversed_availability_range_is_rejected(self):
        with self.assertRaises(ValidationError):
            AvailabilityChangeRequest(
                employee_code="EMP001",
                exception_type="leave",
                approval_status="approved",
                start_date=date(2026, 11, 15),
                end_date=date(2026, 11, 14),
            )

    def test_oversized_note_is_rejected_before_database_write(self):
        with self.assertRaises(ValidationError):
            AvailabilityChangeRequest(
                employee_code="EMP001",
                exception_type="leave",
                approval_status="approved",
                start_date=date(2026, 11, 14),
                end_date=date(2026, 11, 14),
                note="x" * 201,
            )

    def test_invalid_employee_code_lengths_are_rejected(self):
        for employee_code in ("", " " * 4, "E" * 21):
            with self.subTest(employee_code_length=len(employee_code)):
                with self.assertRaises(ValidationError):
                    AvailabilityChangeRequest(
                        employee_code=employee_code,
                        exception_type="leave",
                        approval_status="approved",
                        start_date=date(2026, 11, 14),
                        end_date=date(2026, 11, 14),
                    )

    def test_empty_weekday_list_is_rejected(self):
        with self.assertRaises(ValidationError):
            BrandProcessRuleChangeRequest(operating_weekdays=[])

    def test_duplicate_or_out_of_range_weekdays_are_rejected(self):
        for days in ([0, 0], [-1], [7]):
            with self.subTest(days=days):
                with self.assertRaises(ValidationError):
                    BrandProcessRuleChangeRequest(operating_weekdays=days)

    def test_fractional_staffing_and_weekday_values_are_rejected(self):
        for payload in (
            {"minimum_staff": 1.5, "weekday": 0},
            {"minimum_staff": 1, "weekday": 1.5},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    BrandProcessRuleChangeRequest(**payload)

    def test_null_required_rule_settings_are_rejected(self):
        for payload in (
            {"operating_weekdays": None},
            {"operate_on_public_holidays": None},
            {"can_share_same_day": None},
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    BrandProcessRuleChangeRequest(**payload)

    def test_empty_rule_change_and_weekday_only_change_are_rejected(self):
        for payload in ({}, {"weekday": 2}):
            with self.subTest(payload=payload):
                with self.assertRaises(ValidationError):
                    BrandProcessRuleChangeRequest(**payload)

    def test_staffing_limit_cannot_exceed_active_team_size(self):
        validate_staffing_limit(30, None, 30)
        for minimum, maximum in ((31, None), (None, 10**12)):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(HTTPException) as error:
                    validate_staffing_limit(minimum, maximum, 30)
                self.assertEqual(error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
