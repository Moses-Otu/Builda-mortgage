"""Typed application settings, loaded from environment variables prefixed ``MEIP_``."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
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


class BlobBackend(StrEnum):
    AZURE = "azure"
    """Azure Blob Storage (Azurite locally)."""

    FILESYSTEM = "filesystem"
    """A local directory. Local development and tests only."""


class ConfigurationError(RuntimeError):
    """Raised when settings are invalid or unsafe for the selected environment."""


# The only environments in which the development token issuer may be enabled.
DEV_AUTH_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})

# Environments that never hold real borrower data. Anywhere else, document features need an
# approved retention and erasure decision (D10) and a real blob store.
NON_DEPLOYED_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})

MIB = 1024 * 1024


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

    documents_enabled: bool = Field(
        default=False,
        description="Master switch for document upload and processing. Off unless set.",
    )
    document_retention_approval: str | None = Field(
        default=None,
        description=(
            "Reference to the approved retention and erasure decision (D10). Required to "
            "enable documents outside local and test. A deployment control: the platform "
            "cannot tell synthetic documents from real ones."
        ),
    )
    document_max_bytes: int = Field(default=20 * MIB, gt=0)
    blob_backend: BlobBackend = BlobBackend.AZURE
    blob_root: Path = Field(
        default=Path(".dev/blobs"), description="Directory for the filesystem blob backend"
    )
    blob_connection_string: SecretStr | None = None
    blob_container: str = "documents"

    worker_poll_seconds: float = Field(default=2.0, gt=0)
    worker_lock_seconds: int = Field(
        default=600, gt=0, description="How long a claimed job stays locked to one worker"
    )
    pdf_check_timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _validate_documents(self) -> Settings:
        deployed = self.env not in NON_DEPLOYED_ENVIRONMENTS
        if self.blob_backend is BlobBackend.FILESYSTEM and deployed:
            raise ConfigurationError(
                f"blob_backend=filesystem is forbidden when env={self.env.value}"
            )
        if not self.documents_enabled:
            return self
        if deployed and not (self.document_retention_approval or "").strip():
            raise ConfigurationError(
                "documents_enabled requires MEIP_DOCUMENT_RETENTION_APPROVAL (the approved "
                f"retention and erasure decision, D10) when env={self.env.value}"
            )
        if self.blob_backend is BlobBackend.AZURE and self.blob_connection_string is None:
            raise ConfigurationError(
                "documents_enabled with blob_backend=azure requires MEIP_BLOB_CONNECTION_STRING"
            )
        if deployed and self.blob_connection_string is not None:
            # Values are never echoed: the string contains the account key.
            parts = {
                k.strip().lower(): v.strip()
                for k, _, v in (
                    p.partition("=")
                    for p in self.blob_connection_string.get_secret_value().split(";")
                    if p.strip()
                )
            }
            if parts.get("defaultendpointsprotocol", "").lower() != "https" or any(
                v.lower().startswith("http://") for v in parts.values()
            ):
                raise ConfigurationError(
                    f"MEIP_BLOB_CONNECTION_STRING must use HTTPS when env={self.env.value}"
                )
            if (
                parts.get("accountname", "").lower() == "devstoreaccount1"
                or parts.get("usedevelopmentstorage", "").lower() == "true"
            ):
                raise ConfigurationError(
                    "MEIP_BLOB_CONNECTION_STRING must not be the Azurite development account "
                    f"when env={self.env.value}"
                )
        return self

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
