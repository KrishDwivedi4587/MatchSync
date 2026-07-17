"""Settings validation tests: production fail-fast on the example secret.

``_env_file=None`` keeps these hermetic — no developer ``.env`` can leak in.
Explicit kwargs take precedence over process environment variables, so the
conftest-provided test SECRET_KEY does not mask the cases under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_production_refuses_the_committed_example_secret() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(
            _env_file=None,
            environment="production",
            secret_key="change-me-in-production",
        )


def test_production_refuses_a_short_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(_env_file=None, environment="staging", secret_key="too-short")


def test_production_boots_with_a_strong_secret() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        secret_key="0123456789abcdef0123456789abcdef",  # 32 chars
    )
    assert settings.is_production is True


def test_local_keeps_ergonomic_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Developers must still be able to boot with zero configuration: clear the
    # suite-level SECRET_KEY/ENVIRONMENT env vars to simulate a fresh machine.
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    settings = Settings(_env_file=None)
    assert settings.environment == "local"
    assert settings.secret_key.get_secret_value() == "change-me-in-production"
