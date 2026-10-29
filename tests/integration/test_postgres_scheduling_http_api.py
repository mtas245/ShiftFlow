import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from app.application.register_company import AuthenticatedUser
from app.domain.company_membership import CompanyRole
from app.infrastructure.persistence.models import (
    AvailabilityRecord,
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
    ShiftAssignmentRecord,
    ShiftRecord,
    UserRecord,
)
from app.interfaces.auth import get_authenticated_user
from app.interfaces.main import create_app


pytestmark = pytest.mark.postgres


def _test_database_url():
    raw_url = os.environ.get("SHIFTFLOW_TEST_DATABASE_URL")
    if not raw_url:
        raise RuntimeError(
            "SHIFTFLOW_TEST_DATABASE_URL must target disposable PostgreSQL"
        )
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "::1"}
        or url.database != "shiftflow_test"
        or url.username != "shiftflow_test_user"
    ):
        raise RuntimeError(
            "Refusing PostgreSQL URL outside the dedicated local test database"
        )
    return raw_url


@pytest.fixture
def scheduling_api():
    engine = create_engine(_test_database_url(), pool_pre_ping=True)
    safe_database = False
    try:
        with engine.connect() as connection:
            database, username = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        if database != "shiftflow_test" or username != "shiftflow_test_user":
            pytest.fail(
                "Refusing connection not authenticated to the dedicated test database"
            )
        safe_database = True
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        app = create_app(engine=engine)
        client = TestClient(app, raise_server_exceptions=False)
        yield client, engine, app
        client.close()
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
        engine.dispose()


def _authenticate_as(app, user_id: str) -> None:
    app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
        user_id
    )


def _add_company(engine, company_id: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            CompanyRecord.__table__.insert().values(
                id=company_id,
                company_name=company_id,
                business_email=f"{company_id}@example.test",
                business_phone="+41791234567",
                business_address="1 Test Street",
                industry="Hospitality",
                joining_code=f"{ord(company_id[-1]):08d}",
            )
        )


def _add_member(
    engine,
    company_id: str,
    user_id: str,
    role: CompanyRole,
    *,
    display_name: str | None = None,
) -> str:
    with engine.begin() as connection:
        if connection.scalar(
            select(UserRecord.id).where(UserRecord.id == user_id)
        ) is None:
            connection.execute(
                UserRecord.__table__.insert().values(
                    id=user_id,
                    email=f"{user_id}@example.test",
                    password_hash="not-used-by-scheduling-tests",
                )
            )
        connection.execute(
            CompanyMembershipRecord.__table__.insert().values(
                company_id=company_id,
                user_id=user_id,
                role=role.value,
                account_user_id=user_id,
                display_name=display_name or user_id,
            )
        )
        return connection.scalar(
            select(CompanyMembershipRecord.public_id).where(
                CompanyMembershipRecord.company_id == company_id,
                CompanyMembershipRecord.user_id == user_id,
            )
        )


def _add_legacy_member(engine, company_id: str, user_id: str, role: CompanyRole):
    with engine.begin() as connection:
        connection.execute(
            CompanyMembershipRecord.__table__.insert().values(
                company_id=company_id,
                user_id=user_id,
                role=role.value,
                account_user_id=None,
                display_name=None,
            )
        )


def _availability_payload(start_at="2026-10-28T08:00:00+02:00", end_at="2026-10-28T17:00:00+02:00", **extra):
    return {"start_at": start_at, "end_at": end_at, **extra}


def _shift_payload(start_at="2026-10-28T09:00:00+02:00", end_at="2026-10-28T13:00:00+02:00", **extra):
    return {"start_at": start_at, "end_at": end_at, **extra}


def _list_page(client, path, **params):
    query = {
        "from": "2026-10-27T00:00:00Z",
        "to": "2026-10-30T00:00:00Z",
        **params,
    }
    return client.get(path, params=query)


def _db_time(value):
    return datetime.fromisoformat(value)


def _create_shift(client, company_id, payload=None):
    return client.post(
        f"/companies/{company_id}/shifts",
        json=payload or _shift_payload(),
    )


def _install_reject_insert_trigger(engine, table_name):
    suffix = uuid4().hex
    trigger_name = f"reject_insert_{suffix}"
    function_name = f"reject_insert_{suffix}"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql "
            "AS $$ BEGIN RAISE EXCEPTION 'sensitive persistence detail'; END $$"
        )
        connection.exec_driver_sql(
            f"CREATE TRIGGER {trigger_name} BEFORE INSERT ON {table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
        )
    return trigger_name, function_name


def _remove_reject_insert_trigger(engine, table_name, trigger_name, function_name):
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}"
        )
        connection.exec_driver_sql(f"DROP FUNCTION IF EXISTS {function_name}()")


def test_employee_submits_availability_and_receives_utc(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    employee_membership_id = _add_member(
        engine,
        "company-a",
        "employee-a",
        CompanyRole.EMPLOYEE,
        display_name="Cafe Team Member",
    )
    _authenticate_as(app, "employee-a")

    response = client.post(
        "/companies/company-a/availability",
        json=_availability_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["company_id"] == "company-a"
    assert body["employee_id"] == "employee-a"
    assert body["start_at"] == "2026-10-28T06:00:00+00:00"
    assert body["end_at"] == "2026-10-28T15:00:00+00:00"
    with engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(AvailabilityRecord)
        ) == 1


def test_employee_updates_availability_in_place(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _authenticate_as(app, "employee-a")
    created = client.post(
        "/companies/company-a/availability", json=_availability_payload()
    ).json()

    response = client.put(
        f"/companies/company-a/availability/{created['id']}",
        json=_availability_payload(
            "2026-10-28T10:00:00Z",
            "2026-10-28T18:00:00Z",
        ),
    )

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["start_at"] == "2026-10-28T10:00:00+00:00"
    with engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(AvailabilityRecord)
        ) == 1


def test_employee_reads_only_own_availability(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-a", "employee-b", CompanyRole.EMPLOYEE)
    _authenticate_as(app, "employee-a")
    response = client.post(
        "/companies/company-a/availability", json=_availability_payload()
    )
    assert response.status_code == 201
    _authenticate_as(app, "employee-b")
    assert client.post(
        "/companies/company-a/availability",
        json=_availability_payload("2026-10-29T08:00:00Z", "2026-10-29T09:00:00Z"),
    ).status_code == 201
    _authenticate_as(app, "employee-a")

    response = _list_page(client, "/companies/company-a/availability/mine")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["employee_id"] == "employee-a"


def test_administrator_reads_company_availability(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _authenticate_as(app, "employee-a")
    assert client.post(
        "/companies/company-a/availability", json=_availability_payload()
    ).status_code == 201
    _authenticate_as(app, "admin-a")

    response = _list_page(client, "/companies/company-a/availability")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["employee_id"] == "employee-a"


@pytest.mark.parametrize(
    "payload",
    [
        _availability_payload("2026-10-28T08:00:00", "2026-10-28T09:00:00Z"),
        _availability_payload("not-a-date", "2026-10-28T09:00:00Z"),
        _availability_payload("2026-10-28T09:00:00Z", "2026-10-28T09:00:00Z"),
    ],
)
def test_invalid_availability_timestamp_returns_safe_400(scheduling_api, payload):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _authenticate_as(app, "employee-a")

    response = client.post("/companies/company-a/availability", json=payload)

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_AVAILABILITY"}}
    with engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(AvailabilityRecord)
        ) == 0


def test_availability_overlap_is_conflict_and_extra_identity_is_rejected(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _authenticate_as(app, "employee-a")
    assert client.post(
        "/companies/company-a/availability", json=_availability_payload()
    ).status_code == 201

    overlap = client.post(
        "/companies/company-a/availability",
        json=_availability_payload(
            "2026-10-28T12:00:00Z",
            "2026-10-28T18:00:00Z",
        ),
    )
    spoofed = client.post(
        "/companies/company-a/availability",
        json=_availability_payload(
            "2026-10-29T08:00:00Z",
            "2026-10-29T09:00:00Z",
            employee_id="employee-b",
        ),
    )

    assert overlap.status_code == 409
    assert overlap.json() == {"error": {"code": "AVAILABILITY_OVERLAP"}}
    assert spoofed.status_code == 400
    assert spoofed.json() == {"error": {"code": "INVALID_REQUEST"}}


def test_availability_reads_and_writes_enforce_tenant_and_role(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_company(engine, "company-b")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-a", "employee-b", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(engine, "company-b", "admin-b", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "employee-a")
    created = client.post(
        "/companies/company-a/availability", json=_availability_payload()
    ).json()

    employee_company_read = _list_page(
        client, "/companies/company-a/availability"
    )
    cross_tenant_read = _list_page(
        client, "/companies/company-b/availability"
    )
    cross_tenant_update = client.put(
        f"/companies/company-b/availability/{created['id']}",
        json=_availability_payload(),
    )

    assert employee_company_read.status_code == 404
    assert cross_tenant_read.status_code == 404
    assert cross_tenant_update.status_code == 404


def test_missing_authentication_is_rejected_for_scheduling_routes(scheduling_api):
    client, engine, _ = scheduling_api
    _add_company(engine, "company-a")

    responses = [
        _list_page(client, "/companies/company-a/availability/mine"),
        client.post(
            "/companies/company-a/availability",
            json=_availability_payload(),
        ),
        _create_shift(client, "company-a"),
        _list_page(client, "/companies/company-a/shifts"),
    ]

    assert all(response.status_code == 401 for response in responses)
    assert all(
        response.json() == {"error": {"code": "AUTHENTICATION_REQUIRED"}}
        for response in responses
    )


@pytest.mark.parametrize(
    "payload",
    [
        _shift_payload("2026-10-28T08:00:00", "2026-10-28T09:00:00Z"),
        _shift_payload("not-a-date", "2026-10-28T09:00:00Z"),
        _shift_payload("2026-10-28T09:00:00Z", "2026-10-28T09:00:00Z"),
    ],
)
def test_invalid_shift_timestamp_returns_safe_400(scheduling_api, payload):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")

    response = _create_shift(client, "company-a", payload)

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_SHIFT"}}
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ShiftRecord)) == 0


def test_shift_creation_requires_company_administrator(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_company(engine, "company-b")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-b", "admin-b", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "employee-a")

    forbidden = _create_shift(client, "company-a")
    _authenticate_as(app, "admin-b")
    hidden = _create_shift(client, "company-a")

    assert forbidden.status_code == 403
    assert forbidden.json() == {"error": {"code": "FORBIDDEN"}}
    assert hidden.status_code == 404
    assert hidden.json() == {"error": {"code": "RESOURCE_NOT_FOUND"}}


def test_administrator_creates_and_reads_company_schedule(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")

    created = _create_shift(client, "company-a")
    response = _list_page(client, "/companies/company-a/shifts")

    assert created.status_code == 201
    assert created.json()["start_at"] == "2026-10-28T07:00:00+00:00"
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_assignment_requires_availability_and_valid_employee(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    employee_membership_id = _add_member(
        engine,
        "company-a",
        "employee-a",
        CompanyRole.EMPLOYEE,
        display_name="Cafe Team Member",
    )
    _authenticate_as(app, "admin-a")
    shift = _create_shift(client, "company-a").json()

    response = client.post(
        f"/companies/company-a/shifts/{shift['id']}/assignments",
        json={"membership_id": employee_membership_id},
    )

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "EMPLOYEE_UNAVAILABLE"}}
    with engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(ShiftAssignmentRecord)
        ) == 0


def test_administrator_assigns_shift_and_employee_reads_own_shifts(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    employee_membership_id = _add_member(
        engine,
        "company-a",
        "employee-a",
        CompanyRole.EMPLOYEE,
        display_name="Cafe Team Member",
    )
    _authenticate_as(app, "employee-a")
    availability = client.post(
        "/companies/company-a/availability",
        json=_availability_payload(),
    )
    assert availability.status_code == 201
    _authenticate_as(app, "admin-a")
    shift = _create_shift(client, "company-a").json()

    assignment = client.post(
        f"/companies/company-a/shifts/{shift['id']}/assignments",
        json={"membership_id": employee_membership_id},
    )

    assert assignment.status_code == 201
    assert assignment.json() == {
        "id": assignment.json()["id"],
        "shift_id": shift["id"],
        "company_id": "company-a",
        "membership_id": employee_membership_id,
        "display_name": "Cafe Team Member",
    }
    _authenticate_as(app, "employee-a")
    response = _list_page(client, "/companies/company-a/shifts/mine")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["assignment"]["membership_id"] == employee_membership_id
    assert response.json()["items"][0]["assignment"]["display_name"] == "Cafe Team Member"
    assert "employee_id" not in response.text
    assert "employee-a" not in response.text


def test_overlapping_assignment_is_conflict(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    employee_membership_id = _add_member(
        engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _authenticate_as(app, "employee-a")
    assert client.post(
        "/companies/company-a/availability", json=_availability_payload()
    ).status_code == 201
    _authenticate_as(app, "admin-a")
    first_shift = _create_shift(client, "company-a").json()
    second_shift = _create_shift(
        client,
        "company-a",
        _shift_payload("2026-10-28T10:00:00Z", "2026-10-28T14:00:00Z"),
    ).json()

    first = client.post(
        f"/companies/company-a/shifts/{first_shift['id']}/assignments",
        json={"membership_id": employee_membership_id},
    )
    second = client.post(
        f"/companies/company-a/shifts/{second_shift['id']}/assignments",
        json={"membership_id": employee_membership_id},
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"error": {"code": "SHIFT_ASSIGNMENT_CONFLICT"}}


def test_assignment_rejects_spoofed_identity_and_unknown_employee(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_company(engine, "company-b")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    other_company_membership_id = _add_member(
        engine, "company-b", "employee-b", CompanyRole.EMPLOYEE
    )
    _authenticate_as(app, "admin-a")
    shift = _create_shift(client, "company-a").json()

    spoofed = client.post(
        f"/companies/company-a/shifts/{shift['id']}/assignments",
        json={
            "membership_id": "00000000-0000-0000-0000-000000000001",
            "user_id": "admin-a",
        },
    )
    malformed_membership_id = client.post(
        f"/companies/company-a/shifts/{shift['id']}/assignments",
        json={"membership_id": "not-a-uuid"},
    )
    unknown_employee = client.post(
        f"/companies/company-a/shifts/{shift['id']}/assignments",
        json={"membership_id": "00000000-0000-0000-0000-000000000000"},
    )
    other_company_employee = client.post(
        f"/companies/company-a/shifts/{shift['id']}/assignments",
        json={"membership_id": other_company_membership_id},
    )

    assert spoofed.status_code == 400
    assert spoofed.json() == {"error": {"code": "INVALID_REQUEST"}}
    assert malformed_membership_id.status_code == 404
    assert malformed_membership_id.json() == {
        "error": {"code": "RESOURCE_NOT_FOUND"}
    }
    assert unknown_employee.status_code == 404
    assert unknown_employee.json() == {"error": {"code": "RESOURCE_NOT_FOUND"}}
    assert other_company_employee.status_code == 404
    assert other_company_employee.json() == {
        "error": {"code": "RESOURCE_NOT_FOUND"}
    }


def test_administrator_roster_is_company_scoped_and_excludes_legacy_members(
    scheduling_api,
):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_company(engine, "company-b")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(
        engine,
        "company-a",
        "employee-a",
        CompanyRole.EMPLOYEE,
        display_name="Cafe Team Member",
    )
    _add_legacy_member(
        engine, "company-a", "opaque-legacy-id", CompanyRole.EMPLOYEE
    )
    _add_member(engine, "company-b", "employee-b", CompanyRole.EMPLOYEE)

    _authenticate_as(app, "admin-a")
    roster = client.get("/companies/company-a/employees")
    outsider = client.get("/companies/company-b/employees")

    assert roster.status_code == 200
    assert roster.json()["next_cursor"] is None
    assert roster.json()["items"] == [
        {
            "membership_id": roster.json()["items"][0]["membership_id"],
            "display_name": "Cafe Team Member",
        }
    ]
    assert "opaque-legacy-id" not in roster.text
    assert "employee-a" not in roster.text
    assert "employee-b" not in roster.text
    assert outsider.status_code == 404
    assert outsider.json() == {"error": {"code": "RESOURCE_NOT_FOUND"}}

    _authenticate_as(app, "employee-a")
    forbidden = client.get("/companies/company-a/employees")
    assert forbidden.status_code == 403
    assert forbidden.json() == {"error": {"code": "FORBIDDEN"}}


def test_administrator_roster_uses_bounded_cursor_pages(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-a", "employee-b", CompanyRole.EMPLOYEE)
    _authenticate_as(app, "admin-a")

    first_page = client.get("/companies/company-a/employees?limit=1")
    assert first_page.status_code == 200
    next_page = client.get(
        "/companies/company-a/employees?limit=1&cursor="
        + first_page.json()["next_cursor"]
    )

    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["next_cursor"]
    assert next_page.status_code == 200
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["next_cursor"] is None


def test_administrator_roster_rejects_malformed_cursor(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")

    response = client.get("/companies/company-a/employees?cursor=malformed")

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}


def test_shift_reads_are_role_scoped_and_tenant_safe(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_company(engine, "company-b")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    employee_membership_id = _add_member(
        engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _add_member(engine, "company-a", "employee-b", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-b", "admin-b", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")
    shift = _create_shift(client, "company-a").json()
    _authenticate_as(app, "employee-a")

    employee_schedule = _list_page(client, "/companies/company-a/shifts")
    employee_own = _list_page(client, "/companies/company-a/shifts/mine")
    cross_tenant = _list_page(client, "/companies/company-b/shifts")

    assert employee_schedule.status_code == 403
    assert employee_schedule.json() == {"error": {"code": "FORBIDDEN"}}
    assert employee_own.status_code == 200
    assert employee_own.json() == {"items": [], "next_cursor": None}
    assert cross_tenant.status_code == 404


@pytest.mark.parametrize(
    "path,user_id,role",
    [
        ("/companies/company-a/availability/mine", "employee-a", CompanyRole.EMPLOYEE),
        ("/companies/company-a/availability", "admin-a", CompanyRole.ADMINISTRATOR),
        ("/companies/company-a/shifts", "admin-a", CompanyRole.ADMINISTRATOR),
        ("/companies/company-a/shifts/mine", "employee-a", CompanyRole.EMPLOYEE),
    ],
)
def test_scheduling_lists_require_valid_bounded_time_window(
    scheduling_api, path, user_id, role
):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", user_id, role)
    _authenticate_as(app, user_id)

    missing = client.get(path)
    too_wide = _list_page(
        client,
        path,
        **{"from": "2026-10-01T00:00:00Z", "to": "2026-11-02T00:00:00Z"},
    )
    zero_width = _list_page(
        client,
        path,
        **{"from": "2026-10-28T00:00:00Z", "to": "2026-10-28T00:00:00Z"},
    )
    reversed_window = _list_page(
        client,
        path,
        **{"from": "2026-10-29T00:00:00Z", "to": "2026-10-28T00:00:00Z"},
    )
    missing_offset = _list_page(
        client,
        path,
        **{"from": "2026-10-28T00:00:00", "to": "2026-10-29T00:00:00Z"},
    )
    invalid_limit = _list_page(client, path, limit=0)
    malformed_cursor = _list_page(client, path, cursor="malformed")

    assert [response.status_code for response in (
        missing,
        too_wide,
        zero_width,
        reversed_window,
        missing_offset,
        invalid_limit,
        malformed_cursor,
    )] == [400, 400, 400, 400, 400, 400, 400]
    assert all(
        response.json() == {"error": {"code": "INVALID_REQUEST"}}
        for response in (
            missing,
            too_wide,
            zero_width,
            reversed_window,
            missing_offset,
            invalid_limit,
            malformed_cursor,
        )
    )


def test_schedule_pages_use_half_open_overlap_and_stable_keyset_order(
    scheduling_api,
):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")
    with engine.begin() as connection:
        connection.execute(
            ShiftRecord.__table__.insert(),
            [
                {
                    "id": "shift-overnight",
                    "company_id": "company-a",
                    "start_at": _db_time("2026-10-27T23:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T01:00:00+00:00"),
                },
                {
                    "id": "shift-a",
                    "company_id": "company-a",
                    "start_at": _db_time("2026-10-28T08:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T10:00:00+00:00"),
                },
                {
                    "id": "shift-b",
                    "company_id": "company-a",
                    "start_at": _db_time("2026-10-28T08:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T10:00:00+00:00"),
                },
                {
                    "id": "shift-ended-at-from",
                    "company_id": "company-a",
                    "start_at": _db_time("2026-10-27T22:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T00:00:00+00:00"),
                },
                {
                    "id": "shift-starts-at-to",
                    "company_id": "company-a",
                    "start_at": _db_time("2026-10-29T00:00:00+00:00"),
                    "end_at": _db_time("2026-10-29T02:00:00+00:00"),
                },
            ],
        )

    first = _list_page(
        client,
        "/companies/company-a/shifts",
        limit=2,
        **{"from": "2026-10-28T00:00:00Z", "to": "2026-10-29T00:00:00Z"},
    )
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    assert set(first.json()) == {"items", "next_cursor"}
    assert [item["id"] for item in first.json()["items"]] == [
        "shift-overnight",
        "shift-a",
    ]
    assert first.json()["items"][0]["start_at"] == "2026-10-27T23:00:00+00:00"
    assert first.json()["next_cursor"] is not None
    second = _list_page(
        client,
        "/companies/company-a/shifts",
        limit=2,
        cursor=first.json()["next_cursor"],
        **{"from": "2026-10-28T00:00:00Z", "to": "2026-10-29T00:00:00Z"},
    )

    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["shift-b"]
    assert second.json()["next_cursor"] is None


def test_schedule_default_and_maximum_page_limits_are_enforced(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")
    starts_at = _db_time("2026-10-28T08:00:00+00:00")
    with engine.begin() as connection:
        connection.execute(
            ShiftRecord.__table__.insert(),
            [
                {
                    "id": f"shift-{index:03d}",
                    "company_id": "company-a",
                    "start_at": starts_at + timedelta(minutes=index),
                    "end_at": starts_at + timedelta(minutes=index + 30),
                }
                for index in range(201)
            ],
        )

    default_page = _list_page(client, "/companies/company-a/shifts")
    maximum_page = _list_page(
        client,
        "/companies/company-a/shifts",
        limit=200,
    )
    overflow_page = _list_page(
        client,
        "/companies/company-a/shifts",
        limit=201,
    )

    assert default_page.status_code == 200
    assert len(default_page.json()["items"]) == 100
    assert default_page.json()["next_cursor"] is not None
    assert maximum_page.status_code == 200
    assert len(maximum_page.json()["items"]) == 200
    assert maximum_page.json()["next_cursor"] is not None
    assert overflow_page.status_code == 400
    assert overflow_page.json() == {"error": {"code": "INVALID_REQUEST"}}


def test_availability_pages_are_scoped_ordered_and_do_not_lose_sentinel(
    scheduling_api,
):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    _add_member(engine, "company-a", "employee-b", CompanyRole.EMPLOYEE)
    with engine.begin() as connection:
        connection.execute(
            AvailabilityRecord.__table__.insert(),
            [
                {
                    "id": "availability-overnight",
                    "company_id": "company-a",
                    "employee_id": "employee-a",
                    "start_at": _db_time("2026-10-27T23:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T01:00:00+00:00"),
                },
                {
                    "id": "availability-a",
                    "company_id": "company-a",
                    "employee_id": "employee-a",
                    "start_at": _db_time("2026-10-28T03:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T05:00:00+00:00"),
                },
                {
                    "id": "availability-b",
                    "company_id": "company-a",
                    "employee_id": "employee-b",
                    "start_at": _db_time("2026-10-28T03:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T05:00:00+00:00"),
                },
                {
                    "id": "availability-ended-at-from",
                    "company_id": "company-a",
                    "employee_id": "employee-b",
                    "start_at": _db_time("2026-10-27T22:00:00+00:00"),
                    "end_at": _db_time("2026-10-28T00:00:00+00:00"),
                },
                {
                    "id": "availability-starts-at-to",
                    "company_id": "company-a",
                    "employee_id": "employee-b",
                    "start_at": _db_time("2026-10-29T00:00:00+00:00"),
                    "end_at": _db_time("2026-10-29T02:00:00+00:00"),
                },
            ],
        )
    _authenticate_as(app, "admin-a")

    first = _list_page(
        client,
        "/companies/company-a/availability",
        limit=2,
        **{"from": "2026-10-28T00:00:00Z", "to": "2026-10-29T00:00:00Z"},
    )
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == "no-store"
    assert [item["id"] for item in first.json()["items"]] == [
        "availability-overnight",
        "availability-a",
    ]
    assert first.json()["items"][0]["start_at"] == "2026-10-27T23:00:00+00:00"
    assert first.json()["next_cursor"] is not None
    second = _list_page(
        client,
        "/companies/company-a/availability",
        limit=2,
        cursor=first.json()["next_cursor"],
        **{"from": "2026-10-28T00:00:00Z", "to": "2026-10-29T00:00:00Z"},
    )
    assert second.status_code == 200
    assert [item["id"] for item in second.json()["items"]] == ["availability-b"]
    assert second.json()["next_cursor"] is None

    _authenticate_as(app, "employee-a")
    own = _list_page(
        client,
        "/companies/company-a/availability/mine",
        **{"from": "2026-10-28T00:00:00Z", "to": "2026-10-29T00:00:00Z"},
    )
    assert [item["id"] for item in own.json()["items"]] == [
        "availability-overnight",
        "availability-a",
    ]


def test_employee_shift_pages_preserve_membership_assignment_privacy(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    membership_id = _add_member(
        engine,
        "company-a",
        "employee-a",
        CompanyRole.EMPLOYEE,
        display_name="Cafe Team Member",
    )
    _authenticate_as(app, "employee-a")
    assert client.post(
        "/companies/company-a/availability",
        json=_availability_payload("2026-10-28T08:00:00Z", "2026-10-28T16:00:00Z"),
    ).status_code == 201
    _authenticate_as(app, "admin-a")
    shifts = [
        _create_shift(
            client,
            "company-a",
            _shift_payload(
                f"2026-10-28T{hour:02d}:00:00Z",
                f"2026-10-28T{hour + 1:02d}:00:00Z",
            ),
        ).json()
        for hour in (9, 10)
    ]
    for shift in shifts:
        assert client.post(
            f"/companies/company-a/shifts/{shift['id']}/assignments",
            json={"membership_id": membership_id},
        ).status_code == 201
    _authenticate_as(app, "employee-a")

    first = _list_page(
        client, "/companies/company-a/shifts/mine", limit=1
    )
    second = _list_page(
        client,
        "/companies/company-a/shifts/mine",
        limit=1,
        cursor=first.json()["next_cursor"],
    )

    assert first.status_code == second.status_code == 200
    assert len(first.json()["items"]) == len(second.json()["items"]) == 1
    assert first.json()["items"][0]["assignment"]["membership_id"] == membership_id
    assert second.json()["items"][0]["assignment"]["membership_id"] == membership_id
    assert first.json()["items"][0]["assignment"]["display_name"] == "Cafe Team Member"
    assert "employee_id" not in first.text + second.text


def test_schedule_cursor_is_bound_to_principal_company_endpoint_and_filters(
    scheduling_api,
):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_company(engine, "company-b")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(engine, "company-b", "admin-a", CompanyRole.ADMINISTRATOR)
    _add_member(engine, "company-a", "admin-b", CompanyRole.ADMINISTRATOR)
    _authenticate_as(app, "admin-a")
    _create_shift(client, "company-a")
    _create_shift(
        client,
        "company-a",
        _shift_payload("2026-10-28T15:00:00Z", "2026-10-28T16:00:00Z"),
    )
    first = _list_page(client, "/companies/company-a/shifts", limit=1)
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    contexts = [
        ("/companies/company-a/shifts", {"from": "2026-10-26T00:00:00Z"}),
        ("/companies/company-b/shifts", {}),
        ("/companies/company-a/availability", {}),
    ]
    for path, overrides in contexts:
        params = {
            "from": "2026-10-27T00:00:00Z",
            "to": "2026-10-30T00:00:00Z",
            "limit": 1,
            "cursor": cursor,
            **overrides,
        }
        response = client.get(path, params=params)
        assert response.status_code == 400
        assert response.json() == {"error": {"code": "INVALID_REQUEST"}}

    _authenticate_as(app, "admin-b")
    other_principal = _list_page(
        client, "/companies/company-a/shifts", limit=1, cursor=cursor
    )
    assert other_principal.status_code == 400
    assert other_principal.json() == {"error": {"code": "INVALID_REQUEST"}}


def test_scheduling_persistence_failures_are_safe_and_atomic(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    trigger_name, function_name = _install_reject_insert_trigger(engine, "shifts")
    _authenticate_as(app, "admin-a")

    try:
        response = _create_shift(client, "company-a")

        assert response.status_code == 500
        assert response.json() == {"error": {"code": "SHIFT_SAVE_FAILED"}}
        assert "sensitive persistence detail" not in response.text
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(ShiftRecord)) == 0
    finally:
        _remove_reject_insert_trigger(engine, "shifts", trigger_name, function_name)


def test_availability_persistence_failure_is_safe_and_atomic(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "employee-a", CompanyRole.EMPLOYEE)
    trigger_name, function_name = _install_reject_insert_trigger(
        engine, "availabilities"
    )
    _authenticate_as(app, "employee-a")

    try:
        response = client.post(
            "/companies/company-a/availability",
            json=_availability_payload(),
        )

        assert response.status_code == 500
        assert response.json() == {"error": {"code": "AVAILABILITY_SAVE_FAILED"}}
        assert "sensitive persistence detail" not in response.text
        with engine.connect() as connection:
            assert connection.scalar(
                select(func.count()).select_from(AvailabilityRecord)
            ) == 0
    finally:
        _remove_reject_insert_trigger(
            engine, "availabilities", trigger_name, function_name
        )


def test_assignment_persistence_failure_is_safe_and_atomic(scheduling_api):
    client, engine, app = scheduling_api
    _add_company(engine, "company-a")
    _add_member(engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR)
    employee_membership_id = _add_member(
        engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _authenticate_as(app, "employee-a")
    assert client.post(
        "/companies/company-a/availability",
        json=_availability_payload(),
    ).status_code == 201
    _authenticate_as(app, "admin-a")
    shift = _create_shift(client, "company-a").json()
    trigger_name, function_name = _install_reject_insert_trigger(
        engine, "shift_assignments"
    )

    try:
        response = client.post(
            f"/companies/company-a/shifts/{shift['id']}/assignments",
            json={"membership_id": employee_membership_id},
        )

        assert response.status_code == 500
        assert response.json() == {"error": {"code": "SHIFT_SAVE_FAILED"}}
        assert "sensitive persistence detail" not in response.text
        with engine.connect() as connection:
            assert connection.scalar(
                select(func.count()).select_from(ShiftAssignmentRecord)
            ) == 0
    finally:
        _remove_reject_insert_trigger(
            engine, "shift_assignments", trigger_name, function_name
        )
