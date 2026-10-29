import pytest

from app.interfaces.demo_seed import (
    DemoSeedRefused,
    main,
    validate_local_demo_environment,
)


LOCAL_URL = (
    "postgresql+psycopg://shiftflow_dev_user:local-only@db:5432/shiftflow_dev"
)


def _environment(**overrides):
    values = {
        "SHIFTFLOW_DEMO_SEED": "local-development",
        "DATABASE_URL": LOCAL_URL,
        "PUBLIC_ORIGIN": "https://localhost:8443",
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def test_accepts_the_compose_development_database():
    assert (
        validate_local_demo_environment(_environment(), confirmed=True)
        == LOCAL_URL
    )


def test_requires_the_cli_confirmation_flag():
    with pytest.raises(DemoSeedRefused):
        validate_local_demo_environment(_environment(), confirmed=False)


@pytest.mark.parametrize("value", [None, "", "1", "true", "production"])
def test_requires_the_explicit_environment_opt_in(value):
    with pytest.raises(DemoSeedRefused):
        validate_local_demo_environment(
            _environment(SHIFTFLOW_DEMO_SEED=value), confirmed=True
        )


@pytest.mark.parametrize(
    "database_url",
    [
        None,
        "postgresql+psycopg://shiftflow_dev_user:x@db:5432/shiftflow_test",
        "postgresql+psycopg://shiftflow_dev_user:x@db:5432/shiftflow",
        "postgresql+psycopg://shiftflow_dev_user:x@prod.example.com:5432/shiftflow_dev",
        "postgresql+psycopg://shiftflow_dev_user:x@10.0.0.5:5432/shiftflow_dev",
        "postgresql+psycopg://postgres:x@db:5432/shiftflow_dev",
        "postgresql://shiftflow_dev_user:x@db:5432/shiftflow_dev",
        "sqlite:///shiftflow_dev",
    ],
)
def test_refuses_anything_but_the_local_development_database(database_url):
    with pytest.raises(DemoSeedRefused):
        validate_local_demo_environment(
            _environment(DATABASE_URL=database_url), confirmed=True
        )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "https://shiftflow.example.com",
        "http://localhost:8443",
        "https://localhost.evil.example",
    ],
)
def test_refuses_a_non_local_public_origin(origin):
    with pytest.raises(DemoSeedRefused):
        validate_local_demo_environment(
            _environment(PUBLIC_ORIGIN=origin), confirmed=True
        )


def test_cli_refuses_without_confirmation_and_never_connects(monkeypatch, capsys):
    monkeypatch.setattr(
        "app.interfaces.demo_seed.create_engine",
        lambda *args, **kwargs: pytest.fail("must not connect when refused"),
    )

    assert main([], environ=_environment()) == 2

    assert "Refusing" in capsys.readouterr().err
