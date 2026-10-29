import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from app.application.authenticate_user import AuthenticationFailed
from app.application.list_my_companies import ListMyCompanies
from app.application.manage_availability import ManageAvailability
from app.application.manage_shifts import ManageShifts
from app.application.register_company import AuthenticatedUser
from app.infrastructure.composition import build_authenticate_user
from app.infrastructure.persistence.models import (
    AvailabilityRecord,
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
    UserRecord,
)
from app.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)
from app.interfaces.demo_seed import (
    DEMO_ADMIN_EMAIL,
    DEMO_COMPANY_NAME,
    DEMO_EMPLOYEE_EMAIL,
    seed_demo_data,
)


pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc)
WINDOW = (NOW - timedelta(days=7), NOW + timedelta(days=21))


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
def engine():
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
        yield engine
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def credentials_path(tmp_path):
    return tmp_path / "demo" / "demo-credentials.json"


def _credentials(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _count(engine, record):
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(record))


def _login(engine, email, password):
    return build_authenticate_user(engine).login(email, password)


def _user_id(engine, email):
    with engine.connect() as connection:
        return connection.scalar(
            select(UserRecord.id).where(UserRecord.email == email)
        )


def test_seed_creates_verified_accounts_that_can_log_in(engine, credentials_path):
    seed_demo_data(engine, credentials_path, now=NOW)

    credentials = _credentials(credentials_path)
    assert credentials["administrator"]["email"] == DEMO_ADMIN_EMAIL
    assert credentials["employee"]["email"] == DEMO_EMPLOYEE_EMAIL
    for role in ("administrator", "employee"):
        account = credentials[role]
        assert len(account["password"]) >= 16
        assert _login(engine, account["email"], account["password"])
    with engine.connect() as connection:
        unverified = connection.scalar(
            select(func.count())
            .select_from(UserRecord)
            .where(UserRecord.email_verified_at.is_(None))
        )
    assert unverified == 0


def test_seed_creates_company_roles_availability_and_assigned_shifts(
    engine, credentials_path
):
    seed_demo_data(engine, credentials_path, now=NOW)

    factory = SqlAlchemyUnitOfWorkFactory(engine)
    admin = AuthenticatedUser(_user_id(engine, DEMO_ADMIN_EMAIL))
    employee = AuthenticatedUser(_user_id(engine, DEMO_EMPLOYEE_EMAIL))
    admin_companies = ListMyCompanies(factory).execute(admin)
    employee_companies = ListMyCompanies(factory).execute(employee)
    assert [(c.company_name, c.role) for c in admin_companies] == [
        (DEMO_COMPANY_NAME, "administrator")
    ]
    assert [(c.company_name, c.role) for c in employee_companies] == [
        (DEMO_COMPANY_NAME, "employee")
    ]
    company_id = admin_companies[0].company_id

    availability = ManageAvailability(factory).list_own(
        employee, company_id, from_at=WINDOW[0], to_at=WINDOW[1], limit=200
    )
    shifts = ManageShifts(factory).list_for_company(
        admin, company_id, from_at=WINDOW[0], to_at=WINDOW[1], limit=200
    )
    own_shifts = ManageShifts(factory).list_for_employee(
        employee, company_id, from_at=WINDOW[0], to_at=WINDOW[1], limit=200
    )
    assert len(availability) >= 8
    assert any(
        item.end_at - item.start_at > timedelta(hours=4) for item in availability
    )
    assigned = [shift for shift in shifts if shift.assignment is not None]
    assert len(assigned) >= 4
    assert any(shift.assignment is None for shift in shifts)
    assert {shift.id for shift in own_shifts} == {shift.id for shift in assigned}
    for shift in assigned:
        assert any(
            item.start_at <= shift.start_at and item.end_at >= shift.end_at
            for item in availability
        )
    assert any(
        shift.start_at.astimezone(timezone(timedelta(hours=1))).date()
        != (shift.end_at - timedelta(seconds=1))
        .astimezone(timezone(timedelta(hours=1)))
        .date()
        for shift in shifts
    )


def test_seed_is_idempotent_and_keeps_credentials(engine, credentials_path):
    seed_demo_data(engine, credentials_path, now=NOW)
    first = _credentials(credentials_path)
    counts = {
        record: _count(engine, record)
        for record in (
            UserRecord,
            CompanyRecord,
            CompanyMembershipRecord,
            AvailabilityRecord,
        )
    }
    shifts = _count_table(engine, "shifts")
    assignments = _count_table(engine, "shift_assignments")

    seed_demo_data(engine, credentials_path, now=NOW)

    assert _credentials(credentials_path) == first
    for record, count in counts.items():
        assert _count(engine, record) == count
    assert _count_table(engine, "shifts") == shifts
    assert _count_table(engine, "shift_assignments") == assignments
    assert _login(engine, first["employee"]["email"], first["employee"]["password"])


def _count_table(engine, table):
    with engine.connect() as connection:
        return connection.scalar(text(f"SELECT count(*) FROM {table}"))


def test_missing_credentials_file_resets_only_demo_passwords_via_recovery_flow(
    engine, credentials_path
):
    seed_demo_data(engine, credentials_path, now=NOW)
    original = _credentials(credentials_path)
    credentials_path.unlink()

    seed_demo_data(engine, credentials_path, now=NOW)

    renewed = _credentials(credentials_path)
    assert renewed["employee"]["password"] != original["employee"]["password"]
    assert _login(engine, renewed["employee"]["email"], renewed["employee"]["password"])
    with pytest.raises(AuthenticationFailed):
        _login(
            engine,
            original["employee"]["email"],
            original["employee"]["password"],
        )
    assert _count(engine, CompanyRecord) == 1


def test_seed_never_modifies_unrelated_accounts_or_companies(
    engine, credentials_path
):
    other_hash = "$argon2id$unrelated"
    with engine.begin() as connection:
        connection.execute(
            UserRecord.__table__.insert().values(
                id="unrelated-user",
                email="real.person@example.org",
                password_hash=other_hash,
            )
        )

    seed_demo_data(engine, credentials_path, now=NOW)

    with engine.connect() as connection:
        row = connection.execute(
            select(UserRecord.password_hash, UserRecord.email_verified_at).where(
                UserRecord.id == "unrelated-user"
            )
        ).one()
    assert row.password_hash == other_hash
    assert row.email_verified_at is None


def test_credentials_directory_is_git_ignored():
    root = Path(__file__).resolve().parents[2]
    assert ".local/" in (root / ".gitignore").read_text().splitlines()
