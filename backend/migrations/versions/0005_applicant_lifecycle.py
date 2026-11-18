"""Phase 1.1-D: applicant roles, title ownership, primary applicant, soft removal.

* ``position`` -> ``display_order`` (presentation only), unique among *active* applicants.
* ``party_role`` (borrower | guarantor) and ``on_title`` (NULL = not yet known) are separate:
  liability and ownership vary independently (D5).
* ``is_primary``: at most one active primary per case, and only a borrower.
* Soft removal: ``status``, ``removed_at``, ``removed_by_user_id``, ``removal_reason``,
  ``removal_note``. The runtime role loses DELETE: applicants are never hard-deleted by the
  application (erasure is a separate, owner-run retention process).
* ``employment_status`` dropped (D3): employment belongs to income sources (1.1-E).

Data: existing applicants become active borrowers; in each case the lowest-positioned one
becomes primary.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
REMOVAL_CONSISTENCY = (
    "(status = 'active' AND removed_at IS NULL AND removal_reason IS NULL"
    " AND removal_note IS NULL)"
    " OR (status = 'removed' AND removed_at IS NOT NULL AND removal_reason IS NOT NULL"
    " AND NOT is_primary)"
)
EMPLOYMENT_STATUSES = (
    "employed",
    "self_employed",
    "company_director",
    "contractor",
    "retired",
    "not_employed",
    "other",
)


def upgrade() -> None:
    op.drop_constraint(op.f("uq_applicants_case_id_position"), "applicants", type_="unique")
    op.drop_constraint(op.f("ck_applicants_position"), "applicants", type_="check")
    op.alter_column("applicants", "position", new_column_name="display_order")

    op.add_column(
        "applicants",
        sa.Column("party_role", sa.String(16), nullable=False, server_default="borrower"),
    )
    op.add_column("applicants", sa.Column("on_title", sa.Boolean(), nullable=True))
    op.add_column(
        "applicants",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "applicants",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column("applicants", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column("applicants", sa.Column("removed_by_user_id", sa.Uuid()))
    op.add_column("applicants", sa.Column("removal_reason", sa.String(32)))
    op.add_column("applicants", sa.Column("removal_note", sa.String(500)))
    for column in ("party_role", "is_primary", "status"):
        op.alter_column("applicants", column, server_default=None)
    op.create_foreign_key(
        op.f("fk_applicants_removed_by_user_id_users"),
        "applicants",
        "users",
        ["removed_by_user_id"],
        ["id"],
    )

    op.execute(
        "UPDATE applicants a SET is_primary = true FROM ("
        " SELECT DISTINCT ON (case_id) id FROM applicants ORDER BY case_id, display_order"
        ") first WHERE a.id = first.id"
    )

    op.drop_constraint(op.f("ck_applicants_employment_status"), "applicants", type_="check")
    op.drop_column("applicants", "employment_status")

    op.create_check_constraint("display_order", "applicants", "display_order BETWEEN 1 AND 8")
    op.create_check_constraint(
        "party_role", "applicants", "party_role IN ('borrower', 'guarantor')"
    )
    op.create_check_constraint("status", "applicants", "status IN ('active', 'removed')")
    op.create_check_constraint(
        "removal_reason",
        "applicants",
        "removal_reason IS NULL OR removal_reason IN "
        "('added_in_error', 'no_longer_applying', 'replaced', 'other')",
    )
    op.create_check_constraint("removal_consistency", "applicants", REMOVAL_CONSISTENCY)
    op.create_check_constraint(
        "primary_is_borrower", "applicants", "NOT is_primary OR party_role = 'borrower'"
    )
    op.create_index(
        "uq_applicants_active_display_order",
        "applicants",
        ["case_id", "display_order"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_applicants_active_primary",
        "applicants",
        ["case_id"],
        unique=True,
        postgresql_where=sa.text("is_primary AND status = 'active'"),
    )

    op.execute(f"REVOKE DELETE ON applicants FROM {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"GRANT DELETE ON applicants TO {APP_ROLE}")
    op.drop_index("uq_applicants_active_primary", table_name="applicants")
    op.drop_index("uq_applicants_active_display_order", table_name="applicants")
    for name in (
        "primary_is_borrower",
        "removal_consistency",
        "removal_reason",
        "status",
        "party_role",
        "display_order",
    ):
        op.drop_constraint(op.f(f"ck_applicants_{name}"), "applicants", type_="check")
    # Removed applicants cannot be represented in the old schema.
    op.execute("DELETE FROM applicants WHERE status = 'removed'")
    op.add_column("applicants", sa.Column("employment_status", sa.String(32)))
    op.create_check_constraint(
        "employment_status",
        "applicants",
        "employment_status IS NULL OR employment_status IN ("
        + ", ".join(repr(v) for v in EMPLOYMENT_STATUSES)
        + ")",
    )
    op.drop_constraint(
        op.f("fk_applicants_removed_by_user_id_users"), "applicants", type_="foreignkey"
    )
    for column in (
        "removal_note",
        "removal_reason",
        "removed_by_user_id",
        "removed_at",
        "status",
        "is_primary",
        "on_title",
        "party_role",
    ):
        op.drop_column("applicants", column)
    op.alter_column("applicants", "display_order", new_column_name="position")
    op.create_check_constraint("position", "applicants", "position BETWEEN 1 AND 8")
    op.create_unique_constraint(
        op.f("uq_applicants_case_id_position"), "applicants", ["case_id", "position"]
    )
