from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import (
    AvailabilityException,
    Brand,
    BrandPriority,
    BrandProcessSchedule,
    BrandProcessRule,
    CoverageRequirement,
    EligibilityType,
    Employee,
    EmployeeEligibility,
    EmployeePodMembership,
    EmployeePreference,
    EmployeeSkill,
    EmployeeWeeklyOff,
    EmployeeWorkContract,
    PreferenceType,
    Process,
    PublicHoliday,
    Pod,
    PodSchedule,
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
    ("Pod Alpha", "Customer support, retention, and cart recovery"),
    ("Pod Beta", "Delivery operations, NDR, and cross-trained coverage"),
]

BRAND_NAMES = [
    "Cedar & Coast", "Morrow Home", "Good Earth Pantry", "Northstar Living", "Little Loom",
    "BrightCart", "Juneberry Skin", "Harbor & Pine", "Kindred Kitchen", "Cloudberry Kids",
    "Saffron Trail", "Daybreak Goods", "Blue Fern Market", "Orbit Organics", "Paper Kite Co.",
]

PROCESS_SPECS = [
    ("CS", "Customer Support", "Respond to customer questions and issues.", "Customer support", 6.0),
    ("ACC", "Abandoned Cart Conversions", "Follow up with customers about abandoned carts.", "Cart recovery", 3.0),
    ("RET", "Retention", "Handle retention and repeat-customer activities.", "Customer retention", 3.0),
    ("NDR", "Non-Delivery Report Management", "Follow up on non-delivery reports.", "Delivery resolution", 6.0),
]

SINGLE_BRAND_PROCESS_SETS = [
    ["CS", "ACC"], ["NDR"], ["ACC"], ["RET"], ["CS"], ["RET"],
    ["ACC"], ["NDR"], ["CS"], ["RET"], ["ACC"], ["NDR"],
]


def _calendar_for(brand_index: int, process_index: int) -> tuple[list[int], bool]:
    """Seed three explicit calendar examples plus a practical weekday baseline."""
    if (brand_index, process_index) == (0, 0):  # 365 days, including public holidays
        return list(range(7)), True
    if (brand_index, process_index) == (1, 3):  # every day except public holidays
        return list(range(7)), False
    if (brand_index, process_index) == (2, 1):  # Monday-Saturday, public holidays off
        return list(range(6)), False
    # The remaining synthetic processes use a Monday-Friday baseline. This keeps
    # the sample workload within the capacity of its 30-person team while still
    # demonstrating all three client-specific calendar patterns above.
    return list(range(5)), False


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

        session.add_all([
            PodSchedule(pod_id=pods[0].id, shift_name="Day Window", shift_start="09:00", shift_end="18:00",
                        break_start="13:00", break_end="14:00", allocation_capacity_hours=6.0),
            PodSchedule(pod_id=pods[1].id, shift_name="Late Window", shift_start="13:00", shift_end="22:00",
                        break_start="17:00", break_end="18:00", allocation_capacity_hours=6.0),
        ])

        lead_indexes = {0, 15}
        employees = [
            Employee(
                employee_code=f"EMP{idx + 1:03d}",
                full_name=full_name,
                role_title="Pod Lead" if idx in lead_indexes else "Operations Associate",
                primary_pod=pods[idx % 2],
                status="active",
                is_pod_lead=idx in lead_indexes,
            )
            for idx, full_name in enumerate(EMPLOYEE_NAMES)
        ]
        session.add_all(employees)
        session.flush()
        for employee in employees:
            session.add(EmployeePodMembership(employee_id=employee.id, pod_id=employee.primary_pod_id))
        for pod, lead_index in zip(pods, [0, 15]):
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
        employee_skill_pairs.update({(14, "CS"), (15, "CS"), (16, "CS"), (15, "ACC")})
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
                    daily_capacity_hours=6.0,
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
                process_codes = ["CS", "ACC"]
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
            session.add(BrandPriority(
                brand_id=brand.id, priority=5 if brand_index in {0, 13} else 3,
                deadline_note="Urgent demo deadline" if brand_index in {0, 13} else "Routine demo deadline",
            ))
        session.flush()

        session.add_all(
            BrandProcessSchedule(brand_process_rule_id=rule.id, pod_id=pods[brand_index % 2].id)
            for (brand_index, _), rule in rules.items()
        )

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

        # BRD014 has a shared two-person CS requirement and a separate ACC owner.
        for employee_index, process_code in [(14, "CS"), (15, "CS"), (16, "CS"), (15, "ACC")]:
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
            (19, 0, "CS"), (19, 4, "CS"), (20, 8, "CS"),
            (21, 1, "NDR"), (23, 7, "NDR"), (25, 11, "NDR"),
            (29, 10, "ACC"),
            (22, 3, "RET"), (28, 5, "RET"), (27, 9, "RET"),
        ]
        for employee_index, brand_index, process_code in backup_eligibility:
            session.add(
                EmployeeEligibility(
                    employee=employees[employee_index],
                    brand_process=rules[(brand_index, process_code)],
                    eligibility_type=EligibilityType.BACKUP,
                )
            )

        # The cross-trained pool can serve either pod when skills, explicit
        # eligibility, time-window conflicts, and capacity allow it.
        membership_pairs = {(employee.id, employee.primary_pod_id) for employee in employees}
        cross_pod_indexes = {10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 23, 25, 27, 28, 29}
        for employee_index in cross_pod_indexes:
            employee = employees[employee_index]
            other_pod = pods[1 - employee.primary_pod_id]
            if (employee.id, other_pod.id) not in membership_pairs:
                session.add(EmployeePodMembership(employee_id=employee.id, pod_id=other_pod.id))
                membership_pairs.add((employee.id, other_pod.id))
        for employee_index, brand_index, process_code in backup_eligibility:
            employee = employees[employee_index]
            required_pod = pods[brand_index % 2]
            if (employee.id, required_pod.id) not in membership_pairs:
                session.add(EmployeePodMembership(employee_id=employee.id, pod_id=required_pod.id))
                membership_pairs.add((employee.id, required_pod.id))

        # Give each ordinary rule a small qualified backup pool so the sample
        # can demonstrate a complete roster without relaxing employee limits.
        # BRD012/NDR remains the deliberate no-qualified-backup example.
        # Track existing qualified records and spread additional backup links
        # across the cross-trained pool instead of concentrating them on one person.
        session.flush()
        existing_by_rule: dict[int, set[int]] = defaultdict(set)
        employees_by_skill: dict[str, set[int]] = defaultdict(set)
        for employee_index, skill_code in employee_skill_pairs:
            employees_by_skill[skill_code].add(employee_index)
        employee_index_by_id = {employee.id: index for index, employee in enumerate(employees)}
        employee_offs_by_id = {
            employee.id: {first_off.weekday for first_off in employee.weekly_offs}
            for employee in employees
        }
        backup_load: Counter[int] = Counter()
        for eligibility_record in session.scalars(select(EmployeeEligibility)).all():
            existing_by_rule[eligibility_record.brand_process_rule_id].add(eligibility_record.employee_id)
            if eligibility_record.eligibility_type == EligibilityType.BACKUP:
                backup_load[eligibility_record.employee_id] += 1
        for (brand_index, process_code), rule in rules.items():
            if brand_index == 11 and process_code == "NDR":
                continue
            qualified_existing = {
                employee_id for employee_id in existing_by_rule[rule.id]
                if employee_index_by_id[employee_id] in employees_by_skill[process_code]
            }
            target_qualified = 5 if brand_index == 13 and process_code == "CS" else 4
            needed = max(0, target_qualified - len(qualified_existing))
            if not needed:
                continue
            qualified_ids = [employees[index].id for index in employees_by_skill[process_code]
                             if employees[index].id not in existing_by_rule[rule.id]]
            qualified_pool = set(qualified_ids)
            for _ in range(needed):
                if not qualified_pool:
                    break
                def backup_score(candidate_id: int) -> tuple[int, int, int, int]:
                    team = qualified_existing | {candidate_id}
                    fully_off_operating_days = sum(
                        all(weekday in employee_offs_by_id[item_id] for item_id in team)
                        for weekday in rule.operating_weekdays
                    )
                    overlap_with_team = sum(
                        len(employee_offs_by_id[candidate_id] & employee_offs_by_id[item_id])
                        for item_id in qualified_existing
                    )
                    return (fully_off_operating_days, overlap_with_team,
                            backup_load[candidate_id], candidate_id)

                employee_id = min(qualified_pool, key=backup_score)
                session.add(EmployeeEligibility(
                    employee_id=employee_id,
                    brand_process=rule,
                    eligibility_type=EligibilityType.BACKUP,
                ))
                existing_by_rule[rule.id].add(employee_id)
                qualified_existing.add(employee_id)
                qualified_pool.remove(employee_id)
                backup_load[employee_id] += 1

        session.flush()
        for (brand_index, _), rule in rules.items():
            work_pod_id = pods[brand_index % 2].id
            for employee_id in existing_by_rule[rule.id]:
                pair = (employee_id, work_pod_id)
                if pair not in membership_pairs:
                    session.add(EmployeePodMembership(employee_id=employee_id, pod_id=work_pod_id))
                    membership_pairs.add(pair)

        session.commit()
        return database_counts(session)


def database_counts(session: Session) -> dict[str, int]:
    models = {
        "employees": Employee,
        "pods": Pod,
        "pod_schedules": PodSchedule,
        "employee_pod_memberships": EmployeePodMembership,
        "brands": Brand,
        "processes": Process,
        "brand_process_rules": BrandProcessRule,
        "brand_process_schedules": BrandProcessSchedule,
        "brand_priorities": BrandPriority,
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
