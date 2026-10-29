import pytest

from app.domain.user import User, UserValidationError


def test_user_normalizes_email_and_has_only_public_identity_fields():
    user = User.create(email="  Alice.Example@EXAMPLE.COM ")

    assert user.email == "alice.example@example.com"
    assert user.id
    assert not hasattr(user, "password_hash")
    assert "password" not in repr(user).lower()
    assert "password_hash" not in repr(user).lower()


@pytest.mark.parametrize(
    "email",
    [
        "",
        "  ",
        "alice.example.com",
        "@example.com",
        "alice@",
        "alice@@example.com",
        "alice@localhost",
        "alice@exam ple.com",
        "alice@example..com",
    ],
)
def test_user_rejects_invalid_email(email):
    with pytest.raises(UserValidationError):
        User.create(email=email)
