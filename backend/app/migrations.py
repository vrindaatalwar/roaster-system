"""Small, idempotent schema upgrades for the assessment app (no Alembic setup yet)."""
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .database import Base


_ADDITIVE_COLUMNS = {
    "brand_priorities": {
        "client_tier": "INTEGER NOT NULL DEFAULT 3",
        "deadline_date": "DATE",
    },
    "brand_process_rules": {"demand_units": "INTEGER"},
    "roster_runs": {"allocation_snapshot": "JSON"},
    "roster_generation_reports": {
        "hours_layer_status": "VARCHAR(30) NOT NULL DEFAULT 'disabled'",
        "solve_time_seconds": "FLOAT NOT NULL DEFAULT 0",
    },
    "roster_assignments": {"hours_units": "INTEGER NOT NULL DEFAULT 0"},
}


def upgrade_schema(engine: Engine) -> None:
    """Create new tables and add columns to databases created by earlier app versions."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in _ADDITIVE_COLUMNS.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, ddl_type in columns.items():
                if name not in existing:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {ddl_type}'))
    # SQLAlchemy create_all installs these checks for fresh databases. Existing
    # databases need equivalent checks after their new columns are added.
    if engine.dialect.name == "postgresql":
        checks = {
            "brand_priorities": ("ck_brand_client_tier_range", "client_tier BETWEEN 1 AND 5"),
            "brand_process_rules": ("ck_brand_process_demand_units", "demand_units IS NULL OR demand_units >= 0"),
        }
        for table, (name, expression) in checks.items():
            existing = {row["name"] for row in inspect(engine).get_check_constraints(table)}
            if name not in existing:
                with engine.begin() as connection:
                    connection.execute(text(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" CHECK ({expression})'))
    elif engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS ck_brand_priority_client_tier_insert
                BEFORE INSERT ON brand_priorities WHEN NEW.client_tier < 1 OR NEW.client_tier > 5
                BEGIN SELECT RAISE(ABORT, 'client_tier must be between 1 and 5'); END"""))
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS ck_brand_priority_client_tier_update
                BEFORE UPDATE OF client_tier ON brand_priorities WHEN NEW.client_tier < 1 OR NEW.client_tier > 5
                BEGIN SELECT RAISE(ABORT, 'client_tier must be between 1 and 5'); END"""))
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS ck_brand_process_demand_insert
                BEFORE INSERT ON brand_process_rules WHEN NEW.demand_units IS NOT NULL AND NEW.demand_units < 0
                BEGIN SELECT RAISE(ABORT, 'demand_units must be nonnegative'); END"""))
            connection.execute(text("""CREATE TRIGGER IF NOT EXISTS ck_brand_process_demand_update
                BEFORE UPDATE OF demand_units ON brand_process_rules WHEN NEW.demand_units IS NOT NULL AND NEW.demand_units < 0
                BEGIN SELECT RAISE(ABORT, 'demand_units must be nonnegative'); END"""))
    _upgrade_cs_weekly_hours(engine)


def _upgrade_cs_weekly_hours(engine: Engine) -> None:
    """Raise the weekly contract ceiling and migrate CS-skilled employees once."""
    inspector = inspect(engine)
    has_cs_data = all(inspector.has_table(table) for table in (
        "employee_work_contracts", "employee_skills", "skills",
    ))
    if inspector.has_table("employee_work_contracts"):
        checks = inspector.get_check_constraints("employee_work_contracts")
        old_cap_check = next((row for row in checks
                              if "max_hours_per_week" in (row.get("sqltext") or "").lower()
                              and "40" in (row.get("sqltext") or "")), None)
        if old_cap_check and engine.dialect.name == "postgresql":
            constraint_name = old_cap_check.get("name")
            with engine.begin() as connection:
                if constraint_name:
                    connection.execute(text(
                        f'ALTER TABLE employee_work_contracts DROP CONSTRAINT "{constraint_name}"'
                    ))
                connection.execute(text("""ALTER TABLE employee_work_contracts
                    ADD CONSTRAINT ck_max_hours_per_week CHECK (max_hours_per_week BETWEEN 1 AND 45)"""))
        elif old_cap_check and engine.dialect.name == "sqlite":
            # SQLite cannot alter a CHECK in place; this table has no inbound FKs.
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS employee_work_contracts__new"))
                connection.execute(text("""CREATE TABLE employee_work_contracts__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    daily_capacity_hours FLOAT NOT NULL,
                    max_days_per_week INTEGER NOT NULL,
                    max_hours_per_week FLOAT NOT NULL,
                    minimum_days_off_per_week INTEGER NOT NULL,
                    UNIQUE (employee_id),
                    CONSTRAINT ck_daily_capacity CHECK (daily_capacity_hours > 0 AND daily_capacity_hours <= 8),
                    CONSTRAINT ck_max_days_per_week CHECK (max_days_per_week BETWEEN 1 AND 5),
                    CONSTRAINT ck_max_hours_per_week CHECK (max_hours_per_week BETWEEN 1 AND 45)
                )"""))
                connection.execute(text("""INSERT INTO employee_work_contracts__new
                    (id, employee_id, daily_capacity_hours, max_days_per_week,
                     max_hours_per_week, minimum_days_off_per_week)
                    SELECT id, employee_id, daily_capacity_hours, max_days_per_week,
                           max_hours_per_week, minimum_days_off_per_week
                    FROM employee_work_contracts"""))
                connection.execute(text("DROP TABLE employee_work_contracts"))
                connection.execute(text(
                    "ALTER TABLE employee_work_contracts__new RENAME TO employee_work_contracts"
                ))

    with engine.begin() as connection:
        connection.execute(text("""CREATE TABLE IF NOT EXISTS app_schema_migrations (
            version VARCHAR(100) PRIMARY KEY
        )"""))
        applied = connection.execute(text(
            "SELECT version FROM app_schema_migrations WHERE version = 'cs_weekly_hours_45'"
        )).first()
        if applied:
            return
        if has_cs_data:
            connection.execute(text("""UPDATE employee_work_contracts
                SET max_hours_per_week = 45
                WHERE employee_id IN (
                    SELECT employee_skills.employee_id
                    FROM employee_skills JOIN skills ON skills.id = employee_skills.skill_id
                    WHERE skills.code = 'CS'
                )"""))
        connection.execute(text(
            "INSERT INTO app_schema_migrations (version) VALUES ('cs_weekly_hours_45')"
        ))
