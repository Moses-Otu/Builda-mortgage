"""Case status state machine.

The full lifecycle is defined now so later phases plug in rather than redesign it. Some
transitions depend on capabilities that do not exist yet (document processing, review);
they are declared but report themselves as unavailable instead of pretending to work.
"""

from __future__ import annotations

from meip.cases.models import CaseStatus

S = CaseStatus

TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    S.DRAFT: frozenset({S.COLLECTING_EVIDENCE, S.CLOSED}),
    S.COLLECTING_EVIDENCE: frozenset({S.DRAFT, S.PROCESSING, S.CLOSED}),
    S.PROCESSING: frozenset({S.COLLECTING_EVIDENCE, S.IN_REVIEW, S.CLOSED}),
    S.IN_REVIEW: frozenset({S.COLLECTING_EVIDENCE, S.APPROVED, S.CLOSED}),
    S.APPROVED: frozenset({S.CLOSED}),
    S.CLOSED: frozenset(),
}

# Transitions that need capabilities from a later phase, with the reason shown to clients.
NOT_YET_AVAILABLE: dict[tuple[CaseStatus, CaseStatus], str] = {
    (S.COLLECTING_EVIDENCE, S.PROCESSING): "Document processing is not available yet",
    (S.PROCESSING, S.COLLECTING_EVIDENCE): "Document processing is not available yet",
    (S.PROCESSING, S.IN_REVIEW): "Document processing is not available yet",
    (S.IN_REVIEW, S.COLLECTING_EVIDENCE): "Broker review is not available yet",
    (S.IN_REVIEW, S.APPROVED): "Broker review and approval is not available yet",
}

# Statuses in which case details and applicants may still be edited.
EDITABLE_STATUSES = frozenset({S.DRAFT, S.COLLECTING_EVIDENCE})


def is_defined(current: CaseStatus, target: CaseStatus) -> bool:
    return target in TRANSITIONS[current]


def unavailable_reason(current: CaseStatus, target: CaseStatus) -> str | None:
    return NOT_YET_AVAILABLE.get((current, target))
