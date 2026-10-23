from dataclasses import dataclass
from enum import Enum


class MembershipValidationError(ValueError):
    pass


class CompanyRole(Enum):
    ADMINISTRATOR = "administrator"


@dataclass(frozen=True, slots=True)
class CompanyMembership:
    company_id: str
    user_id: str
    role: CompanyRole

    def __post_init__(self) -> None:
        if not isinstance(self.company_id, str) or not self.company_id.strip():
            raise MembershipValidationError("company_id must not be empty")
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise MembershipValidationError("user_id must not be empty")
        if not isinstance(self.role, CompanyRole):
            raise MembershipValidationError("role must be a CompanyRole")

    @classmethod
    def create(
        cls, *, company_id: str, user_id: str, role: CompanyRole
    ) -> "CompanyMembership":
        return cls(company_id=company_id, user_id=user_id, role=role)
