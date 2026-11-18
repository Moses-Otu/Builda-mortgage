"""Acceptance criteria 7 (token validation) and 8 (dev issuer refused in production)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from meip.auth.dev import generate_key, load_or_create_key, mint_token
from meip.auth.factory import build_auth_provider
from meip.auth.provider import JWKSKeyResolver, JWTAuthProvider, StaticKeyResolver
from meip.core.config import AuthMode, ConfigurationError, Environment, Settings
from meip.core.errors import UnauthorizedError

ISS, AUD = "https://issuer.test/", "meip-api"


@pytest.fixture(scope="module")
def key() -> RSAPrivateKey:
    return generate_key()


@pytest.fixture(scope="module")
def provider(key: RSAPrivateKey) -> JWTAuthProvider:
    return JWTAuthProvider(
        issuer=ISS, audience=AUD, key_resolver=StaticKeyResolver(key.public_key()), leeway_seconds=0
    )


async def test_valid_token(key: RSAPrivateKey, provider: JWTAuthProvider) -> None:
    token = mint_token(key, issuer=ISS, audience=AUD, subject="s1", email="a@b.test", name="A B")
    principal = await provider.authenticate(token)
    assert (principal.issuer, principal.subject) == (ISS, "s1")
    assert (principal.email, principal.display_name) == ("a@b.test", "A B")


async def test_preferred_username_used_when_email_absent(
    key: RSAPrivateKey, provider: JWTAuthProvider
) -> None:
    now = datetime.now(UTC)
    claims = {"iss": ISS, "aud": AUD, "sub": "s", "iat": now, "exp": now + timedelta(minutes=5)}
    token = jwt.encode({**claims, "preferred_username": "u@x.test"}, key, algorithm="RS256")
    assert (await provider.authenticate(token)).email == "u@x.test"


@pytest.mark.parametrize(
    ("overrides", "label"),
    [
        ({"audience": "other-api"}, "wrong audience"),
        ({"issuer": "https://evil.test/"}, "wrong issuer"),
        ({"now": datetime.now(UTC) - timedelta(hours=2)}, "expired"),
    ],
)
async def test_rejected_claims(
    key: RSAPrivateKey, provider: JWTAuthProvider, overrides: dict[str, object], label: str
) -> None:
    kwargs: dict[str, object] = {"issuer": ISS, "audience": AUD, "subject": "s", **overrides}
    token = mint_token(key, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedError):
        await provider.authenticate(token)


async def test_signature_from_other_key_rejected(provider: JWTAuthProvider) -> None:
    token = mint_token(generate_key(), issuer=ISS, audience=AUD, subject="s")
    with pytest.raises(UnauthorizedError):
        await provider.authenticate(token)


async def test_missing_subject_rejected(key: RSAPrivateKey, provider: JWTAuthProvider) -> None:
    now = datetime.now(UTC)
    token = jwt.encode(
        {"iss": ISS, "aud": AUD, "iat": now, "exp": now + timedelta(minutes=5)},
        key,
        algorithm="RS256",
    )
    with pytest.raises(UnauthorizedError):
        await provider.authenticate(token)


async def test_unsigned_alg_none_rejected(provider: JWTAuthProvider) -> None:
    def b64(obj: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(datetime.now(UTC).timestamp())
    header = b64({"alg": "none", "typ": "JWT"})
    payload = b64({"iss": ISS, "aud": AUD, "sub": "s", "iat": now, "exp": now + 300})
    token = f"{header}.{payload}."
    with pytest.raises(UnauthorizedError):
        await provider.authenticate(token)


async def test_garbage_rejected(provider: JWTAuthProvider) -> None:
    with pytest.raises(UnauthorizedError):
        await provider.authenticate("not-a-jwt")


def test_dev_key_persisted_and_reloaded(tmp_dir: Path) -> None:
    path = tmp_dir / "keys" / "dev.pem"
    first = load_or_create_key(path)
    second = load_or_create_key(path)
    assert first.private_numbers() == second.private_numbers()


def test_factory_builds_dev_provider(tmp_dir: Path) -> None:
    settings = Settings(
        env=Environment.LOCAL, auth_mode=AuthMode.DEV, dev_auth_key_path=tmp_dir / "k.pem"
    )
    provider = build_auth_provider(settings)
    assert provider.issuer == settings.dev_auth_issuer


def test_factory_builds_oidc_provider() -> None:
    settings = Settings(
        env=Environment.PRODUCTION,
        auth_mode=AuthMode.OIDC,
        oidc_issuer="https://login.example/",
        oidc_audience="api://meip",
        oidc_jwks_url="https://login.example/keys",
    )
    provider = build_auth_provider(settings)
    assert isinstance(provider, JWTAuthProvider)
    assert isinstance(provider._keys, JWKSKeyResolver)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PRODUCTION])
def test_factory_refuses_dev_mode_even_if_settings_validation_bypassed(env: Environment) -> None:
    unsafe = Settings.model_construct(env=env, auth_mode=AuthMode.DEV)
    with pytest.raises(ConfigurationError):
        build_auth_provider(unsafe)


def test_factory_wires_entra_identity_claims() -> None:
    settings = Settings(
        env=Environment.PRODUCTION,
        auth_mode=AuthMode.OIDC,
        oidc_issuer="https://login.example/",
        oidc_audience="api://meip",
        oidc_jwks_url="https://login.example/keys",
        oidc_tenant_id="dir-123",
    )
    provider = build_auth_provider(settings)
    assert isinstance(provider, JWTAuthProvider)
    assert provider._subject_claim == "oid"
    assert provider._tenant_id == "dir-123"


# --- Identity claims (Entra-style: oid + tid) ---------------------------------------------


def _entra_token(key: RSAPrivateKey, **claims: object) -> str:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "iss": ISS,
        "aud": AUD,
        "sub": "pairwise-per-client-app",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    return jwt.encode({**base, **claims}, key, algorithm="RS256")


@pytest.fixture(scope="module")
def entra(key: RSAPrivateKey) -> JWTAuthProvider:
    return JWTAuthProvider(
        issuer=ISS,
        audience=AUD,
        key_resolver=StaticKeyResolver(key.public_key()),
        leeway_seconds=0,
        subject_claim="oid",
        required_tenant_id="dir-123",
    )


async def test_identity_is_object_id_not_per_app_sub(
    key: RSAPrivateKey, entra: JWTAuthProvider
) -> None:
    principal = await entra.authenticate(_entra_token(key, oid="obj-1", tid="dir-123"))
    assert principal.subject == "obj-1"

    # The same person through a different client app: different sub, same oid -> same identity.
    other_app = _entra_token(key, oid="obj-1", tid="dir-123", sub="different-pairwise-value")
    assert (await entra.authenticate(other_app)).subject == "obj-1"


async def test_missing_object_id_rejected(key: RSAPrivateKey, entra: JWTAuthProvider) -> None:
    with pytest.raises(UnauthorizedError):
        await entra.authenticate(_entra_token(key, tid="dir-123"))


@pytest.mark.parametrize("oid", ["", "   ", 12345])
async def test_blank_or_non_string_object_id_rejected(
    key: RSAPrivateKey, entra: JWTAuthProvider, oid: object
) -> None:
    with pytest.raises(UnauthorizedError):
        await entra.authenticate(_entra_token(key, oid=oid, tid="dir-123"))


@pytest.mark.parametrize("claims", [{"tid": "other-directory"}, {}])
async def test_token_from_other_or_unknown_directory_rejected(
    key: RSAPrivateKey, entra: JWTAuthProvider, claims: dict[str, object]
) -> None:
    with pytest.raises(UnauthorizedError):
        await entra.authenticate(_entra_token(key, oid="obj-1", **claims))
