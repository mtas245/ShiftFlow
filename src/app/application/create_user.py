from dataclasses import dataclass, field
from typing import Protocol

from app.domain.user import User, UserValidationError


class UserRepository(Protocol):
    def add(self, user: User, password_hash: str) -> None: ...


class UnitOfWork(Protocol):
    users: UserRepository

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, error_type, error, traceback) -> bool | None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...


class DuplicateEmailConflict(Exception):
    """Raised only for a duplicate normalized account email."""


class InvalidAccount(Exception):
    code = "INVALID_ACCOUNT"


class DuplicateEmail(Exception):
    code = "DUPLICATE_EMAIL"


class PasswordHashingFailed(Exception):
    code = "PASSWORD_HASHING_FAILED"


class UserCreationFailed(Exception):
    code = "USER_CREATION_FAILED"


@dataclass(frozen=True, slots=True)
class CreateUserCommand:
    email: str
    password: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CreateUser:
    _unit_of_work_factory: UnitOfWorkFactory
    _password_hasher: PasswordHasher

    def execute(self, command: CreateUserCommand) -> User:
        try:
            user = User.create(email=command.email)
        except UserValidationError:
            raise InvalidAccount from None

        if (
            not isinstance(command.password, str)
            or not 12 <= len(command.password) <= 1024
        ):
            raise InvalidAccount

        try:
            password_hash = self._password_hasher.hash(command.password)
        except PasswordHashingFailed:
            raise

        try:
            with self._unit_of_work_factory() as unit_of_work:
                unit_of_work.users.add(user, password_hash)
        except DuplicateEmailConflict:
            raise DuplicateEmail from None
        except Exception:
            raise UserCreationFailed from None

        return user
