import pytest

from app.application.create_user import (
    CreateUser,
    CreateUserCommand,
    DuplicateEmail,
    DuplicateEmailConflict,
    InvalidAccount,
    UserCreationFailed,
)


class InMemoryDatabase:
    def __init__(self):
        self.users = {}
        self.fail_add = False

    def unit_of_work(self):
        return InMemoryUnitOfWork(self)


class InMemoryUnitOfWork:
    def __init__(self, database):
        self.database = database
        self.pending = []

    def __enter__(self):
        self.users = InMemoryUserRepository(self)
        return self

    def __exit__(self, error_type, _error, _traceback):
        if error_type is None:
            for user, password_hash in self.pending:
                self.database.users[user.email] = (user, password_hash)
        return False


class InMemoryUserRepository:
    def __init__(self, unit_of_work):
        self.unit_of_work = unit_of_work

    def add(self, user, password_hash):
        if user.email in self.unit_of_work.database.users:
            raise DuplicateEmailConflict
        self.unit_of_work.pending.append((user, password_hash))
        if self.unit_of_work.database.fail_add:
            raise RuntimeError("simulated user persistence failure")


class FakePasswordHasher:
    def hash(self, password):
        return f"hashed:{password}"


def _create_user(database):
    return CreateUser(database.unit_of_work, FakePasswordHasher())


def test_create_user_hashes_password_and_returns_identity_without_credential():
    database = InMemoryDatabase()
    password = "a-long-test-password"

    user = _create_user(database).execute(
        CreateUserCommand(email=" Alice@Example.com ", password=password)
    )

    assert user.email == "alice@example.com"
    assert database.users[user.email][1] == f"hashed:{password}"
    assert "password" not in repr(user).lower()
    assert "password_hash" not in repr(user).lower()


def test_create_user_rejects_short_password_before_hashing():
    database = InMemoryDatabase()
    service = _create_user(database)

    with pytest.raises(InvalidAccount):
        service.execute(CreateUserCommand(email="a@example.com", password="short"))

    assert database.users == {}


def test_create_user_rejects_duplicate_normalized_email():
    database = InMemoryDatabase()
    service = _create_user(database)
    service.execute(
        CreateUserCommand(email="Alice@Example.com", password="a-long-password")
    )

    with pytest.raises(DuplicateEmail):
        service.execute(
            CreateUserCommand(email=" alice@example.COM ", password="another-long-password")
        )

    assert len(database.users) == 1


def test_create_user_rolls_back_after_unexpected_persistence_failure():
    database = InMemoryDatabase()
    database.fail_add = True
    password = "do-not-leak-this-password"

    with pytest.raises(UserCreationFailed) as error:
        _create_user(database).execute(
            CreateUserCommand(email="alice@example.com", password=password)
        )

    assert database.users == {}
    assert password not in repr(error.value)
    assert password not in repr(CreateUserCommand("a@example.com", password))
