"""Phase 1.1-A: actors beyond users, and erasable audit details.

* ``audit_events``: ``on_behalf_of_user_id`` (system/integration work caused by a user),
  ``correlation_id`` (job ID), the ``integration`` actor type, a stricter actor-consistency
  check, and ``UNIQUE (tenant_id, id)`` as the target for tenant-scoped references.
* ``audit_event_details``: erasable values (amounts, reasons, notes) split out of the
  immutable event row, so retention erasure never has to modify ``audit_events``.
* ``cases.created_by_user_id`` becomes nullable: cases may be created by integrations, and
  the audit log is the authoritative record of who did what (D11).

Constraint names: CHECK constraints are given *short* names so the metadata naming
convention produces ``ck_<table>_<name>``; other constraints use ``op.f`` final names.
(Migration 0001 passed full CHECK names and so produced doubled prefixes; 0003 fixes them.)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"

ACTOR_CONSISTENCY = (
    "(actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL"
    " AND on_behalf_of_user_id IS NULL)"
    " OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_ref IS NOT NULL"
    " AND on_behalf_of_user_id IS NULL)"
    " OR (actor_type = 'system' AND actor_user_id IS NULL)"
    " OR (actor_type = 'integration' AND actor_user_id IS NULL AND actor_ref IS NOT NULL)"
)
OLD_ACTOR_CONSISTENCY = (
    "(actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL)"
    " OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_ref IS NOT NULL)"
    " OR (actor_type = 'system' AND actor_user_id IS NULL)"
)


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("on_behalf_of_user_id", sa.Uuid(), nullable=True))
    op.add_column("audit_events", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        op.f("fk_audit_events_on_behalf_of_user_id_users"),
        "audit_events",
        "users",
        ["on_behalf_of_user_id"],
        ["id"],
    )
    op.create_unique_constraint(
        op.f("uq_audit_events_tenant_id_id"), "audit_events", ["tenant_id", "id"]
    )
    op.drop_constraint(
        op.f("ck_audit_events_ck_audit_events_actor_type"), "audit_events", type_="check"
    )
    op.drop_constraint(
        op.f("ck_audit_events_ck_audit_events_actor_consistency"), "audit_events", type_="check"
    )
    op.create_check_constraint(
        "actor_type",
        "audit_events",
        "actor_type IN ('user', 'operator', 'system', 'integration')",
    )
    op.create_check_constraint("actor_consistency", "audit_events", ACTOR_CONSISTENCY)

    op.create_table(
        "audit_event_details",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_audit_event_details")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_audit_event_details_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            ["audit_events.tenant_id", "audit_events.id"],
            name=op.f("fk_audit_event_details_event"),
        ),
    )
    op.create_index(op.f("ix_audit_event_details_tenant_id"), "audit_event_details", ["tenant_id"])
    op.execute("ALTER TABLE audit_event_details ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON audit_event_details "
        "USING (tenant_id = app_current_tenant()) "
        "WITH CHECK (tenant_id = app_current_tenant())"
    )
    # Append-only for the runtime role. Deletion is reserved for the owner-run retention
    # process (D10), which is not part of Phase 1.1.
    op.execute(f"GRANT SELECT, INSERT ON audit_event_details TO {APP_ROLE}")

    op.alter_column("cases", "created_by_user_id", nullable=True)


def downgrade() -> None:
    op.alter_column("cases", "created_by_user_id", nullable=False)
    op.drop_table("audit_event_details")
    op.drop_constraint(op.f("ck_audit_events_actor_consistency"), "audit_events", type_="check")
    op.drop_constraint(op.f("ck_audit_events_actor_type"), "audit_events", type_="check")
    op.create_check_constraint(
        op.f("ck_audit_events_ck_audit_events_actor_type"),
        "audit_events",
        "actor_type IN ('user', 'operator', 'system')",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_ck_audit_events_actor_consistency"),
        "audit_events",
        OLD_ACTOR_CONSISTENCY,
    )
    op.drop_constraint(op.f("uq_audit_events_tenant_id_id"), "audit_events", type_="unique")
    op.drop_constraint(
        op.f("fk_audit_events_on_behalf_of_user_id_users"), "audit_events", type_="foreignkey"
    )
    op.drop_column("audit_events", "correlation_id")
    op.drop_column("audit_events", "on_behalf_of_user_id")
