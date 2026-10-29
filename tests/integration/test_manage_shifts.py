from datetime import datetime, timezone
from typing import TypedDict

import pytest

from app.application.manage_shifts import (
    EmployeeUnavailable,
    Forbidden,
    ManageShifts,
    ResourceNotFound,
    ShiftAssignmentConflict,
    ShiftSaveFailed,
)
from app.application.register_company import AuthenticatedUser
from app.domain.availability import Availability
from app.domain.company_membership import CompanyMembership, CompanyRole


class InMemoryDatabase:
    def __init__(self):
        self.memberships = []
        self.availabilities = []
        self.shifts = []
        self.assignments = []
        self.events = []
        self.fail_shift_add = False
        self.fail_assignment_add = False

    def unit_of_work(self):
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    def __init__(self, database):
        self.database = database
        self.pending_shifts = []
        self.pending_assignments = []

    def __enter__(self):
        self.memberships = MembershipRepository(self.database)
        self.availabilities = AvailabilityRepository(self.database)
        self.shifts = ShiftRepository(self)
        self.assignments = AssignmentRepository(self)
        return self

    def __exit__(self, error_type, _error, _traceback):
        if error_type is None:
            self.database.shifts.extend(self.pending_shifts)
            self.database.assignments.extend(self.pending_assignments)
        return False

    def lock_employee(self, employee_id):
        self.database.events.append(("lock", employee_id))


class MembershipRepository:
    def __init__(self, database):
        self.database = database

    def get(self, company_id, user_id):
        self.database.events.append(("membership_get", company_id, user_id))
        return next(
            (
                item
                for item in self.database.memberships
                if item.company_id == company_id and item.user_id == user_id
            ),
            None,
        )

    def get_for_account(self, company_id, account_user_id):
        self.database.events.append(
            ("membership_get_account", company_id, account_user_id)
        )
        return next(
            (
                item
                for item in self.database.memberships
                if item.company_id == company_id
                and item.account_user_id == account_user_id
            ),
            None,
        )

    def get_by_public_id(self, company_id, public_id):
        self.database.events.append(("membership_get_public", company_id, public_id))
        return next(
            (
                item
                for item in self.database.memberships
                if item.company_id == company_id
                and item.public_id == public_id
                and item.account_user_id is not None
            ),
            None,
        )


class AvailabilityRepository:
    def __init__(self, database):
        self.database = database

    def list_for_employee(self, company_id, employee_id):
        self.database.events.append(("availability_list", company_id, employee_id))
        return [
            item
            for item in self.database.availabilities
            if item.company_id == company_id and item.employee_id == employee_id
        ]

class ShiftRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def add(self, shift):
        self.unit_of_work.database.events.append(("shift_add", shift.id))
        if self.unit_of_work.database.fail_shift_add:
            raise RuntimeError("simulated shift persistence failure")
        self.unit_of_work.pending_shifts.append(shift)

    def get(self, company_id, shift_id):
        self.unit_of_work.database.events.append(("shift_get", company_id, shift_id))
        return next(
            (
                item
                for item in self.unit_of_work.database.shifts
                if item.company_id == company_id and item.id == shift_id
            ),
            None,
        )

    def list_for_company(self, company_id):
        self.unit_of_work.database.events.append(("shift_list_company", company_id))
        return [
            self._with_assignment(item)
            for item in self.unit_of_work.database.shifts
            if item.company_id == company_id
        ]

    def list_for_employee(self, company_id, employee_id):
        self.unit_of_work.database.events.append(
            ("shift_list_employee", company_id, employee_id)
        )
        return [
            self._with_assignment(shift)
            for shift in self.unit_of_work.database.shifts
            if shift.company_id == company_id
            and any(
                item.shift_id == shift.id and item.employee_id == employee_id
                for item in self.unit_of_work.database.assignments
            )
        ]

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
            self.list_for_company(company_id),
            from_at,
            to_at,
            after_start,
            after_id,
            limit,
        )

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
            self.list_for_employee(company_id, employee_id),
            from_at,
            to_at,
            after_start,
            after_id,
            limit,
        )

    @staticmethod
    def _window(shifts, from_at, to_at, after_start, after_id, limit):
        matching = [
            shift
            for shift in shifts
            if shift.start_at < to_at and shift.end_at > from_at
        ]
        if after_start is not None and after_id is not None:
            matching = [
                shift
                for shift in matching
                if (shift.start_at, shift.id) > (after_start, after_id)
            ]
        return sorted(matching, key=lambda item: (item.start_at, item.id))[
            : limit + 1
        ]

    def _with_assignment(self, shift):
        assignment = next(
            (
                item
                for item in self.unit_of_work.database.assignments
                if item.shift_id == shift.id
            ),
            None,
        )
        return shift.with_assignment(assignment) if assignment else shift


class AssignmentRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def get_for_shift(self, company_id, shift_id):
        self.unit_of_work.database.events.append(
            ("assignment_get", company_id, shift_id)
        )
        return next(
            (
                item
                for item in self.unit_of_work.database.assignments
                if item.company_id == company_id and item.shift_id == shift_id
            ),
            None,
        )

    def has_overlap(self, account_user_id, start_at, end_at):
        self.unit_of_work.database.events.append(
            ("assignment_overlap", account_user_id)
        )
        return any(
            self._membership(item).account_user_id == account_user_id
            and self._shift(item).start_at < end_at
            and start_at < self._shift(item).end_at
            for item in self.unit_of_work.database.assignments
        )

    def add(self, assignment):
        self.unit_of_work.database.events.append(
            ("assignment_add", assignment.shift_id)
        )
        if self.unit_of_work.database.fail_assignment_add:
            raise RuntimeError("simulated assignment persistence failure")
        self.unit_of_work.pending_assignments.append(assignment)

    def _shift(self, assignment):
        return next(
            shift
            for shift in self.unit_of_work.database.shifts
            if shift.id == assignment.shift_id
        )

    def _membership(self, assignment):
        return next(
            membership
            for membership in self.unit_of_work.database.memberships
            if membership.company_id == assignment.company_id
            and membership.user_id == assignment.employee_id
        )


def instant(day, hour):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


class ListingWindow(TypedDict):
    from_at: datetime
    to_at: datetime
    limit: int


def listing_window() -> ListingWindow:
    return {
        "from_at": instant(1, 0),
        "to_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
        "limit": 100,
    }


def member(company_id, user_id, role, *, display_name=None):
    return CompanyMembership.create(
        company_id=company_id,
        user_id=user_id,
        role=role,
        account_user_id=user_id,
        display_name=display_name or user_id,
    )


def membership_id(database, company_id, user_id):
    return next(
        (
            item.public_id
            for item in database.memberships
            if item.company_id == company_id and item.user_id == user_id
        ),
        None,
    )


def seed_company(database, company_id, *memberships):
    assert all(item.company_id == company_id for item in memberships)
    database.memberships.extend(memberships)


def seed_availability(database, company_id, employee_id, start_at, end_at):
    availability = Availability.create(
        company_id=company_id,
        employee_id=employee_id,
        start_at=start_at,
        end_at=end_at,
    )
    database.availabilities.append(availability)
    return availability


def service_for(database):
    return ManageShifts(database.unit_of_work)


def test_administrator_creates_company_scoped_shift():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
    )

    shift = service_for(database).create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )

    assert shift.company_id == "company-a"
    assert database.shifts == [shift]


def test_nonadministrator_cannot_create_or_assign_shift():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )

    with pytest.raises(Forbidden):
        service.create(
            AuthenticatedUser("employee-a"),
            "company-a",
            instant(1, 13),
            instant(1, 15),
        )
    with pytest.raises(Forbidden):
        service.assign(
            AuthenticatedUser("employee-a"),
            "company-a",
            shift.id,
            membership_id(database, "company-a", "employee-a"),
        )

    assert len(database.shifts) == 1
    assert database.assignments == []


def test_nonmember_cannot_create_or_read_another_company_shift():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )

    with pytest.raises(ResourceNotFound):
        service.create(
            AuthenticatedUser("outsider"),
            "company-a",
            instant(1, 13),
            instant(1, 15),
        )
    with pytest.raises(ResourceNotFound):
        service.list_for_company(
            AuthenticatedUser("outsider"), "company-a", **listing_window()
        )
    with pytest.raises(ResourceNotFound):
        service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            shift.id,
            "00000000-0000-0000-0000-000000000000",
        )


def test_assignment_requires_full_availability_coverage_and_serializes_checks():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 10), instant(1, 13)
    )

    with pytest.raises(EmployeeUnavailable):
        service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            shift.id,
            membership_id(database, "company-a", "employee-a"),
        )
    assert database.events.index(("lock", "employee-a")) < database.events.index(
        ("availability_list", "company-a", "employee-a")
    )


def test_assignment_resolves_company_membership_and_locks_global_account_identity():
    database = InMemoryDatabase()
    employee = member(
        "company-a",
        "employee-a",
        CompanyRole.EMPLOYEE,
        display_name="Cafe Team Member",
    )
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        employee,
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    seed_availability(
        database,
        "company-a",
        employee.user_id,
        instant(1, 8),
        instant(1, 13),
    )

    assignment = service.assign(
        AuthenticatedUser("admin-a"),
        "company-a",
        shift.id,
        employee.public_id,
    )

    assert ("lock", "employee-a") in database.events
    assert ("lock", employee.public_id) not in database.events
    assert assignment.employee_id == "employee-a"
    assert assignment.membership_id == employee.public_id
    assert assignment.display_name == "Cafe Team Member"


def test_assignment_requires_one_availability_interval_to_cover_entire_shift():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 9), instant(1, 10)
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 10), instant(1, 12)
    )

    with pytest.raises(EmployeeUnavailable):
        service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            shift.id,
            membership_id(database, "company-a", "employee-a"),
        )


def test_valid_assignment_is_persisted_and_shift_can_only_be_assigned_once():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
        member("company-a", "employee-b", CompanyRole.EMPLOYEE),
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 8), instant(1, 13)
    )
    seed_availability(
        database, "company-a", "employee-b", instant(1, 8), instant(1, 13)
    )

    assignment = service.assign(
        AuthenticatedUser("admin-a"),
        "company-a",
        shift.id,
        membership_id(database, "company-a", "employee-a"),
    )

    assert assignment.shift_id == shift.id
    assert database.assignments == [assignment]
    with pytest.raises(ShiftAssignmentConflict):
        service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            shift.id,
            membership_id(database, "company-a", "employee-b"),
        )
    assert database.assignments == [assignment]


def test_overlapping_assignments_conflict_across_companies_but_adjacency_is_valid():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
    )
    seed_company(
        database,
        "company-b",
        member("company-b", "admin-b", CompanyRole.ADMINISTRATOR),
        member("company-b", "employee-a", CompanyRole.EMPLOYEE),
    )
    service = service_for(database)
    first = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    conflict = service.create(
        AuthenticatedUser("admin-b"),
        "company-b",
        instant(1, 11),
        instant(1, 13),
    )
    adjacent = service.create(
        AuthenticatedUser("admin-b"),
        "company-b",
        instant(1, 12),
        instant(1, 14),
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 8), instant(1, 15)
    )
    seed_availability(
        database, "company-b", "employee-a", instant(1, 8), instant(1, 15)
    )
    service.assign(
        AuthenticatedUser("admin-a"),
        "company-a",
        first.id,
        membership_id(database, "company-a", "employee-a"),
    )
    with pytest.raises(ShiftAssignmentConflict):
        service.assign(
            AuthenticatedUser("admin-b"),
            "company-b",
            conflict.id,
            membership_id(database, "company-b", "employee-a"),
        )

    assignment = service.assign(
        AuthenticatedUser("admin-b"),
        "company-b",
        adjacent.id,
        membership_id(database, "company-b", "employee-a"),
    )

    assert assignment.shift_id == adjacent.id
    assert len(database.assignments) == 2


def test_shift_reads_are_role_scoped_and_company_scoped():
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
        member("company-a", "employee-b", CompanyRole.EMPLOYEE),
    )
    seed_company(
        database,
        "company-b",
        member("company-b", "admin-b", CompanyRole.ADMINISTRATOR),
        member("company-b", "employee-a", CompanyRole.EMPLOYEE),
    )
    service = service_for(database)
    assigned = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    unassigned = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 12),
        instant(1, 14),
    )
    other_company = service.create(
        AuthenticatedUser("admin-b"),
        "company-b",
        instant(1, 9),
        instant(1, 12),
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 8), instant(1, 13)
    )
    service.assign(
        AuthenticatedUser("admin-a"),
        "company-a",
        assigned.id,
        membership_id(database, "company-a", "employee-a"),
    )

    company_shifts = service.list_for_company(
        AuthenticatedUser("admin-a"), "company-a", **listing_window()
    )
    employee_shifts = service.list_for_employee(
        AuthenticatedUser("employee-a"), "company-a", **listing_window()
    )

    assert {item.id for item in company_shifts} == {assigned.id, unassigned.id}
    assert company_shifts[0].assignment is not None
    assert [item.id for item in employee_shifts] == [assigned.id]
    with pytest.raises(ResourceNotFound):
        service.list_for_company(
            AuthenticatedUser("admin-a"), "company-b", **listing_window()
        )
    with pytest.raises(Forbidden):
        service.list_for_company(
            AuthenticatedUser("employee-a"), "company-a", **listing_window()
        )
    assert service.list_for_employee(
        AuthenticatedUser("employee-b"), "company-a", **listing_window()
    ) == []
    assert other_company.company_id == "company-b"


@pytest.mark.parametrize("operation", ["create", "assign"])
def test_failed_shift_transaction_rolls_back(operation):
    database = InMemoryDatabase()
    seed_company(
        database,
        "company-a",
        member("company-a", "admin-a", CompanyRole.ADMINISTRATOR),
        member("company-a", "employee-a", CompanyRole.EMPLOYEE),
    )
    service = service_for(database)
    shift = service.create(
        AuthenticatedUser("admin-a"),
        "company-a",
        instant(1, 9),
        instant(1, 12),
    )
    seed_availability(
        database, "company-a", "employee-a", instant(1, 8), instant(1, 13)
    )

    if operation == "create":
        database.fail_shift_add = True
        action = lambda: service.create(
            AuthenticatedUser("admin-a"),
            "company-a",
            instant(1, 13),
            instant(1, 15),
        )
    else:
        database.fail_assignment_add = True
        action = lambda: service.assign(
            AuthenticatedUser("admin-a"),
            "company-a",
            shift.id,
            membership_id(database, "company-a", "employee-a"),
        )

    with pytest.raises(ShiftSaveFailed):
        action()
    assert database.shifts == [shift]
    assert database.assignments == []
