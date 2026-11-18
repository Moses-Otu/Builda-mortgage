from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from meip.audit.service import creation, diff
from meip.core.config import AuthMode, ConfigurationError, Environment, Settings
from meip.core.ids import uuid7
from meip.core.money import Money


class TestUuid7:
    def test_version_and_variant(self) -> None:
        value = uuid7()
        assert value.version == 7
        assert value.variant == uuid.RFC_4122

    def test_time_ordered_across_milliseconds(self) -> None:
        first = uuid7()
        time.sleep(0.002)
        second = uuid7()
        assert first < second

    def test_unique(self) -> None:
        assert len({uuid7() for _ in range(10_000)}) == 10_000


class TestMoney:
    def test_major_units_are_exact_decimals(self) -> None:
        assert Money.gbp(4_500_001).major == Decimal("45000.01")

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Money(amount_minor=-1)

    def test_only_gbp(self) -> None:
        with pytest.raises(ValidationError):
            Money.model_validate({"amount_minor": 1, "currency": "EUR"})

    def test_floats_with_fractions_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Money.model_validate({"amount_minor": 10.5})

    def test_optional(self) -> None:
        assert Money.from_optional_minor(None) is None
        assert Money.from_optional_minor(5) == Money.gbp(5)


OIDC = {
    "oidc_issuer": "https://login.example/",
    "oidc_audience": "api://meip",
    "oidc_jwks_url": "https://login.example/keys",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No MEIP_* variables from the developer's shell leak into these tests."""
    import os

    for name in list(os.environ):
        if name.startswith("MEIP_"):
            monkeypatch.delenv(name)
    return monkeypatch


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.usefixtures("clean_env")
class TestSettings:
    def test_environment_is_required(self) -> None:
        with pytest.raises(ValidationError, match="env"):
            _settings(**OIDC)

    def test_missing_environment_cannot_enable_dev_auth(self) -> None:
        with pytest.raises(ValidationError, match="env"):
            _settings(auth_mode=AuthMode.DEV)

    def test_missing_environment_from_process_env_is_rejected(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        clean_env.setenv("MEIP_AUTH_MODE", "dev")
        with pytest.raises(ValidationError, match="env"):
            Settings(_env_file=None)

    def test_unknown_environment_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _settings(env="dev", auth_mode=AuthMode.DEV)

    @pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
    def test_dev_auth_forbidden_outside_local_and_test(self, env: Environment) -> None:
        with pytest.raises(ConfigurationError, match="forbidden"):
            _settings(env=env, auth_mode=AuthMode.DEV)

    @pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
    def test_dev_auth_forbidden_even_with_oidc_configured(self, env: Environment) -> None:
        with pytest.raises(ConfigurationError, match="forbidden"):
            _settings(env=env, auth_mode=AuthMode.DEV, **OIDC)

    @pytest.mark.parametrize("env", [Environment.LOCAL, Environment.TEST])
    def test_dev_auth_allowed_only_when_explicit(self, env: Environment) -> None:
        assert _settings(env=env, auth_mode=AuthMode.DEV).auth_mode is AuthMode.DEV

    @pytest.mark.parametrize("env", list(Environment))
    def test_auth_mode_defaults_to_oidc(self, env: Environment) -> None:
        assert _settings(env=env, **OIDC).auth_mode is AuthMode.OIDC

    def test_local_without_auth_mode_does_not_fall_back_to_dev(self) -> None:
        with pytest.raises(ConfigurationError, match="requires"):
            _settings(env=Environment.LOCAL)

    def test_oidc_requires_issuer_audience_and_jwks(self) -> None:
        with pytest.raises(ConfigurationError, match="requires"):
            _settings(env=Environment.PRODUCTION, auth_mode=AuthMode.OIDC, oidc_issuer="x")

    def test_valid_production_oidc_uses_object_id(self) -> None:
        s = _settings(env=Environment.PRODUCTION, **OIDC)
        assert s.auth_mode is AuthMode.OIDC
        assert s.oidc_subject_claim == "oid"


class TestAuditDiff:
    def test_only_changed_fields(self) -> None:
        assert diff({"a": 1, "b": 2}, {"a": 1, "b": 3}) == {"b": {"from": 2, "to": 3}}

    def test_identifiers_redacted(self) -> None:
        changes = diff(
            {"first_name": "Sam", "date_of_birth": date(1990, 1, 1)},
            {"first_name": "Samuel", "date_of_birth": date(1990, 1, 2)},
        )
        assert changes == {"first_name": {"changed": True}, "date_of_birth": {"changed": True}}
        assert "Sam" not in str(changes)

    def test_values_made_json_safe(self) -> None:
        ident = uuid.uuid4()
        when = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        changes = creation({"id": ident, "at": when, "on": date(2026, 1, 2), "n": None})
        assert changes == {
            "id": {"from": None, "to": str(ident)},
            "at": {"from": None, "to": "2026-01-01T12:00:00+00:00"},
            "on": {"from": None, "to": "2026-01-02"},
        }
