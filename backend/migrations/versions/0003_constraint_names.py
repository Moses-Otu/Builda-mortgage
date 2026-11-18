"""Phase 1.1-B: fix doubled CHECK constraint names from 0001.

Migration 0001 passed full names (``ck_<table>_<name>``) to CHECK constraints, and the
metadata naming convention prefixed them again (``ck_<table>_ck_<table>_<name>``). Renaming is
metadata-only: no data or validation changes. 0002 already recreated the two audit_events
checks with correct names.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOUBLED: dict[str, tuple[str, ...]] = {
    "tenants": ("status", "fca_frn_format"),
    "memberships": ("role", "status", "activation_requires_acceptance"),
    "cases": ("purpose", "status", "term", "property_value", "loan_amount"),
    "applicants": (
        "position",
        "employment_status",
        "declared_income",
        "declared_commitments",
    ),
}


def _rename(reverse: bool) -> None:
    for table, names in DOUBLED.items():
        for name in names:
            doubled, correct = f"ck_{table}_ck_{table}_{name}", f"ck_{table}_{name}"
            old, new = (correct, doubled) if reverse else (doubled, correct)
            op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old} TO {new}")


def upgrade() -> None:
    _rename(reverse=False)


def downgrade() -> None:
    _rename(reverse=True)
