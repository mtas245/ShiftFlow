import os
from datetime import datetime, timezone
from pathlib import Path
import socket
import subprocess
import sys
import shutil
import tempfile
from threading import Thread
import time
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.infrastructure.password_hasher import Argon2PasswordHasher
from app.infrastructure.persistence.models import Base, UserRecord
from app.interfaces.main import create_app


pytestmark = [pytest.mark.e2e, pytest.mark.postgres]


class AllowAllRateLimiter:
    def allow(self, scope: str, key: str) -> bool:
        del scope, key
        return True


def _validated_database_url() -> str:
    raw_url = os.environ.get("SHIFTFLOW_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Set SHIFTFLOW_TEST_DATABASE_URL to run the browser E2E test")
    url = make_url(raw_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"127.0.0.1", "::1"}
        or url.database != "shiftflow_test"
        or url.username != "shiftflow_test_user"
    ):
        raise RuntimeError(
            "Refusing browser test outside the dedicated local PostgreSQL test database"
        )
    return raw_url


@pytest.fixture
def onboarding_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    database_url = _validated_database_url()
    engine = create_engine(database_url, pool_pre_ping=True)
    safe_database = False
    server = None
    server_thread = None
    browser = None
    runtime = None
    certificate_directory = None
    try:
        with engine.connect() as connection:
            database, username = connection.execute(
                text("SELECT current_database(), current_user")
            ).one()
        if database != "shiftflow_test" or username != "shiftflow_test_user":
            raise RuntimeError(
                "Refusing browser test without dedicated database credentials"
            )
        safe_database = True
        Base.metadata.drop_all(engine)
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        environment = os.environ.copy()
        environment["DATABASE_URL"] = database_url
        repository_root = Path(__file__).resolve().parents[2]
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=repository_root,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

        suffix = uuid4().hex[:8]
        admin_email = f"admin-{suffix}@example.test"
        employee_email = f"employee-{suffix}@example.test"
        password = "Synthetic-E2E-Password-123!"
        hasher = Argon2PasswordHasher()
        now = datetime.now(timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                UserRecord.__table__.insert(),
                [
                    {
                        "id": str(uuid4()),
                        "email": admin_email,
                        "password_hash": hasher.hash(password),
                        "email_verified_at": now,
                    },
                    {
                        "id": str(uuid4()),
                        "email": employee_email,
                        "password_hash": hasher.hash(password),
                        "email_verified_at": now,
                    },
                ],
            )

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        origin = f"https://127.0.0.1:{port}"
        openssl = shutil.which("openssl")
        if openssl is None and os.name == "nt":
            candidates = (
                Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
                / "Git"
                / "usr"
                / "bin"
                / "openssl.exe",
                Path(os.environ.get("ProgramFiles", "C:\\Program Files"))
                / "Git"
                / "mingw64"
                / "bin"
                / "openssl.exe",
            )
            openssl = next(
                (str(candidate) for candidate in candidates if candidate.is_file()),
                None,
            )
        if openssl is None:
            pytest.skip("OpenSSL is required to create the local test TLS certificate")
        certificate_directory = tempfile.TemporaryDirectory()
        certificate_path = Path(certificate_directory.name)
        certificate = certificate_path / "test-cert.pem"
        private_key = certificate_path / "test-key.pem"
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(private_key),
                "-out",
                str(certificate),
                "-sha256",
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=127.0.0.1",
                "-addext",
                "subjectAltName=IP:127.0.0.1",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        app = create_app(
            engine=engine,
            public_origin=origin,
            rate_limiter=AllowAllRateLimiter(),
        )
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="error",
                lifespan="off",
                ssl_certfile=str(certificate),
                ssl_keyfile=str(private_key),
            )
        )
        server_thread = Thread(target=server.run, daemon=True)
        server_thread.start()
        for _ in range(100):
            if server.started:
                break
            if not server_thread.is_alive():
                raise RuntimeError("FastAPI browser test server failed to start")
            time.sleep(0.1)
        if not server.started:
            raise RuntimeError("FastAPI browser test server did not become ready")

        runtime = playwright.sync_playwright().start()
        browser = runtime.chromium.launch(headless=True)
        yield browser, origin, admin_email, employee_email, password
    finally:
        if browser is not None:
            browser.close()
        if runtime is not None:
            runtime.stop()
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=10)
        if certificate_directory is not None:
            certificate_directory.cleanup()
        if safe_database:
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()


def _sign_in(page, origin: str, email: str, password: str) -> None:
    page.goto(f"{origin}/login")
    page.get_by_label("Email address").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{origin}/")


def _create_company(page, origin: str, name: str) -> dict[str, str]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: page_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.goto(f"{origin}/companies")
    page.get_by_label("Company name").fill(name)
    page.get_by_label("Business email").fill(f"contact-{uuid4().hex[:8]}@example.test")
    page.get_by_label("Business phone").fill("+41791234567")
    page.get_by_label("Business address").fill("1 Synthetic Street")
    page.get_by_label("Industry").fill("Hospitality")
    page.get_by_label("Your display name in this company").first.fill(
        "Synthetic Admin"
    )
    with page.expect_response(
        lambda response: response.url == f"{origin}/companies"
        and response.request.method == "POST"
    ) as response_info:
        page.get_by_role("button", name="Create company").click()
    response = response_info.value
    assert response.status == 201
    payload = response.json()
    feedback = page.locator(
        '[data-company-form="register-company"] [data-form-message]'
    )
    try:
        feedback.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError as error:
        raise AssertionError(
            "Company creation UI did not report success; "
            f"feedback={feedback.inner_text()!r}, "
            f"payload_keys={sorted(payload)}, page_errors={page_errors!r}"
        ) from error
    assert feedback.inner_text() == "Company created. You are its administrator.", (
        f"payload={payload!r}, page_errors={page_errors!r}"
    )
    assert set(payload) == {"company_id", "joining_code", "role"}
    page.locator("[data-joining-code]").wait_for()
    assert page.locator("[data-joining-code]").inner_text() == payload["joining_code"]
    assert page.locator("[data-joining-code-panel]").is_visible()
    assert page.evaluate(
        "code => Object.values(localStorage).some(value => value === code)",
        payload["joining_code"],
    ) is False
    return payload


def test_login_company_creation_joining_and_cross_tenant_isolation(
    onboarding_browser,
):
    from playwright.sync_api import expect

    browser, origin, admin_email, employee_email, password = onboarding_browser
    admin_context = browser.new_context(
        ignore_https_errors=True,
        reduced_motion="reduce",
        timezone_id="Europe/Zurich",
    )
    admin_context.clock.install(time="2026-10-22T12:00:00+00:00")
    admin_context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin=origin,
    )
    admin = admin_context.new_page()
    _sign_in(admin, origin, admin_email, password)

    first_company = _create_company(admin, origin, "North Star Cafe")
    other_company = _create_company(admin, origin, "Harbor Bistro")
    assert first_company["role"] == "administrator"
    admin.get_by_role("button", name="Copy code").click()
    admin.get_by_text(
        "Code copied. Share it only with intended employees."
    ).wait_for()
    assert (
        admin.evaluate("navigator.clipboard.readText()")
        == other_company["joining_code"]
    )
    admin.get_by_role("button", name="Hide code").click()
    assert admin.locator("[data-joining-code]").inner_text() == ""
    admin.locator("#company-switcher-select").select_option(
        label="North Star Cafe"
    )
    admin.goto(f"{origin}/")
    expect(admin.locator("[data-dashboard-company-name]")).to_have_text(
        "North Star Cafe"
    )
    assert first_company["joining_code"] not in admin.url
    assert other_company["joining_code"] not in admin.url

    admin.get_by_role("button", name="Sign out").click()
    admin.wait_for_url(f"{origin}/login")

    employee_context = browser.new_context(
        ignore_https_errors=True,
        reduced_motion="reduce",
        timezone_id="Europe/Zurich",
    )
    employee_context.clock.install(time="2026-10-22T12:00:00+00:00")
    employee = employee_context.new_page()
    employee_errors: list[str] = []
    employee.on("pageerror", lambda error: employee_errors.append(str(error)))
    employee.on(
        "console",
        lambda message: employee_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    _sign_in(employee, origin, employee_email, password)
    employee.goto(f"{origin}/companies")
    employee.get_by_role("heading", name="No companies yet").wait_for()

    employee.locator("#joining-code-input").fill("UNKNOWN1")
    employee.get_by_label("Your display name in this company").last.fill(
        "Synthetic Employee"
    )
    employee.get_by_role("button", name="Join company").click()
    employee.get_by_text(
        "That joining code is not valid. Check it and try again."
    ).wait_for()

    employee.locator("#joining-code-input").fill(first_company["joining_code"])
    with employee.expect_response(
        lambda response: response.url == f"{origin}/companies/join"
        and response.request.method == "POST"
    ) as join_response_info:
        employee.get_by_role("button", name="Join company").click()
    join_response = join_response_info.value
    assert join_response.status == 201
    employee.get_by_text("You joined the company successfully.").wait_for()

    memberships = employee.evaluate(
        """async () => {
          const response = await fetch('/companies/mine', {
            credentials: 'same-origin',
            cache: 'no-store'
          });
          return { status: response.status, body: await response.json() };
        }"""
    )
    assert memberships["status"] == 200
    assert len(memberships["body"]["items"]) == 1
    membership = memberships["body"]["items"][0]
    assert membership["company_id"] == first_company["company_id"]
    assert membership["name"] == "North Star Cafe"
    assert membership["role"] == "employee"
    assert "email" not in membership
    assert "user_id" not in membership
    assert (
        employee.locator("#company-switcher-select").input_value()
        == first_company["company_id"]
    )

    employee.locator("#joining-code-input").fill(first_company["joining_code"])
    employee.get_by_label("Your display name in this company").last.fill(
        "Synthetic Employee"
    )
    with employee.expect_response(
        lambda response: response.url == f"{origin}/companies/join"
        and response.request.method == "POST"
    ) as duplicate_response_info:
        employee.get_by_role("button", name="Join company").click()
    duplicate_response = duplicate_response_info.value
    assert duplicate_response.status == 409
    employee.get_by_text(
        "You already belong to this company. Select it from your company list."
    ).wait_for()

    owner_only_access = employee.evaluate(
        """async companyId => {
          const response = await fetch(`/companies/${companyId}/employees`, {
            credentials: 'same-origin',
            cache: 'no-store'
          });
          return { status: response.status, body: await response.json() };
        }""",
        # Harbor Bistro is a different tenant and the employee has no membership there.
        other_company["company_id"],
    )
    assert owner_only_access == {
        "status": 404,
        "body": {"error": {"code": "RESOURCE_NOT_FOUND"}},
    }

    employee.locator("#joining-code-input").fill(other_company["joining_code"])
    employee.get_by_label("Your display name in this company").last.fill(
        "Synthetic Employee"
    )
    with employee.expect_response(
        lambda response: response.url == f"{origin}/companies/join"
        and response.request.method == "POST"
    ) as second_join_info:
        employee.get_by_role("button", name="Join company").click()
    assert second_join_info.value.status == 201
    employee.get_by_text("You joined the company successfully.").wait_for()

    employee.goto(f"{origin}/availability")
    try:
        expect(employee.locator("#company-switcher-select")).to_contain_text(
            "Harbor Bistro"
        )
    except AssertionError as error:
        raise AssertionError(
            f"Company discovery did not render options: {employee_errors!r}"
        ) from error
    employee.locator("#company-switcher-select").select_option(
        label="Harbor Bistro"
    )
    expect(employee.locator("[data-availability-empty]")).to_be_visible()
    employee.locator("#company-switcher-select").select_option(
        label="North Star Cafe"
    )
    expect(employee.locator("[data-availability-empty]")).to_be_visible()

    local_times = employee.evaluate(
        """() => {
          const nextLastSunday = month => {
            let year = new Date().getFullYear();
            const lastSunday = currentYear => {
              const date = new Date(currentYear, month + 1, 0);
              date.setDate(date.getDate() - date.getDay());
              return date;
            };
            let date = lastSunday(year);
            if (date <= new Date()) {
              year += 1;
              date = lastSunday(year);
            }
            return date;
          };
          const fallbackSunday = nextLastSunday(9);
          const start = new Date(fallbackSunday);
          start.setDate(start.getDate() - 1);
          start.setHours(22, 0, 0, 0);
          const end = new Date(fallbackSunday);
          end.setHours(6, 0, 0, 0);
          const updatedEnd = new Date(end);
          updatedEnd.setHours(7);
          const springSunday = nextLastSunday(2);
          const format = (date, hour) =>
            `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}T${String(hour).padStart(2, "0")}:00`;
          return {
            start: format(start, 22),
            end: format(end, 6),
            updatedEnd: format(updatedEnd, 7),
            overlapStart: format(end, 6).replace("T06:00", "T06:30"),
            overlapEnd: format(end, 8),
            adjacentStart: format(end, 7),
            adjacentEnd: format(end, 8),
            springGapStart: format(new Date(springSunday.getFullYear(), 2, springSunday.getDate()), 2).replace("T02:00", "T02:30"),
            springGapEnd: format(new Date(springSunday.getFullYear(), 2, springSunday.getDate()), 4)
          };
        }"""
    )
    expect(employee.locator("[data-local-time-zone]").first).to_have_text(
        "Europe/Zurich"
    )
    employee.locator("#availability-start-at").fill(
        local_times["springGapStart"]
    )
    employee.locator("#availability-end-at").fill(local_times["springGapEnd"])
    write_requests: list[str] = []
    employee.on(
        "request",
        lambda request: write_requests.append(request.method)
        if request.url.endswith("/availability") and request.method == "POST"
        else None,
    )
    employee.get_by_role("button", name="Save availability").click()
    expect(
        employee.locator("[data-availability-form-message]")
    ).to_contain_text("Choose valid local times")
    assert write_requests == []

    employee.locator("#availability-start-at").fill(local_times["start"])
    employee.locator("#availability-end-at").fill(local_times["end"])
    with employee.expect_response(
        lambda response: response.url.endswith("/companies/"
            + first_company["company_id"]
            + "/availability")
        and response.request.method == "POST"
    ) as availability_create_info:
        employee.get_by_role("button", name="Save availability").click()
    availability_create = availability_create_info.value
    assert availability_create.status == 201
    create_payload = availability_create.request.post_data_json
    assert create_payload["start_at"].endswith("+02:00")
    assert create_payload["end_at"].endswith("+01:00")
    created_interval = availability_create.json()
    expect(employee.locator("[data-availability-item]")).to_contain_text(
        "(+1 day)"
    )

    employee.locator("[data-availability-edit]").click()
    try:
        expect(employee.locator("[data-availability-submit]")).to_have_text(
            "Update availability"
        )
    except AssertionError as error:
        raise AssertionError(
            "Edit control did not enter update mode: "
            f"form_mode={employee.locator('[data-availability-form]').get_attribute('data-availability-form')!r}, "
            f"interval_id={employee.locator('[data-availability-edit]').get_attribute('data-availability-edit')!r}, "
            f"errors={employee_errors!r}"
        ) from error
    employee.locator("#availability-end-at").fill(local_times["updatedEnd"])
    with employee.expect_response(
        lambda response: response.url.endswith(
            "/companies/"
            + first_company["company_id"]
            + "/availability/"
            + created_interval["id"]
        )
        and response.request.method == "PUT"
    ) as availability_update_info:
        employee.locator("[data-availability-submit]").click()
    assert availability_update_info.value.status == 200
    expect(employee.locator("[data-availability-item]")).to_contain_text("07:00")

    employee.locator("#availability-start-at").fill(local_times["overlapStart"])
    employee.locator("#availability-end-at").fill(local_times["overlapEnd"])
    with employee.expect_response(
        lambda response: response.url.endswith(
            "/companies/" + first_company["company_id"] + "/availability"
        )
        and response.request.method == "POST"
    ) as overlap_info:
        employee.get_by_role("button", name="Save availability").click()
    assert overlap_info.value.status == 409
    expect(
        employee.locator("[data-availability-form-message]")
    ).to_contain_text("overlaps another availability interval")

    employee.locator("#availability-start-at").fill(local_times["adjacentStart"])
    employee.locator("#availability-end-at").fill(local_times["adjacentEnd"])
    with employee.expect_response(
        lambda response: response.url.endswith(
            "/companies/" + first_company["company_id"] + "/availability"
        )
        and response.request.method == "POST"
    ) as adjacent_info:
        employee.get_by_role("button", name="Save availability").click()
    assert adjacent_info.value.status == 201
    expect(employee.locator("[data-availability-item]")).to_have_count(2)

    employee.reload()
    try:
        expect(employee.locator("[data-availability-item]")).to_have_count(2)
    except AssertionError as error:
        raise AssertionError(
            "Availability was not restored after reload: "
            f"week={employee.locator('[data-availability-week-label]').inner_text()!r}, "
            f"company={employee.locator('#company-switcher-select').input_value()!r}, "
            f"status={employee.locator('[data-availability-status]').inner_text()!r}, "
            f"errors={employee_errors!r}"
        ) from error
    employee.locator("#company-switcher-select").select_option(
        label="Harbor Bistro"
    )
    expect(employee.locator("[data-availability-empty]")).to_be_visible()
    employee.locator("#company-switcher-select").select_option(
        label="North Star Cafe"
    )
    expect(employee.locator("[data-availability-item]")).to_have_count(2)

    employee.reload()
    expect(
        employee.locator("#company-switcher-select")
    ).to_have_value(first_company["company_id"])
    expect(employee.locator("#company-switcher-select")).to_contain_text(
        "Harbor Bistro"
    )
    expect(employee.locator("[data-availability-item]")).to_have_count(2)
    employee.set_viewport_size({"width": 390, "height": 844})
    assert employee.locator("nav.mobile-navigation").is_visible()
    assert employee.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )

    _sign_in(admin, origin, admin_email, password)
    admin.locator("#company-switcher-select").select_option(
        label="North Star Cafe"
    )
    admin.goto(f"{origin}/schedule")
    expect(admin.locator("[data-schedule-roster-message]")).to_contain_text(
        "1 eligible employee"
    )
    admin.locator("#schedule-start-at").fill("2026-10-24T23:00")
    admin.locator("#schedule-end-at").fill("2026-10-25T03:00")
    with admin.expect_response(
        lambda response: response.url.endswith(
            "/companies/" + first_company["company_id"] + "/shifts"
        )
        and response.request.method == "POST"
    ) as shift_create_info:
        admin.get_by_role("button", name="Create shift").click()
    assert shift_create_info.value.status == 201
    assert shift_create_info.value.request.post_data_json["start_at"].endswith(
        "+02:00"
    )
    assert shift_create_info.value.request.post_data_json["end_at"].endswith(
        "+01:00"
    )
    expect(admin.locator("[data-schedule-shift]")).to_have_count(1)
    expect(admin.locator("[data-schedule-assigned-to]")).to_contain_text(
        "Unassigned"
    )
    expect(admin.locator("[data-schedule-employee-select]")).to_contain_text(
        "Synthetic Employee"
    )
    admin.locator("[data-schedule-employee-select]").select_option(
        label="Synthetic Employee"
    )
    with admin.expect_response(
        lambda response: (
            f"/companies/{first_company['company_id']}/shifts/" in response.url
            and response.url.endswith("/assignments")
        )
        and response.request.method == "POST"
    ) as assignment_info:
        admin.locator("[data-schedule-assign]").click()
    assert assignment_info.value.status == 201
    expect(admin.locator("[data-schedule-assigned-to]")).to_contain_text(
        "Synthetic Employee"
    )

    admin.locator("#schedule-start-at").fill("2026-10-25T02:30")
    expect(admin.locator("[data-schedule-start-occurrence-wrap]")).to_be_visible()
    admin.locator("[data-schedule-start-occurrence]").select_option("1")
    admin.locator("#schedule-end-at").fill("2026-10-25T03:00")
    with admin.expect_response(
        lambda response: response.url.endswith(
            "/companies/" + first_company["company_id"] + "/shifts"
        )
        and response.request.method == "POST"
    ) as overlap_shift_info:
        admin.get_by_role("button", name="Create shift").click()
    assert overlap_shift_info.value.status == 201
    assert overlap_shift_info.value.request.post_data_json["start_at"].endswith(
        "+01:00"
    )
    expect(admin.locator("[data-schedule-shift]")).to_have_count(2)
    admin.locator("[data-schedule-employee-select]").last.select_option(
        label="Synthetic Employee"
    )
    with admin.expect_response(
        lambda response: response.request.method == "POST"
        and response.url.endswith("/assignments")
    ) as conflict_info:
        admin.locator("[data-schedule-assign]").last.click()
    assert conflict_info.value.status == 409
    expect(admin.locator("[data-schedule-status]")).to_contain_text(
        "already assigned to an overlapping shift"
    )
    expect(admin.locator("[data-schedule-shift]")).to_have_count(2)

    admin.reload()
    expect(admin.locator("[data-schedule-shift]")).to_have_count(2)
    expect(admin.locator("[data-schedule-assigned-to]").first).to_contain_text(
        "Synthetic Employee"
    )
    expect(admin.locator("[data-schedule-assigned-to]").last).to_contain_text(
        "Unassigned"
    )

    employee.set_viewport_size({"width": 1280, "height": 900})
    employee.goto(f"{origin}/schedule")
    expect(employee.locator("[data-schedule-shift]")).to_have_count(1)
    expect(employee.locator("[data-schedule-assigned-to]")).to_contain_text(
        "Your shift"
    )
    expect(employee.locator("[data-schedule-shift]")).not_to_contain_text(
        "Synthetic Employee"
    )
    employee.locator("#company-switcher-select").select_option(
        label="Harbor Bistro"
    )
    expect(employee.locator("[data-schedule-empty]")).to_be_visible()
    employee.locator("#company-switcher-select").select_option(
        label="North Star Cafe"
    )
    expect(employee.locator("[data-schedule-shift]")).to_have_count(1)
    employee.reload()
    expect(employee.locator("[data-schedule-shift]")).to_have_count(1)
