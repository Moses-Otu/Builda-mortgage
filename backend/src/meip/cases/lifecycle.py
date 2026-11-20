"""Case status state machine.

Statuses describe where the *broker's* work stands, never system activity: document
processing is reported by a derived indicator (Phase 2), not by a case status.

``verified`` means a broker has approved the financial profile (Stage 3-B). The only way in
is approving the profile (``POST …/profile/approvals``), which writes the approval and the
status together; the status route refuses ``in_review → verified``. Database CHECKs tie
``verified`` to a current approval.
"""

from __future__ import annotations

from meip.cases.models import CaseStatus

S = CaseStatus

TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.DRAFT: frozenset({S.COLLECTING_EVIDENCE, S.CLOSED}),
    S.COLLECTING_EVIDENCE: frozenset({S.DRAFT, S.IN_REVIEW, S.CLOSED}),
    S.IN_REVIEW: frozenset({S.COLLECTING_EVIDENCE, S.VERIFIED, S.CLOSED}),
    # Leaving 'verified' (other than closing) is a reopen: it supersedes the approval.
    S.VERIFIED: frozenset({S.COLLECTING_EVIDENCE, S.IN_REVIEW, S.CLOSED}),
    # Reopening a closed case returns it to evidence collection.
    S.CLOSED: frozenset({S.COLLECTING_EVIDENCE}),
}

# Transitions that need capabilities from a later phase, with the reason shown to clients.
NOT_YET_AVAILABLE: dict[tuple[CaseStatus, CaseStatus], str] = {
    (S.IN_REVIEW, S.VERIFIED): "Approve the profile to verify the case",
}

# Statuses in which case details and applicants may still be edited. A verified case must
# be reopened first (which supersedes its approval); a closed case must be reopened.
EDITABLE_STATUSES = frozenset({S.DRAFT, S.COLLECTING_EVIDENCE, S.IN_REVIEW})

# Transitions that reopen finished work and therefore need a written reason.
REOPENING = frozenset(
    {
        (S.CLOSED, S.COLLECTING_EVIDENCE),
        (S.VERIFIED, S.COLLECTING_EVIDENCE),
        (S.VERIFIED, S.IN_REVIEW),
    }
)


def is_defined(current: CaseStatus, target: CaseStatus) -> bool:
    return target in TRANSITIONS[current]


def unavailable_reason(current: CaseStatus, target: CaseStatus) -> str | None:
    return NOT_YET_AVAILABLE.get((current, target))


def is_reopening(current: CaseStatus, target: CaseStatus) -> bool:
    return (current, target) in REOPENING
