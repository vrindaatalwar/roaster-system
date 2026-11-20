from __future__ import annotations

from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import (
    AvailabilityException,
    Brand,
    BrandProcessRule,
    CoverageRequirement,
    EligibilityType,
    Employee,
    EmployeeEligibility,
    EmployeePreference,
    EmployeeSkill,
    EmployeeWeeklyOff,
    EmployeeWorkContract,
    PreferenceType,
    Process,
    PublicHoliday,
    Pod,
    Skill,
)


EMPLOYEE_NAMES = [
    "Aarav Mehta", "Aditi Shah", "Akhil Nair", "Ananya Rao", "Arjun Iyer", "Diya Menon",
    "Ishaan Das", "Kavya Pillai", "Meera Joshi", "Neel Kapoor", "Nisha Reddy", "Rohan Kulkarni",
    "Sana Qureshi", "Dev Malhotra", "Ira Banerjee", "Kabir Sethi", "Leela Thomas", "Mira Sood",
    "Nikhil Bose", "Pooja Anand", "Rahul Verma", "Saanvi Dutta", "Tara Bhat", "Vikram Rao",
    "Yash Khanna", "Zoya Khan", "Advik Sen", "Kiara Rao", "Manav Gupta", "Reva Nair",
]

POD_SPECS = [
    ("Pod North", "Customer support and retention"),
    ("Pod South", "Customer support and cart recovery"),
    ("Pod East", "Delivery operations and NDR"),
    ("Pod West", "Cross-trained brand operations"),
    ("Pod Central", "Shared backup coverage"),
]

BRAND_NAMES = [
    "Cedar & Coast", "Morrow Home", "Good Earth Pantry", "Northstar Living", "Little Loom",
    "BrightCart", "Juneberry Skin", "Harbor & Pine", "Kindred Kitchen", "Cloudberry Kids",
    "Saffron Trail", "Daybreak Goods", "Blue Fern Market", "Orbit Organics", "Paper Kite Co.",
]

PROCESS_SPECS = [
    ("CS", "Customer Support", "Respond to customer questions and issues.", "Customer support", 8.0),
    ("ACC", "Abandoned Cart Conversions", "Follow up with customers about abandoned carts.", "Cart recovery", 4.0),
    ("RET", "Retention", "Handle retention and repeat-customer activities.", "Customer retention", 4.0),
    ("NDR", "Non-Delivery Report Management", "Follow up on non-delivery reports.", "Delivery resolution", 8.0),
]

SINGLE_BRAND_PROCESS_SETS = [
    ["CS", "ACC", "RET"], ["CS", "NDR"], ["CS", "ACC"], ["CS", "RET"],
    ["CS", "ACC", "NDR"], ["CS", "RET"], ["CS", "ACC"], ["CS", "NDR", "RET"],
    ["CS", "ACC"], ["CS", "RET", "NDR"], ["CS", "ACC", "RET"], ["NDR"],
]


def _calendar_for(brand_index: int, process_index: int) -> tuple[list[int], bool]:
    """Return one of the three calendars required by the project brief."""
    template = (brand_index + process_index) % 3
    if template == 0:  # 365 days, including public holidays
        return list(range(7)), True
    if template == 1:  # every day except public holidays
        return list(range(7)), False
    return list(range(6)), False  # Monday-Saturday, public holidays off


def seed_database() -> dict[str, int]:
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        if session.scalar(select(Employee.id).limit(1)) is not None:
            # Remove fixed absence scenarios from old demo seeds; keep manager-entered records.
            session.execute(delete(AvailabilityException).where(
                AvailabilityException.note.in_([
                    "Stage 1 three-person shared-process absence fixture.",
                    "Stage 1 sole-owner, no-qualified-backup fixture.",
                    "Synthetic approved leave example.",
                    "Synthetic pending leave example.",
                    "Synthetic approved comp-off example.",
                ])
            ))
            # Ensure the existing demo database has an explicit cross-brand backup example.
            cross_brand_employee = session.scalar(
                select(Employee).where(Employee.employee_code == "EMP020")
            )
            cross_brand_rule = session.scalar(
                select(BrandProcessRule)
                .join(BrandProcessRule.brand)
                .join(BrandProcessRule.process)
                .where(Brand.code == "BRD002", Process.code == "CS")
            )
            if cross_brand_employee is not None and cross_brand_rule is not None:
                already_eligible = session.scalar(
                    select(EmployeeEligibility.id).where(
                        EmployeeEligibility.employee_id == cross_brand_employee.id,
                        EmployeeEligibility.brand_process_rule_id == cross_brand_rule.id,
                    )
                )
                if already_eligible is None:
                    session.add(EmployeeEligibility(
                        employee=cross_brand_employee,
                        brand_process=cross_brand_rule,
                        eligibility_type=EligibilityType.BACKUP,
                    ))
            session.commit()
            return database_counts(session)

        skills = {
            code: Skill(code=code, name=skill_name)
            for code, _, _, skill_name, _ in PROCESS_SPECS
        }
        session.add_all(skills.values())
        session.flush()

        pods = [Pod(name=name, specialization=specialization) for name, specialization in POD_SPECS]
        session.add_all(pods)
        session.flush()

        lead_indexes = {0, 6, 12, 18, 24}
        employees = [
            Employee(
                employee_code=f"EMP{idx + 1:03d}",
                full_name=full_name,
                role_title="Pod Lead" if idx in lead_indexes else "Operations Associate",
                primary_pod=pods[idx // 6],
                status="active",
                is_pod_lead=idx in lead_indexes,
            )
            for idx, full_name in enumerate(EMPLOYEE_NAMES)
        ]
        session.add_all(employees)
        session.flush()
        for pod, lead_index in zip(pods, [0, 6, 12, 18, 24]):
            pod.lead = employees[lead_index]

        processes: dict[str, Process] = {}
        for code, name, description, _, default_hours in PROCESS_SPECS:
            processes[code] = Process(
                code=code,
                name=name,
                description=description,
                required_skill=skills[code],
                default_effort_hours=default_hours,
                enabled=True,
            )
        session.add_all(processes.values())
        session.flush()

        # Skill assignments match primary work, with extra skills for shared/back-up coverage.
        employee_skill_pairs: set[tuple[int, str]] = set()
        for brand_index in range(12):
            for process_code in SINGLE_BRAND_PROCESS_SETS[brand_index]:
                employee_skill_pairs.add((brand_index, process_code))
        employee_skill_pairs.update({(12, "CS"), (13, "ACC")})
        employee_skill_pairs.update({(14, "CS"), (15, "CS"), (16, "CS"), (14, "NDR"), (15, "ACC"), (16, "RET")})
        employee_skill_pairs.update({(17, "RET"), (18, "RET")})
        backup_skill_patterns = [
            {"CS", "ACC"}, {"CS", "RET"}, {"NDR", "CS"}, {"ACC", "RET"},
            {"NDR", "RET"}, {"CS", "NDR"}, {"ACC", "NDR"}, {"CS", "RET"},
            {"ACC", "CS"}, {"RET", "NDR"}, {"CS", "ACC", "NDR"},
        ]
        for offset, codes in enumerate(backup_skill_patterns):
            employee_skill_pairs.update((19 + offset, code) for code in codes)
        session.add_all(
            EmployeeSkill(employee_id=employees[index].id, skill_id=skills[code].id)
            for index, code in sorted(employee_skill_pairs)
        )

        # Contract defaults are configurable; the seed uses the documented five-day pattern.
        for employee in employees:
            session.add(
                EmployeeWorkContract(
                    employee=employee,
                    daily_capacity_hours=8.0,
                    max_days_per_week=5,
                    max_hours_per_week=40.0,
                    minimum_days_off_per_week=2,
                )
            )
            first_off = employee.id % 7
            second_off = (employee.id + 3) % 7
            if second_off == first_off:
                second_off = (first_off + 1) % 7
            session.add_all(
                [
                    EmployeeWeeklyOff(employee=employee, weekday=first_off),
                    EmployeeWeeklyOff(employee=employee, weekday=second_off),
                ]
            )

        for employee_index in [2, 7, 14, 21, 28]:
            session.add(
                EmployeePreference(
                    employee=employees[employee_index],
                    weekday=6,
                    preference_type=PreferenceType.PREFERRED,
                    note="Prefers Sunday off when coverage permits.",
                )
            )
        for employee_index in [4, 11, 19, 25]:
            session.add(
                EmployeePreference(
                    employee=employees[employee_index],
                    weekday=5,
                    preference_type=PreferenceType.UNPREFERRED,
                    note="Prefers not to work Saturdays when coverage permits.",
                )
            )

        session.add_all(
            [
                PublicHoliday(name="Demo Holiday A", holiday_date=date(2026, 1, 1)),
                PublicHoliday(name="Demo Holiday B", holiday_date=date(2026, 5, 1)),
                PublicHoliday(name="Demo Holiday C", holiday_date=date(2026, 12, 25)),
            ]
        )

        brands = [
            Brand(code=f"BRD{index + 1:03d}", name=name, is_active=True, is_demo_data=True)
            for index, name in enumerate(BRAND_NAMES)
        ]
        session.add_all(brands)
        session.flush()

        rules: dict[tuple[int, str], BrandProcessRule] = {}
        for brand_index, brand in enumerate(brands):
            if brand_index < 12:
                process_codes = SINGLE_BRAND_PROCESS_SETS[brand_index]
            elif brand_index == 12:
                process_codes = ["CS", "ACC"]
            elif brand_index == 13:
                process_codes = ["CS", "ACC", "NDR", "RET"]
            else:
                process_codes = ["RET"]

            for process_index, (process_code, _, _, _, _) in enumerate(PROCESS_SPECS):
                if process_code not in process_codes:
                    continue
                weekdays, holiday_open = _calendar_for(brand_index, process_index)
                effort = processes[process_code].default_effort_hours
                if brand_index < 12 and process_code in {"ACC", "RET"}:
                    effort = 4.0
                rule = BrandProcessRule(
                    brand=brand,
                    process=processes[process_code],
                    required_skill=skills[process_code],
                    operating_weekdays=weekdays,
                    operate_on_public_holidays=holiday_open,
                    effort_hours_per_employee=effort,
                    can_share_same_day=(process_code in {"ACC", "RET"}
                                         or (brand_index == 13 and process_code == "CS")),
                    continuous_presence=False,
                    notes="Synthetic sample rule; not a statement of actual DataStraw policy.",
                )
                rules[(brand_index, process_code)] = rule
                session.add(rule)
        session.flush()

        for (brand_index, process_code), rule in rules.items():
            minimum = 2 if brand_index == 13 and process_code == "CS" else 1
            for weekday in range(7):
                session.add(
                    CoverageRequirement(
                        brand_process=rule,
                        weekday=weekday,
                        minimum_staff=minimum if weekday in rule.operating_weekdays else 0,
                    )
                )

        # Twelve single-primary-owner brands; the BRD012/NDR owner has no backup.
        for brand_index in range(12):
            for process_code in SINGLE_BRAND_PROCESS_SETS[brand_index]:
                session.add(
                    EmployeeEligibility(
                        employee=employees[brand_index],
                        brand_process=rules[(brand_index, process_code)],
                        eligibility_type=EligibilityType.PRIMARY,
                    )
                )

        # BRD013 demonstrates separate process ownership.
        for employee_index, process_code in [(12, "CS"), (13, "ACC")]:
            session.add(
                EmployeeEligibility(
                    employee=employees[employee_index],
                    brand_process=rules[(12, process_code)],
                    eligibility_type=EligibilityType.PRIMARY,
                )
            )

        # BRD014 has a shared CS process with minimum coverage of two and other separate roles.
        for employee_index, process_code in [
            (14, "CS"), (15, "CS"), (16, "CS"),
            (14, "NDR"), (15, "ACC"), (16, "RET"),
        ]:
            session.add(
                EmployeeEligibility(
                    employee=employees[employee_index],
                    brand_process=rules[(13, process_code)],
                    eligibility_type=EligibilityType.PRIMARY,
                )
            )

        # BRD015 has two primary employees sharing Retention.
        for employee_index in [17, 18]:
            session.add(
                EmployeeEligibility(
                    employee=employees[employee_index],
                    brand_process=rules[(14, "RET")],
                    eligibility_type=EligibilityType.PRIMARY,
                )
            )

        # Explicit backup pool; EMP020 is a cross-pod backup for BRD001 Customer Support.
        backup_eligibility = [
            (19, 0, "CS"), (19, 1, "CS"), (20, 1, "CS"), (21, 4, "NDR"), (22, 3, "RET"),
            (23, 4, "NDR"), (24, 5, "CS"), (25, 7, "NDR"), (26, 8, "ACC"),
            (27, 9, "CS"), (28, 9, "RET"), (29, 10, "CS"),
        ]
        for employee_index, brand_index, process_code in backup_eligibility:
            session.add(
                EmployeeEligibility(
                    employee=employees[employee_index],
                    brand_process=rules[(brand_index, process_code)],
                    eligibility_type=EligibilityType.BACKUP,
                )
            )

        session.commit()
        return database_counts(session)


def database_counts(session: Session) -> dict[str, int]:
    models = {
        "employees": Employee,
        "pods": Pod,
        "brands": Brand,
        "processes": Process,
        "brand_process_rules": BrandProcessRule,
        "coverage_requirements": CoverageRequirement,
        "employee_eligibility": EmployeeEligibility,
        "employee_skills": EmployeeSkill,
        "availability_exceptions": AvailabilityException,
        "public_holidays": PublicHoliday,
    }
    return {
        label: session.scalar(select(func.count()).select_from(model)) or 0
        for label, model in models.items()
    }


if __name__ == "__main__":
    counts = seed_database()
    print("Stage 1 seed complete:")
    for name, count in counts.items():
        print(f"- {name}: {count}")
