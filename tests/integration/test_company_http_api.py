import re
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from app.application.register_company import AuthenticatedUser
from app.application.register_company import JoiningCodeUnavailable
from app.infrastructure.persistence.models import (
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
)
from app.interfaces.auth import get_authenticated_user
from app.interfaces.main import create_app


@pytest.fixture
def api_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app(engine=engine)
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, engine, app
    finally:
        client.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _authenticate_as(app, user_id: str) -> None:
    app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(
        user_id
    )


def _company_request(**overrides):
    payload = {
        "company_name": "North Star Cafe",
        "business_email": " SCHEDULE@EXAMPLE.COM ",
        "business_phone": "+41791234567",
        "business_address": "1 Main Street",
        "industry": "  Food   and Hospitality ",
        "display_name": "Company Admin",
    }
    payload.update(overrides)
    return payload


def test_registration_requires_authenticated_principal(api_client):
    client, engine, _ = api_client

    response = client.post(
        "/companies",
        json=_company_request(),
        headers={"X-User-Id": "untrusted-client"},
    )

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "AUTHENTICATION_REQUIRED"}}
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 0


def test_registration_creates_company_and_administrator(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "trusted-admin")

    response = client.post("/companies", json=_company_request())

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"company_id", "joining_code", "role"}
    assert re.fullmatch(r"[A-Z0-9]{8}", body["joining_code"])
    assert body["role"] == "administrator"
    with engine.connect() as connection:
        business_email, industry = connection.execute(
            select(CompanyRecord.business_email, CompanyRecord.industry).where(
                CompanyRecord.id == body["company_id"]
            )
        ).one()
        (
            membership_role,
            membership_user,
            account_user_id,
            display_name,
            public_id,
        ) = connection.execute(
            select(
                CompanyMembershipRecord.role,
                CompanyMembershipRecord.user_id,
                CompanyMembershipRecord.account_user_id,
                CompanyMembershipRecord.display_name,
                CompanyMembershipRecord.public_id,
            ).where(
                CompanyMembershipRecord.company_id == body["company_id"]
            )
        ).one()
    assert business_email == "SCHEDULE@example.com"
    assert industry == "Food and Hospitality"
    assert membership_user == "trusted-admin"
    assert membership_role == "administrator"
    assert account_user_id == "trusted-admin"
    assert display_name == "Company Admin"
    assert public_id


def test_registration_collision_exhaustion_returns_safe_503(api_client):
    client, _, app = api_client
    _authenticate_as(app, "trusted-admin")

    def exhaust_codes(authenticated_user, command):
        del authenticated_user, command
        raise JoiningCodeUnavailable

    app.state.register_company = SimpleNamespace(execute=exhaust_codes)

    response = client.post("/companies", json=_company_request())

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "JOINING_CODE_UNAVAILABLE"}}


def test_registration_database_error_is_safe_and_atomic(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "trusted-admin")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_membership BEFORE INSERT ON company_memberships "
            "WHEN NEW.user_id = 'trusted-admin' "
            "BEGIN SELECT RAISE(ABORT, 'sensitive database detail'); END"
        )

    response = client.post(
        "/companies", json=_company_request()
    )

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "COMPANY_REGISTRATION_FAILED"}}
    assert "sensitive database detail" not in response.text
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 0
        assert (
            connection.scalar(
                select(func.count()).select_from(CompanyMembershipRecord)
            )
            == 0
        )


def test_registration_rejects_client_supplied_acting_identity(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "trusted-admin")

    response = client.post(
        "/companies",
        json=_company_request(user_id="attacker", acting_user_id="attacker"),
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 0


def test_join_requires_authenticated_principal(api_client):
    client, engine, _ = api_client

    response = client.post("/companies/join", json={"joining_code": "AB12CD34"})

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "AUTHENTICATION_REQUIRED"}}
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(CompanyMembershipRecord)
            )
            == 0
        )


def test_employee_joins_using_trusted_principal(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "company-owner")
    registration = client.post("/companies", json=_company_request())
    joining_code = registration.json()["joining_code"]
    _authenticate_as(app, "trusted-employee")

    response = client.post(
        "/companies/join",
        json={
            "joining_code": joining_code,
            "display_name": "Employee One",
        },
    )

    assert response.status_code == 201
    membership = response.json()
    assert membership == {
        "company_id": registration.json()["company_id"],
        "role": "employee",
    }
    with engine.connect() as connection:
        stored_membership = connection.scalar(
            select(CompanyMembershipRecord.role).where(
                CompanyMembershipRecord.company_id == membership["company_id"],
                CompanyMembershipRecord.user_id == "trusted-employee",
            )
        )
    assert stored_membership == "employee"


def test_join_does_not_accept_client_supplied_identity(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "trusted-employee")

    response = client.post(
        "/companies/join",
        json={
            "joining_code": "AB12CD34",
            "display_name": "Employee One",
            "user_id": "attacker",
        },
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(CompanyMembershipRecord)
            )
            == 0
        )


def test_join_database_error_is_safe_and_does_not_create_membership(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "company-owner")
    registration = client.post("/companies", json=_company_request())
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_membership BEFORE INSERT ON company_memberships "
            "WHEN NEW.user_id = 'failing-employee' "
            "BEGIN SELECT RAISE(ABORT, 'sensitive membership detail'); END"
        )
    _authenticate_as(app, "failing-employee")

    response = client.post(
        "/companies/join",
        json={
            "joining_code": registration.json()["joining_code"],
            "display_name": "Employee One",
        },
    )

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "MEMBERSHIP_CREATION_FAILED"}}
    assert "sensitive membership detail" not in response.text
    with engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(CompanyMembershipRecord)
                .where(
                    CompanyMembershipRecord.company_id
                    == registration.json()["company_id"],
                    CompanyMembershipRecord.user_id == "failing-employee",
                )
            )
            == 0
        )


@pytest.mark.parametrize("joining_code", ["ab12cd34", "AB12-CD4", "UNKNOWN1"])
def test_invalid_or_unknown_joining_code_has_generic_error(api_client, joining_code):
    client, _, app = api_client
    _authenticate_as(app, "trusted-employee")

    response = client.post(
        "/companies/join",
        json={"joining_code": joining_code, "display_name": "Employee One"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_JOINING_CODE"}}


def test_duplicate_join_returns_already_a_member(api_client):
    client, _, app = api_client
    _authenticate_as(app, "company-owner")
    registration = client.post("/companies", json=_company_request())
    _authenticate_as(app, "trusted-employee")
    payload = {
        "joining_code": registration.json()["joining_code"],
        "display_name": "Employee One",
    }
    assert client.post("/companies/join", json=payload).status_code == 201

    response = client.post("/companies/join", json=payload)

    assert response.status_code == 409
    assert response.json() == {"error": {"code": "ALREADY_A_MEMBER"}}


def test_invalid_company_data_uses_safe_error_envelope(api_client):
    client, engine, app = api_client
    _authenticate_as(app, "trusted-admin")

    response = client.post(
        "/companies", json=_company_request(business_phone="not-a-phone")
    )

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}
    assert "not-a-phone" not in response.text
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(CompanyRecord)) == 0


def test_company_discovery_is_account_linked_and_principal_scoped(api_client):
    client, _, app = api_client
    _authenticate_as(app, "account-a")
    registered = client.post(
        "/companies",
        json=_company_request(display_name="Admin A"),
    )
    company_id = registered.json()["company_id"]

    account_a = client.get("/companies/mine")
    _authenticate_as(app, "account-b")
    account_b_before_join = client.get("/companies/mine")
    joined = client.post(
        "/companies/join",
        json={
            "joining_code": registered.json()["joining_code"],
            "display_name": "Employee B",
        },
    )
    account_b_after_join = client.get("/companies/mine")
    _authenticate_as(app, "account-a")
    account_a_after_join = client.get("/companies/mine")

    assert account_a.status_code == account_b_before_join.status_code == 200
    assert account_a.json()["items"][0] == {
        "membership_id": account_a.json()["items"][0]["membership_id"],
        "company_id": company_id,
        "name": "North Star Cafe",
        "role": "administrator",
    }
    assert account_b_before_join.json() == {"items": [], "next_cursor": None}
    assert joined.status_code == 201
    assert account_b_after_join.json()["items"][0]["role"] == "employee"
    assert account_b_after_join.json()["items"][0]["company_id"] == company_id
    assert account_a_after_join.json()["items"][0]["role"] == "administrator"
    for response in (account_a, account_b_before_join, account_b_after_join):
        assert "account-a" not in response.text
        assert "account-b" not in response.text
        assert "user_id" not in response.text


def test_company_discovery_supports_maximum_page_and_cursor(api_client):
    client, engine, app = api_client
    account_id = "account-with-many-companies"
    _authenticate_as(app, account_id)
    with engine.begin() as connection:
        for index in range(201):
            company_id = str(UUID(int=1000 + index))
            connection.execute(
                CompanyRecord.__table__.insert().values(
                    id=company_id,
                    company_name=f"Company {index}",
                    business_email=f"company-{index}@example.test",
                    business_phone="+41791234567",
                    business_address="1 Test Street",
                    industry="Hospitality",
                    joining_code=f"{index:08}",
                )
            )
            connection.execute(
                CompanyMembershipRecord.__table__.insert().values(
                    company_id=company_id,
                    user_id=account_id,
                    role="employee",
                    account_user_id=account_id,
                    public_id=str(UUID(int=index + 1)),
                    display_name=f"Employee {index}",
                )
            )

    first_page = client.get("/companies/mine?limit=200")
    assert first_page.status_code == 200
    second_page = client.get(
        "/companies/mine?limit=200&cursor="
        + first_page.json()["next_cursor"]
    )

    assert len(first_page.json()["items"]) == 200
    assert first_page.json()["next_cursor"]
    assert second_page.status_code == 200, second_page.text
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["next_cursor"] is None


def test_company_discovery_rejects_malformed_cursor(api_client):
    client, _, app = api_client
    _authenticate_as(app, "account-a")

    response = client.get("/companies/mine?cursor=malformed")

    assert response.status_code == 400
    assert response.json() == {"error": {"code": "INVALID_REQUEST"}}


def test_application_factory_requires_database_configuration(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL must be set"):
        create_app()


def test_application_factory_requires_public_origin(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.delenv("PUBLIC_ORIGIN", raising=False)

    with pytest.raises(RuntimeError, match="PUBLIC_ORIGIN must be set"):
        create_app()
