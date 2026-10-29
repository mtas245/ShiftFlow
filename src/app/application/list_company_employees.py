from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.application.list_my_companies import (
    InvalidMembershipQuery,
    MembershipDiscoveryUnavailable,
)
from app.application.manage_shifts import (
    AuthenticatedPrincipal,
    AuthenticationRequired,
    Forbidden,
    ResourceNotFound,
)
from app.domain.company_membership import CompanyMembership, CompanyRole


@dataclass(frozen=True, slots=True)
class MembershipEmployee:
    membership_id: str
    display_name: str


class MembershipRepository(Protocol):
    def get_for_account(
        self, company_id: str, account_user_id: str
    ) -> CompanyMembership | None: ...

    def list_linked_employees(
        self,
        company_id: str,
        *,
        after_public_id: str | None,
        limit: int,
    ) -> Sequence[MembershipEmployee]: ...


class UnitOfWork(Protocol):
    memberships: MembershipRepository

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


@dataclass(frozen=True, slots=True)
class ListCompanyEmployees:
    _unit_of_work_factory: UnitOfWorkFactory

    def execute(
        self,
        principal: AuthenticatedPrincipal | None,
        company_id: str,
        *,
        limit: int = 100,
        after_public_id: str | None = None,
    ) -> Sequence[MembershipEmployee]:
        if (
            principal is None
            or not isinstance(principal.user_id, str)
            or not principal.user_id.strip()
        ):
            raise AuthenticationRequired
        if type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidMembershipQuery
        if after_public_id is not None:
            try:
                if str(UUID(after_public_id)) != after_public_id:
                    raise ValueError
            except ValueError:
                raise InvalidMembershipQuery from None

        with self._unit_of_work_factory() as unit_of_work:
            administrator = unit_of_work.memberships.get_for_account(
                company_id,
                principal.user_id,
            )
            if administrator is None:
                raise ResourceNotFound
            if administrator.role is not CompanyRole.ADMINISTRATOR:
                raise Forbidden
            return unit_of_work.memberships.list_linked_employees(
                company_id,
                after_public_id=after_public_id,
                limit=limit,
            )
