"""Phase 1.2 item 7: audit details can only be written in their event's own transaction.

* ``audit_events.txid xid8``: the database transaction that wrote the event. Existing rows
  get ``'0'`` (added as a constant default, so no UPDATE runs against the append-only table
  and no existing row can ever match a live transaction); new rows default to
  ``pg_current_xact_id()``, which is the top-level transaction id even inside savepoints.
* ``audit_event_details_same_transaction`` (BEFORE INSERT, row level, so it also fires for
  ``INSERT ... SELECT``, ``ON CONFLICT`` and ``COPY``) rejects details whose event was written
  by another transaction.
* The runtime role's table-level INSERT on ``audit_events`` becomes column-level INSERT on
  every column except ``txid``: it can never supply a transaction id itself.

Bypass analysis (see docs/phases/PHASE-1.2-PLAN.md, item 7): the runtime role cannot UPDATE
or DELETE either table, cannot disable triggers (owns nothing), cannot set
``session_replication_role`` (superuser only), and no SECURITY DEFINER function writes
details. The owner role remains a trusted, operator-only path.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
INSERTABLE = (
    "id, tenant_id, occurred_at, actor_type, actor_user_id, actor_ref, on_behalf_of_user_id,"
    " action, entity_type, entity_id, case_id, request_id, correlation_id, changes"
)


def upgrade() -> None:
    # A constant default backfills existing rows without an UPDATE (the table is
    # append-only); new rows then get the writing transaction's id.
    op.execute("ALTER TABLE audit_events ADD COLUMN txid xid8 NOT NULL DEFAULT '0'::xid8")
    op.execute("ALTER TABLE audit_events ALTER COLUMN txid SET DEFAULT pg_current_xact_id()")

    op.execute(
        """
        CREATE FUNCTION audit_event_details_same_transaction() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF NOT EXISTS (SELECT 1 FROM audit_events
                            WHERE id = NEW.event_id AND txid = pg_current_xact_id()) THEN
               RAISE EXCEPTION 'audit details must be written in the same transaction as '
                 'their event' USING ERRCODE = 'insufficient_privilege';
             END IF;
             RETURN NEW;
           END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_event_details_same_transaction BEFORE INSERT "
        "ON audit_event_details FOR EACH ROW "
        "EXECUTE FUNCTION audit_event_details_same_transaction()"
    )

    op.execute(f"REVOKE INSERT ON audit_events FROM {APP_ROLE}")
    op.execute(f"GRANT INSERT ({INSERTABLE}) ON audit_events TO {APP_ROLE}")


def downgrade() -> None:
    # Drops txid. It carries no information once its event's transaction has committed
    # (it is only compared while details are inserted); re-upgrading sets '0' for all rows.
    op.execute(f"REVOKE INSERT ({INSERTABLE}) ON audit_events FROM {APP_ROLE}")
    op.execute(f"GRANT INSERT ON audit_events TO {APP_ROLE}")
    op.execute("DROP TRIGGER IF EXISTS audit_event_details_same_transaction ON audit_event_details")
    op.execute("DROP FUNCTION IF EXISTS audit_event_details_same_transaction()")
    op.execute("ALTER TABLE audit_events DROP COLUMN txid")
