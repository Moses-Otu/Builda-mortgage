from __future__ import annotations

from meip.auth.dev import load_or_create_key
from meip.auth.provider import AuthProvider, JWKSKeyResolver, JWTAuthProvider, StaticKeyResolver
from meip.core.config import DEV_AUTH_ENVIRONMENTS, AuthMode, ConfigurationError, Settings


def build_auth_provider(settings: Settings) -> AuthProvider:
    if settings.auth_mode is AuthMode.DEV:
        if settings.env not in DEV_AUTH_ENVIRONMENTS:  # defence in depth
            raise ConfigurationError("Development auth issuer cannot run in this environment")
        key = load_or_create_key(settings.dev_auth_key_path)
        return JWTAuthProvider(
            issuer=settings.dev_auth_issuer,
            audience=settings.dev_auth_audience,
            key_resolver=StaticKeyResolver(key.public_key()),
            leeway_seconds=settings.jwt_leeway_seconds,
        )

    # Presence is guaranteed by Settings validation; narrowed here for the type checker.
    assert settings.oidc_issuer  # noqa: S101
    assert settings.oidc_audience  # noqa: S101
    assert settings.oidc_jwks_url  # noqa: S101
    return JWTAuthProvider(
        issuer=settings.oidc_issuer,
        audience=settings.oidc_audience,
        key_resolver=JWKSKeyResolver(settings.oidc_jwks_url),
        leeway_seconds=settings.jwt_leeway_seconds,
        subject_claim=settings.oidc_subject_claim,
        required_tenant_id=settings.oidc_tenant_id,
    )
