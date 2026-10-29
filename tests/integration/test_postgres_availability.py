import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier, Event
from typing import TypedDict

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url

from app.application.manage_availability import (
    AvailabilityOverlap,
    ManageAvailability,
    ResourceNotFound,
)
from app.application.register_company import AuthenticatedUser
from app.domain.availability import Availability
from app.domain.company_membership import CompanyRole
from app.infrastructure.persistence.models import (
    Base,
    CompanyMembershipRecord,
    CompanyRecord,
)
from app.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWorkFactory


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


def _add_member(engine, company_id, user_id, role="employee"):
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
        connection.execute(
            CompanyMembershipRecord.__table__.insert().values(
                company_id=company_id,
                user_id=user_id,
                role=role.value if isinstance(role, CompanyRole) else role,
            )
        )


def _service(engine):
    return ManageAvailability(SqlAlchemyUnitOfWorkFactory(engine))


def _instant(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


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


def test_availability_model_defines_persistence_integrity():
    table = Base.metadata.tables["availabilities"]

    assert {"company_id", "employee_id", "start_at", "end_at"} <= set(table.c.keys())
    assert any(
        constraint.name == "ck_availabilities_end_after_start"
        for constraint in table.constraints
    )
    assert any(
        {column.name for column in constraint.columns}
        == {"company_id", "employee_id"}
        and any(
            element.target_fullname
            in {"company_memberships.company_id", "company_memberships.user_id"}
            for element in constraint.elements
        )
        for constraint in table.foreign_key_constraints
    )
    assert any(
        index.columns.keys() == ["company_id", "employee_id", "start_at"]
        for index in table.indexes
    )
    assert table.c.start_at.type.timezone
    assert table.c.end_at.type.timezone


def test_postgres_persists_utc_overnight_intervals_and_allows_adjacency(
    postgres_engine,
):
    _add_member(postgres_engine, "company-a", "employee-a")
    service = _service(postgres_engine)
    principal = AuthenticatedUser("employee-a")

    overnight = service.submit(
        principal,
        "company-a",
        datetime.fromisoformat("2026-10-28T22:00:00+02:00"),
        datetime.fromisoformat("2026-10-29T01:00:00+02:00"),
    )
    adjacent = service.submit(
        principal,
        "company-a",
        _instant(28, 23),
        _instant(29, 1),
    )

    assert overnight.start_at == _instant(28, 20)
    assert overnight.end_at == _instant(28, 23)
    assert adjacent.start_at == overnight.end_at
    assert service.list_own(
        principal, "company-a", **_listing_window()
    ) == [overnight, adjacent]


def test_postgres_update_is_in_place_and_rejects_overlap(postgres_engine):
    _add_member(postgres_engine, "company-a", "employee-a")
    service = _service(postgres_engine)
    principal = AuthenticatedUser("employee-a")
    original = service.submit(
        principal, "company-a", _instant(28, 9), _instant(28, 12)
    )

    updated = service.update(
        principal,
        "company-a",
        original.id,
        _instant(28, 10),
        _instant(28, 13),
    )

    assert updated.id == original.id
    assert service.list_own(
        principal, "company-a", **_listing_window()
    ) == [updated]

    with pytest.raises(AvailabilityOverlap):
        service.submit(
            principal, "company-a", _instant(28, 12), _instant(28, 14)
        )
    assert service.list_own(
        principal, "company-a", **_listing_window()
    ) == [updated]


def test_postgres_reads_are_scoped_to_authorized_tenant_and_employee(
    postgres_engine,
):
    _add_member(postgres_engine, "company-a", "employee-a")
    _add_member(
        postgres_engine,
        "company-a",
        "admin-a",
        CompanyRole.ADMINISTRATOR,
    )
    _add_member(postgres_engine, "company-b", "employee-b")
    service = _service(postgres_engine)

    availability_a = service.submit(
        AuthenticatedUser("employee-a"),
        "company-a",
        _instant(28, 9),
        _instant(28, 12),
    )
    availability_b = service.submit(
        AuthenticatedUser("employee-b"),
        "company-b",
        _instant(28, 9),
        _instant(28, 12),
    )

    assert service.list_own(
        AuthenticatedUser("employee-a"),
        "company-a",
        **_listing_window(),
    ) == [availability_a]
    assert service.list_for_company(
        AuthenticatedUser("admin-a"),
        "company-a",
        **_listing_window(),
    ) == [availability_a]
    with pytest.raises(ResourceNotFound):
        service.list_for_company(
            AuthenticatedUser("admin-a"),
            "company-b",
            **_listing_window(),
        )
    with pytest.raises(ResourceNotFound):
        service.update(
            AuthenticatedUser("employee-a"),
            "company-a",
            availability_b.id,
            _instant(28, 10),
            _instant(28, 13),
        )


def test_postgres_uow_rolls_back_flushed_availability(postgres_engine):
    _add_member(postgres_engine, "company-a", "employee-a")
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    availability = Availability.create(
        company_id="company-a",
        employee_id="employee-a",
        start_at=_instant(28, 9),
        end_at=_instant(28, 12),
    )

    with pytest.raises(RuntimeError, match="abort transaction"):
        with factory() as unit_of_work:
            unit_of_work.availabilities.add(availability)
            raise RuntimeError("abort transaction")

    with postgres_engine.connect() as connection:
        assert connection.scalar(
            select(func.count()).select_from(
                Base.metadata.tables["availabilities"]
            )
        ) == 0


def test_concurrent_overlapping_submissions_only_commit_one(postgres_engine):
    _add_member(postgres_engine, "company-a", "employee-a")
    service = _service(postgres_engine)
    barrier = Barrier(2)

    def submit(start_hour, end_hour):
        barrier.wait(timeout=5)
        try:
            service.submit(
                AuthenticatedUser("employee-a"),
                "company-a",
                _instant(28, start_hour),
                _instant(28, end_hour),
            )
            return "created"
        except AvailabilityOverlap:
            return "overlap"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda interval: submit(*interval),
                [(9, 12), (10, 13)],
            )
        )

    assert sorted(results) == ["created", "overlap"]
    assert len(
        service.list_own(
            AuthenticatedUser("employee-a"),
            "company-a",
            **_listing_window(),
        )
    ) == 1


def test_advisory_lock_serializes_same_global_identity_across_companies(
    postgres_engine,
):
    factory = SqlAlchemyUnitOfWorkFactory(postgres_engine)
    attempting_lock = Event()
    acquired_lock = Event()

    def acquire_second_lock():
        attempting_lock.set()
        with factory() as unit_of_work:
            unit_of_work.lock_employee("same-global-employee")
        acquired_lock.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with factory() as first_unit_of_work:
            first_unit_of_work.lock_employee("same-global-employee")
            future = executor.submit(acquire_second_lock)
            assert attempting_lock.wait(timeout=5)
            assert not acquired_lock.wait(timeout=0.2)
        future.result(timeout=5)
    assert acquired_lock.is_set()
