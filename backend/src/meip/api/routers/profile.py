"""The financial profile (Stage 3-A) and its approval (Stage 3-B)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from meip.api.deps import DocumentReadCtx, ProfileApproveCtx, etag
from meip.profile import approvals, service
from meip.profile.schemas import ApprovalDetailOut, ApprovalOut, ApprovedOut, ApproveIn, ProfileOut

router = APIRouter(prefix="/api/v1/cases/{case_id}/profile", tags=["profile"])


@router.get(
    "",
    response_model=ProfileOut,
    summary="Get the financial profile",
    description=(
        "The case's financial profile under rule `profile/1`: every current (not retired) "
        "figure from items that are not withdrawn, labelled by origin (extracted, corrected, "
        "declared or broker entry), each with its provenance; what supports each item; the "
        "applicants, items, bank accounts, both rule sets' checks, every document, and the "
        "members referenced. Nothing is chosen, averaged, annualised or summed. Also, derived "
        "and outside the fingerprint: `readiness` (what blocks approval, notices, and the "
        "attestation wording) and `approval` (the current approval and superseded versions). "
        "`fingerprint` is the SHA-256 of the profile's canonical JSON. Needs `document:read`."
    ),
    responses={404: {"description": "Case not found"}},
)
async def get_profile(case_id: uuid.UUID, ctx: DocumentReadCtx) -> ProfileOut:
    return await service.build_profile(ctx, case_id)


@router.post(
    "/approvals",
    response_model=ApprovedOut,
    status_code=status.HTTP_201_CREATED,
    summary="Approve the profile",
    description=(
        "Attests the profile exactly as loaded: send its `fingerprint` and the current "
        "`attestation_version` with `confirmed: true`. In one transaction under the case lock, "
        "the profile is rebuilt; any blocker, or a different fingerprint, refuses. Otherwise "
        "the approval is stored (the canonical snapshot text, its hash, and the exact "
        "attestation wording) as the next version, and the case becomes `verified`. Approving "
        "is the only way to verify a case. Needs `profile:approve` (firm admin, adviser)."
    ),
    responses={
        404: {"description": "Case not found"},
        409: {
            "description": "`approval-blocked` (with the blockers) or `profile-changed` "
            "(reload and review again)"
        },
        422: {"description": "Not the current attestation, or not confirmed"},
    },
)
async def approve(
    case_id: uuid.UUID, body: ApproveIn, ctx: ProfileApproveCtx, response: Response
) -> ApprovedOut:
    out = await approvals.approve(ctx, case_id, body)
    await ctx.commit()
    response.headers["ETag"] = etag(out.case_version)
    return out


@router.get(
    "/approvals",
    response_model=list[ApprovalOut],
    summary="List the profile's approvals",
    description=(
        "Every approval of the case's profile, newest first: version, who approved it and "
        "when, the snapshot's hash, the attestation version, and whether it is current or was "
        "superseded by reopening the case (and when). Approvals are never changed or deleted."
    ),
    responses={404: {"description": "Case not found"}},
)
async def list_approvals(case_id: uuid.UUID, ctx: DocumentReadCtx) -> list[ApprovalOut]:
    return await approvals.list_approvals(ctx, case_id)


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalDetailOut,
    summary="Get one approval with its snapshot",
    description=(
        "One approval with the exact attestation wording the broker accepted and the approved "
        "profile, after checking the stored snapshot against its SHA-256 (a mismatch refuses "
        "with `snapshot-integrity`)."
    ),
    responses={
        404: {"description": "Case or approval not found"},
        409: {"description": "`snapshot-integrity`"},
    },
)
async def get_approval(
    case_id: uuid.UUID, approval_id: uuid.UUID, ctx: DocumentReadCtx
) -> ApprovalDetailOut:
    return await approvals.get_approval(ctx, case_id, approval_id)
