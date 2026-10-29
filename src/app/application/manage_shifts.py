from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar

from app.application.scheduling_queries import (
    InvalidSchedulingQuery,
    validate_scheduling_window,
)
from app.domain.availability import Availability
from app.domain.company_membership import CompanyMembership, CompanyRole
from app.domain.shift import (
    InvalidShift,
    Shift,
    ShiftAssignment,
)


class AuthenticatedPrincipal(Protocol):
    user_id: str


class MembershipRepository(Protocol):
    def get(
        self, company_id: str, user_id: str
    ) -> CompanyMembership | None: ...

    def get_for_account(
        self, company_id: str, account_user_id: str
    ) -> CompanyMembership | None: ...

    def get_by_public_id(
        self, company_id: str, public_id: str
    ) -> CompanyMembership | None: ...


class AvailabilityRepository(Protocol):
    def list_for_employee(
        self, company_id: str, employee_id: str
    ) -> Sequence[Availability]: ...


class ShiftRepository(Protocol):
    def add(self, shift: Shift) -> None: ...

    def get(self, company_id: str, shift_id: str) -> Shift | None: ...

    def list_company_window(
        self,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> Sequence[Shift]: ...

    def list_employee_window(
        self,
        company_id: str,
        employee_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> Sequence[Shift]: ...


class ShiftAssignmentRepository(Protocol):
    def add(self, assignment: ShiftAssignment) -> None: ...

    def get_for_shift(
        self, company_id: str, shift_id: str
    ) -> ShiftAssignment | None: ...

    def has_overlap(
        self, account_user_id: str, start_at: datetime, end_at: datetime
    ) -> bool: ...


class UnitOfWork(Protocol):
    memberships: MembershipRepository
    availabilities: AvailabilityRepository
    shifts: ShiftRepository
    assignments: ShiftAssignmentRepository

    def lock_employee(self, employee_id: str) -> None: ...

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class AuthenticationRequired(Exception):
    code = "AUTHENTICATION_REQUIRED"


class ResourceNotFound(Exception):
    code = "RESOURCE_NOT_FOUND"


class Forbidden(Exception):
    code = "FORBIDDEN"


class EmployeeUnavailable(Exception):
    code = "EMPLOYEE_UNAVAILABLE"


class ShiftAssignmentConflict(Exception):
    code = "SHIFT_ASSIGNMENT_CONFLICT"


class ShiftSaveFailed(Exception):
    code = "SHIFT_SAVE_FAILED"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ManageShifts:
    _unit_of_work_factory: UnitOfWorkFactory

    def create(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Shift:
        employee_id = self._authenticated_user_id(principal)
        shift = Shift.create(
            company_id=company_id,
            start_at=start_at,
            end_at=end_at,
        )

        def create_in_transaction(unit_of_work: UnitOfWork) -> Shift:
            self._require_administrator(unit_of_work, company_id, employee_id)
            unit_of_work.shifts.add(shift)
            return shift

        return self._run(create_in_transaction)

    def assign(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        shift_id: str,
        membership_id: str,
    ) -> ShiftAssignment:
        administrator_id = self._authenticated_user_id(principal)

        def assign_in_transaction(unit_of_work: UnitOfWork) -> ShiftAssignment:
            self._require_administrator(
                unit_of_work, company_id, administrator_id
            )
            employee = unit_of_work.memberships.get_by_public_id(
                company_id,
                membership_id,
            )
            if (
                employee is None
                or employee.role is not CompanyRole.EMPLOYEE
                or employee.account_user_id is None
            ):
                raise ResourceNotFound
            unit_of_work.lock_employee(employee.account_user_id)

            shift = unit_of_work.shifts.get(company_id, shift_id)
            if shift is None:
                raise ResourceNotFound
            if unit_of_work.assignments.get_for_shift(company_id, shift_id):
                raise ShiftAssignmentConflict

            availability = unit_of_work.availabilities.list_for_employee(
                company_id, employee.user_id
            )
            if not any(
                interval.start_at <= shift.start_at
                and interval.end_at >= shift.end_at
                for interval in availability
            ):
                raise EmployeeUnavailable
            if unit_of_work.assignments.has_overlap(
                employee.account_user_id,
                shift.start_at,
                shift.end_at,
            ):
                raise ShiftAssignmentConflict

            assignment = ShiftAssignment.create(
                shift_id=shift.id,
                company_id=company_id,
                employee_id=employee.user_id,
                membership_id=employee.public_id,
                display_name=employee.display_name,
            )
            shift.with_assignment(assignment)
            unit_of_work.assignments.add(assignment)
            return assignment

        return self._run(assign_in_transaction)

    def list_for_company(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        limit: int,
        after_start: datetime | None = None,
        after_id: str | None = None,
    ) -> Sequence[Shift]:
        from_at, to_at = validate_scheduling_window(from_at, to_at)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidSchedulingQuery
        if (after_start is None) != (after_id is None):
            raise InvalidSchedulingQuery
        user_id = self._authenticated_user_id(principal)

        def list_in_transaction(unit_of_work: UnitOfWork) -> Sequence[Shift]:
            self._require_administrator(unit_of_work, company_id, user_id)
            return unit_of_work.shifts.list_company_window(
                company_id,
                from_at=from_at,
                to_at=to_at,
                after_start=after_start,
                after_id=after_id,
                limit=limit,
            )

        return self._run(list_in_transaction)

    def list_for_employee(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        limit: int,
        after_start: datetime | None = None,
        after_id: str | None = None,
    ) -> Sequence[Shift]:
        from_at, to_at = validate_scheduling_window(from_at, to_at)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidSchedulingQuery
        if (after_start is None) != (after_id is None):
            raise InvalidSchedulingQuery
        employee_id = self._authenticated_user_id(principal)

        def list_in_transaction(unit_of_work: UnitOfWork) -> Sequence[Shift]:
            membership = self._require_employee(
                unit_of_work,
                company_id,
                employee_id,
            )
            return unit_of_work.shifts.list_employee_window(
                company_id,
                membership.user_id,
                from_at=from_at,
                to_at=to_at,
                after_start=after_start,
                after_id=after_id,
                limit=limit,
            )

        return self._run(list_in_transaction)

    @staticmethod
    def _authenticated_user_id(
        principal: AuthenticatedPrincipal | None,
    ) -> str:
        if (
            principal is None
            or not isinstance(principal.user_id, str)
            or not principal.user_id.strip()
        ):
            raise AuthenticationRequired
        return principal.user_id

    @staticmethod
    def _require_administrator(
        unit_of_work: UnitOfWork, company_id: str, user_id: str
    ) -> CompanyMembership:
        membership = unit_of_work.memberships.get_for_account(
            company_id,
            user_id,
        )
        if membership is None:
            raise ResourceNotFound
        if membership.role is not CompanyRole.ADMINISTRATOR:
            raise Forbidden
        return membership

    @staticmethod
    def _require_employee(
        unit_of_work: UnitOfWork, company_id: str, employee_id: str
    ) -> CompanyMembership:
        membership = unit_of_work.memberships.get_for_account(
            company_id,
            employee_id,
        )
        if (
            membership is None
            or membership.role is not CompanyRole.EMPLOYEE
        ):
            raise ResourceNotFound
        return membership

    def _run(self, operation: Callable[[UnitOfWork], T]) -> T:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                return operation(unit_of_work)
        except (
            AuthenticationRequired,
            ResourceNotFound,
            Forbidden,
            EmployeeUnavailable,
            ShiftAssignmentConflict,
            ShiftSaveFailed,
            InvalidSchedulingQuery,
            InvalidShift,
        ):
            raise
        except Exception as error:
            raise ShiftSaveFailed from error
