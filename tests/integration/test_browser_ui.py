from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from app.application.authenticate_user import token_digest
from app.application.authenticate_user import AuthenticateUser
from app.infrastructure.persistence.models import (
    Base,
    UserRecord,
    UserSessionRecord,
)
from app.interfaces.main import create_app


@pytest.fixture
def browser_client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    app = create_app(engine=engine, public_origin="https://testserver")
    client = TestClient(
        app,
        base_url="https://testserver",
        raise_server_exceptions=False,
    )
    try:
        yield client, engine
    finally:
        client.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _authenticated_cookie(engine, client):
    session_token = "s" * 43
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            UserRecord.__table__.insert().values(
                id="account-private-id",
                email="private@example.test",
                password_hash="unused",
                email_verified_at=now,
            )
        )
        connection.execute(
            UserSessionRecord.__table__.insert().values(
                id="session-private-id",
                user_id="account-private-id",
                token_hash=token_digest(session_token),
                csrf_token_hash=token_digest("c" * 43),
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
    client.headers["Cookie"] = f"__Host-shiftflow_session={session_token}"


@pytest.mark.parametrize(
    ("path", "title", "section"),
    [
        ("/", "Overview", "overview"),
        ("/availability", "Availability", "availability"),
        ("/schedule", "Schedule", "schedule"),
    ],
)
def test_browser_pages_render_accessible_shell(
    browser_client, path, title, section
):
    client, _ = browser_client

    response = client.get(path)

    assert response.status_code == 200
    assert title in response.text
    assert 'aria-label="Primary navigation"' in response.text
    assert "<main" in response.text
    assert 'aria-current="page"' in response.text
    assert f'data-page="{section}"' in response.text
    if path == "/availability":
        assert "Placeholder" not in response.text
        assert "Sign in to manage availability" in response.text
        assert 'data-availability-form="create"' not in response.text
    elif path == "/schedule":
        assert "Placeholder" not in response.text
        assert "Coming soon" not in response.text
        assert "Sign in to view your schedule" in response.text
    else:
        assert "Placeholder" in response.text
        assert "Coming soon" in response.text


def test_unauthenticated_shell_discloses_no_private_data_or_api_content(
    browser_client,
):
    client, _ = browser_client

    response = client.get("/")

    assert response.status_code == 200
    assert "Sign in required" in response.text
    assert "status-dot is-inactive" in response.text
    assert "private@example.test" not in response.text
    assert "account-private-id" not in response.text
    assert "data-authenticated=\"false\"" in response.text
    assert "/auth/session/bootstrap" not in response.text


def test_health_endpoint_reports_application_and_database_readiness(browser_client):
    client, _ = browser_client

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_health_endpoint_fails_closed_when_database_is_unavailable(
    browser_client, monkeypatch
):
    client, engine = browser_client

    def unavailable():
        raise OperationalError(
            "SELECT 1",
            {},
            Exception("private database connection detail"),
        )

    monkeypatch.setattr(engine, "connect", unavailable)
    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert "private database connection detail" not in response.text


def test_authenticated_shell_uses_trusted_principal_without_exposing_credentials(
    browser_client, monkeypatch
):
    client, engine = browser_client
    _authenticated_cookie(engine, client)
    monkeypatch.setattr(
        AuthenticateUser,
        "authenticate",
        lambda self, _: SimpleNamespace(
            user_id="account-private-id",
            csrf_token_hash="private-csrf-digest",
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
        ),
    )

    response = client.get("/schedule")

    assert response.status_code == 200
    assert "Signed in" in response.text
    assert "status-dot is-inactive" not in response.text
    assert 'data-authenticated="true"' in response.text
    assert "/static/js/app.js" in response.text
    assert "private@example.test" not in response.text
    assert "account-private-id" not in response.text
    assert "session-private-id" not in response.text
    assert "s" * 43 not in response.text
    assert "c" * 43 not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_unvalidated_cookie_does_not_render_authenticated_shell(
    browser_client, monkeypatch
):
    client, _ = browser_client
    client.headers["Cookie"] = "__Host-shiftflow_session=" + ("x" * 43)
    monkeypatch.setattr(AuthenticateUser, "authenticate", lambda self, _: None)

    response = client.get("/schedule")

    assert response.status_code == 200
    assert 'data-authenticated="false"' in response.text
    assert "Sign in required" in response.text
    assert "/auth/session/bootstrap" not in response.text


def test_pages_never_request_protected_api_data_server_side(browser_client):
    client, _ = browser_client

    for path in ("/", "/availability", "/schedule"):
        response = client.get(path)
        assert response.status_code == 200
        assert '"items"' not in response.text
        assert "employee_id" not in response.text


def test_first_party_static_assets_are_served(browser_client):
    client, _ = browser_client

    page = client.get("/")
    stylesheet = client.get("/static/css/app.css")
    script = client.get("/static/js/app.js")

    assert stylesheet.status_code == 200
    assert "text/css" in stylesheet.headers["content-type"]
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "/static/css/app.css" in page.text
    assert "/static/js/app.js" in page.text
    assert "https://" not in page.text


def test_css_includes_accessibility_and_responsive_system(browser_client):
    client, _ = browser_client

    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    assert "@media (prefers-color-scheme: dark)" in response.text
    assert "@media (prefers-reduced-motion: reduce)" in response.text
    assert "@media (min-width: 48rem)" in response.text
    assert "@media (min-width: 72rem)" in response.text
    assert ":focus-visible" in response.text
    assert "env(safe-area-inset-bottom)" in response.text


def test_css_defines_reusable_form_loading_badge_and_feedback_components(
    browser_client,
):
    client, _ = browser_client

    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    for selector in (
        ".button",
        ".form-field",
        ".badge",
        ".loading-state",
        ".empty-state",
        ".notice",
        ".auth-card",
        ".auth-form[aria-busy=\"true\"]",
    ):
        assert selector in response.text


def test_csrf_bootstrap_javascript_is_same_origin_and_memory_only(browser_client):
    client, _ = browser_client

    response = client.get("/static/js/app.js")
    script = response.text

    assert response.status_code == 200
    assert "/auth/session/bootstrap" in script
    assert "credentials: \"same-origin\"" in script
    assert "cache: \"no-store\"" in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script
    assert "csrfToken = localStorage" not in script
    assert "localStorage.setItem(\"csrf" not in script


def test_static_route_does_not_shadow_api_routes(browser_client):
    client, _ = browser_client

    response = client.get("/companies/mine")

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "AUTHENTICATION_REQUIRED"}}


@pytest.mark.parametrize(
    ("path", "page_name", "form_names"),
    [
        ("/register", "Create your account", ("register",)),
        ("/login", "Welcome back", ("login",)),
        (
            "/verify-email",
            "Verify your email",
            ("verify-email", "resend-verification"),
        ),
        ("/forgot-password", "Reset your password", ("request-reset",)),
        ("/reset-password", "Choose a new password", ("confirm-reset",)),
    ],
)
def test_account_access_pages_render_post_only_accessible_forms(
    browser_client, path, page_name, form_names
):
    client, _ = browser_client

    response = client.get(path)

    assert response.status_code == 200
    assert page_name in response.text
    assert 'aria-label="Primary navigation"' in response.text
    assert 'data-authenticated="false"' in response.text
    assert 'Cache-Control' in response.headers
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    for form_name in form_names:
        assert f'data-auth-form="{form_name}"' in response.text
    if path in {"/register", "/verify-email", "/forgot-password"}:
        assert 'autocomplete="email"' in response.text
    if path == "/login":
        assert 'autocomplete="username"' in response.text
    assert 'aria-live="polite"' in response.text


def test_account_forms_use_post_api_contracts_and_keep_codes_out_of_urls(
    browser_client,
):
    client, _ = browser_client

    register = client.get("/register").text
    login = client.get("/login").text
    verify = client.get("/verify-email").text
    forgot = client.get("/forgot-password").text
    reset = client.get("/reset-password").text

    for path, html, api_path in (
        ("/register", register, "/auth/register"),
        ("/login", login, "/auth/login"),
        ("/verify-email", verify, "/auth/email-verification/confirm"),
        ("/forgot-password", forgot, "/auth/password-reset/request"),
        ("/reset-password", reset, "/auth/password-reset/confirm"),
    ):
        assert f'action="{api_path}"' in html
        assert 'method="post"' in html
        assert "?" not in html.split('action="', 1)[1].split('"', 1)[0]
    assert 'action="/auth/email-verification/request"' in verify
    assert 'name="token"' in verify
    assert 'name="token"' in reset
    assert 'name="new_password"' in reset
    assert "location.search" not in client.get("/static/js/app.js").text


def test_account_access_pages_redirect_authenticated_users_to_dashboard(
    browser_client, monkeypatch
):
    client, _ = browser_client
    client.headers["Cookie"] = "__Host-shiftflow_session=" + ("s" * 43)
    monkeypatch.setattr(
        AuthenticateUser,
        "authenticate",
        lambda self, _: SimpleNamespace(
            user_id="account-private-id",
            csrf_token_hash="private-csrf-digest",
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
        ),
    )

    for path in (
        "/register",
        "/login",
        "/verify-email",
        "/forgot-password",
        "/reset-password",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert 'data-authenticated="true"' in dashboard.text
    assert 'data-auth-logout' in dashboard.text
    assert "/login" not in dashboard.text
    assert "/register" not in dashboard.text


def test_anonymous_navigation_links_to_account_access(browser_client):
    client, _ = browser_client

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/login"' in response.text
    assert 'href="/register"' in response.text
    assert 'data-auth-logout' not in response.text


def test_registration_screen_cannot_bypass_missing_release_dependencies(
    browser_client,
):
    client, _ = browser_client

    page = client.get("/register")
    response = client.post(
        "/auth/register",
        headers={"Origin": "https://testserver"},
        json={
            "email": "new-user@example.test",
            "password": "secure-test-password-long",
        },
    )

    assert page.status_code == 200
    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "RATE_LIMITING_UNAVAILABLE"}
    }


def test_account_access_script_preserves_memory_only_tokens_and_safe_errors(
    browser_client,
):
    client, _ = browser_client

    response = client.get("/static/js/app.js")

    assert response.status_code == 200
    for endpoint in (
        "/auth/register",
        "/auth/login",
        "/auth/logout",
        "/auth/email-verification/request",
        "/auth/email-verification/confirm",
        "/auth/password-reset/request",
        "/auth/password-reset/confirm",
    ):
        assert endpoint in response.text
    assert "credentials: \"same-origin\"" in response.text
    assert "cache: \"no-store\"" in response.text
    assert "X-CSRF-Token" in response.text
    assert "sessionStorage" not in response.text
    assert "document.cookie" not in response.text
    assert '"shiftflow.csrf"' not in response.text
    assert "'shiftflow.csrf'" not in response.text
    assert "console." not in response.text
    assert "location.replace(\"/\")" in response.text
    assert "location.search" not in response.text


def test_company_page_requires_an_authenticated_session(browser_client):
    client, _ = browser_client

    response = client.get("/companies", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_authenticated_company_page_renders_onboarding_and_discovery_forms(
    browser_client, monkeypatch
):
    client, engine = browser_client
    _authenticated_cookie(engine, client)
    monkeypatch.setattr(
        AuthenticateUser,
        "authenticate",
        lambda self, _: SimpleNamespace(
            user_id="account-private-id",
            csrf_token_hash="private-csrf-digest",
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
        ),
    )

    response = client.get("/companies")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert 'data-authenticated="true"' in response.text
    assert 'data-company-form="register-company"' in response.text
    assert 'data-company-form="join-company"' in response.text
    for field in (
        'name="company_name"',
        'name="business_email"',
        'name="business_phone"',
        'name="business_address"',
        'name="industry"',
        'name="display_name"',
        'name="joining_code"',
    ):
        assert field in response.text
    assert 'action="/companies"' in response.text
    assert 'action="/companies/join"' in response.text
    assert 'data-company-list' in response.text
    assert 'data-company-switcher' in response.text
    assert "private@example.test" not in response.text
    assert "account-private-id" not in response.text
    assert "private-csrf-digest" not in response.text
    assert 'name="user_id"' not in response.text
    assert 'name="account_user_id"' not in response.text


def test_company_switcher_is_available_from_navigation_and_dashboard(
    browser_client, monkeypatch
):
    client, engine = browser_client
    _authenticated_cookie(engine, client)
    monkeypatch.setattr(
        AuthenticateUser,
        "authenticate",
        lambda self, _: SimpleNamespace(
            user_id="account-private-id",
            csrf_token_hash="private-csrf-digest",
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
        ),
    )

    dashboard = client.get("/")
    companies = client.get("/companies")

    assert dashboard.status_code == 200
    assert companies.status_code == 200
    assert 'href="/companies"' in dashboard.text
    assert 'data-dashboard-company' in dashboard.text
    assert 'data-company-switcher' in dashboard.text
    assert 'data-company-switcher' in companies.text


def test_company_script_uses_bounded_same_origin_api_and_safe_code_display(
    browser_client,
):
    client, _ = browser_client

    response = client.get("/static/js/app.js")
    script = response.text

    assert response.status_code == 200
    for contract in (
        '"/companies"',
        '"/companies/join"',
        "/companies/mine?",
        "next_cursor",
        "joining_code",
        "navigator.clipboard",
        "localStorage",
        "X-CSRF-Token",
    ):
        assert contract in script
    assert "credentials: \"same-origin\"" in script
    assert "cache: \"no-store\"" in script
    assert "account_user_id" not in script
    assert "user_id" not in script


def test_company_ui_includes_responsive_accessible_controls(browser_client):
    client, _ = browser_client

    response = client.get("/static/css/app.css")

    assert response.status_code == 200
    for selector in (
        ".company-switcher",
        ".company-card",
        ".joining-code",
        ".company-form-grid",
    ):
        assert selector in response.text


def test_authenticated_availability_page_renders_employee_workflow(
    browser_client, monkeypatch
):
    client, engine = browser_client
    _authenticated_cookie(engine, client)
    monkeypatch.setattr(
        AuthenticateUser,
        "authenticate",
        lambda self, _: SimpleNamespace(
            user_id="account-private-id",
            csrf_token_hash="private-csrf-digest",
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
        ),
    )

    response = client.get("/availability")

    assert response.status_code == 200
    assert "Your availability" in response.text
    assert 'data-availability-form="create"' in response.text
    assert 'type="datetime-local"' in response.text
    assert 'data-availability-week-grid' in response.text
    assert 'data-availability-week-label' in response.text
    assert 'data-local-time-zone' in response.text
    assert "Intervals use your local time zone" in response.text
    assert "overnight" in response.text.lower()
    grid_start = response.text.index('data-availability-week-grid')
    assert response.text.index('data-availability-access') < grid_start
    assert response.text.index('data-availability-company-empty') < grid_start
    assert "private@example.test" not in response.text
    assert "account-private-id" not in response.text
    assert "private-csrf-digest" not in response.text


def test_anonymous_availability_page_does_not_render_write_controls(browser_client):
    client, _ = browser_client

    response = client.get("/availability")

    assert response.status_code == 200
    assert 'data-authenticated="false"' in response.text
    assert 'data-availability-form="create"' not in response.text
    assert 'data-availability-week-grid' not in response.text


def test_availability_script_uses_bounded_same_origin_csrf_api(browser_client):
    client, _ = browser_client
    script = client.get("/static/js/app.js").text

    for endpoint in (
        "/companies/",
        "/availability/mine?",
        "start_at",
        "end_at",
        "next_cursor",
        "X-CSRF-Token",
        "timeZone",
        "getTimezoneOffset",
    ):
        assert endpoint in script
    assert "credentials: \"same-origin\"" in script
    assert "cache: \"no-store\"" in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script


def test_availability_styles_have_week_grid_and_accessible_mobile_layout(
    browser_client,
):
    client, _ = browser_client
    styles = client.get("/static/css/app.css").text

    assert ".availability-week-grid" in styles
    assert ".availability-day" in styles
    assert ".availability-interval" in styles
    assert "@media (max-width: 48rem)" in styles
    assert ":focus-visible" in styles


def test_authenticated_schedule_page_renders_role_aware_workflow(
    browser_client, monkeypatch
):
    client, engine = browser_client
    _authenticated_cookie(engine, client)
    monkeypatch.setattr(
        AuthenticateUser,
        "authenticate",
        lambda self, _: SimpleNamespace(
            user_id="account-private-id",
            csrf_token_hash="private-csrf-digest",
            previous_csrf_token_hash=None,
            previous_csrf_expires_at=None,
        ),
    )

    response = client.get("/schedule")

    assert response.status_code == 200
    assert "Schedule" in response.text
    assert 'data-schedule-week-grid' in response.text
    assert 'data-schedule-week-label' in response.text
    assert 'data-schedule-create-form' in response.text
    assert 'data-schedule-status' in response.text
    assert 'data-schedule-admin-tools' in response.text
    assert "private@example.test" not in response.text
    assert "account-private-id" not in response.text
    assert "private-csrf-digest" not in response.text


def test_anonymous_schedule_page_does_not_render_schedule_or_write_controls(
    browser_client,
):
    client, _ = browser_client

    response = client.get("/schedule")

    assert response.status_code == 200
    assert 'data-authenticated="false"' in response.text
    assert "Sign in to view your schedule" in response.text
    assert 'data-schedule-create-form' not in response.text
    assert 'data-schedule-week-grid' not in response.text


def test_schedule_script_uses_bounded_authorized_csrf_apis(browser_client):
    client, _ = browser_client
    script = client.get("/static/js/app.js").text

    for contract in (
        '"shifts/mine"',
        "/employees?${query}",
        "/assignments`",
        "membership_id",
        "next_cursor",
        "X-CSRF-Token",
        "getTimezoneOffset",
    ):
        assert contract in script
    assert "credentials: \"same-origin\"" in script
    assert "cache: \"no-store\"" in script
    assert "sessionStorage" not in script
    assert "document.cookie" not in script


def test_schedule_styles_support_week_agenda_and_accessible_controls(
    browser_client,
):
    client, _ = browser_client
    styles = client.get("/static/css/app.css").text

    assert ".schedule-week-grid" in styles
    assert ".schedule-day" in styles
    assert ".schedule-shift" in styles
    assert ".schedule-editor" in styles
    assert "@media (max-width: 48rem)" in styles
    assert ":focus-visible" in styles
