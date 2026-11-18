"""Typed application settings, loaded from environment variables prefixed ``MEIP_``."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    OIDC = "oidc"
    """Validate bearer tokens issued by an external OpenID Connect provider (e.g. Entra)."""

    DEV = "dev"
    """Validate tokens signed by a local development key. Never allowed in production."""


class ConfigurationError(RuntimeError):
    """Raised when settings are invalid or unsafe for the selected environment."""


# The only environments in which the development token issuer may be enabled.
DEV_AUTH_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEIP_", env_file=".env", extra="ignore")

    # Required, with no default: a deployment that forgets to declare its environment must
    # fail to start rather than silently inherit permissive local behaviour.
    env: Environment
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = Field(
        default="postgresql+asyncpg://meip_app:meip_app_local@localhost:5432/meip",
        description="Runtime connection. Must use the non-owner application role.",
    )
    database_pool_size: int = 10

    auth_mode: AuthMode = AuthMode.OIDC
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None
    oidc_subject_claim: str = Field(
        default="oid",
        description=(
            "Claim holding the stable user identifier. Entra: 'oid' (immutable object ID). "
            "Do not use 'sub' with Entra: it differs per client application."
        ),
    )
    oidc_tenant_id: str | None = Field(
        default=None,
        description="If set, tokens must carry this directory ID in their 'tid' claim.",
    )
    jwt_leeway_seconds: int = 30

    dev_auth_issuer: str = "https://meip-dev.localhost/"
    dev_auth_audience: str = "meip-api"
    dev_auth_key_path: Path = Path(".dev/dev_auth_key.pem")

    max_applicants_per_case: int = Field(default=4, ge=1, le=8)

    @model_validator(mode="after")
    def _validate_auth(self) -> Settings:
        if self.auth_mode is AuthMode.DEV and self.env not in DEV_AUTH_ENVIRONMENTS:
            raise ConfigurationError(
                f"auth_mode=dev is forbidden when env={self.env.value}; configure OIDC instead"
            )
        if self.auth_mode is AuthMode.OIDC and not (
            self.oidc_issuer and self.oidc_audience and self.oidc_jwks_url
        ):
            raise ConfigurationError(
                "auth_mode=oidc requires MEIP_OIDC_ISSUER, MEIP_OIDC_AUDIENCE "
                "and MEIP_OIDC_JWKS_URL"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # all values, including the required env, come from MEIP_* variables
