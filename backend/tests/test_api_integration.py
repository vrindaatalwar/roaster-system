import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_session
import app.main as main
from app.main import app
from app.models import Employee, EmployeeWorkContract, Pod


class ApiIntegrationTests(unittest.TestCase):
    """Exercise the public HTTP boundary, including its demo-role middleware."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as session:
            pod = Pod(name="Test Pod", specialization="Test")
            session.add(pod)
            session.flush()
            employee = Employee(
                employee_code="EMP001",
                full_name="Test Employee",
                role_title="Associate",
                primary_pod_id=pod.id,
                status="active",
                is_pod_lead=False,
            )
            session.add(employee)
            session.flush()
            session.add(EmployeeWorkContract(
                employee_id=employee.id,
                daily_capacity_hours=8,
                max_days_per_week=5,
                max_hours_per_week=40,
                minimum_days_off_per_week=2,
            ))
            session.commit()

        def override_session():
            with self.factory() as session:
                yield session

        self.previous_session_local = main.SessionLocal
        self.previous_dependency = app.dependency_overrides.get(get_session)
        main.SessionLocal = self.factory
        app.dependency_overrides[get_session] = override_session
        # Do not enter TestClient as a context manager: application startup runs
        # production schema upgrades and an external holiday-feed refresh.
        self.client = TestClient(app)

    def tearDown(self):
        if self.previous_dependency is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = self.previous_dependency
        main.SessionLocal = self.previous_session_local
        self.engine.dispose()

    def test_health_is_public_and_api_requires_a_valid_demo_role(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/seed-summary").status_code, 401)
        self.assertEqual(self.client.get(
            "/api/v1/seed-summary", headers={"X-App-Role": "owner"}
        ).status_code, 401)

    def test_browser_preflight_allows_demo_role_headers_from_local_frontend(self):
        response = self.client.options("/api/v1/seed-summary", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-app-role,x-employee-code",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")

    def test_manager_and_employee_receive_expected_summary_scope(self):
        manager = self.client.get(
            "/api/v1/seed-summary", headers={"X-App-Role": "manager"}
        )
        self.assertEqual(manager.status_code, 200, manager.text)
        self.assertEqual([item["code"] for item in manager.json()["employees"]], ["EMP001"])

        employee = self.client.get("/api/v1/seed-summary", headers={
            "X-App-Role": "employee", "X-Employee-Code": "EMP001",
        })
        self.assertEqual(employee.status_code, 200, employee.text)
        self.assertEqual([item["code"] for item in employee.json()["employees"]], ["EMP001"])
        self.assertEqual(employee.json()["brands"], [])
        self.assertEqual(employee.json()["process_catalog"], [])

    def test_staff_role_requires_an_active_matching_identity(self):
        missing = self.client.get(
            "/api/v1/seed-summary", headers={"X-App-Role": "employee"}
        )
        self.assertEqual(missing.status_code, 401)

        unknown = self.client.get("/api/v1/seed-summary", headers={
            "X-App-Role": "employee", "X-Employee-Code": "MISSING",
        })
        self.assertEqual(unknown.status_code, 403)

        not_a_pod_lead = self.client.get("/api/v1/seed-summary", headers={
            "X-App-Role": "pod_lead", "X-Employee-Code": "EMP001",
        })
        self.assertEqual(not_a_pod_lead.status_code, 403)

    def test_non_manager_roles_cannot_write_through_any_http_verb(self):
        mutation_requests = [
            ("POST", "/api/v1/employees"),
            ("PUT", "/api/v1/allocation-settings"),
            ("PATCH", "/api/v1/employees/EMP001"),
            ("DELETE", "/api/v1/public-holidays/1"),
        ]
        for role in ("employee", "pod_lead"):
            for method, path in mutation_requests:
                with self.subTest(role=role, method=method, path=path):
                    response = self.client.request(
                        method,
                        path,
                        headers={"X-App-Role": role},
                    )
                    self.assertEqual(response.status_code, 403, response.text)

        # Managers still reach normal endpoint validation and the demo flows.
        manager_response = self.client.post(
            "/api/v1/employees",
            headers={"X-App-Role": "manager"},
            json={},
        )
        self.assertEqual(manager_response.status_code, 422)

    def test_demo_identity_list_only_contains_active_employees(self):
        with self.factory() as session:
            pod = session.query(Pod).one()
            inactive = Employee(
                employee_code="EMP002",
                full_name="Inactive Employee",
                role_title="Associate",
                primary_pod_id=pod.id,
                status="inactive",
                is_pod_lead=False,
            )
            session.add(inactive)
            session.commit()

        response = self.client.get("/api/v1/demo-identities")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([item["employee_code"] for item in response.json()], ["EMP001"])


if __name__ == "__main__":
    unittest.main()
