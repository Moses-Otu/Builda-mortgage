"""Guided journey: product details on the case; assessments bound to a profile approval.

* ``cases.product_rate_percent`` (optional): the mortgage product's rate, when known. Without
  it the assessment uses the policy's assumed rate and says so.
* ``cases.policy_code`` (optional): the lending policy to assess against.
* ``assessments.approval_id``: the approved financial profile an assessment assessed. An
  assessment can only run on an approved profile (enforced by the service), and a new approval
  makes earlier assessments stale. Composite FK: same tenant and case.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"


def upgrade() -> None:
    op.add_column("cases", sa.Column("product_rate_percent", sa.Numeric(6, 3), nullable=True))
    op.add_column("cases", sa.Column("policy_code", sa.String(48), nullable=True))
    op.create_check_constraint(
        "product_rate_percent",
        "cases",
        "product_rate_percent IS NULL OR product_rate_percent BETWEEN 0 AND 20",
    )
    op.add_column("assessments", sa.Column("approval_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_assessments_approval",
        "assessments",
        "profile_approvals",
        ["tenant_id", "case_id", "approval_id"],
        ["tenant_id", "case_id", "id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_assessments_approval", "assessments", type_="foreignkey")
    op.drop_column("assessments", "approval_id")
    op.drop_constraint(op.f("ck_cases_product_rate_percent"), "cases", type_="check")
    op.drop_column("cases", "policy_code")
    op.drop_column("cases", "product_rate_percent")
