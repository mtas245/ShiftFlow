from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TypedDict

import pytest

from app.application.manage_availability import (
    AuthenticationRequired,
    AvailabilityOverlap,
    ManageAvailability,
    ResourceNotFound,
)
from app.application.register_company import AuthenticatedUser
from app.domain.company_membership import CompanyRole


class ListingWindow(TypedDict):
    from_at: datetime
    to_at: datetime
    limit: int


def listing_window() -> ListingWindow:
    return {
        "from_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "to_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "limit": 100,
    }


class UnitOfWork:
    def __init__(self, memberships=(), availabilities=()):
        self.membership_items = list(memberships)
        self.records = list(availabilities)
        self.locked_employee_ids = []
        self.memberships_repository = MembershipRepository(self)
        self.availabilities_repository = AvailabilityRepository(self)

    def __enter__(self):
        self.memberships = self.memberships_repository
        self.availabilities = self.availabilities_repository
        return self

    def __exit__(self, _error_type, _error, _traceback):
        return False

    def lock_employee(self, employee_id):
        self.locked_employee_ids.append(employee_id)


class MembershipRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def get(self, company_id, employee_id):
        return next(
            (
                item
                for item in self.unit_of_work.membership_items
                if item.company_id == company_id
                and item.user_id == employee_id
            ),
            None,
        )


class AvailabilityRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def add(self, availability):
        self.unit_of_work.records.append(availability)

    def update(self, availability):
        self.unit_of_work.records = [
            availability if item.id == availability.id else item
            for item in self.unit_of_work.records
        ]

    def get(self, company_id, availability_id):
        return next(
            (
                item
                for item in self.unit_of_work.records
                if item.company_id == company_id and item.id == availability_id
            ),
            None,
        )

    def list_for_employee(self, company_id, employee_id):
        return [
            item
            for item in self.unit_of_work.records
            if item.company_id == company_id and item.employee_id == employee_id
        ]

    def list_for_company(self, company_id):
        return [
            item
            for item in self.unit_of_work.records
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
                for item in self.unit_of_work.records
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
                for item in self.unit_of_work.records
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


def instant(hour):
    return datetime(2026, 10, 1, hour, tzinfo=timezone.utc)


def member(company_id="company-1", employee_id="employee-1", role=None):
    return SimpleNamespace(
        company_id=company_id,
        user_id=employee_id,
        role=role or CompanyRole.EMPLOYEE,
    )


def service_for(*, memberships=(), availabilities=()):
    unit_of_work = UnitOfWork(memberships, availabilities)
    return ManageAvailability(lambda: unit_of_work), unit_of_work


def test_submit_rejects_missing_authenticated_principal():
    service, _ = service_for()

    with pytest.raises(AuthenticationRequired):
        service.submit(None, "company-1", instant(9), instant(12))


def test_submit_requires_membership_in_requested_company():
    service, _ = service_for(memberships=[member("company-2")])

    with pytest.raises(ResourceNotFound):
        service.submit(
            AuthenticatedUser("employee-1"),
            "company-1",
            instant(9),
            instant(12),
        )


def test_administrator_cannot_submit_employee_availability():
    service, _ = service_for(
        memberships=[member(role=CompanyRole.ADMINISTRATOR)]
    )

    with pytest.raises(ResourceNotFound):
        service.submit(
            AuthenticatedUser("employee-1"),
            "company-1",
            instant(9),
            instant(12),
        )


def test_administrator_cannot_read_employee_availability_as_their_own():
    service, _ = service_for(
        memberships=[member(role=CompanyRole.ADMINISTRATOR)]
    )

    with pytest.raises(ResourceNotFound):
        service.list_own(
            AuthenticatedUser("employee-1"), "company-1", **listing_window()
        )


def test_submit_locks_authenticated_employee_before_overlap_query():
    service, unit_of_work = service_for(memberships=[member()])

    result = service.submit(
        AuthenticatedUser("employee-1"),
        "company-1",
        instant(9),
        instant(12),
    )

    assert result.employee_id == "employee-1"
    assert unit_of_work.locked_employee_ids == ["employee-1"]


def test_submit_rejects_existing_overlap_for_same_company_and_employee():
    service, _ = service_for(memberships=[member()])
    principal = AuthenticatedUser("employee-1")
    service.submit(principal, "company-1", instant(9), instant(12))

    with pytest.raises(AvailabilityOverlap):
        service.submit(principal, "company-1", instant(11), instant(13))


def test_company_listing_requires_administrator_membership():
    employee_role = SimpleNamespace(value="employee")
    service, _ = service_for(
        memberships=[member(role=employee_role)]
    )

    with pytest.raises(ResourceNotFound):
        service.list_for_company(
            AuthenticatedUser("employee-1"),
            "company-1",
            **listing_window(),
        )
