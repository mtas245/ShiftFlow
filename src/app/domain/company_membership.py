from dataclasses import dataclass
from enum import Enum
from unicodedata import category
from uuid import UUID, uuid4


class MembershipValidationError(ValueError):
    pass


class CompanyRole(Enum):
    ADMINISTRATOR = "administrator"
    EMPLOYEE = "employee"


@dataclass(frozen=True, slots=True)
class CompanyMembership:
    company_id: str
    user_id: str
    role: CompanyRole
    account_user_id: str | None = None
    public_id: str = ""
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.company_id, str) or not self.company_id.strip():
            raise MembershipValidationError("company_id must not be empty")
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            raise MembershipValidationError("user_id must not be empty")
        if not isinstance(self.role, CompanyRole):
            raise MembershipValidationError("role must be a CompanyRole")
        if self.account_user_id is not None:
            if (
                not isinstance(self.account_user_id, str)
                or not self.account_user_id.strip()
            ):
                raise MembershipValidationError(
                    "account_user_id must not be empty"
                )
            if self.user_id != self.account_user_id:
                raise MembershipValidationError(
                    "linked membership identity must use the account identity"
                )
            if self.display_name is None:
                raise MembershipValidationError(
                    "linked membership requires a display name"
                )
        if self.display_name is not None:
            if not isinstance(self.display_name, str):
                raise MembershipValidationError(
                    "display_name must be a string"
                )
            normalized_name = self.display_name.strip()
            if (
                not normalized_name
                or len(normalized_name) > 80
                or any(category(character) == "Cc" for character in normalized_name)
            ):
                raise MembershipValidationError("display_name is invalid")
            object.__setattr__(self, "display_name", normalized_name)
        if not self.public_id:
            object.__setattr__(self, "public_id", str(uuid4()))
        try:
            normalized_public_id = str(UUID(self.public_id))
        except (TypeError, ValueError, AttributeError):
            raise MembershipValidationError("public_id must be a UUID") from None
        object.__setattr__(self, "public_id", normalized_public_id)

    @classmethod
    def create(
        cls,
        *,
        company_id: str,
        user_id: str,
        role: CompanyRole,
        account_user_id: str | None = None,
        public_id: str | None = None,
        display_name: str | None = None,
    ) -> "CompanyMembership":
        return cls(
            company_id=company_id,
            user_id=user_id,
            role=role,
            account_user_id=account_user_id,
            public_id=public_id or str(uuid4()),
            display_name=display_name,
        )
