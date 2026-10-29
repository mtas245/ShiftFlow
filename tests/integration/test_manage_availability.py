from datetime import datetime, timezone
from typing import TypedDict

import pytest

from app.application.manage_availability import (
    AuthenticationRequired,
    AvailabilityOverlap,
    AvailabilitySaveFailed,
    ManageAvailability,
    ResourceNotFound,
)
from app.application.register_company import AuthenticatedUser
from app.domain.company_membership import CompanyMembership, CompanyRole


class InMemoryDatabase:
    def __init__(self):
        self.memberships = []
        self.availabilities = []
        self.events = []
        self.fail_add = False
        self.fail_update = False

    def unit_of_work(self):
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    def __init__(self, database):
        self.database = database
        self.pending_adds = []
        self.pending_updates = []

    def __enter__(self):
        self.memberships = InMemoryMembershipRepository(self)
        self.availabilities = InMemoryAvailabilityRepository(self)
        return self

    def __exit__(self, exception_type, _exception, _traceback):
        if exception_type is None:
            self.database.availabilities.extend(self.pending_adds)
            for updated in self.pending_updates:
                self.database.availabilities = [
                    updated if item.id == updated.id else item
                    for item in self.database.availabilities
                ]
        return False

    def lock_employee(self, employee_id):
        self.database.events.append(("lock", employee_id))


class InMemoryMembershipRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def get(self, company_id, employee_id):
        self.unit_of_work.database.events.append(("membership_get", company_id))
        return next(
            (
                membership
                for membership in self.unit_of_work.database.memberships
                if membership.company_id == company_id
                and membership.user_id == employee_id
            ),
            None,
        )


class InMemoryAvailabilityRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def add(self, availability):
        self.unit_of_work.database.events.append(("add", availability.id))
        if self.unit_of_work.database.fail_add:
            raise RuntimeError("simulated persistence failure")
        self.unit_of_work.pending_adds.append(availability)

    def update(self, availability):
        self.unit_of_work.database.events.append(("update", availability.id))
        if self.unit_of_work.database.fail_update:
            raise RuntimeError("simulated persistence failure")
        self.unit_of_work.pending_updates.append(availability)

    def get(self, company_id, availability_id):
        self.unit_of_work.database.events.append(("availability_get", company_id))
        return next(
            (
                item
                for item in self.unit_of_work.database.availabilities
                if item.company_id == company_id and item.id == availability_id
            ),
            None,
        )

    def list_for_employee(self, company_id, employee_id):
        self.unit_of_work.database.events.append(("list_employee", company_id))
        return [
            item
            for item in self.unit_of_work.database.availabilities
            if item.company_id == company_id and item.employee_id == employee_id
        ]

    def list_for_company(self, company_id):
        self.unit_of_work.database.events.append(("list_company", company_id))
        return [
            item
            for item in self.unit_of_work.database.availabilities
            if item.company_id == company_id
        ]

    def list_employee_window(
        self,
        company_id,
        employee_id,
        *,
        from_at,
        to_at,
        after_start,
        after_id,
        limit,
    ):
        return self._window(
            [
                item
                for item in self.unit_of_work.database.availabilities
                if item.company_id == company_id and item.employee_id == employee_id
            ],
            from_at,
            to_at,
            after_start,
            after_id,
            limit,
        )

    def list_company_window(
        self,
        company_id,
        *,
        from_at,
        to_at,
        after_start,
        after_id,
        limit,
    ):
        return self._window(
            [
                item
                for item in self.unit_of_work.database.availabilities
                if item.company_id == company_id
            ],
            from_at,
            to_at,
            after_start,
            after_id,
            limit,
        )

    @staticmethod
    def _window(items, from_at, to_at, after_start, after_id, limit):
        matching = [
            item
            for item in items
            if item.start_at < to_at and item.end_at > from_at
        ]
        if after_start is not None and after_id is not None:
            matching = [
                item
                for item in matching
                if (item.start_at, item.id) > (after_start, after_id)
            ]
        return sorted(matching, key=lambda item: (item.start_at, item.id))[
            : limit + 1
        ]


def instant(hour, *, day=1):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


class ListingWindow(TypedDict):
    from_at: datetime
    to_at: datetime
    limit: int


def listing_window() -> ListingWindow:
    return {
        "from_at": instant(0, day=1),
        "to_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "limit": 100,
    }


def member(
    company_id, employee_id, role=CompanyRole.EMPLOYEE
):
    return CompanyMembership.create(
        company_id=company_id,
        user_id=employee_id,
        role=role,
    )


def seed_memberships(database, *memberships):
    database.memberships.extend(memberships)


def test_employee_submits_availability_using_authenticated_identity():
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)

    availability = service.submit(
        AuthenticatedUser("employee-1"),
        "company-1",
        instant(9),
        instant(12),
    )

    assert availability.company_id == "company-1"
    assert availability.employee_id == "employee-1"
    assert database.availabilities == [availability]
    assert database.events.index(("lock", "employee-1")) < database.events.index(
        ("list_employee", "company-1")
    )
    assert database.events.index(("lock", "employee-1")) < database.events.index(
        ("add", availability.id)
    )


def test_employee_can_submit_adjacent_intervals_but_not_overlapping_ones():
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)
    principal = AuthenticatedUser("employee-1")
    service.submit(principal, "company-1", instant(9), instant(12))

    adjacent = service.submit(
        principal, "company-1", instant(12), instant(14)
    )

    assert adjacent.start_at == instant(12)
    with pytest.raises(AvailabilityOverlap) as error:
        service.submit(
            principal, "company-1", instant(11), instant(13)
        )
    assert error.value.code == "AVAILABILITY_OVERLAP"
    assert len(database.availabilities) == 2


def test_employee_updates_availability_in_place_and_excludes_itself_from_overlap():
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)
    principal = AuthenticatedUser("employee-1")
    original = service.submit(
        principal, "company-1", instant(9), instant(12)
    )

    updated = service.update(
        principal,
        "company-1",
        original.id,
        instant(10),
        instant(13),
    )

    assert updated.id == original.id
    assert len(database.availabilities) == 1
    assert database.availabilities[0] == updated
    update_lock_index = max(
        index
        for index, event in enumerate(database.events)
        if event == ("lock", "employee-1")
    )
    assert update_lock_index < database.events.index(
        ("availability_get", "company-1"), update_lock_index
    )
    assert update_lock_index < database.events.index(
        ("list_employee", "company-1"), update_lock_index
    )
    assert update_lock_index < database.events.index(
        ("update", updated.id), update_lock_index
    )


def test_update_rejects_overlap_with_another_interval_without_changing_records():
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)
    principal = AuthenticatedUser("employee-1")
    first = service.submit(principal, "company-1", instant(9), instant(12))
    service.submit(principal, "company-1", instant(13), instant(15))

    with pytest.raises(AvailabilityOverlap):
        service.update(
            principal, "company-1", first.id, instant(11), instant(14)
        )

    assert [(item.id, item.start_at, item.end_at) for item in database.availabilities] == [
        (first.id, instant(9), instant(12)),
        (database.availabilities[1].id, instant(13), instant(15)),
    ]


def test_nonmember_and_cross_company_access_fail_without_disclosing_resources():
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)

    with pytest.raises(ResourceNotFound) as error:
        service.submit(
            AuthenticatedUser("employee-1"),
            "company-2",
            instant(9),
            instant(12),
        )
    assert error.value.code == "RESOURCE_NOT_FOUND"
    assert database.availabilities == []


def test_employees_read_only_their_own_company_availability():
    database = InMemoryDatabase()
    seed_memberships(
        database,
        member("company-1", "employee-1"),
        member("company-1", "employee-2"),
        member("company-2", "employee-1"),
    )
    service = ManageAvailability(database.unit_of_work)
    service.submit(
        AuthenticatedUser("employee-1"), "company-1", instant(9), instant(12)
    )
    service.submit(
        AuthenticatedUser("employee-2"), "company-1", instant(13), instant(15)
    )
    service.submit(
        AuthenticatedUser("employee-1"), "company-2", instant(9), instant(12)
    )

    own = service.list_own(
        AuthenticatedUser("employee-1"),
        "company-1",
        **listing_window(),
    )

    assert len(own) == 1
    assert own[0].employee_id == "employee-1"
    assert own[0].company_id == "company-1"


def test_unauthenticated_user_cannot_submit_or_read_availability():
    database = InMemoryDatabase()
    service = ManageAvailability(database.unit_of_work)

    with pytest.raises(AuthenticationRequired) as submit_error:
        service.submit(None, "company-1", instant(9), instant(12))
    with pytest.raises(AuthenticationRequired) as read_error:
        service.list_own(None, "company-1", **listing_window())

    assert submit_error.value.code == "AUTHENTICATION_REQUIRED"
    assert read_error.value.code == "AUTHENTICATION_REQUIRED"
    assert database.availabilities == []


def test_company_administrator_lists_only_availability_in_their_company():
    database = InMemoryDatabase()
    seed_memberships(
        database,
        member("company-1", "admin-1", CompanyRole.ADMINISTRATOR),
        member("company-1", "employee-1"),
        member("company-2", "employee-2"),
    )
    service = ManageAvailability(database.unit_of_work)
    service.submit(
        AuthenticatedUser("employee-1"), "company-1", instant(9), instant(12)
    )
    service.submit(
        AuthenticatedUser("employee-2"), "company-2", instant(13), instant(15)
    )

    items = service.list_for_company(
        AuthenticatedUser("admin-1"),
        "company-1",
        **listing_window(),
    )

    assert len(items) == 1
    assert items[0].company_id == "company-1"


def test_nonadministrator_cannot_list_company_availability():
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)

    with pytest.raises(ResourceNotFound) as error:
        service.list_for_company(
            AuthenticatedUser("employee-1"),
            "company-1",
            **listing_window(),
        )

    assert error.value.code == "RESOURCE_NOT_FOUND"


def test_employee_cannot_update_another_employees_availability():
    database = InMemoryDatabase()
    seed_memberships(
        database,
        member("company-1", "employee-1"),
        member("company-1", "employee-2"),
    )
    service = ManageAvailability(database.unit_of_work)
    other = service.submit(
        AuthenticatedUser("employee-1"),
        "company-1",
        instant(9),
        instant(12),
    )

    with pytest.raises(ResourceNotFound):
        service.update(
            AuthenticatedUser("employee-2"),
            "company-1",
            other.id,
            instant(10),
            instant(13),
        )

    assert database.availabilities[0] == other


@pytest.mark.parametrize("operation", ["submit", "update"])
def test_persistence_failure_rolls_back_and_uses_generic_save_error(operation):
    database = InMemoryDatabase()
    seed_memberships(database, member("company-1", "employee-1"))
    service = ManageAvailability(database.unit_of_work)
    principal = AuthenticatedUser("employee-1")
    existing = service.submit(
        principal, "company-1", instant(9), instant(12)
    )
    if operation == "submit":
        database.fail_add = True
        action = lambda: service.submit(
            principal, "company-1", instant(13), instant(15)
        )
    else:
        database.fail_update = True
        action = lambda: service.update(
            principal,
            "company-1",
            existing.id,
            instant(10),
            instant(13),
        )

    with pytest.raises(AvailabilitySaveFailed) as error:
        action()

    assert error.value.code == "AVAILABILITY_SAVE_FAILED"
    assert database.availabilities == [existing]
