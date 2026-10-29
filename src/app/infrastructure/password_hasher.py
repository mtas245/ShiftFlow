from argon2 import PasswordHasher, Type
from argon2.exceptions import HashingError, InvalidHashError, VerificationError

from app.application.authenticate_user import PasswordVerificationFailed
from app.application.create_user import PasswordHashingFailed


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._hasher = PasswordHasher(
            time_cost=3,
            memory_cost=65536,
            parallelism=4,
            type=Type.ID,
        )

    def hash(self, password: str) -> str:
        try:
            return self._hasher.hash(password)
        except HashingError:
            raise PasswordHashingFailed from None

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError):
            return False
        except HashingError:
            raise PasswordVerificationFailed from None
