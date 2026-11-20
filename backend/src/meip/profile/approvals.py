"""Approving the financial profile (Stage 3-B), and reading approvals.

**Atomic.** :func:`approve` runs in one transaction. Its first statement locks the case row
(``SELECT … FOR UPDATE``), which every user write that can change a profile input also takes
before writing. Inside the lock it rebuilds the profile, checks readiness and compares the
fingerprint with the one the broker saw, then inserts the approval (the same canonical text
that produced the fingerprint) and marks the case verified, writing one audit event. Nothing
can change the profile between the rebuild and the commit; anything that changed before it
gives ``profile-changed``. The background worker, the one writer without the lock, only moves
documents out of processing, and a processing document blocks approval.

**Immutable.** Approval rows are insert-only for the runtime role. Reopening clears the case's
pointer (``cases.approval_id``); the row remains. Database CHECKs tie ``verified`` to a
current approval.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import structlog
from sqlalchemy import func, select

from meip.audit import service as audit
from meip.audit.service import AuditAction
from meip.cases.models import CaseStatus
from meip.cases.service import get_case
from meip.core.db import utcnow
from meip.core.errors import BusinessRuleError, ConflictError, NotFoundError
from meip.profile import attestation
from meip.profile.models import ProfileApproval
from meip.profile.schemas import (
    ApprovalDetailOut,
    ApprovalOut,
    ApprovedOut,
    ApproveIn,
    ProfileSnapshot,
)
from meip.profile.service import _members, _ref, readiness, sha256_of, snapshot, superseded_at
from meip.tenancy.context import OperationContext

log = structlog.get_logger()


class ApprovalBlockedError(ConflictError):
    error_type = "approval-blocked"
    title = "The profile can't be approved yet"


class ProfileChangedError(ConflictError):
    error_type = "profile-changed"
    title = "The profile changed since you loaded it"


class SnapshotIntegrityError(ConflictError):
    error_type = "snapshot-integrity"
    title = "The approval's snapshot doesn't match its hash"


async def approve(ctx: OperationContext, case_id: uuid.UUID, body: ApproveIn) -> ApprovedOut:
    # 1. Lock the case row first: no user write to this case can start its change until we
    #    commit, and a waiting one will then find the case verified and be refused.
    case = await get_case(ctx, case_id, for_update=True)
    # 2. Rebuild the profile inside the lock.
    built = await snapshot(ctx, case_id)
    # 3. Genuine outstanding work blocks.
    ready = readiness(case, built.snapshot)
    if not ready.ready:
        raise ApprovalBlockedError(
            "; ".join(b.message for b in ready.blockers),
            extra={"blockers": [b.model_dump(mode="json") for b in ready.blockers]},
        )
    # 4. The broker must be approving exactly what is there now.
    if built.fingerprint != body.fingerprint:
        raise ProfileChangedError(
            "The profile changed since you loaded it. Reload it and review it again."
        )
    # 5. The attestation: the server's wording for a known, current version.
    text = attestation.text_of(body.attestation_version)
    if text is None or body.attestation_version != attestation.CURRENT:
        raise BusinessRuleError(
            f"Approve with the current attestation ({attestation.CURRENT}); reload the profile"
        )
    if not body.confirmed:
        raise BusinessRuleError("Tick the attestation to approve")

    # 6. Insert the approval: the next version, read inside the lock.
    latest = await ctx.session.scalar(
        select(func.max(ProfileApproval.version)).where(ProfileApproval.case_id == case_id)
    )
    approval = ProfileApproval(
        tenant_id=ctx.tenant_id,
        case_id=case_id,
        version=(latest or 0) + 1,
        profile_rules=built.snapshot.rules,
        snapshot=built.text,
        snapshot_sha256=built.fingerprint,
        attestation_version=body.attestation_version,
        attestation_text=text,
        note=body.note,
        approved_by_user_id=ctx.actor.user_id,
    )
    ctx.session.add(approval)
    await ctx.session.flush()

    # 7. The case: verified, pointing at this approval (the CHECKs require both together).
    before = audit.snapshot(case, ("status",))
    case.status = CaseStatus.VERIFIED.value
    case.approval_id = approval.id
    case.status_changed_at = utcnow()
    await ctx.session.flush()

    # 8. One audit event; the note goes to the erasable details.
    changes = audit.diff(before, audit.snapshot(case, ("status",)))
    for name, value in (
        ("approval_id", approval.id),
        ("approval_version", approval.version),
        ("snapshot_sha256", approval.snapshot_sha256),
        ("attestation_version", approval.attestation_version),
        ("profile_rules", approval.profile_rules),
        ("note", body.note),
    ):
        changes.note(name, value)
    await audit.record(
        ctx.session,
        tenant_id=ctx.tenant_id,
        actor=ctx.actor,
        action=AuditAction.PROFILE_APPROVED,
        entity_type="profile_approval",
        entity_id=approval.id,
        case_id=case_id,
        change_set=changes,
    )
    names = await _members(ctx, {ctx.actor.user_id} if ctx.actor.user_id else set())
    return ApprovedOut(
        **_out(approval, current=True, names=names).model_dump(),
        case_version=case.version,
    )


def _out(
    a: ProfileApproval,
    *,
    current: bool,
    names: dict[uuid.UUID, str],
    ended: datetime | None = None,
) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        case_id=a.case_id,
        version=a.version,
        profile_rules=a.profile_rules,
        snapshot_sha256=a.snapshot_sha256,
        attestation_version=a.attestation_version,
        note=a.note,
        approved_by=_ref(a.approved_by_user_id, names),
        approved_at=a.approved_at,
        current=current,
        superseded_at=ended,
    )


async def list_approvals(ctx: OperationContext, case_id: uuid.UUID) -> list[ApprovalOut]:
    case = await get_case(ctx, case_id)
    rows = list(
        await ctx.session.scalars(
            select(ProfileApproval)
            .where(ProfileApproval.case_id == case_id)
            .order_by(ProfileApproval.version.desc())
        )
    )
    names = await _members(ctx, {r.approved_by_user_id for r in rows if r.approved_by_user_id})
    ended = await superseded_at(ctx, case_id)
    return [
        _out(r, current=r.id == case.approval_id, names=names, ended=ended.get(str(r.id)))
        for r in rows
    ]


async def get_approval(
    ctx: OperationContext, case_id: uuid.UUID, approval_id: uuid.UUID
) -> ApprovalDetailOut:
    case = await get_case(ctx, case_id)
    row = await ctx.session.scalar(
        select(ProfileApproval).where(
            ProfileApproval.case_id == case_id, ProfileApproval.id == approval_id
        )
    )
    if row is None:
        raise NotFoundError("Approval not found")
    if sha256_of(row.snapshot) != row.snapshot_sha256:
        # Should be impossible without the owner role: refuse rather than show it.
        log.error("profile_snapshot_integrity", approval_id=str(row.id), case_id=str(case_id))
        raise SnapshotIntegrityError()
    names = await _members(ctx, {row.approved_by_user_id} if row.approved_by_user_id else set())
    ended = await superseded_at(ctx, case_id)
    base = _out(row, current=row.id == case.approval_id, names=names, ended=ended.get(str(row.id)))
    return ApprovalDetailOut(
        **base.model_dump(),
        attestation_text=row.attestation_text,
        snapshot=ProfileSnapshot.model_validate(json.loads(row.snapshot)),
    )
