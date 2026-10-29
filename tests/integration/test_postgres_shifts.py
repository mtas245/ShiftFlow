import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from typing import TypedDict

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.application.manage_availability import ManageAvailability
from app.application.manage_shifts import (
    EmployeeUnavailable,
    ManageShifts,
    ShiftAssignmentConflict,
    ShiftSaveFailed,
)
from app.application.register_company import AuthenticatedUser
from app.domain.company_membership import CompanyRole
from app.domain.shift import Shift, ShiftAssignment
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
def postgres_engine():
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


def _seed_member(
    engine,
    company_id,
    user_id,
    role,
):
    with engine.begin() as connection:
        if not connection.scalar(
            select(func.count())
            .select_from(CompanyRecord)
            .where(CompanyRecord.id == company_id)
        ):
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
        if connection.scalar(
            select(UserRecord.id).where(UserRecord.id == user_id)
        ) is None:
            connection.execute(
            UserRecord.__table__.insert().values(
                id=user_id,
                email=f"{user_id}@example.test",
                password_hash="not-used-by-shift-tests",
            )
            )
        connection.execute(
            CompanyMembershipRecord.__table__.insert().values(
            company_id=company_id,
            user_id=user_id,
            role=role.value,
            account_user_id=user_id,
            display_name=user_id,
            )
        )
        return connection.scalar(
            select(CompanyMembershipRecord.public_id).where(
            CompanyMembershipRecord.company_id == company_id,
            CompanyMembershipRecord.user_id == user_id,
            )
        )


def _membership_id(engine, company_id, user_id):
    with engine.connect() as connection:
        return connection.scalar(
            select(CompanyMembershipRecord.public_id).where(
            CompanyMembershipRecord.company_id == company_id,
            CompanyMembershipRecord.user_id == user_id,
            )
        )


def _factory(engine):
    return SqlAlchemyUnitOfWorkFactory(engine)


def _shifts(engine):
    return ManageShifts(_factory(engine))


def _instant(day, hour):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


class _ListingWindow(TypedDict):
    from_at: datetime
    to_at: datetime
    limit: int


def _listing_window() -> _ListingWindow:
    return {
        "from_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "to_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "limit": 100,
    }


def _seed_availability(engine, company_id, employee_id, start_at, end_at):
    return ManageAvailability(_factory(engine)).submit(
        AuthenticatedUser(employee_id),
        company_id,
        start_at,
        end_at,
    )


def _create_shift(service, company_id, administrator_id, start_at, end_at):
    return service.create(
        AuthenticatedUser(administrator_id),
        company_id,
        start_at,
        end_at,
    )


def test_shift_tables_enforce_interval_membership_and_single_assignment():
    shifts = Base.metadata.tables["shifts"]
    assignments = Base.metadata.tables["shift_assignments"]

    assert {"company_id", "start_at", "end_at"} <= set(shifts.c.keys())
    assert shifts.c.start_at.type.timezone
    assert shifts.c.end_at.type.timezone
    assert any(
        constraint.name == "ck_shifts_end_after_start"
        for constraint in shifts.constraints
    )
    assert any(
        constraint.name == "uq_shift_assignments_one_per_shift"
        for constraint in assignments.constraints
    )
    assert any(
        {element.target_fullname for element in constraint.elements}
        == {"shifts.id", "shifts.company_id"}
        for constraint in assignments.foreign_key_constraints
    )
    assert any(
        {element.target_fullname for element in constraint.elements}
        == {
            "company_memberships.company_id",
            "company_memberships.user_id",
        }
        for constraint in assignments.foreign_key_constraints
    )


def test_postgres_persists_assignments_and_tenant_scoped_schedule_reads(
    postgres_engine,
):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _seed_member(
        postgres_engine, "company-b", "admin-b", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-b", "employee-a", CompanyRole.EMPLOYEE
    )
    service = _shifts(postgres_engine)
    first = _create_shift(
        service, "company-a", "admin-a", _instant(28, 9), _instant(28, 12)
    )
    unassigned = _create_shift(
        service, "company-a", "admin-a", _instant(28, 12), _instant(28, 14)
    )
    other_company = _create_shift(
        service, "company-b", "admin-b", _instant(28, 9), _instant(28, 12)
    )
    _seed_availability(
        postgres_engine,
        "company-a",
        "employee-a",
        _instant(28, 8),
        _instant(28, 14),
    )
    assignment = service.assign(
        AuthenticatedUser("admin-a"),
        "company-a",
        first.id,
        _membership_id(postgres_engine, "company-a", "employee-a"),
    )

    admin_schedule = service.list_for_company(
        AuthenticatedUser("admin-a"), "company-a", **_listing_window()
    )
    employee_schedule = service.list_for_employee(
        AuthenticatedUser("employee-a"), "company-a", **_listing_window()
    )

    assert assignment.shift_id == first.id
    assert [item.id for item in admin_schedule] == [first.id, unassigned.id]
    assert admin_schedule[0].assignment == assignment
    assert admin_schedule[1].assignment is None
    assert [item.id for item in employee_schedule] == [first.id]
    assert all(item.id != other_company.id for item in admin_schedule)


def test_postgres_requires_one_availability_interval_to_cover_shift(
    postgres_engine,
):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    service = _shifts(postgres_engine)
    shift = _create_shift(
        service, "company-a", "admin-a", _instant(28, 9), _instant(28, 12)
    )
    _seed_availability(
        postgres_engine,
        "company-a",
        "employee-a",
        _instant(28, 9),
        _instant(28, 10),
    )
    _seed_availability(
        postgres_engine,
        "company-a",
        "employee-a",
        _instant(28, 10),
        _instant(28, 12),
    )

    with pytest.raises(EmployeeUnavailable):
        service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            shift.id,
            _membership_id(postgres_engine, "company-a", "employee-a"),
        )


def test_postgres_allows_adjacent_employee_assignments_across_companies(
    postgres_engine,
):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _seed_member(
        postgres_engine, "company-b", "admin-b", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-b", "employee-a", CompanyRole.EMPLOYEE
    )
    service = _shifts(postgres_engine)
    first = _create_shift(
        service, "company-a", "admin-a", _instant(28, 9), _instant(28, 12)
    )
    adjacent = _create_shift(
        service, "company-b", "admin-b", _instant(28, 12), _instant(28, 14)
    )
    _seed_availability(
        postgres_engine,
        "company-a",
        "employee-a",
        _instant(28, 8),
        _instant(28, 12),
    )
    _seed_availability(
        postgres_engine,
        "company-b",
        "employee-a",
        _instant(28, 12),
        _instant(28, 15),
    )

    first_assignment = service.assign(
        AuthenticatedUser("admin-a"),
        "company-a",
        first.id,
        _membership_id(postgres_engine, "company-a", "employee-a"),
    )
    adjacent_assignment = service.assign(
        AuthenticatedUser("admin-b"),
        "company-b",
        adjacent.id,
        _membership_id(postgres_engine, "company-b", "employee-a"),
    )

    assert first_assignment.employee_id == adjacent_assignment.employee_id


def test_concurrent_overlapping_assignments_across_companies_commit_one(
    postgres_engine,
):
    for company_id, administrator_id in (
        ("company-a", "admin-a"),
        ("company-b", "admin-b"),
    ):
        _seed_member(
            postgres_engine,
            company_id,
            administrator_id,
            CompanyRole.ADMINISTRATOR,
        )
        _seed_member(
            postgres_engine,
            company_id,
            "employee-a",
            CompanyRole.EMPLOYEE,
        )
        _seed_availability(
            postgres_engine,
            company_id,
            "employee-a",
            _instant(28, 8),
            _instant(28, 15),
        )

    service = _shifts(postgres_engine)
    shifts = [
        _create_shift(
            service,
            company_id,
            administrator_id,
            start_at,
            end_at,
        )
        for company_id, administrator_id, start_at, end_at in (
            ("company-a", "admin-a", _instant(28, 9), _instant(28, 12)),
            ("company-b", "admin-b", _instant(28, 11), _instant(28, 13)),
        )
    ]
    barrier = Barrier(2)

    def assign(company_id, administrator_id, shift):
        barrier.wait(timeout=5)
        try:
            service.assign(
                AuthenticatedUser(administrator_id),
                company_id,
                shift.id,
                _membership_id(postgres_engine, company_id, "employee-a"),
            )
            return "assigned"
        except ShiftAssignmentConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda parameters: assign(*parameters),
                [
                    ("company-a", "admin-a", shifts[0]),
                    ("company-b", "admin-b", shifts[1]),
                ],
            )
        )

    assert sorted(outcomes) == ["assigned", "conflict"]
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(
                Base.metadata.tables["shift_assignments"]
            )
        ) == 1


def test_concurrent_assignment_of_same_shift_returns_safe_conflict(
    postgres_engine, monkeypatch
):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    for employee_id in ("employee-a", "employee-b"):
        _seed_member(
            postgres_engine, "company-a", employee_id, CompanyRole.EMPLOYEE
        )
        _seed_availability(
            postgres_engine,
            "company-a",
            employee_id,
            _instant(28, 8),
            _instant(28, 13),
        )

    service = _shifts(postgres_engine)
    shift = _create_shift(
        service, "company-a", "admin-a", _instant(28, 9), _instant(28, 12)
    )
    from app.infrastructure.persistence.repositories import (
        SqlAlchemyShiftAssignmentRepository,
    )

    original_get = SqlAlchemyShiftAssignmentRepository.get_for_shift
    checked_assignment = Barrier(2)

    def synchronized_get(repository, company_id, shift_id):
        result = original_get(repository, company_id, shift_id)
        checked_assignment.wait(timeout=5)
        return result

    monkeypatch.setattr(
        SqlAlchemyShiftAssignmentRepository,
        "get_for_shift",
        synchronized_get,
    )

    def assign(employee_id):
        try:
            service.assign(
                AuthenticatedUser("admin-a"),
                "company-a",
                shift.id,
                _membership_id(postgres_engine, "company-a", employee_id),
            )
            return "assigned"
        except ShiftAssignmentConflict:
            return "conflict"
        except ShiftSaveFailed:
            return "save-failed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(assign, ("employee-a", "employee-b")))

    assert sorted(outcomes) == ["assigned", "conflict"]
    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(
                Base.metadata.tables["shift_assignments"]
            )
        ) == 1


def test_postgres_assignment_foreign_keys_enforce_company_consistency(
    postgres_engine,
):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _seed_member(
        postgres_engine, "company-b", "employee-b", CompanyRole.EMPLOYEE
    )
    shift = _create_shift(
        _shifts(postgres_engine),
        "company-a",
        "admin-a",
        _instant(28, 9),
        _instant(28, 12),
    )

    for assignment_id, company_id, employee_id in (
        ("bad-company", "company-b", "employee-b"),
        ("bad-membership", "company-a", "not-a-member"),
    ):
        with pytest.raises(IntegrityError):
            with postgres_engine.begin() as connection:
                connection.execute(
                    Base.metadata.tables["shift_assignments"]
                    .insert()
                    .values(
                        id=assignment_id,
                        shift_id=shift.id,
                        company_id=company_id,
                        employee_id=employee_id,
                    )
                )


def test_postgres_rejects_nonpositive_shift_interval(postgres_engine):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )

    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["shifts"].insert().values(
                    id="invalid-shift",
                    company_id="company-a",
                    start_at=_instant(28, 12),
                    end_at=_instant(28, 12),
                )
            )


def test_postgres_unit_of_work_rolls_back_shift_and_assignment_writes(
    postgres_engine, monkeypatch
):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    factory = _factory(postgres_engine)
    shift = Shift.create(
        company_id="company-a",
        start_at=_instant(28, 9),
        end_at=_instant(28, 12),
    )

    with pytest.raises(RuntimeError, match="abort transaction"):
        with factory() as unit_of_work:
            unit_of_work.shifts.add(shift)
            raise RuntimeError("abort transaction")

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(Base.metadata.tables["shifts"])
        ) == 0

    service = _shifts(postgres_engine)
    persisted_shift = _create_shift(
        service, "company-a", "admin-a", _instant(28, 9), _instant(28, 12)
    )
    _seed_availability(
        postgres_engine,
        "company-a",
        "employee-a",
        _instant(28, 8),
        _instant(28, 13),
    )
    assignment = ShiftAssignment.create(
        shift_id=persisted_shift.id,
        company_id="company-a",
        employee_id="employee-a",
    )
    from app.infrastructure.persistence.repositories import (
        SqlAlchemyShiftAssignmentRepository,
    )

    original_add = SqlAlchemyShiftAssignmentRepository.add

    def add_then_fail(repository, value):
        original_add(repository, value)
        raise RuntimeError("abort assignment transaction")

    monkeypatch.setattr(SqlAlchemyShiftAssignmentRepository, "add", add_then_fail)

    with pytest.raises(ShiftSaveFailed):
        service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            persisted_shift.id,
            _membership_id(postgres_engine, "company-a", "employee-a"),
        )

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(
                Base.metadata.tables["shift_assignments"]
            )
        ) == 0


def test_postgres_enforces_one_assignment_per_shift(postgres_engine):
    _seed_member(
        postgres_engine, "company-a", "admin-a", CompanyRole.ADMINISTRATOR
    )
    _seed_member(
        postgres_engine, "company-a", "employee-a", CompanyRole.EMPLOYEE
    )
    _seed_member(
        postgres_engine, "company-a", "employee-b", CompanyRole.EMPLOYEE
    )
    service = _shifts(postgres_engine)
    shift = _create_shift(
        service, "company-a", "admin-a", _instant(28, 9), _instant(28, 12)
    )
    with postgres_engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["shift_assignments"].insert().values(
                id="assignment-a",
                shift_id=shift.id,
                company_id="company-a",
                employee_id="employee-a",
            )
        )

    with pytest.raises(IntegrityError):
        with postgres_engine.begin() as connection:
            connection.execute(
                Base.metadata.tables["shift_assignments"].insert().values(
                    id="assignment-b",
                    shift_id=shift.id,
                    company_id="company-a",
                    employee_id="employee-b",
                )
            )
