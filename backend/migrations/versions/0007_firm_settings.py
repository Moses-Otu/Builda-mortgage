"""Phase 1.1-F: per-firm settings.

``firm_settings`` replaces the global ``MEIP_MAX_APPLICANTS_PER_CASE`` environment setting
with a per-firm value (1..8), and stores the four-eyes verification switch (enforced from
Phase 9). One row per firm, created at onboarding; existing firms are backfilled with the
previous default. The runtime role may read and update its own firm's row, never create one.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"


def upgrade() -> None:
    op.create_table(
        "firm_settings",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("max_applicants", sa.SmallInteger(), nullable=False),
        sa.Column("require_four_eyes_verification", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_firm_settings")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_firm_settings_tenant_id_tenants")
        ),
        sa.CheckConstraint("max_applicants BETWEEN 1 AND 8", name="max_applicants"),
    )
    op.execute(
        "INSERT INTO firm_settings (tenant_id, max_applicants, require_four_eyes_verification,"
        " updated_at, version) SELECT id, 4, false, now(), 1 FROM tenants"
    )
    op.execute("ALTER TABLE firm_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON firm_settings "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant())"
    )
    op.execute(f"GRANT SELECT, UPDATE ON firm_settings TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("firm_settings")
