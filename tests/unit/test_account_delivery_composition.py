import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.infrastructure.email_delivery import PostmarkEmailDelivery
from app.interfaces.main import create_app


class AllowAllRateLimiter:
    def allow(self, scope: str, key: str) -> bool:
        del scope, key
        return True


def test_application_composes_and_closes_postmark_delivery(monkeypatch):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "test-server-token")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "noreply@example.test")
    engine = create_engine("sqlite://")

    try:
        app = create_app(
            engine=engine,
            public_origin="https://testserver",
            rate_limiter=AllowAllRateLimiter(),
        )

        assert isinstance(app.state.email_delivery, PostmarkEmailDelivery)
        with TestClient(app):
            pass
    finally:
        engine.dispose()


def test_application_rejects_partial_postmark_configuration(monkeypatch):
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "test-server-token")
    monkeypatch.delenv("EMAIL_FROM_ADDRESS", raising=False)
    engine = create_engine("sqlite://")

    try:
        with pytest.raises(
            RuntimeError,
            match="POSTMARK_SERVER_TOKEN and EMAIL_FROM_ADDRESS",
        ):
            create_app(
                engine=engine,
                public_origin="https://testserver",
                rate_limiter=AllowAllRateLimiter(),
            )
    finally:
        engine.dispose()
