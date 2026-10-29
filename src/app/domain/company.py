import re
from dataclasses import dataclass, field
from uuid import uuid4


class CompanyValidationError(ValueError):
    pass


def _required_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CompanyValidationError(f"{field_name} must not be empty")


def _normalize_business_email(value: str) -> str:
    _required_text(value, "business_email")
    email = value.strip()
    if any(character.isspace() for character in email):
        raise CompanyValidationError("business_email has an invalid format")

    local_part, separator, domain = email.partition("@")
    if not separator or not local_part or not domain or "@" in domain:
        raise CompanyValidationError("business_email has an invalid format")

    labels = domain.split(".")
    label_pattern = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
    if any(len(label) > 63 or not label_pattern.fullmatch(label) for label in labels):
        raise CompanyValidationError("business_email has an invalid format")

    return f"{local_part}@{domain.lower()}"


def _validate_phone(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\+[1-9][0-9]{1,14}", value):
        raise CompanyValidationError("business_phone must use E.164 format")


def _normalize_industry(value: str) -> str:
    _required_text(value, "industry")
    return " ".join(value.split())


@dataclass(frozen=True, slots=True)
class Company:
    company_name: str
    business_email: str
    business_phone: str
    business_address: str
    industry: str
    joining_code: str
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        _required_text(self.company_name, "company_name")
        _required_text(self.business_address, "business_address")
        object.__setattr__(
            self, "business_email", _normalize_business_email(self.business_email)
        )
        _validate_phone(self.business_phone)
        object.__setattr__(self, "industry", _normalize_industry(self.industry))
        if not isinstance(self.joining_code, str) or not re.fullmatch(
            r"[A-Z0-9]{8}", self.joining_code
        ):
            raise CompanyValidationError("joining_code must be 8 uppercase alphanumeric characters")

    @classmethod
    def create(
        cls,
        *,
        company_name: str,
        business_email: str,
        business_phone: str,
        business_address: str,
        industry: str,
        joining_code: str,
    ) -> "Company":
        return cls(
            company_name=company_name,
            business_email=business_email,
            business_phone=business_phone,
            business_address=business_address,
            industry=industry,
            joining_code=joining_code,
        )
