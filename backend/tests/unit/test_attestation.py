"""Stage 3-B: published attestation wording never changes (a new wording is a new version)."""

from __future__ import annotations

import hashlib

from meip.profile import attestation

# SHA-256 of each published version's exact text. Never edit an entry: add a new version.
PINNED = {
    "attestation/1": "21347645137dd2d2f569d1a4382c4dc7f4d68e2e0a2ec98a480c857e2fc50c29",
}


def test_published_wording_never_changes() -> None:
    actual = {
        v: hashlib.sha256(t.encode()).hexdigest() for v, t in attestation.ATTESTATIONS.items()
    }
    for version, digest in PINNED.items():
        assert actual[version] == digest, f"{version} was edited: publish a new version instead"


def test_every_version_is_pinned_and_the_current_one_exists() -> None:
    assert set(PINNED) == set(attestation.ATTESTATIONS), "pin every published version here"
    assert attestation.CURRENT in attestation.ATTESTATIONS
    assert attestation.text_of("attestation/0") is None
