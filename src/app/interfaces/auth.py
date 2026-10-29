from fastapi import Request

from app.application.join_company import AuthenticationRequired
from app.application.register_company import AuthenticatedUser


def get_authenticated_user(request: Request) -> AuthenticatedUser:
    principal = getattr(request.state, "authenticated_user", None)
    if (
        not isinstance(principal, AuthenticatedUser)
        or not isinstance(principal.user_id, str)
        or not principal.user_id.strip()
    ):
        raise AuthenticationRequired
    return principal
