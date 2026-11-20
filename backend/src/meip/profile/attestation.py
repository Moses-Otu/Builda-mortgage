"""What a broker attests when approving the profile (Stage 3-B).

Every published version's wording is kept here for ever and **never edited**: new wording is
a new version. A test pins each version to the SHA-256 of its text, so an edit fails the
build. The server supplies the text (the client sends only the version), and each approval
stores the exact text it was given, so what a broker agreed to is preserved in the approval
record itself.
"""

from __future__ import annotations

ATTESTATIONS: dict[str, str] = {
    "attestation/1": (
        "I have reviewed the evidence and the figures in this profile, and I confirm that they "
        "reflect the documents and information provided. The checks listed have been reviewed "
        "and every discrepancy has been acknowledged. This approval records the evidence "
        "position on this date; it is not a lending decision, an affordability assessment or "
        "advice."
    ),
}

CURRENT = "attestation/1"


def text_of(version: str) -> str | None:
    return ATTESTATIONS.get(version)
