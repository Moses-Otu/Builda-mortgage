"""Typed application settings, loaded from environment variables prefixed ``MEIP_``."""

from __future__ import annotations

import os
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

    PASSWORD = "password"  # noqa: S105 - a mode name, not a secret
    """Phase 4 pilot: email and password accounts; the platform signs its own tokens with
    MEIP_AUTH_SIGNING_KEY_PATH. No external identity provider needed."""


class BlobBackend(StrEnum):
    AZURE = "azure"
    """Azure Blob Storage (Azurite locally)."""

    FILESYSTEM = "filesystem"
    """A local directory. In a deployment (Phase 4 pilot): a private volume on an encrypted
    disk, never served directly; set ``MEIP_BLOB_ROOT`` to an absolute path."""


class ExtractionProvider(StrEnum):
    SYNTHETIC = "synthetic"
    """Reads layouts embedded in synthetic test PDFs. Local and test only."""

    OPENAI = "openai"
    """Phase 4: the OpenAI API transcribes each page (structured outputs); the deterministic
    rules do the rest. Needs MEIP_OPENAI_API_KEY."""


class ConfigurationError(RuntimeError):
    """Raised when settings are invalid or unsafe for the selected environment."""


# The only environments in which the development token issuer may be enabled.
DEV_AUTH_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})

# Environments that never hold real borrower data. Anywhere else, document features need an
# approved retention and erasure decision (D10) and a real blob store.
NON_DEPLOYED_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.TEST})

MIB = 1024 * 1024

# Local and test only: a fixed key so fingerprints are stable across restarts in development.
DEVELOPMENT_FINGERPRINT_KEY = b"meip-local-development-account-fingerprint-key"


def _env_file() -> str | None:
    """The dotenv file to read: ``MEIP_ENV_FILE`` if set (empty means none), else ``.env``.
    The test suite sets it to empty so a developer's local ``.env`` can't leak into tests."""
    return os.environ.get("MEIP_ENV_FILE", ".env") or None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEIP_", env_file=_env_file(), extra="ignore")

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

    auth_signing_key_path: Path | None = Field(
        default=None, description="RSA private key (PEM) for password-mode tokens; a secret"
    )
    password_auth_issuer: str = "meip-accounts"  # noqa: S105 - token issuer name
    password_auth_audience: str = "meip-api"  # noqa: S105 - token audience name
    session_minutes: int = Field(default=480, ge=5, le=1440)

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
    extraction_provider: ExtractionProvider = ExtractionProvider.SYNTHETIC
    openai_api_key: SecretStr | None = Field(
        default=None, description="Server-side only; never sent to the browser or logged"
    )
    openai_model: str = Field(
        default="gpt-4.1", description="A vision-capable model that supports structured outputs"
    )
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: float = Field(default=120.0, gt=0)
    openai_max_attempts: int = Field(default=3, ge=1, le=6)
    bank_statements_enabled: bool = Field(
        default=False,
        description=(
            "Outside local and test, bank statements stay refused unless this is set, which "
            "should only follow a DPIA that covers statements (Stage 2-B plan, section 2.3)"
        ),
    )
    account_fingerprint_key: SecretStr | None = Field(
        default=None,
        description=(
            "Secret key for bank account fingerprints (HMAC of sort code and account number). "
            "Optional in local and test (a fixed development key is used); required anywhere "
            "else before bank statements can be enabled. Rotating it changes every fingerprint."
        ),
    )

    @property
    def bank_statements_allowed(self) -> bool:
        """Stage 2-B: bank statements are allowed in local and test. Anywhere else (Phase 4)
        only when ``MEIP_BANK_STATEMENTS_ENABLED`` is set, with a fingerprint key; that switch
        belongs after a DPIA that covers statements (Stage 2-B plan, section 2.3)."""
        if self.env in NON_DEPLOYED_ENVIRONMENTS:
            return True
        return self.bank_statements_enabled and self.account_fingerprint_key is not None

    def fingerprint_key(self) -> bytes:
        if self.account_fingerprint_key is not None:
            return self.account_fingerprint_key.get_secret_value().encode()
        if self.env in NON_DEPLOYED_ENVIRONMENTS:
            return DEVELOPMENT_FINGERPRINT_KEY
        raise ConfigurationError(
            f"MEIP_ACCOUNT_FINGERPRINT_KEY is required when env={self.env.value}"
        )

    @model_validator(mode="after")
    def _validate_documents(self) -> Settings:
        deployed = self.env not in NON_DEPLOYED_ENVIRONMENTS
        if (
            self.blob_backend is BlobBackend.FILESYSTEM
            and deployed
            and not self.blob_root.is_absolute()
        ):
            raise ConfigurationError(
                "blob_backend=filesystem needs an absolute MEIP_BLOB_ROOT "
                f"when env={self.env.value}"
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
        if self.extraction_provider is ExtractionProvider.OPENAI and self.openai_api_key is None:
            raise ConfigurationError("extraction_provider=openai requires MEIP_OPENAI_API_KEY")
        if (
            self.extraction_provider is ExtractionProvider.OPENAI
            and deployed
            and not (self.openai_base_url.startswith("https://"))
        ):
            raise ConfigurationError(
                f"MEIP_OPENAI_BASE_URL must use HTTPS when env={self.env.value}"
            )
        # Last: the synthetic provider trusts a layout embedded by whoever uploads the file, so
        # it stays off wherever real data could exist, even when every other setting is right.
        if deployed and self.extraction_provider is ExtractionProvider.SYNTHETIC:
            raise ConfigurationError(
                "extraction_provider=synthetic is test-only and forbidden "
                f"when env={self.env.value}"
            )
        return self

    @model_validator(mode="after")
    def _validate_auth(self) -> Settings:
        if self.auth_mode is AuthMode.DEV and self.env not in DEV_AUTH_ENVIRONMENTS:
            raise ConfigurationError(
                f"auth_mode=dev is forbidden when env={self.env.value}; configure OIDC instead"
            )
        if self.auth_mode is AuthMode.PASSWORD and self.auth_signing_key_path is None:
            raise ConfigurationError("auth_mode=password requires MEIP_AUTH_SIGNING_KEY_PATH")
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
