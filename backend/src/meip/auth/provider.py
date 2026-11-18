"""Bearer-token authentication.

The platform never handles passwords. Users authenticate with a managed identity provider
(target: Microsoft Entra External ID); the API only validates the resulting signed JWT.
``AuthProvider`` is the seam that keeps the IdP interchangeable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import anyio.to_thread
import jwt
from jwt import PyJWKClient

from meip.core.errors import UnauthorizedError

ALLOWED_ALGORITHMS = ["RS256"]


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity. Not yet authorised for any tenant.

    ``(issuer, subject)`` is the identity key. ``email``/``display_name`` are the person's own
    token claims: shown back to them by ``/me`` but never stored or shown to any firm.
    """

    issuer: str
    subject: str
    email: str | None
    display_name: str | None


class AuthProvider(Protocol):
    issuer: str

    async def authenticate(self, token: str) -> Principal: ...


class KeyResolver(Protocol):
    async def key_for(self, token: str) -> Any: ...


class JWKSKeyResolver:
    """Fetches signing keys from the IdP's JWKS endpoint (cached by PyJWT)."""

    def __init__(self, jwks_url: str) -> None:
        self._client = PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=10)

    async def key_for(self, token: str) -> Any:
        signing_key = await anyio.to_thread.run_sync(self._client.get_signing_key_from_jwt, token)
        return signing_key.key


class StaticKeyResolver:
    """A single fixed public key. Used for the local development issuer and tests."""

    def __init__(self, public_key: Any) -> None:
        self._key = public_key

    async def key_for(self, token: str) -> Any:
        return self._key


class JWTAuthProvider:
    """Validates RS256 JWTs from one issuer.

    ``subject_claim`` selects the stable user identifier (``oid`` for Entra, ``sub`` for the
    development issuer). ``required_tenant_id``, when set, pins tokens to one directory via
    the ``tid`` claim, as defence in depth on top of issuer validation.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        key_resolver: KeyResolver,
        leeway_seconds: int = 30,
        subject_claim: str = "sub",
        required_tenant_id: str | None = None,
    ) -> None:
        self.issuer = issuer
        self._audience = audience
        self._keys = key_resolver
        self._leeway = leeway_seconds
        self._subject_claim = subject_claim
        self._tenant_id = required_tenant_id

    async def authenticate(self, token: str) -> Principal:
        try:
            key = await self._keys.key_for(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=ALLOWED_ALGORITHMS,
                audience=self._audience,
                issuer=self.issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", self._subject_claim]},
            )
        except jwt.PyJWTError as exc:
            # Do not echo the reason to the client; it can aid token forgery attempts.
            raise UnauthorizedError("Invalid or expired bearer token") from exc

        subject = claims[self._subject_claim]
        if not isinstance(subject, str) or not subject.strip():
            raise UnauthorizedError("Invalid or expired bearer token")
        if self._tenant_id is not None and claims.get("tid") != self._tenant_id:
            raise UnauthorizedError("Invalid or expired bearer token")

        email = claims.get("email") or claims.get("preferred_username")
        return Principal(
            issuer=str(claims["iss"]),
            subject=subject,
            email=str(email) if email else None,
            display_name=str(claims["name"]) if claims.get("name") else None,
        )
