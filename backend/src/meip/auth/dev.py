"""Local development token issuer.

FOR LOCAL DEVELOPMENT AND AUTOMATED TESTS ONLY. Settings validation refuses
``auth_mode=dev`` in staging and production, and ``create_app`` re-checks at startup.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

DEV_KEY_ID = "meip-dev-1"


def generate_key() -> RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def load_or_create_key(path: Path) -> RSAPrivateKey:
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, RSAPrivateKey):
            raise TypeError(f"{path} does not contain an RSA private key")
        return key
    key = generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return key


def mint_token(
    key: RSAPrivateKey,
    *,
    issuer: str,
    audience: str,
    subject: str,
    email: str | None = None,
    name: str | None = None,
    ttl: timedelta = timedelta(hours=1),
    now: datetime | None = None,
    extra: dict[str, object] | None = None,
) -> str:
    issued = now or datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "iat": issued,
        "nbf": issued,
        "exp": issued + ttl,
    }
    if email:
        claims["email"] = email
    if name:
        claims["name"] = name
    if extra:
        claims.update(extra)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": DEV_KEY_ID})
