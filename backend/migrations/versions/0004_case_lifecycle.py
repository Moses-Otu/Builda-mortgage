"""Phase 1.1-C: case lifecycle, closure reasons, purpose and property use.

* Statuses: ``processing`` (system activity, not a case stage) and ``approved`` (could be
  read as lender approval) are retired. ``verified`` is deliberately *not* admitted by the
  CHECK yet: it arrives with profile approvals (Phase 9), together with a pointer CHECK, so a
  case can never be stored as verified without a durable approval.
* ``closure_reason`` / ``closure_note``; a closed case must have a reason.
* ``property_use`` separate from ``purpose``; ``porting`` added to purposes.
* Term CHECK relaxed to a sanity range (1-600 months); typical ranges are API warnings.

Data: existing ``processing`` rows move to ``collecting_evidence`` and ``approved`` rows to
``in_review`` (neither was reachable through the API). Existing closed cases get
``closure_reason = 'other'`` with an explanatory note.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSES = ("purchase", "remortgage", "further_advance", "product_transfer", "porting")
STATUSES = ("draft", "collecting_evidence", "in_review", "closed")
PROPERTY_USES = ("residential", "buy_to_let", "second_home")
CLOSURE_REASONS = (
    "completed",
    "not_proceeding",
    "declined_by_lender",
    "withdrawn_by_firm",
    "duplicate",
    "other",
)
OLD_PURPOSES = ("purchase", "remortgage", "further_advance", "product_transfer")
OLD_STATUSES = ("draft", "collecting_evidence", "processing", "in_review", "approved", "closed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.add_column("cases", sa.Column("property_use", sa.String(32), nullable=True))
    op.add_column("cases", sa.Column("closure_reason", sa.String(32), nullable=True))
    op.add_column("cases", sa.Column("closure_note", sa.String(500), nullable=True))

    op.drop_constraint(op.f("ck_cases_status"), "cases", type_="check")
    op.drop_constraint(op.f("ck_cases_purpose"), "cases", type_="check")
    op.drop_constraint(op.f("ck_cases_term"), "cases", type_="check")

    op.execute("UPDATE cases SET status = 'collecting_evidence' WHERE status = 'processing'")
    op.execute("UPDATE cases SET status = 'in_review' WHERE status = 'approved'")
    op.execute(
        "UPDATE cases SET closure_reason = 'other', "
        "closure_note = 'Closed before closure reasons were recorded' "
        "WHERE status = 'closed'"
    )

    op.create_check_constraint("status", "cases", _in("status", STATUSES))
    op.create_check_constraint("purpose", "cases", _in("purpose", PURPOSES))
    op.create_check_constraint(
        "property_use", "cases", "property_use IS NULL OR " + _in("property_use", PROPERTY_USES)
    )
    op.create_check_constraint(
        "closure_reason",
        "cases",
        "closure_reason IS NULL OR " + _in("closure_reason", CLOSURE_REASONS),
    )
    op.create_check_constraint(
        "closure_consistency", "cases", "(status = 'closed') = (closure_reason IS NOT NULL)"
    )
    op.create_check_constraint(
        "term", "cases", "term_months IS NULL OR term_months BETWEEN 1 AND 600"
    )


def downgrade() -> None:
    for name in ("term", "closure_consistency", "closure_reason", "property_use", "purpose"):
        op.drop_constraint(op.f(f"ck_cases_{name}"), "cases", type_="check")
    op.drop_constraint(op.f("ck_cases_status"), "cases", type_="check")
    op.execute("UPDATE cases SET purpose = 'purchase' WHERE purpose = 'porting'")
    # Terms outside the old 12-480 range cannot be represented after downgrade.
    op.execute("UPDATE cases SET term_months = NULL WHERE term_months NOT BETWEEN 12 AND 480")
    op.create_check_constraint("status", "cases", _in("status", OLD_STATUSES))
    op.create_check_constraint("purpose", "cases", _in("purpose", OLD_PURPOSES))
    op.create_check_constraint(
        "term", "cases", "term_months IS NULL OR term_months BETWEEN 12 AND 480"
    )
    op.drop_column("cases", "closure_note")
    op.drop_column("cases", "closure_reason")
    op.drop_column("cases", "property_use")
