from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates


_INTERFACES_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_INTERFACES_DIR / "templates"))

_PAGES = (
    ("/", "dashboard.html", "overview"),
    ("/companies", "companies.html", "companies"),
    ("/availability", "availability.html", "availability"),
    ("/schedule", "schedule.html", "schedule"),
)
_ACCOUNT_PAGES = (
    ("/register", "register.html", "register"),
    ("/login", "login.html", "login"),
    ("/verify-email", "verify-email.html", "verify_email"),
    ("/forgot-password", "forgot-password.html", "forgot_password"),
    ("/reset-password", "reset-password.html", "reset_password"),
)


def register_browser_routes(app: FastAPI) -> None:
    app.mount(
        "/static",
        StaticFiles(directory=str(_INTERFACES_DIR / "static")),
        name="static",
    )

    for path, template, section in _PAGES:
        app.add_api_route(
            path,
            _page_handler(
                template,
                section,
                authenticated_only=path == "/companies",
            ),
            methods=["GET"],
            response_class=HTMLResponse,
            name=f"browser_{section}",
        )

    for path, template, section in _ACCOUNT_PAGES:
        app.add_api_route(
            path,
            _page_handler(template, section, anonymous_only=True),
            methods=["GET"],
            response_class=HTMLResponse,
            name=f"browser_{section}",
        )


def _page_handler(
    template: str,
    section: str,
    *,
    anonymous_only: bool = False,
    authenticated_only: bool = False,
):
    async def render_page(request: Request) -> Response:
        authenticated = (
            getattr(request.state, "authenticated_user", None) is not None
        )
        if authenticated_only and not authenticated:
            return RedirectResponse(
                "/login",
                status_code=303,
                headers={
                    "Cache-Control": "no-store",
                    "Vary": "Cookie",
                    "Referrer-Policy": "no-referrer",
                },
            )
        if anonymous_only and authenticated:
            return RedirectResponse(
                "/",
                status_code=303,
                headers={
                    "Cache-Control": "no-store",
                    "Vary": "Cookie",
                    "Referrer-Policy": "no-referrer",
                },
            )
        return _TEMPLATES.TemplateResponse(
            request,
            template,
            {
                "authenticated": authenticated,
                "section": section,
            },
            headers={
                "Cache-Control": "no-store",
                "Vary": "Cookie",
                "Referrer-Policy": "no-referrer",
            },
        )

    return render_page
