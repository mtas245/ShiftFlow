import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

from app.application.register_company import (
    AuthenticatedUser,
    CompanyMembershipRepository,
    CompanyRepository,
    UnitOfWorkFactory,
)
from app.domain.company_membership import CompanyMembership, CompanyRole


_JOINING_CODE_PATTERN = re.compile(r"[A-Z0-9]{8}\Z")


class AuthenticationRequired(Exception):
    code = "AUTHENTICATION_REQUIRED"


class InvalidJoiningCode(Exception):
    code = "INVALID_JOINING_CODE"


class AlreadyAMember(Exception):
    code = "ALREADY_A_MEMBER"


class DuplicateMembershipConflict(Exception):
    """Raised only for the membership identity uniqueness constraint."""


class MembershipCreationFailed(Exception):
    code = "MEMBERSHIP_CREATION_FAILED"


class UnitOfWork(Protocol):
    companies: CompanyRepository
    memberships: CompanyMembershipRepository

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class JoinCompanyCommand:
    joining_code: str
    display_name: str


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class JoinCompany:
    _unit_of_work_factory: UnitOfWorkFactory

    def execute(
        self,
        authenticated_user: AuthenticatedUser | None,
        command: JoinCompanyCommand,
    ) -> CompanyMembership:
        user_id = self._authenticated_user_id(authenticated_user)
        joining_code = command.joining_code
        if (
            not isinstance(joining_code, str)
            or _JOINING_CODE_PATTERN.fullmatch(joining_code) is None
        ):
            raise InvalidJoiningCode

        def join_in_transaction(unit_of_work: UnitOfWork) -> CompanyMembership:
            company = unit_of_work.companies.find_by_joining_code(joining_code)
            if company is None:
                raise InvalidJoiningCode
            if unit_of_work.memberships.get(company.id, user_id) is not None:
                raise AlreadyAMember

            membership = CompanyMembership.create(
                company_id=company.id,
                user_id=user_id,
                role=CompanyRole.EMPLOYEE,
                account_user_id=user_id,
                display_name=command.display_name,
            )
            unit_of_work.memberships.add(membership)
            return membership

        try:
            return self._run(join_in_transaction)
        except DuplicateMembershipConflict as error:
            raise AlreadyAMember from error

    @staticmethod
    def _authenticated_user_id(
        authenticated_user: AuthenticatedUser | None,
    ) -> str:
        user_id = (
            authenticated_user.user_id
            if authenticated_user is not None
            else None
        )
        if not isinstance(user_id, str) or not user_id.strip():
            raise AuthenticationRequired
        return user_id

    def _run(self, operation: Callable[[UnitOfWork], T]) -> T:
        try:
            with self._unit_of_work_factory() as unit_of_work:
                return operation(unit_of_work)
        except (
            AuthenticationRequired,
            InvalidJoiningCode,
            AlreadyAMember,
            DuplicateMembershipConflict,
            MembershipCreationFailed,
        ):
            raise
        except Exception as error:
            raise MembershipCreationFailed from error
