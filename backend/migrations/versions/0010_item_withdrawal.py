"""Phase 1.2 item 6: irreversible withdrawal of financial items entered in error.

* ``income_sources``, ``commitments`` and ``expenditure_items`` gain ``withdrawn_at``,
  ``withdrawn_by_user_id`` and ``withdrawal_reason`` (``entered_in_error`` | ``duplicate``),
  with a CHECK that the time and reason are set together.
* ``financial_items_frozen_after_withdrawal`` (BEFORE UPDATE on all three tables) rejects any
  change to a withdrawn row: withdrawal is irreversible and the item accepts no edits.
* ``financial_observations_item_not_withdrawn`` (BEFORE INSERT) rejects new values for a
  withdrawn item. It locks the item row ``FOR SHARE``, which conflicts with the withdrawal's
  row lock, so an insert racing a withdrawal waits for it and then sees the result.
* ``*_withdrawn_has_no_active_values`` (deferred constraint triggers on the three item
  tables): at commit, a withdrawn item has no active values. Withdrawal therefore cannot
  commit without retiring them, whichever path writes it.
* ``*_parties_frozen_after_withdrawal`` (BEFORE INSERT OR DELETE on ``commitment_parties``
  and ``expenditure_parties``): a withdrawn item's parties cannot change.
* ``retired_reason`` gains ``item_withdrawn``: the service retires an item's active values
  in the same transaction as the withdrawal.
* The downgrade refuses to run while any item is withdrawn: dropping the columns would
  silently reinstate it.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ITEM_TABLES = ("income_sources", "commitments", "expenditure_items")
PARTY_TABLES = (
    ("commitment_parties", "commitments", "commitment_id"),
    ("expenditure_parties", "expenditure_items", "expenditure_item_id"),
)
RETIRED_REASONS = "('superseded', 'entered_in_error', 'item_withdrawn')"
OLD_RETIRED_REASONS = "('superseded', 'entered_in_error')"


def upgrade() -> None:
    for table in ITEM_TABLES:
        op.add_column(table, sa.Column("withdrawn_at", sa.DateTime(timezone=True)))
        op.add_column(table, sa.Column("withdrawn_by_user_id", sa.Uuid()))
        op.add_column(table, sa.Column("withdrawal_reason", sa.String(32)))
        op.create_foreign_key(
            op.f(f"fk_{table}_withdrawn_by_user_id_users"),
            table,
            "users",
            ["withdrawn_by_user_id"],
            ["id"],
        )
        op.create_check_constraint(
            "withdrawal_reason",
            table,
            "withdrawal_reason IS NULL OR withdrawal_reason IN ('entered_in_error', 'duplicate')",
        )
        op.create_check_constraint(
            "withdrawal_consistency",
            table,
            "(withdrawn_at IS NULL) = (withdrawal_reason IS NULL)",
        )

    op.execute(
        """
        CREATE FUNCTION financial_items_frozen_after_withdrawal() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF OLD.withdrawn_at IS NOT NULL THEN
               RAISE EXCEPTION '% % has been withdrawn and cannot change', TG_TABLE_NAME, OLD.id
                 USING ERRCODE = 'integrity_constraint_violation';
             END IF;
             RETURN NEW;
           END $$
        """
    )
    for table in ITEM_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_frozen_after_withdrawal BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION financial_items_frozen_after_withdrawal()"
        )

    op.execute(
        """
        CREATE FUNCTION financial_observations_item_not_withdrawn() RETURNS trigger
        LANGUAGE plpgsql AS
        $$
        DECLARE withdrawn timestamptz;
        BEGIN
          -- FOR SHARE waits for a concurrent withdrawal (row lock) and then reads its result.
          IF NEW.income_source_id IS NOT NULL THEN
            SELECT withdrawn_at INTO withdrawn FROM income_sources
            WHERE id = NEW.income_source_id FOR SHARE;
          ELSIF NEW.commitment_id IS NOT NULL THEN
            SELECT withdrawn_at INTO withdrawn FROM commitments
            WHERE id = NEW.commitment_id FOR SHARE;
          ELSE
            SELECT withdrawn_at INTO withdrawn FROM expenditure_items
            WHERE id = NEW.expenditure_item_id FOR SHARE;
          END IF;
          IF withdrawn IS NOT NULL THEN
            RAISE EXCEPTION 'values cannot be recorded for a withdrawn item'
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER financial_observations_item_not_withdrawn BEFORE INSERT "
        "ON financial_observations FOR EACH ROW "
        "EXECUTE FUNCTION financial_observations_item_not_withdrawn()"
    )

    # At commit, a withdrawn item has no active values.
    op.execute(
        """
        CREATE FUNCTION financial_items_withdrawn_has_no_active_values() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF TG_TABLE_NAME = 'income_sources' THEN
               PERFORM 1 FROM financial_observations
               WHERE income_source_id = NEW.id AND retired_at IS NULL;
             ELSIF TG_TABLE_NAME = 'commitments' THEN
               PERFORM 1 FROM financial_observations
               WHERE commitment_id = NEW.id AND retired_at IS NULL;
             ELSE
               PERFORM 1 FROM financial_observations
               WHERE expenditure_item_id = NEW.id AND retired_at IS NULL;
             END IF;
             IF FOUND THEN
               RAISE EXCEPTION '% % was withdrawn with active values; retire them in the same '
                 'transaction', TG_TABLE_NAME, NEW.id
                 USING ERRCODE = 'integrity_constraint_violation';
             END IF;
             RETURN NULL;
           END $$
        """
    )
    for table in ITEM_TABLES:
        op.execute(
            f"CREATE CONSTRAINT TRIGGER {table}_withdrawn_has_no_active_values "
            f"AFTER UPDATE OF withdrawn_at ON {table} DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW WHEN (NEW.withdrawn_at IS NOT NULL) "
            "EXECUTE FUNCTION financial_items_withdrawn_has_no_active_values()"
        )

    # A withdrawn item's parties are frozen with it.
    for parties, parent, column in PARTY_TABLES:
        op.execute(
            f"""
            CREATE FUNCTION {parties}_frozen_after_withdrawal() RETURNS trigger
            LANGUAGE plpgsql AS
            $$
            DECLARE withdrawn timestamptz;
            BEGIN
              IF TG_OP = 'DELETE' THEN
                SELECT withdrawn_at INTO withdrawn FROM {parent}
                WHERE id = OLD.{column} FOR SHARE;
              ELSE
                SELECT withdrawn_at INTO withdrawn FROM {parent}
                WHERE id = NEW.{column} FOR SHARE;
              END IF;
              IF withdrawn IS NOT NULL THEN
                RAISE EXCEPTION 'the parties of a withdrawn item cannot change'
                  USING ERRCODE = 'integrity_constraint_violation';
              END IF;
              IF TG_OP = 'DELETE' THEN
                RETURN OLD;
              END IF;
              RETURN NEW;
            END
            $$
            """
        )
        op.execute(
            f"CREATE TRIGGER {parties}_frozen_after_withdrawal BEFORE INSERT OR DELETE "
            f"ON {parties} FOR EACH ROW EXECUTE FUNCTION {parties}_frozen_after_withdrawal()"
        )

    op.drop_constraint(
        op.f("ck_financial_observations_retired_reason"), "financial_observations", type_="check"
    )
    op.create_check_constraint(
        "retired_reason",
        "financial_observations",
        f"retired_reason IS NULL OR retired_reason IN {RETIRED_REASONS}",
    )


def downgrade() -> None:
    # Refuse rather than silently reinstate withdrawn items by dropping their columns.
    withdrawn = " OR ".join(
        f"EXISTS (SELECT 1 FROM {t} WHERE withdrawn_at IS NOT NULL)" for t in ITEM_TABLES
    )
    op.execute(
        f"""
        DO $$
        BEGIN
          IF {withdrawn} THEN
            RAISE EXCEPTION 'Withdrawn items exist; downgrading 0010 would silently reinstate '
              'them';
          END IF;
        END $$
        """
    )
    for parties, _, _ in PARTY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {parties}_frozen_after_withdrawal ON {parties}")
        op.execute(f"DROP FUNCTION IF EXISTS {parties}_frozen_after_withdrawal()")
    for table in ITEM_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_withdrawn_has_no_active_values ON {table}")
    op.execute("DROP FUNCTION IF EXISTS financial_items_withdrawn_has_no_active_values()")
    op.drop_constraint(
        op.f("ck_financial_observations_retired_reason"), "financial_observations", type_="check"
    )
    op.create_check_constraint(
        "retired_reason",
        "financial_observations",
        f"retired_reason IS NULL OR retired_reason IN {OLD_RETIRED_REASONS}",
    )
    op.execute(
        "DROP TRIGGER IF EXISTS financial_observations_item_not_withdrawn ON financial_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS financial_observations_item_not_withdrawn()")
    for table in ITEM_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_frozen_after_withdrawal ON {table}")
    op.execute("DROP FUNCTION IF EXISTS financial_items_frozen_after_withdrawal()")
    for table in ITEM_TABLES:
        op.drop_constraint(op.f(f"ck_{table}_withdrawal_consistency"), table, type_="check")
        op.drop_constraint(op.f(f"ck_{table}_withdrawal_reason"), table, type_="check")
        op.drop_constraint(
            op.f(f"fk_{table}_withdrawn_by_user_id_users"), table, type_="foreignkey"
        )
        for column in ("withdrawal_reason", "withdrawn_by_user_id", "withdrawn_at"):
            op.drop_column(table, column)
