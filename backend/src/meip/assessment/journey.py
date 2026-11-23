"""The guided mortgage journey: where a case is, what's done, what's outstanding, what's next.

Six stages, derived from the case's real state on every read (nothing extra is stored, so the
journey can't drift from the data):

1. **Applicant and loan details** — an active borrower, an income source, the property value,
   loan amount and term.
2. **Income evidence** — "Payslips" for employed applicants; for other income, the evidence
   its type needs. Adaptive: requirements come from the same evidence rules the agent uses.
3. **Bank statements** — showing the salary arriving, where there's payroll income.
4. **Approve financial profile** — the broker accepts the reviewed figures as supported enough
   to assess. Not a mortgage approval.
5. **Mortgage assessment** — the agent's run against the approved profile.
6. **Broker decision** — accept or override (with a reason); this completes the journey.

Evidence stages guide, they don't gate: approval is blocked only by genuine outstanding work
(documents processing or unreviewed, unacknowledged discrepancies), as before. The assessment
itself is gated on the backend (see ``service.blockers``).
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from meip.assessment import evidence
from meip.assessment.inputs import PAYROLL
from meip.assessment.inputs import load as load_inputs
from meip.assessment.models import (
    Assessment,
    AssessmentDecision,
    AssessmentStatus,
    EvidenceRequest,
    RequestStatus,
)
from meip.assessment.policy import get_policy
from meip.assessment.schemas import (
    EvidenceCountsOut,
    JourneyActionOut,
    JourneyOut,
    JourneyStageOut,
)
from meip.assessment.service import FINAL_DECISIONS, blockers, policy_for
from meip.cases.models import CaseStatus
from meip.cases.service import get_case
from meip.profile import service as profile_service
from meip.tenancy.context import OperationContext

PROCESSING = {"received", "ready"}
TO_REVIEW = {"extracted"}


async def journey(ctx: OperationContext, case_id: uuid.UUID, today: date) -> JourneyOut:
    case = await get_case(ctx, case_id)
    policy = get_policy(policy_for(case, None))
    inputs, snapshot = await load_inputs(ctx, case_id, policy, today)
    ready = profile_service.readiness(case, snapshot)
    gaps = evidence.identify(policy, inputs, snapshot.checks, today)
    docs = snapshot.documents

    def count(kind: str | None) -> EvidenceCountsOut:
        mine = [d for d in docs if kind is None or d.document_type == kind]
        return EvidenceCountsOut(
            received=len(mine),
            processing=sum(1 for d in mine if d.status in PROCESSING),
            to_review=sum(1 for d in mine if d.status in TO_REVIEW),
            reviewed=sum(1 for d in mine if d.status == "reviewed"),
            failed=sum(1 for d in mine if d.status in {"failed", "unrecognised"}),
        )

    payslips, statements = count("payslip"), count("bank_statement")
    payroll = [x for x in inputs.income if x.kind in PAYROLL]
    other = [x for x in inputs.income if x.kind not in PAYROLL]
    borrowers = [a for a in inputs.applicants if a.party_role == "borrower"]

    # 1. Details
    details_missing: list[str] = []
    if not borrowers:
        details_missing.append("Add the applicant.")
    if not inputs.income:
        details_missing.append("Add the applicant's income (employment or other).")
    for name, value in (
        ("property value", case.property_value_minor),
        ("loan amount", case.loan_amount_minor),
        ("mortgage term", case.term_months),
    ):
        if not value:
            details_missing.append(f"Add the {name}.")
    for g in gaps:
        if g.code.startswith(("employment_basis:", "employment_start:")):
            who = f"{g.applicant_label}: " if g.applicant_label else ""
            details_missing.append(f"{who}{g.title.lower()}.")

    # 2. Income evidence (adaptive)
    income_title = "Payslips" if payroll or not other else "Income evidence"
    income_req: list[str] = []
    for g in gaps:
        if g.code.startswith("payslips:"):
            income_req.append(f"{g.applicant_label}: {g.title}.")
        elif g.code.startswith("income:"):
            income_req.append(
                f"{g.applicant_label}: {g.title}"
                + (
                    " (give to your broker to review and record)"
                    if not g.supported_on_platform
                    else ""
                )
                + "."
            )
    if payslips.to_review:
        income_req.insert(0, f"Review {payslips.to_review} payslip(s) waiting for you.")
    if payslips.processing:
        income_req.insert(0, f"{payslips.processing} payslip(s) still being read.")
    income_done = (
        not any(
            g.code.startswith("payslips:")
            or (g.code.startswith("income:") and g.supported_on_platform)
            for g in gaps
        )
        and not (payslips.to_review or payslips.processing)
        and bool(inputs.income)
    )

    # 3. Statements
    statement_req = [
        f"{g.applicant_label}: {g.title}." for g in gaps if g.code.startswith("statements:")
    ]
    if statements.to_review:
        statement_req.insert(0, f"Confirm {statements.to_review} statement(s) waiting for you.")
    if statements.processing:
        statement_req.insert(0, f"{statements.processing} statement(s) still being read.")
    statements_needed = bool(payroll)
    if statements_needed and not statements.received:
        statement_req.append(
            "Upload the bank statements for the months the payslips cover, so each salary "
            "payment can be matched."
        )
    statements_done = (
        not statement_req and statements.reviewed > 0
        if statements_needed
        else not (statements.to_review or statements.processing)
    )

    # 4. Profile approval
    approved = case.status == CaseStatus.VERIFIED.value and case.approval_id is not None
    approval_req = [b.message for b in ready.blockers if b.code != "case_not_open"]
    open_disc = [
        f
        for f in snapshot.checks.findings
        if f.status == "discrepancy" and f.acknowledgement is None
    ]
    if open_disc and not approved:
        approval_req = [m for m in approval_req if "discrepanc" not in m.lower()]
        approval_req.insert(
            0,
            f"Review {len(open_disc)} discrepanc{'y' if len(open_disc) == 1 else 'ies'} "
            "between the documents.",
        )

    # 5 and 6. Assessment and decision
    rows = list(
        await ctx.session.scalars(
            select(Assessment)
            .where(Assessment.case_id == case_id)
            .order_by(Assessment.version.desc())
        )
    )
    running = next(
        (
            a
            for a in rows
            if a.status in {AssessmentStatus.QUEUED.value, AssessmentStatus.RUNNING.value}
        ),
        None,
    )
    completed = [a for a in rows if a.status == AssessmentStatus.COMPLETED.value]
    latest = completed[0] if completed else None
    current = latest if latest and approved and latest.approval_id == case.approval_id else None
    decided = False
    if current is not None:
        decided = bool(
            await ctx.session.scalar(
                select(AssessmentDecision.id)
                .where(
                    AssessmentDecision.assessment_id == current.id,
                    AssessmentDecision.decision.in_(FINAL_DECISIONS),
                )
                .limit(1)
            )
        )
    # New evidence after a completed assessment: the case was reopened (approval cleared) or
    # re-approved since. The previous result is kept; a new version is needed.
    reassess = latest is not None and current is None and running is None
    assessment_blockers = blockers(case)
    failed = rows[0] if rows and rows[0].status == AssessmentStatus.FAILED.value else None

    open_requests = list(
        await ctx.session.scalars(
            select(EvidenceRequest).where(
                EvidenceRequest.case_id == case_id,
                EvidenceRequest.status == RequestStatus.OPEN.value,
            )
        )
    )

    stages: list[JourneyStageOut] = [
        JourneyStageOut(
            key="details",
            number=1,
            title="Applicant and loan details",
            done=not details_missing,
            summary=(
                f"{len(borrowers)} applicant(s); loan £{(case.loan_amount_minor or 0) / 100:,.0f} "
                f"on £{(case.property_value_minor or 0) / 100:,.0f}"
                if not details_missing
                else "Details needed"
            ),
            requirements=details_missing,
        ),
        JourneyStageOut(
            key="income",
            number=2,
            title=income_title,
            done=income_done,
            summary=f"{payslips.reviewed} reviewed of {payslips.received} received"
            if payslips.received
            else ("Nothing uploaded yet" if payroll or not other else "See requirements"),
            requirements=income_req,
        ),
        JourneyStageOut(
            key="statements",
            number=3,
            title="Bank statements",
            done=statements_done,
            optional=not statements_needed,
            summary=(
                f"{statements.reviewed} confirmed of {statements.received} received"
                if statements.received
                else ("Nothing uploaded yet" if statements_needed else "Optional for this income")
            ),
            requirements=statement_req,
        ),
        JourneyStageOut(
            key="profile",
            number=4,
            title="Approve financial profile",
            done=approved,
            summary="Approved" if approved else "Not approved yet",
            requirements=[] if approved else approval_req,
        ),
        JourneyStageOut(
            key="assessment",
            number=5,
            title="Mortgage assessment",
            done=current is not None,
            summary=(
                "In progress"
                if running
                else "Reassessment required"
                if reassess
                else "Complete"
                if current
                else "Not run yet"
            ),
            requirements=[b["message"] for b in assessment_blockers] if current is None else [],
        ),
        JourneyStageOut(
            key="decision",
            number=6,
            title="Broker decision",
            done=decided,
            summary="Recorded"
            if decided
            else "Awaiting your decision"
            if current
            else "After the assessment",
            requirements=[],
        ),
    ]

    # Current stage and the one next action.
    if running is not None:
        stage, action = (
            "assessment",
            JourneyActionOut(
                label="View the assessment in progress", target="assessment", kind="navigate"
            ),
        )
    elif decided:
        stage, action = (
            "complete",
            JourneyActionOut(label="View the decision", target="decision", kind="navigate"),
        )
    elif current is not None:
        stage, action = (
            "decision",
            JourneyActionOut(
                label="Next: Review Assessment and Decide", target="decision", kind="navigate"
            ),
        )
    elif approved:
        if assessment_blockers:
            stage, action = (
                "details",
                JourneyActionOut(label="Next: Add Loan Details", target="details", kind="navigate"),
            )
        else:
            stage, action = (
                "assessment",
                JourneyActionOut(
                    label="Reassess Mortgage Application"
                    if reassess
                    else "Next: Run Mortgage Assessment",
                    target="assessment",
                    kind="start_assessment",
                ),
            )
    elif details_missing:
        stage, action = (
            "details",
            JourneyActionOut(
                label="Next: Add Applicant Details", target="details", kind="navigate"
            ),
        )
    elif not income_done:
        stage, action = (
            "income",
            JourneyActionOut(
                label=(
                    "Next: Review Payslips"
                    if payslips.to_review
                    else "Next: Upload Payslips"
                    if payroll
                    else "Next: Add Income Evidence"
                ),
                target="income",
                kind="navigate",
            ),
        )
    elif not statements_done and (
        statements.to_review or statements.processing or not statements.received
    ):
        # Statements still to upload or confirm. Once every statement on file is confirmed, a
        # month the statements don't show stays listed on the stage, but doesn't hold up the
        # approval: the assessment treats that salary as unverified and says so.
        stage, action = (
            "statements",
            JourneyActionOut(
                label="Next: Confirm Bank Statements"
                if statements.to_review
                else "Next: Upload Bank Statements",
                target="statements",
                kind="navigate",
            ),
        )
    else:
        stage, action = (
            "profile",
            JourneyActionOut(
                label="Reassess Mortgage Application"
                if reassess
                else "Next: Review and Approve Profile",
                target="profile",
                kind="navigate",
            ),
        )

    newest = running or latest or failed
    return JourneyOut(
        case_id=case.id,
        case_status=case.status,
        stage=stage,
        stages=stages,
        next_action=action,
        reassessment_required=reassess,
        reassessment_message=(
            "New evidence received — Reassessment required." if reassess else None
        ),
        profile_approved=approved,
        approval_id=case.approval_id,
        latest_assessment_id=newest.id if newest else None,
        assessment_running=running is not None,
        assessment_failed=failed is not None and running is None,
        assessment_blockers=[b["message"] for b in assessment_blockers],
        approval_blockers=approval_req if not approved else [],
        open_evidence_requests=len(open_requests),
        policy_code=policy.code,
        payslips=payslips,
        statements=statements,
    )
