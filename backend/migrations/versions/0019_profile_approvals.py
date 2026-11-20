"""Stage 3-B: broker approval of the financial profile.

* ``profile_approvals``: one row per approval, versioned per case. The snapshot is stored as
  the exact canonical text that was hashed, and the attestation as the exact wording the
  broker accepted. Insert-only for the runtime role (SELECT, INSERT), RLS.
* ``cases.approval_id``: the current approval, pointing only to an approval of the same tenant
  and case. ``verified`` becomes storable, and two CHECKs keep status and pointer consistent:
  a verified case always has an approval; only a verified case, or one closed from verified,
  may keep one. Reopening must therefore clear the pointer; the approval row remains.

The downgrade refuses while any approval exists.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)
OLD_STATUSES = ("draft", "collecting_evidence", "in_review", "closed")
NEW_STATUSES = ("draft", "collecting_evidence", "in_review", "verified", "closed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "profile_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("profile_rules", sa.String(32), nullable=False),
        sa.Column("snapshot", sa.Text(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("attestation_version", sa.String(32), nullable=False),
        sa.Column("attestation_text", sa.Text(), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_approvals")),
        sa.UniqueConstraint(
            "tenant_id", "case_id", "id", name=op.f("uq_profile_approvals_tenant_id_case_id_id")
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "version",
            name=op.f("uq_profile_approvals_tenant_id_case_id_version"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_profile_approvals_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name=op.f("fk_profile_approvals_case"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"],
            ["users.id"],
            name=op.f("fk_profile_approvals_approved_by_user_id_users"),
        ),
        sa.CheckConstraint("version >= 1", name="version"),
        sa.CheckConstraint("snapshot_sha256 ~ '^[0-9a-f]{64}$'", name="snapshot_sha256"),
        sa.CheckConstraint("length(attestation_text) > 0", name="attestation_text"),
        sa.CheckConstraint(
            "note IS NULL OR length(trim(note)) BETWEEN 1 AND 1000", name="note_length"
        ),
    )
    op.create_index(op.f("ix_profile_approvals_tenant_id"), "profile_approvals", ["tenant_id"])
    op.execute("ALTER TABLE profile_approvals ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON profile_approvals "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant())"
    )
    # Insert-only for the runtime role: no UPDATE, DELETE or TRUNCATE.
    op.execute(f"GRANT SELECT, INSERT ON profile_approvals TO {APP_ROLE}")

    op.add_column("cases", sa.Column("approval_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f("fk_cases_approval"),
        "cases",
        "profile_approvals",
        ["tenant_id", "id", "approval_id"],
        ["tenant_id", "case_id", "id"],
    )
    op.drop_constraint(op.f("ck_cases_status"), "cases", type_="check")
    op.create_check_constraint("status", "cases", _in("status", NEW_STATUSES))
    # A verified case always has a current approval.
    op.create_check_constraint(
        "verified_has_approval", "cases", "status <> 'verified' OR approval_id IS NOT NULL"
    )
    # Only a verified case, or one closed from verified (its final approval), keeps one.
    op.create_check_constraint(
        "approval_status",
        "cases",
        "approval_id IS NULL OR status IN ('verified', 'closed')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM profile_approvals) THEN
            RAISE EXCEPTION 'Profile approvals exist; downgrading 0019 would discard them';
          END IF;
        END $$
        """
    )
    op.drop_constraint(op.f("ck_cases_approval_status"), "cases", type_="check")
    op.drop_constraint(op.f("ck_cases_verified_has_approval"), "cases", type_="check")
    op.drop_constraint(op.f("ck_cases_status"), "cases", type_="check")
    op.create_check_constraint("status", "cases", _in("status", OLD_STATUSES))
    op.drop_constraint(op.f("fk_cases_approval"), "cases", type_="foreignkey")
    op.drop_column("cases", "approval_id")
    op.drop_table("profile_approvals")
