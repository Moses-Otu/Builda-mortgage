"""Phase 1.2 items 4-5: supersession integrity and the current-value rule.

Supersession (B supersedes A):
* ``financial_observations_supersession_check`` (BEFORE INSERT, when
  ``supersedes_observation_id`` is set): A must have the same item, measure, qualifier,
  source kind and period key as B, and already be retired as ``superseded``. With the
  existing unique index on ``supersedes_observation_id``, each value has at most one successor.
* ``financial_observations_superseded_has_successor`` (deferred constraint trigger): at
  commit, every row retired as ``superseded`` must have its successor. A value can therefore
  never be "superseded" without its replacement being recorded in the same transaction.

Current value:
* ``uq_financial_observations_current_value``: at most one *active* declared or broker value
  per (item, measure, qualifier, source kind, frequency, period start, period end, tax year,
  as-of date), with NULLs treated as equal. Document-derived values (Phase 2) are excluded:
  they follow processing-run semantics instead.
* The migration stops, listing the offending keys, if duplicates already exist; it does not
  guess which value to keep.

Trigger functions run with the caller's rights, so row-level security applies to their
lookups. Requires PostgreSQL 15+ (NULLS NOT DISTINCT).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KEY = (
    "income_source_id, commitment_id, expenditure_item_id, measure, qualifier, source_kind,"
    " frequency, period_start, period_end, tax_year, as_of_date"
)
CURRENT = "retired_at IS NULL AND source_kind IN ('declared', 'broker_entry')"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        DECLARE duplicates text;
        BEGIN
          SELECT string_agg(k, '; ') INTO duplicates FROM (
            SELECT concat_ws('/', {KEY}) AS k
            FROM financial_observations WHERE {CURRENT}
            GROUP BY {KEY} HAVING count(*) > 1 LIMIT 20
          ) d;
          IF duplicates IS NOT NULL THEN
            RAISE EXCEPTION 'Duplicate current financial values must be resolved before '
              'migration 0009 (retire or supersede all but one): %', duplicates;
          END IF;
        END $$
        """
    )
    op.create_index(
        "uq_financial_observations_current_value",
        "financial_observations",
        [c.strip() for c in KEY.split(",")],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text(CURRENT),
    )

    op.execute(
        """
        CREATE FUNCTION financial_observations_supersession_check() RETURNS trigger
        LANGUAGE plpgsql AS
        $$
        DECLARE p financial_observations%ROWTYPE;
        BEGIN
          SELECT * INTO p FROM financial_observations WHERE id = NEW.supersedes_observation_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'superseded observation % not found', NEW.supersedes_observation_id
              USING ERRCODE = 'foreign_key_violation';
          END IF;
          IF p.retired_reason IS DISTINCT FROM 'superseded' THEN
            RAISE EXCEPTION 'observation % must be retired as superseded before its successor '
              'is recorded', p.id USING ERRCODE = 'check_violation';
          END IF;
          IF (p.income_source_id, p.commitment_id, p.expenditure_item_id, p.measure,
              p.qualifier, p.source_kind, p.frequency, p.period_start, p.period_end,
              p.tax_year, p.as_of_date)
             IS DISTINCT FROM
             (NEW.income_source_id, NEW.commitment_id, NEW.expenditure_item_id, NEW.measure,
              NEW.qualifier, NEW.source_kind, NEW.frequency, NEW.period_start, NEW.period_end,
              NEW.tax_year, NEW.as_of_date) THEN
            RAISE EXCEPTION 'a correction must match the item, measure, qualifier, source and '
              'period of the value it supersedes' USING ERRCODE = 'check_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER financial_observations_supersession_check BEFORE INSERT "
        "ON financial_observations FOR EACH ROW "
        "WHEN (NEW.supersedes_observation_id IS NOT NULL) "
        "EXECUTE FUNCTION financial_observations_supersession_check()"
    )
    op.execute(
        """
        CREATE FUNCTION financial_observations_successor_check() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF NOT EXISTS (SELECT 1 FROM financial_observations
                            WHERE supersedes_observation_id = NEW.id) THEN
               RAISE EXCEPTION 'observation % was retired as superseded without a successor',
                 NEW.id USING ERRCODE = 'check_violation';
             END IF;
             RETURN NULL;
           END $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER financial_observations_superseded_has_successor "
        "AFTER INSERT OR UPDATE OF retired_reason ON financial_observations "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "WHEN (NEW.retired_reason = 'superseded') "
        "EXECUTE FUNCTION financial_observations_successor_check()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS financial_observations_superseded_has_successor "
        "ON financial_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS financial_observations_successor_check()")
    op.execute(
        "DROP TRIGGER IF EXISTS financial_observations_supersession_check ON financial_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS financial_observations_supersession_check()")
    op.drop_index("uq_financial_observations_current_value", table_name="financial_observations")
