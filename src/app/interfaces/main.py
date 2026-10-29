import base64
import binascii
import hmac
import logging
import os
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Protocol, TypeVar
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from app.application.account_lifecycle import (
    AccountLifecycleUnavailable,
    EmailDelivery,
    InvalidAccountActionToken,
    InvalidAccountRegistration,
    RateLimitExceeded,
    RateLimiterUnavailable,
    RateLimiter,
)
from app.application.authenticate_user import (
    AuthenticationFailed,
    AuthenticationUnavailable,
    is_valid_session_token,
    token_digest,
)
from app.application.join_company import (
    AlreadyAMember,
    AuthenticationRequired,
    InvalidJoiningCode,
    JoinCompanyCommand,
    MembershipCreationFailed,
)
from app.application.list_my_companies import (
    InvalidMembershipQuery,
    MembershipDiscoveryUnavailable,
)
from app.application.manage_availability import (
    AvailabilityOverlap,
    AvailabilitySaveFailed,
    ResourceNotFound as AvailabilityResourceNotFound,
)
from app.application.manage_shifts import (
    EmployeeUnavailable,
    Forbidden,
    ResourceNotFound as ShiftResourceNotFound,
    ShiftAssignmentConflict,
    ShiftSaveFailed,
)
from app.application.scheduling_queries import InvalidSchedulingQuery
from app.application.register_company import (
    AuthenticatedUser,
    JoiningCodeUnavailable,
    RegisterCompanyCommand,
)
from app.domain.availability import InvalidAvailability
from app.domain.company import CompanyValidationError
from app.domain.company_membership import (
    CompanyRole,
    MembershipValidationError,
)
from app.domain.shift import InvalidShift
from app.infrastructure.composition import (
    build_account_lifecycle,
    build_authenticate_user,
    build_join_company,
    build_list_my_companies,
    build_list_company_employees,
    build_manage_availability,
    build_manage_shifts,
    build_postgres_rate_limiter,
    build_postmark_email_delivery,
    build_register_company,
)
from app.interfaces.browser import register_browser_routes
from app.infrastructure.email_delivery import PostmarkEmailDelivery
from app.infrastructure.trusted_proxy import (
    ClientAddressUnavailable,
    parse_trusted_proxy_cidrs,
    resolve_client_address,
)
from app.interfaces.auth import get_authenticated_user
from app.interfaces.schemas import (
    AccountActionTokenRequest,
    AccountLifecycleMessageResponse,
    AccountRegistrationRequest,
    AvailabilityListResponse,
    AvailabilityRequest,
    AvailabilityResponse,
    CompanyJoinRequest,
    CompanyJoinResponse,
    CompanyMembershipListResponse,
    CompanyMembershipResponse,
    CompanyRegistrationRequest,
    CompanyRegistrationResponse,
    ErrorResponse,
    EmailAddressRequest,
    EmployeeMembershipResponse,
    EmployeeRosterResponse,
    LoginRequest,
    LoginResponse,
    PasswordResetConfirmationRequest,
    ShiftAssignmentRequest,
    ShiftAssignmentResponse,
    ShiftListResponse,
    ShiftRequest,
    ShiftResponse,
)
from app.interfaces.pagination import (
    decode_scheduling_cursor,
    encode_scheduling_cursor,
    parse_scheduling_window,
)
from app.interfaces.timestamps import parse_utc_timestamp


_LOGGER = logging.getLogger(__name__)
_SESSION_COOKIE = "__Host-shiftflow_session"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class _PaginatedScheduleItem(Protocol):
    id: str
    start_at: datetime


_ScheduleItem = TypeVar("_ScheduleItem", bound=_PaginatedScheduleItem)


def _schedule_page(
    results: Sequence[_ScheduleItem],
    *,
    limit: int,
    endpoint: str,
    user_id: str,
    company_id: str,
    from_at: datetime,
    to_at: datetime,
) -> tuple[list[_ScheduleItem], str | None]:
    page = results[:limit]
    next_cursor = (
        encode_scheduling_cursor(
            endpoint=endpoint,
            user_id=user_id,
            company_id=company_id,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            last_start=page[-1].start_at,
            last_id=page[-1].id,
        )
        if len(results) > limit
        else None
    )
    return page, next_cursor


def create_app(
    *,
    engine: Engine | None = None,
    public_origin: str | None = None,
    email_delivery: EmailDelivery | None = None,
    rate_limiter: RateLimiter | None = None,
) -> FastAPI:
    owns_engine = engine is None
    database_url = os.environ.get("DATABASE_URL") if owns_engine else None
    if owns_engine and not database_url:
        raise RuntimeError("DATABASE_URL must be set to create the application")

    configured_origin = (
        public_origin if public_origin is not None else os.environ.get("PUBLIC_ORIGIN")
    )
    expected_origin = _normalize_origin(configured_origin)
    if owns_engine and expected_origin is None:
        raise RuntimeError("PUBLIC_ORIGIN must be set to create the application")
    if owns_engine:
        engine = create_engine(database_url, pool_pre_ping=True)
    if engine is None:
        raise RuntimeError("Application database engine was not initialized")
    if email_delivery is None:
        postmark_token = os.environ.get("POSTMARK_SERVER_TOKEN")
        email_from_address = os.environ.get("EMAIL_FROM_ADDRESS")
        if bool(postmark_token) != bool(email_from_address):
            raise RuntimeError(
                "POSTMARK_SERVER_TOKEN and EMAIL_FROM_ADDRESS must be configured together"
            )
        if postmark_token and email_from_address:
            email_delivery = build_postmark_email_delivery(
                server_token=postmark_token,
                from_address=email_from_address,
            )
    if rate_limiter is None:
        rate_limit_secret = os.environ.get("RATE_LIMIT_HMAC_SECRET")
        if rate_limit_secret:
            rate_limiter = build_postgres_rate_limiter(
                engine,
                hmac_secret=rate_limit_secret,
            )
    try:
        trusted_proxy_cidrs = parse_trusted_proxy_cidrs(
            os.environ.get("TRUSTED_PROXY_CIDRS")
        )
    except ClientAddressUnavailable:
        raise RuntimeError("TRUSTED_PROXY_CIDRS is invalid") from None

    register_company = build_register_company(engine)
    join_company = build_join_company(engine)
    list_my_companies = build_list_my_companies(engine)
    list_company_employees = build_list_company_employees(engine)
    manage_availability = build_manage_availability(engine)
    manage_shifts = build_manage_shifts(engine)
    authenticate_user = build_authenticate_user(engine)
    account_lifecycle = (
        build_account_lifecycle(engine, email_delivery)
        if email_delivery is not None and rate_limiter is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        try:
            yield
        finally:
            if isinstance(email_delivery, PostmarkEmailDelivery):
                email_delivery.close()
            if owns_engine:
                engine.dispose()

    app = FastAPI(title="ShiftFlow", lifespan=lifespan)
    register_browser_routes(app)
    app.state.register_company = register_company
    app.state.join_company = join_company
    app.state.list_my_companies = list_my_companies
    app.state.list_company_employees = list_company_employees
    app.state.manage_availability = manage_availability
    app.state.manage_shifts = manage_shifts
    app.state.authenticate_user = authenticate_user
    app.state.account_lifecycle = account_lifecycle
    app.state.email_delivery = email_delivery
    app.state.rate_limiter = rate_limiter

    @app.get("/health", include_in_schema=False)
    def health_check() -> JSONResponse:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except SQLAlchemyError:
            _LOGGER.warning("Database readiness check failed", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unavailable"},
                headers={"Cache-Control": "no-store"},
            )
        return JSONResponse(
            content={"status": "ok"},
            headers={"Cache-Control": "no-store"},
        )

    def account_action_allowed(
        request: Request,
        scope: str,
        email: str | None,
    ) -> bool:
        if rate_limiter is None:
            raise RateLimiterUnavailable
        try:
            client_address = resolve_client_address(
                peer_address=(
                    request.client.host if request.client is not None else None
                ),
                forwarded_for=request.headers.get("X-Forwarded-For"),
                trusted_proxy_cidrs=trusted_proxy_cidrs,
            )
        except ClientAddressUnavailable:
            raise RateLimiterUnavailable from None

        email_allowed = True
        if email is not None:
            email_allowed = rate_limiter.allow(
                f"{scope}:email",
                email.strip().casefold(),
            )
        ip_allowed = rate_limiter.allow(f"{scope}:ip", client_address)
        return email_allowed and ip_allowed

    def add_safe_error_handler(
        error_type: type[Exception],
        status_code: int,
        error_code: str,
    ) -> None:
        async def handler(_: Request, _error: Exception) -> JSONResponse:
            if status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
                cause = _error.__cause__ or _error
                _LOGGER.error(
                    "Scheduling request failed",
                    exc_info=(type(cause), cause, cause.__traceback__),
                    extra={"error_code": error_code},
                )
            return JSONResponse(
                status_code=status_code,
                content={"error": {"code": error_code}},
            )

        app.add_exception_handler(error_type, handler)

    @app.exception_handler(RequestValidationError)
    async def invalid_request_handler(
        _: Request, _error: RequestValidationError
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_REQUEST"}},
        )

    @app.exception_handler(AuthenticationFailed)
    async def authentication_failed_handler(
        _: Request, _error: AuthenticationFailed
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": {"code": "AUTHENTICATION_FAILED"}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(AuthenticationUnavailable)
    async def authentication_unavailable_handler(
        _: Request, _error: AuthenticationUnavailable
    ) -> JSONResponse:
        del _error
        _LOGGER.error("Authentication persistence is unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "AUTHENTICATION_UNAVAILABLE"}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(AuthenticationRequired)
    async def authentication_required_handler(
        _: Request, _error: AuthenticationRequired
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"error": {"code": "AUTHENTICATION_REQUIRED"}},
        )

    @app.exception_handler(InvalidMembershipQuery)
    async def invalid_membership_query_handler(
        _: Request, _error: InvalidMembershipQuery
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_REQUEST"}},
        )

    @app.exception_handler(InvalidSchedulingQuery)
    async def invalid_scheduling_query_handler(
        _: Request, _error: InvalidSchedulingQuery
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_REQUEST"}},
        )

    @app.exception_handler(MembershipDiscoveryUnavailable)
    async def membership_discovery_unavailable_handler(
        _: Request, _error: MembershipDiscoveryUnavailable
    ) -> JSONResponse:
        del _error
        _LOGGER.error("Company membership discovery is unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "MEMBERSHIP_DISCOVERY_UNAVAILABLE"}},
        )

    @app.exception_handler(MembershipValidationError)
    async def invalid_membership_handler(
        _: Request, _error: MembershipValidationError
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_REQUEST"}},
        )

    @app.exception_handler(InvalidAccountRegistration)
    async def invalid_account_registration_handler(
        _: Request, _error: InvalidAccountRegistration
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_ACCOUNT"}},
        )

    @app.exception_handler(InvalidAccountActionToken)
    async def invalid_account_action_token_handler(
        _: Request, _error: InvalidAccountActionToken
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_ACCOUNT_ACTION_TOKEN"}},
        )

    @app.exception_handler(AccountLifecycleUnavailable)
    async def account_lifecycle_unavailable_handler(
        _: Request, _error: AccountLifecycleUnavailable
    ) -> JSONResponse:
        del _error
        _LOGGER.error("Account lifecycle dependency is unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "ACCOUNT_LIFECYCLE_UNAVAILABLE"}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RateLimiterUnavailable)
    async def rate_limiter_unavailable_handler(
        _: Request, _error: RateLimiterUnavailable
    ) -> JSONResponse:
        del _error
        _LOGGER.error("Account rate-limiting dependency is unavailable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "RATE_LIMITING_UNAVAILABLE"}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_exceeded_handler(
        _: Request, _error: RateLimitExceeded
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": {"code": "RATE_LIMITED"}},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(CompanyValidationError)
    async def invalid_company_handler(
        _: Request, _error: CompanyValidationError
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_REQUEST"}},
        )

    @app.exception_handler(JoiningCodeUnavailable)
    async def joining_code_unavailable_handler(
        _: Request, _error: JoiningCodeUnavailable
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"error": {"code": "JOINING_CODE_UNAVAILABLE"}},
        )

    @app.exception_handler(InvalidJoiningCode)
    async def invalid_joining_code_handler(
        _: Request, _error: InvalidJoiningCode
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": {"code": "INVALID_JOINING_CODE"}},
        )

    @app.exception_handler(AlreadyAMember)
    async def already_a_member_handler(
        _: Request, _error: AlreadyAMember
    ) -> JSONResponse:
        del _error
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": {"code": "ALREADY_A_MEMBER"}},
        )

    @app.exception_handler(MembershipCreationFailed)
    async def membership_creation_failed_handler(
        request: Request, error: MembershipCreationFailed
    ) -> JSONResponse:
        _LOGGER.exception(
            "Company membership creation failed",
            exc_info=error.__cause__ or error,
            extra={"path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "MEMBERSHIP_CREATION_FAILED"}},
        )

    for error_type, status_code, error_code in (
        (InvalidAvailability, status.HTTP_400_BAD_REQUEST, "INVALID_AVAILABILITY"),
        (AvailabilityOverlap, status.HTTP_409_CONFLICT, "AVAILABILITY_OVERLAP"),
        (
            AvailabilityResourceNotFound,
            status.HTTP_404_NOT_FOUND,
            "RESOURCE_NOT_FOUND",
        ),
        (
            AvailabilitySaveFailed,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "AVAILABILITY_SAVE_FAILED",
        ),
        (InvalidShift, status.HTTP_400_BAD_REQUEST, "INVALID_SHIFT"),
        (Forbidden, status.HTTP_403_FORBIDDEN, "FORBIDDEN"),
        (
            ShiftResourceNotFound,
            status.HTTP_404_NOT_FOUND,
            "RESOURCE_NOT_FOUND",
        ),
        (
            EmployeeUnavailable,
            status.HTTP_409_CONFLICT,
            "EMPLOYEE_UNAVAILABLE",
        ),
        (
            ShiftAssignmentConflict,
            status.HTTP_409_CONFLICT,
            "SHIFT_ASSIGNMENT_CONFLICT",
        ),
        (
            ShiftSaveFailed,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "SHIFT_SAVE_FAILED",
        ),
    ):
        add_safe_error_handler(error_type, status_code, error_code)

    @app.middleware("http")
    async def authenticate_and_protect_cookie_requests(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        bootstrap_headers = (
            {"Cache-Control": "no-store", "Vary": "Cookie"}
            if request.url.path == "/auth/session/bootstrap"
            else None
        )
        session_token = request.cookies.get(_SESSION_COOKIE)
        session_context = None
        if session_token is not None:
            try:
                session_context = await run_in_threadpool(
                    authenticate_user.authenticate, session_token
                )
            except AuthenticationUnavailable:
                return JSONResponse(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    content={"error": {"code": "AUTHENTICATION_UNAVAILABLE"}},
                    headers=bootstrap_headers,
                )
            if session_context is not None:
                request.state.authenticated_user = AuthenticatedUser(
                    session_context.user_id
                )
                request.state.session_token = session_token
                request.state.csrf_token_hash = session_context.csrf_token_hash

        if request.method not in _SAFE_METHODS:
            origin_protected = request.url.path in {
                "/auth/session/bootstrap",
                "/auth/login",
                "/auth/register",
                "/auth/email-verification/request",
                "/auth/email-verification/confirm",
                "/auth/password-reset/request",
                "/auth/password-reset/confirm",
            }
            if origin_protected:
                if expected_origin is None:
                    return JSONResponse(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        content={
                            "error": {"code": "AUTHENTICATION_UNAVAILABLE"}
                        },
                        headers=bootstrap_headers,
                    )
                if not _request_origin_matches(request, expected_origin):
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"error": {"code": "CSRF_FAILED"}},
                        headers=bootstrap_headers,
                    )
            if (
                session_context is not None
                and request.url.path
                not in {"/auth/login", "/auth/session/bootstrap"}
                and (
                    expected_origin is None
                    or not _request_origin_matches(request, expected_origin)
                    or not _csrf_token_matches(
                        request.headers.get("X-CSRF-Token"),
                        session_context.csrf_token_hash,
                        session_context.previous_csrf_token_hash,
                        session_context.previous_csrf_expires_at,
                    )
                )
            ):
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": {"code": "CSRF_FAILED"}},
                )

        response = await call_next(request)
        if request.url.path == "/auth/session/bootstrap":
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie"
        if session_context is not None:
            response.headers["Cache-Control"] = "no-store"
        if (
            session_token is not None
            and session_context is None
            and request.url.path != "/auth/login"
        ):
            _delete_session_cookie(response)
        return response

    @app.post(
        "/auth/register",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=AccountLifecycleMessageResponse,
        responses={
            400: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def register_account_endpoint(
        payload: AccountRegistrationRequest,
        request: Request,
        response: Response,
    ) -> AccountLifecycleMessageResponse:
        response.headers["Cache-Control"] = "no-store"
        if rate_limiter is None:
            raise RateLimiterUnavailable
        if account_lifecycle is None or email_delivery is None:
            raise AccountLifecycleUnavailable
        if not account_action_allowed(request, "register", payload.email):
            return AccountLifecycleMessageResponse()
        account_lifecycle.register(payload.email, payload.password)
        return AccountLifecycleMessageResponse()

    @app.post(
        "/auth/email-verification/request",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=AccountLifecycleMessageResponse,
        responses={503: {"model": ErrorResponse}},
    )
    def request_email_verification_endpoint(
        payload: EmailAddressRequest,
        request: Request,
        response: Response,
    ) -> AccountLifecycleMessageResponse:
        response.headers["Cache-Control"] = "no-store"
        if rate_limiter is None:
            raise RateLimiterUnavailable
        if account_lifecycle is None:
            raise AccountLifecycleUnavailable
        if not account_action_allowed(request, "verify_email", payload.email):
            return AccountLifecycleMessageResponse()
        try:
            account_lifecycle.request_email_verification(payload.email)
        except AccountLifecycleUnavailable:
            _LOGGER.error("Email verification request could not be completed")
        return AccountLifecycleMessageResponse()

    @app.post(
        "/auth/email-verification/confirm",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            400: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def confirm_email_verification_endpoint(
        payload: AccountActionTokenRequest,
    ) -> Response:
        if account_lifecycle is None:
            raise AccountLifecycleUnavailable
        account_lifecycle.verify_email(payload.token)
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/auth/password-reset/request",
        status_code=status.HTTP_202_ACCEPTED,
        response_model=AccountLifecycleMessageResponse,
        responses={503: {"model": ErrorResponse}},
    )
    def request_password_reset_endpoint(
        payload: EmailAddressRequest,
        request: Request,
        response: Response,
    ) -> AccountLifecycleMessageResponse:
        response.headers["Cache-Control"] = "no-store"
        if rate_limiter is None:
            raise RateLimiterUnavailable
        if account_lifecycle is None:
            raise AccountLifecycleUnavailable
        if not account_action_allowed(request, "reset_password", payload.email):
            return AccountLifecycleMessageResponse()
        try:
            account_lifecycle.request_password_reset(payload.email)
        except AccountLifecycleUnavailable:
            _LOGGER.error("Password reset request could not be completed")
        return AccountLifecycleMessageResponse()

    @app.post(
        "/auth/password-reset/confirm",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={
            400: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def confirm_password_reset_endpoint(
        payload: PasswordResetConfirmationRequest,
    ) -> Response:
        if account_lifecycle is None:
            raise AccountLifecycleUnavailable
        account_lifecycle.reset_password(payload.token, payload.new_password)
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )

    @app.post(
        "/auth/login",
        response_model=LoginResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def login_endpoint(
        payload: LoginRequest,
        request: Request,
        response: Response,
    ) -> LoginResponse:
        response.headers["Cache-Control"] = "no-store"
        if not account_action_allowed(request, "login", payload.email):
            raise RateLimitExceeded
        authenticated = authenticate_user.login(
            payload.email,
            payload.password,
            replaces_token=request.cookies.get(_SESSION_COOKIE),
        )
        response.set_cookie(
            key=_SESSION_COOKIE,
            value=authenticated.session_token,
            max_age=12 * 60 * 60,
            expires=authenticated.expires_at,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return LoginResponse(csrf_token=authenticated.csrf_token)

    @app.post(
        "/auth/session/bootstrap",
        response_model=LoginResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def session_bootstrap_endpoint(
        request: Request,
        response: Response,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> LoginResponse | JSONResponse:
        del principal
        csrf_token = authenticate_user.bootstrap_csrf_token(
            request.state.session_token
        )
        if csrf_token is None:
            unauthorized = JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": {"code": "AUTHENTICATION_REQUIRED"}},
                headers={"Cache-Control": "no-store"},
            )
            _delete_session_cookie(unauthorized)
            return unauthorized

        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
        return LoginResponse(csrf_token=csrf_token)

    @app.post(
        "/auth/logout",
        status_code=status.HTTP_204_NO_CONTENT,
        responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    )
    def logout_endpoint(
        request: Request,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> Response:
        del principal
        authenticate_user.logout(request.state.session_token)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.headers["Cache-Control"] = "no-store"
        _delete_session_cookie(response)
        return response

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request, error: Exception
    ) -> JSONResponse:
        _LOGGER.exception(
            "Unexpected company API failure",
            exc_info=error,
            extra={"path": request.url.path},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": {"code": "COMPANY_REGISTRATION_FAILED"}},
        )

    @app.post(
        "/companies",
        status_code=status.HTTP_201_CREATED,
        response_model=CompanyRegistrationResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def register_company_endpoint(
        payload: CompanyRegistrationRequest,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> CompanyRegistrationResponse:
        company = app.state.register_company.execute(
            principal,
            RegisterCompanyCommand(
                company_name=payload.company_name,
                business_email=payload.business_email,
                business_phone=payload.business_phone,
                business_address=payload.business_address,
                industry=payload.industry,
                display_name=payload.display_name,
            ),
        )
        return CompanyRegistrationResponse(
            company_id=company.id,
            joining_code=company.joining_code,
            role=CompanyRole.ADMINISTRATOR.value,
        )

    @app.post(
        "/companies/join",
        status_code=status.HTTP_201_CREATED,
        response_model=CompanyJoinResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    def join_company_endpoint(
        payload: CompanyJoinRequest,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> CompanyJoinResponse:
        membership = app.state.join_company.execute(
            principal,
            command=JoinCompanyCommand(
                joining_code=payload.joining_code,
                display_name=payload.display_name,
            ),
        )
        return CompanyJoinResponse(
            company_id=membership.company_id,
            role=membership.role.value,
        )

    @app.get(
        "/companies/mine",
        response_model=CompanyMembershipListResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_my_companies_endpoint(
        principal: AuthenticatedUser = Depends(get_authenticated_user),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=128),
    ) -> CompanyMembershipListResponse:
        after_company_id, after_public_id = _decode_membership_cursor(cursor)
        memberships = app.state.list_my_companies.execute(
            principal,
            limit=limit,
            after_company_id=after_company_id,
            after_public_id=after_public_id,
        )
        has_more = len(memberships) > limit
        page = memberships[:limit]
        next_cursor = (
            _encode_membership_cursor(
                page[-1].company_id,
                page[-1].membership_id,
            )
            if has_more
            else None
        )
        return CompanyMembershipListResponse(
            items=[
                CompanyMembershipResponse(
                    membership_id=item.membership_id,
                    company_id=item.company_id,
                    name=item.company_name,
                    role=item.role,
                )
                for item in page
            ],
            next_cursor=next_cursor,
        )

    @app.get(
        "/companies/{company_id}/employees",
        response_model=EmployeeRosterResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_company_employees_endpoint(
        company_id: str,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=128),
    ) -> EmployeeRosterResponse:
        after_public_id = _decode_employee_cursor(cursor)
        employees = app.state.list_company_employees.execute(
            principal,
            company_id,
            limit=limit,
            after_public_id=after_public_id,
        )
        has_more = len(employees) > limit
        page = employees[:limit]
        next_cursor = (
            _encode_employee_cursor(page[-1].membership_id)
            if has_more
            else None
        )
        return EmployeeRosterResponse(
            items=[
                EmployeeMembershipResponse(
                    membership_id=item.membership_id,
                    display_name=item.display_name,
                )
                for item in page
            ],
            next_cursor=next_cursor,
        )

    @app.post(
        "/companies/{company_id}/availability",
        status_code=status.HTTP_201_CREATED,
        response_model=AvailabilityResponse,
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    )
    def submit_availability_endpoint(
        company_id: str,
        payload: AvailabilityRequest,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> AvailabilityResponse:
        availability = app.state.manage_availability.submit(
            principal,
            company_id,
            parse_utc_timestamp(payload.start_at, InvalidAvailability),
            parse_utc_timestamp(payload.end_at, InvalidAvailability),
        )
        return _availability_response(availability)

    @app.put(
        "/companies/{company_id}/availability/{availability_id}",
        response_model=AvailabilityResponse,
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
    )
    def update_availability_endpoint(
        company_id: str,
        availability_id: str,
        payload: AvailabilityRequest,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> AvailabilityResponse:
        availability = app.state.manage_availability.update(
            principal,
            company_id,
            availability_id,
            parse_utc_timestamp(payload.start_at, InvalidAvailability),
            parse_utc_timestamp(payload.end_at, InvalidAvailability),
        )
        return _availability_response(availability)

    @app.get(
        "/companies/{company_id}/availability/mine",
        response_model=AvailabilityListResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    def list_own_availability_endpoint(
        company_id: str,
        response: Response,
        from_: str = Query(alias="from", max_length=64),
        to: str = Query(max_length=64),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=2048),
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> AvailabilityListResponse:
        from_at, to_at = parse_scheduling_window(from_, to)
        after_start, after_id = (
            decode_scheduling_cursor(
                cursor,
                endpoint="own_availability",
                user_id=principal.user_id,
                company_id=company_id,
                from_at=from_at,
                to_at=to_at,
                limit=limit,
            )
            if cursor is not None
            else (None, None)
        )
        results = app.state.manage_availability.list_own(
            principal,
            company_id,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            after_start=after_start,
            after_id=after_id,
        )
        page, next_cursor = _schedule_page(
            results,
            limit=limit,
            endpoint="own_availability",
            user_id=principal.user_id,
            company_id=company_id,
            from_at=from_at,
            to_at=to_at,
        )
        response.headers["Cache-Control"] = "no-store"
        return AvailabilityListResponse(
            items=[_availability_response(item) for item in page],
            next_cursor=next_cursor,
        )

    @app.get(
        "/companies/{company_id}/availability",
        response_model=AvailabilityListResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    def list_company_availability_endpoint(
        company_id: str,
        response: Response,
        from_: str = Query(alias="from", max_length=64),
        to: str = Query(max_length=64),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=2048),
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> AvailabilityListResponse:
        from_at, to_at = parse_scheduling_window(from_, to)
        after_start, after_id = (
            decode_scheduling_cursor(
                cursor,
                endpoint="company_availability",
                user_id=principal.user_id,
                company_id=company_id,
                from_at=from_at,
                to_at=to_at,
                limit=limit,
            )
            if cursor is not None
            else (None, None)
        )
        results = app.state.manage_availability.list_for_company(
            principal,
            company_id,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            after_start=after_start,
            after_id=after_id,
        )
        page, next_cursor = _schedule_page(
            results,
            limit=limit,
            endpoint="company_availability",
            user_id=principal.user_id,
            company_id=company_id,
            from_at=from_at,
            to_at=to_at,
        )
        response.headers["Cache-Control"] = "no-store"
        return AvailabilityListResponse(
            items=[_availability_response(item) for item in page],
            next_cursor=next_cursor,
        )

    @app.post(
        "/companies/{company_id}/shifts",
        status_code=status.HTTP_201_CREATED,
        response_model=ShiftResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    def create_shift_endpoint(
        company_id: str,
        payload: ShiftRequest,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> ShiftResponse:
        shift = app.state.manage_shifts.create(
            principal,
            company_id,
            parse_utc_timestamp(payload.start_at, InvalidShift),
            parse_utc_timestamp(payload.end_at, InvalidShift),
        )
        return _shift_response(shift)

    @app.post(
        "/companies/{company_id}/shifts/{shift_id}/assignments",
        status_code=status.HTTP_201_CREATED,
        response_model=ShiftAssignmentResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
        },
    )
    def assign_shift_endpoint(
        company_id: str,
        shift_id: str,
        payload: ShiftAssignmentRequest,
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> ShiftAssignmentResponse:
        assignment = app.state.manage_shifts.assign(
            principal,
            company_id,
            shift_id,
            payload.membership_id,
        )
        if assignment.membership_id is None:
            raise ShiftSaveFailed
        return ShiftAssignmentResponse(
            id=assignment.id,
            shift_id=assignment.shift_id,
            company_id=assignment.company_id,
            membership_id=assignment.membership_id,
            display_name=assignment.display_name,
        )

    @app.get(
        "/companies/{company_id}/shifts",
        response_model=ShiftListResponse,
        responses={
            400: {"model": ErrorResponse},
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
        },
    )
    def list_company_shifts_endpoint(
        company_id: str,
        response: Response,
        from_: str = Query(alias="from", max_length=64),
        to: str = Query(max_length=64),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=2048),
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> ShiftListResponse:
        from_at, to_at = parse_scheduling_window(from_, to)
        after_start, after_id = (
            decode_scheduling_cursor(
                cursor,
                endpoint="company_shifts",
                user_id=principal.user_id,
                company_id=company_id,
                from_at=from_at,
                to_at=to_at,
                limit=limit,
            )
            if cursor is not None
            else (None, None)
        )
        results = app.state.manage_shifts.list_for_company(
            principal,
            company_id,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            after_start=after_start,
            after_id=after_id,
        )
        page, next_cursor = _schedule_page(
            results,
            limit=limit,
            endpoint="company_shifts",
            user_id=principal.user_id,
            company_id=company_id,
            from_at=from_at,
            to_at=to_at,
        )
        response.headers["Cache-Control"] = "no-store"
        return ShiftListResponse(
            items=[_shift_response(item) for item in page],
            next_cursor=next_cursor,
        )

    @app.get(
        "/companies/{company_id}/shifts/mine",
        response_model=ShiftListResponse,
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    )
    def list_employee_shifts_endpoint(
        company_id: str,
        response: Response,
        from_: str = Query(alias="from", max_length=64),
        to: str = Query(max_length=64),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=2048),
        principal: AuthenticatedUser = Depends(get_authenticated_user),
    ) -> ShiftListResponse:
        from_at, to_at = parse_scheduling_window(from_, to)
        after_start, after_id = (
            decode_scheduling_cursor(
                cursor,
                endpoint="employee_shifts",
                user_id=principal.user_id,
                company_id=company_id,
                from_at=from_at,
                to_at=to_at,
                limit=limit,
            )
            if cursor is not None
            else (None, None)
        )
        results = app.state.manage_shifts.list_for_employee(
            principal,
            company_id,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            after_start=after_start,
            after_id=after_id,
        )
        page, next_cursor = _schedule_page(
            results,
            limit=limit,
            endpoint="employee_shifts",
            user_id=principal.user_id,
            company_id=company_id,
            from_at=from_at,
            to_at=to_at,
        )
        response.headers["Cache-Control"] = "no-store"
        return ShiftListResponse(
            items=[_shift_response(item) for item in page],
            next_cursor=next_cursor,
        )

    return app


def _normalize_origin(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        value != value.strip()
        or parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("PUBLIC_ORIGIN must be an HTTPS origin without a path")
    port = parsed.port
    if port == 443:
        port = None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}" + (f":{port}" if port is not None else "")


def _request_origin_matches(request: Request, expected_origin: str) -> bool:
    request_origin = request.headers.get("Origin")
    if request_origin is None:
        return False
    try:
        return _normalize_origin(request_origin) == expected_origin
    except (RuntimeError, ValueError):
        return False


def _encode_membership_cursor(company_id: str, public_id: str) -> str:
    value = f"{company_id}\n{public_id}".encode("utf-8")
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _encode_employee_cursor(public_id: str) -> str:
    return base64.urlsafe_b64encode(public_id.encode("ascii")).decode("ascii").rstrip(
        "="
    )


def _decode_employee_cursor(cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        padded_cursor = cursor + "=" * (-len(cursor) % 4)
        public_id = base64.b64decode(
            padded_cursor,
            altchars=b"-_",
            validate=True,
        ).decode("ascii")
        if str(UUID(public_id)) != public_id:
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise InvalidMembershipQuery from None
    return public_id


def _decode_membership_cursor(
    cursor: str | None,
) -> tuple[str | None, str | None]:
    if cursor is None:
        return None, None
    try:
        padded_cursor = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            padded_cursor,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        company_id, public_id = decoded.split("\n")
        if str(UUID(company_id)) != company_id or str(UUID(public_id)) != public_id:
            raise ValueError
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise InvalidMembershipQuery from None
    return company_id, public_id


def _csrf_token_matches(
    token: str | None,
    expected_hash: str,
    previous_hash: str | None = None,
    previous_expires_at: datetime | None = None,
) -> bool:
    if token is None or not is_valid_session_token(token):
        return False
    digest = token_digest(token)
    if hmac.compare_digest(digest, expected_hash):
        return True
    return (
        previous_hash is not None
        and previous_expires_at is not None
        and previous_expires_at > datetime.now(timezone.utc)
        and hmac.compare_digest(digest, previous_hash)
    )


def _delete_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_SESSION_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def _availability_response(availability) -> AvailabilityResponse:
    return AvailabilityResponse(
        id=availability.id,
        company_id=availability.company_id,
        employee_id=availability.employee_id,
        start_at=availability.start_at.isoformat(),
        end_at=availability.end_at.isoformat(),
    )


def _shift_response(shift) -> ShiftResponse:
    assignment = shift.assignment
    if assignment is not None and assignment.membership_id is None:
        raise ShiftSaveFailed
    assignment_response = (
        ShiftAssignmentResponse(
            id=assignment.id,
            shift_id=assignment.shift_id,
            company_id=assignment.company_id,
            membership_id=assignment.membership_id,
            display_name=assignment.display_name,
        )
        if assignment is not None
        else None
    )
    return ShiftResponse(
        id=shift.id,
        company_id=shift.company_id,
        start_at=shift.start_at.isoformat(),
        end_at=shift.end_at.isoformat(),
        assignment=assignment_response,
    )
