from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TypeVar

from app.application.scheduling_queries import (
    InvalidSchedulingQuery,
    validate_scheduling_window,
)
from app.domain.availability import Availability, InvalidAvailability
from app.domain.company_membership import CompanyMembership, CompanyRole


class AuthenticatedPrincipal(Protocol):
    user_id: str


class MembershipRepository(Protocol):
    def get(
        self, company_id: str, employee_id: str
    ) -> CompanyMembership | None: ...


class AvailabilityRepository(Protocol):
    def add(self, availability: Availability) -> None: ...

    def update(self, availability: Availability) -> None: ...

    def get(self, company_id: str, availability_id: str) -> Availability | None: ...

    def list_for_employee(
        self, company_id: str, employee_id: str
    ) -> Sequence[Availability]: ...

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
    ) -> Sequence[Availability]: ...

    def list_company_window(
        self,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        after_start: datetime | None,
        after_id: str | None,
        limit: int,
    ) -> Sequence[Availability]: ...


class UnitOfWork(Protocol):
    memberships: MembershipRepository
    availabilities: AvailabilityRepository

    def lock_employee(self, employee_id: str) -> None: ...

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class AuthenticationRequired(Exception):
    code = "AUTHENTICATION_REQUIRED"


class ResourceNotFound(Exception):
    code = "RESOURCE_NOT_FOUND"


class AvailabilityOverlap(Exception):
    code = "AVAILABILITY_OVERLAP"


class AvailabilitySaveFailed(Exception):
    code = "AVAILABILITY_SAVE_FAILED"


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ManageAvailability:
    _unit_of_work_factory: UnitOfWorkFactory

    def submit(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Availability:
        employee_id = self._authenticated_user_id(principal)
        availability = Availability.create(
            company_id=company_id,
            employee_id=employee_id,
            start_at=start_at,
            end_at=end_at,
        )

        def submit_in_transaction(unit_of_work: UnitOfWork) -> Availability:
            self._require_employee_membership(
                unit_of_work, company_id, employee_id
            )
            unit_of_work.lock_employee(employee_id)
            existing = unit_of_work.availabilities.list_for_employee(
                company_id, employee_id
            )
            if any(availability.overlaps(item) for item in existing):
                raise AvailabilityOverlap
            unit_of_work.availabilities.add(availability)
            return availability

        return self._run(submit_in_transaction)

    def update(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        availability_id: str,
        start_at: datetime,
        end_at: datetime,
    ) -> Availability:
        employee_id = self._authenticated_user_id(principal)

        def update_in_transaction(unit_of_work: UnitOfWork) -> Availability:
            self._require_employee_membership(
                unit_of_work, company_id, employee_id
            )
            unit_of_work.lock_employee(employee_id)
            existing = unit_of_work.availabilities.get(
                company_id, availability_id
            )
            if existing is None or existing.employee_id != employee_id:
                raise ResourceNotFound
            updated = existing.with_interval(start_at, end_at)
            other_intervals = unit_of_work.availabilities.list_for_employee(
                company_id, employee_id
            )
            if any(
                updated.overlaps(item)
                for item in other_intervals
                if item.id != existing.id
            ):
                raise AvailabilityOverlap
            unit_of_work.availabilities.update(updated)
            return updated

        return self._run(update_in_transaction)

    def list_own(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        *,
        from_at: datetime,
        to_at: datetime,
        limit: int,
        after_start: datetime | None = None,
        after_id: str | None = None,
    ) -> Sequence[Availability]:
        from_at, to_at = validate_scheduling_window(from_at, to_at)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidSchedulingQuery
        if (after_start is None) != (after_id is None):
            raise InvalidSchedulingQuery
        employee_id = self._authenticated_user_id(principal)

        def list_in_transaction(unit_of_work: UnitOfWork) -> Sequence[Availability]:
            self._require_employee_membership(
                unit_of_work, company_id, employee_id
            )
            return unit_of_work.availabilities.list_employee_window(
                company_id,
                employee_id,
                from_at=from_at,
                to_at=to_at,
                after_start=after_start,
                after_id=after_id,
                limit=limit,
            )

        return self._run(list_in_transaction)

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
    ) -> Sequence[Availability]:
        from_at, to_at = validate_scheduling_window(from_at, to_at)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidSchedulingQuery
        if (after_start is None) != (after_id is None):
            raise InvalidSchedulingQuery
        employee_id = self._authenticated_user_id(principal)

        def list_in_transaction(unit_of_work: UnitOfWork) -> Sequence[Availability]:
            membership = self._require_membership(
                unit_of_work, company_id, employee_id
            )
            if membership.role is not CompanyRole.ADMINISTRATOR:
                raise ResourceNotFound
            return unit_of_work.availabilities.list_company_window(
                company_id,
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
    def _require_membership(
        unit_of_work: UnitOfWork, company_id: str, employee_id: str
    ) -> CompanyMembership:
        membership = unit_of_work.memberships.get(company_id, employee_id)
        if membership is None:
            raise ResourceNotFound
        return membership

    @classmethod
    def _require_employee_membership(
        cls, unit_of_work: UnitOfWork, company_id: str, employee_id: str
    ) -> CompanyMembership:
        membership = cls._require_membership(
            unit_of_work, company_id, employee_id
        )
        if membership.role is not CompanyRole.EMPLOYEE:
            raise ResourceNotFound
        return membership

    def _run(self, operation: Callable[[UnitOfWork], T]) -> T:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                return operation(unit_of_work)
        except (
            AuthenticationRequired,
            ResourceNotFound,
            AvailabilityOverlap,
            AvailabilitySaveFailed,
            InvalidSchedulingQuery,
            InvalidAvailability,
        ):
            raise
        except Exception as error:
            raise AvailabilitySaveFailed from error
