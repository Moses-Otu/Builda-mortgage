"""The financial profile (Stage 3-A): every current figure with its provenance. Read-only."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from meip.api.deps import DocumentReadCtx
from meip.profile import service
from meip.profile.schemas import ProfileOut

router = APIRouter(prefix="/api/v1/cases/{case_id}/profile", tags=["profile"])


@router.get(
    "",
    response_model=ProfileOut,
    summary="Get the financial profile",
    description=(
        "The case's financial profile under rule `profile/1`: every current (not retired) "
        "figure from items that are not withdrawn, labelled by origin (extracted, corrected, "
        "declared or broker entry), each with its provenance. A document figure carries the "
        "document, page and boxes, the extraction, the broker's decision and who reviewed it. "
        "Also the applicants, items, bank accounts, both rule sets' checks and every document "
        "on the case. Nothing is chosen, averaged, annualised or summed. `fingerprint` is the "
        "SHA-256 of the rest of the document. Needs `document:read`, since it includes "
        "document details."
    ),
    responses={404: {"description": "Case not found"}},
)
async def get_profile(case_id: uuid.UUID, ctx: DocumentReadCtx) -> ProfileOut:
    return await service.build_profile(ctx, case_id)
