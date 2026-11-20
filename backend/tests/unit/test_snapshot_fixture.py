"""Stage 3-B: a stored ``profile/1`` snapshot stays readable for ever.

The fixture is a real approval snapshot from the local walkthrough (SYNTHETIC case
SYN-BANK-001), stored byte for byte as it was hashed. Approvals are never rewritten, so every
later version of the code must still read ``profile/1`` snapshots: if a change to the profile
shape breaks this test, add a reader for the new version instead of changing this one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from meip.profile.schemas import ProfileSnapshot
from meip.profile.service import canonical

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "profile_snapshot_v1.json"
SHA256 = "3333e541732d4c3e9b18bef24a6b4163ceaf2fb35154ab37fcd3932afc2da600"


def test_the_stored_snapshot_is_unchanged() -> None:
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == SHA256


def test_a_profile_1_snapshot_still_parses_and_round_trips_exactly() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    snapshot = ProfileSnapshot.model_validate(json.loads(text))
    assert snapshot.rules == "profile/1"
    # Re-serialising gives the identical bytes: the canonical form is stable.
    assert canonical(snapshot) == text


def test_the_snapshot_is_self_contained() -> None:
    snapshot = ProfileSnapshot.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    members = {m.user_id for m in snapshot.members}
    for f in snapshot.figures:
        if f.evidence and f.evidence.reviewed_by:
            assert f.evidence.reviewed_by.user_id in members
    kinds = {k for s in snapshot.support for k in ("declared", "documentary", "corroborating")}
    assert kinds == {"declared", "documentary", "corroborating"}
    assert any(s.corroborating for s in snapshot.support)
    assert snapshot.checks.ruleset_versions == ["payslip-checks/1", "bank-checks/1"]
    assert all(d.original.sha256 for d in snapshot.documents)
