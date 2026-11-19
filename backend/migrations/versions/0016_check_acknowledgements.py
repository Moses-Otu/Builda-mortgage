"""Stage 2-A: broker acknowledgements of reconciliation discrepancies.

* ``check_acknowledgements``: one row per acknowledged finding (``UNIQUE (tenant, case,
  finding_key)``). The key is the finding's fingerprint (64 lowercase hex characters). The
  findings themselves are computed on request and never stored.
* Insert-only for the runtime role (SELECT and INSERT only). No trigger: the note is free text
  a broker wrote, so the owner role must be able to erase rows under the retention policy.
* An acknowledgement changes nothing else: it is not a resolution or an approval.

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
TABLE = "check_acknowledgements"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("finding_key", sa.String(64), nullable=False),
        sa.Column("rule_code", sa.String(32), nullable=False),
        sa.Column("ruleset_version", sa.String(32), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("acknowledged_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_check_acknowledgements")),
        sa.UniqueConstraint(
            "tenant_id",
            "case_id",
            "finding_key",
            name=op.f("uq_check_acknowledgements_tenant_id_case_id_finding_key"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_check_acknowledgements_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            ["cases.tenant_id", "cases.id"],
            name=op.f("fk_check_acknowledgements_case"),
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"],
            ["users.id"],
            name=op.f("fk_check_acknowledgements_acknowledged_by_user_id_users"),
        ),
        sa.CheckConstraint("finding_key ~ '^[0-9a-f]{64}$'", name="finding_key"),
        sa.CheckConstraint("length(trim(note)) BETWEEN 1 AND 1000", name="note_length"),
    )
    op.create_index(op.f("ix_check_acknowledgements_tenant_id"), TABLE, ["tenant_id"])
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {TABLE} "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant())"
    )
    op.execute(f"GRANT SELECT, INSERT ON {TABLE} TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM {TABLE}) THEN
            RAISE EXCEPTION 'Acknowledgements exist; downgrading 0016 would discard them';
          END IF;
        END $$
        """
    )
    op.drop_table(TABLE)
