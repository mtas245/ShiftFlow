import re
from dataclasses import dataclass, field
from uuid import uuid4


class UserValidationError(ValueError):
    pass


def _normalize_email(value: str) -> str:
    if not isinstance(value, str):
        raise UserValidationError("email is invalid")
    email = value.strip()
    if not email or len(email) > 254 or any(character.isspace() for character in email):
        raise UserValidationError("email is invalid")

    local_part, separator, domain = email.partition("@")
    if not separator or not local_part or not domain or "@" in domain:
        raise UserValidationError("email is invalid")

    labels = domain.split(".")
    if len(labels) < 2:
        raise UserValidationError("email is invalid")
    label_pattern = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z")
    if any(len(label) > 63 or not label_pattern.fullmatch(label) for label in labels):
        raise UserValidationError("email is invalid")

    return email.lower()


@dataclass(frozen=True, slots=True)
class User:
    email: str
    id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        object.__setattr__(self, "email", _normalize_email(self.email))
        if not isinstance(self.id, str) or not self.id.strip():
            raise UserValidationError("user id is invalid")

    @classmethod
    def create(cls, *, email: str) -> "User":
        return cls(email=email)
