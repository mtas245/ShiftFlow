import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.domain.availability import Availability
from app.domain.company import Company
from app.domain.company_membership import CompanyMembership, CompanyRole
from app.domain.shift import Shift, ShiftAssignment
from app.infrastructure.persistence.models import (
    Base,
    AvailabilityRecord,
    CompanyMembershipRecord,
    CompanyRecord,
    ShiftAssignmentRecord,
)
from app.infrastructure.persistence.unit_of_work import (
    SqlAlchemyUnitOfWorkFactory,
)


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
def empty_test_database(monkeypatch):
    database_url = _test_database_url()
    engine = create_engine(database_url, pool_pre_ping=True)
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
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        monkeypatch.setenv("DATABASE_URL", database_url)
        yield engine
    finally:
        if safe_database:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _upgrade_to_head() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )


def _upgrade_to_revision(revision: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )


def _downgrade_to_base() -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "base"],
        cwd=_repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )


def test_migration_cli_requires_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_repository_root(),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "DATABASE_URL must be set" in result.stderr


def test_fresh_migration_is_repeatable_matches_metadata_and_supports_repositories(
    empty_test_database,
):
    engine = empty_test_database

    _upgrade_to_head()
    _upgrade_to_head()

    inspector = inspect(engine)
    assert set(Base.metadata.tables).issubset(set(inspector.get_table_names()))

    unique_constraints = {
        constraint["name"]
        for table_name in Base.metadata.tables
        for constraint in inspector.get_unique_constraints(table_name)
    }
    assert {
        "uq_companies_joining_code",
        "uq_company_memberships_company_user",
        "uq_shift_assignments_one_per_shift",
    }.issubset(unique_constraints)

    check_constraints = {
        constraint["name"]
        for table_name in Base.metadata.tables
        for constraint in inspector.get_check_constraints(table_name)
    }
    assert {
        "ck_availabilities_end_after_start",
        "ck_shifts_end_after_start",
    }.issubset(check_constraints)

    foreign_keys = {
        (
            table_name,
            tuple(constraint["constrained_columns"]),
            tuple(constraint["referred_columns"]),
        )
        for table_name in Base.metadata.tables
        for constraint in inspector.get_foreign_keys(table_name)
    }
    assert (
        "availabilities",
        ("company_id", "employee_id"),
        ("company_id", "user_id"),
    ) in foreign_keys
    assert (
        "shift_assignments",
        ("company_id", "employee_id"),
        ("company_id", "user_id"),
    ) in foreign_keys
    assert (
        "shift_assignments",
        ("shift_id", "company_id"),
        ("id", "company_id"),
    ) in foreign_keys

    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []

    company = Company.create(
        company_name="Migration Test Cafe",
        business_email="owner@example.test",
        business_phone="+41791234567",
        business_address="1 Main Street",
        industry="Hospitality",
        joining_code="MIGRATE1",
    )
    employee_id = "migration-employee"
    availability_start = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)
    availability_end = datetime(2026, 10, 28, 17, tzinfo=timezone.utc)
    shift_start = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)
    shift_end = datetime(2026, 10, 28, 13, tzinfo=timezone.utc)

    with SqlAlchemyUnitOfWorkFactory(engine)() as unit_of_work:
        unit_of_work.companies.add(company)
        unit_of_work.memberships.add(
            CompanyMembership.create(
                company_id=company.id,
                user_id="migration-admin",
                role=CompanyRole.ADMINISTRATOR,
            )
        )
        unit_of_work.memberships.add(
            CompanyMembership.create(
                company_id=company.id,
                user_id=employee_id,
                role=CompanyRole.EMPLOYEE,
            )
        )
        unit_of_work.availabilities.add(
            Availability.create(
                company_id=company.id,
                employee_id=employee_id,
                start_at=availability_start,
                end_at=availability_end,
            )
        )
        shift = Shift.create(
            company_id=company.id,
            start_at=shift_start,
            end_at=shift_end,
        )
        unit_of_work.shifts.add(shift)
        unit_of_work.assignments.add(
            ShiftAssignment.create(
                shift_id=shift.id,
                company_id=company.id,
                employee_id=employee_id,
            )
        )

    with engine.connect() as connection:
        assert connection.scalar(
            select(CompanyRecord.id).where(CompanyRecord.id == company.id)
        ) == company.id
        assert connection.scalar(
            select(CompanyMembershipRecord.user_id).where(
                CompanyMembershipRecord.user_id == employee_id
            )
        ) == employee_id
        assert connection.scalar(
            select(AvailabilityRecord.employee_id).where(
                AvailabilityRecord.employee_id == employee_id
            )
        ) == employee_id
        assert connection.scalar(
            select(ShiftAssignmentRecord.employee_id).where(
                ShiftAssignmentRecord.employee_id == employee_id
            )
        ) == employee_id

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                CompanyRecord.__table__.insert().values(
                    id="duplicate-code-company",
                    company_name="Duplicate Code",
                    business_email="duplicate@example.test",
                    business_phone="+41791234567",
                    business_address="2 Main Street",
                    industry="Hospitality",
                    joining_code=company.joining_code,
                )
            )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                AvailabilityRecord.__table__.insert().values(
                    id="invalid-interval",
                    company_id=company.id,
                    employee_id=employee_id,
                    start_at=availability_start,
                    end_at=availability_start,
                )
            )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                ShiftAssignmentRecord.__table__.insert().values(
                    id="duplicate-assignment",
                    shift_id=shift.id,
                    company_id=company.id,
                    employee_id=employee_id,
                )
            )

    _downgrade_to_base()
    assert not set(Base.metadata.tables).intersection(inspect(engine).get_table_names())

    _upgrade_to_head()
    with engine.connect() as connection:
        assert compare_metadata(
            MigrationContext.configure(connection),
            Base.metadata,
        ) == []


def test_membership_migration_preserves_legacy_rows_without_identity_mapping(
    empty_test_database,
):
    engine = empty_test_database
    _upgrade_to_revision("b7e5d1c9a403")
    start_at = datetime(2026, 10, 29, 8, tzinfo=timezone.utc)
    end_at = datetime(2026, 10, 29, 16, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies "
                "(id, company_name, business_email, business_phone, "
                "business_address, industry, joining_code) "
                "VALUES ('legacy-company', 'Legacy Cafe', "
                "'legacy@example.test', '+41791234567', '1 Test Street', "
                "'Hospitality', 'LEGACY01')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO company_memberships (company_id, user_id, role) "
                "VALUES ('legacy-company', 'opaque-legacy-employee', 'employee')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO availabilities "
                "(id, company_id, employee_id, start_at, end_at) "
                "VALUES ('legacy-availability', 'legacy-company', "
                "'opaque-legacy-employee', :start_at, :end_at)"
            ),
            {"start_at": start_at, "end_at": end_at},
        )
        connection.execute(
            text(
                "INSERT INTO shifts (id, company_id, start_at, end_at) "
                "VALUES ('legacy-shift', 'legacy-company', :start_at, :end_at)"
            ),
            {"start_at": start_at, "end_at": end_at},
        )
        connection.execute(
            text(
                "INSERT INTO shift_assignments "
                "(id, shift_id, company_id, employee_id) VALUES "
                "('legacy-assignment', 'legacy-shift', 'legacy-company', "
                "'opaque-legacy-employee')"
            )
        )

    _upgrade_to_head()

    with engine.connect() as connection:
        membership = connection.execute(
            text(
                "SELECT user_id, role, account_user_id, public_id, display_name "
                "FROM company_memberships"
            )
        ).one()
        availability = connection.execute(
            text("SELECT company_id, employee_id FROM availabilities")
        ).one()
        assignment = connection.execute(
            text("SELECT company_id, employee_id FROM shift_assignments")
        ).one()

    assert membership.user_id == "opaque-legacy-employee"
    assert membership.role == "employee"
    assert membership.account_user_id is None
    assert membership.display_name is None
    assert UUID(membership.public_id)
    assert availability == ("legacy-company", "opaque-legacy-employee")
    assert assignment == ("legacy-company", "opaque-legacy-employee")
