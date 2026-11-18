"""Phase 1.2 items 1-2: immutable measure meaning, deprecation, and component qualifiers.

* ``measure_definitions`` gains ``deprecated_at`` and ``qualifier_required``. Its observation
  FK target becomes the full signature (code, subject kind, value type, temporal kind,
  qualifier_required) under the explicit name ``uq_measure_definitions_signature``.
* ``overtime``'s description is clarified to include premium and enhanced rates, before the
  append-only trigger exists (pre-release: no client relies on the old wording). Overtime
  premium is therefore *not* an allowance qualifier.
* ``measure_qualifiers`` (reference data) lists the labels ``allowance`` and ``bonus`` may
  carry, including ``unspecified`` (the source does not say) and ``other`` (known, not listed).
* ``financial_observations`` gains ``qualifier`` and ``qualifier_required``; the database
  requires a listed qualifier exactly when the measure requires one. Existing allowance and
  bonus values (development data) are backfilled with ``unspecified``.
* Trigger ``reference_data_append_only`` on both reference tables rejects every DELETE and
  every UPDATE except setting ``deprecated_at`` once. INSERT stays allowed for future
  migrations.
* Trigger ``financial_observations_not_deprecated`` (BEFORE INSERT) rejects new values for a
  deprecated measure or qualifier, so deprecation is enforced by the database, not only by
  the service. Existing values are untouched and stay valid.
* The downgrade refuses to run (rather than silently discarding information) while any value
  carries a qualifier other than ``unspecified``, anything is deprecated, or a qualifier
  exists beyond the seed.

Self-contained: does not import application code.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
OLD_SIGNATURE = "uq_measure_definitions_code_subject_kind_value_type_tem_81c8"
QUALIFIED = ("allowance", "bonus")
UNSPECIFIED = "Not stated by the source"
OTHER = "A known type not listed here"

# (measure, subject_kind, qualifier, description) - frozen copy of the seed.
QUALIFIERS: tuple[tuple[str, str, str, str], ...] = (
    ("allowance", "income_source", "car", "Car or car cash allowance"),
    ("allowance", "income_source", "shift", "Shift or unsocial hours allowance"),
    ("allowance", "income_source", "location", "Location or area weighting"),
    ("allowance", "income_source", "travel", "Travel allowance"),
    ("allowance", "income_source", "other", OTHER),
    ("allowance", "income_source", "unspecified", UNSPECIFIED),
    ("bonus", "income_source", "guaranteed", "Described as guaranteed or contractual"),
    ("bonus", "income_source", "discretionary", "Described as discretionary"),
    ("bonus", "income_source", "performance", "Described as performance-related"),
    ("bonus", "income_source", "other", OTHER),
    ("bonus", "income_source", "unspecified", UNSPECIFIED),
)


def upgrade() -> None:
    # --- measure definitions ------------------------------------------------------------------
    op.add_column(
        "measure_definitions",
        sa.Column("qualifier_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("measure_definitions", sa.Column("deprecated_at", sa.DateTime(timezone=True)))
    op.execute(
        "UPDATE measure_definitions SET qualifier_required = true "
        f"WHERE code IN {QUALIFIED!r} AND subject_kind = 'income_source'"
    )
    op.execute(
        "UPDATE measure_definitions "
        "SET description = 'Overtime pay, including premium and enhanced rates' "
        "WHERE code = 'overtime' AND subject_kind = 'income_source'"
    )

    # The observations' measure FK moves to the full signature (including qualifier_required).
    op.drop_constraint(
        op.f("fk_financial_observations_measure"), "financial_observations", type_="foreignkey"
    )
    op.drop_constraint(op.f(OLD_SIGNATURE), "measure_definitions", type_="unique")
    op.create_unique_constraint(
        op.f("uq_measure_definitions_signature"),
        "measure_definitions",
        ["code", "subject_kind", "value_type", "temporal_kind", "qualifier_required"],
    )

    # --- qualifiers ---------------------------------------------------------------------------
    op.create_table(
        "measure_qualifiers",
        sa.Column("measure_code", sa.String(48), nullable=False),
        sa.Column("subject_kind", sa.String(16), nullable=False),
        sa.Column("qualifier", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("deprecated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint(
            "measure_code", "subject_kind", "qualifier", name=op.f("pk_measure_qualifiers")
        ),
        sa.ForeignKeyConstraint(
            ["measure_code", "subject_kind"],
            ["measure_definitions.code", "measure_definitions.subject_kind"],
            name=op.f("fk_measure_qualifiers_measure"),
        ),
    )
    op.bulk_insert(
        sa.table(
            "measure_qualifiers",
            sa.column("measure_code", sa.String),
            sa.column("subject_kind", sa.String),
            sa.column("qualifier", sa.String),
            sa.column("description", sa.Text),
        ),
        [
            {"measure_code": m, "subject_kind": k, "qualifier": q, "description": d}
            for m, k, q, d in QUALIFIERS
        ],
    )

    # --- observations -------------------------------------------------------------------------
    op.add_column("financial_observations", sa.Column("qualifier", sa.String(32)))
    op.add_column(
        "financial_observations",
        sa.Column("qualifier_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        "UPDATE financial_observations o SET qualifier_required = d.qualifier_required "
        "FROM measure_definitions d "
        "WHERE d.code = o.measure AND d.subject_kind = o.subject_kind"
    )
    op.execute(
        "UPDATE financial_observations SET qualifier = 'unspecified' WHERE qualifier_required"
    )
    op.alter_column("financial_observations", "qualifier_required", server_default=None)
    op.create_check_constraint(
        "qualifier", "financial_observations", "qualifier_required = (qualifier IS NOT NULL)"
    )
    op.create_foreign_key(
        op.f("fk_financial_observations_measure"),
        "financial_observations",
        "measure_definitions",
        ["measure", "subject_kind", "value_type", "temporal_kind", "qualifier_required"],
        ["code", "subject_kind", "value_type", "temporal_kind", "qualifier_required"],
    )
    op.create_foreign_key(
        op.f("fk_financial_observations_qualifier"),
        "financial_observations",
        "measure_qualifiers",
        ["measure", "subject_kind", "qualifier"],
        ["measure_code", "subject_kind", "qualifier"],
    )

    # --- immutability of meaning --------------------------------------------------------------
    op.execute(
        """
        CREATE FUNCTION reference_data_append_only() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             IF TG_OP = 'DELETE' THEN
               RAISE EXCEPTION '% rows cannot be deleted; deprecate instead', TG_TABLE_NAME
                 USING ERRCODE = 'integrity_constraint_violation';
             END IF;
             -- Only change allowed: deprecated_at from NULL to a value, once.
             IF OLD.deprecated_at IS NOT NULL OR NEW.deprecated_at IS NULL
                OR (to_jsonb(NEW) - 'deprecated_at') <> (to_jsonb(OLD) - 'deprecated_at') THEN
               RAISE EXCEPTION '% meaning is immutable; only deprecation (once) is allowed',
                 TG_TABLE_NAME USING ERRCODE = 'integrity_constraint_violation';
             END IF;
             RETURN NEW;
           END $$
        """
    )
    for table in ("measure_definitions", "measure_qualifiers"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reference_data_append_only()"
        )
    op.execute(f"GRANT SELECT ON measure_qualifiers TO {APP_ROLE}")

    # --- deprecation stops new values ---------------------------------------------------------
    # subject_kind is a generated column, not yet computed in a BEFORE trigger: derive it.
    op.execute(
        """
        CREATE FUNCTION financial_observations_not_deprecated() RETURNS trigger
        LANGUAGE plpgsql AS
        $$
        DECLARE kind text := CASE WHEN NEW.income_source_id IS NOT NULL THEN 'income_source'
                                  WHEN NEW.commitment_id IS NOT NULL THEN 'commitment'
                                  ELSE 'expenditure' END;
        BEGIN
          IF EXISTS (SELECT 1 FROM measure_definitions
                     WHERE code = NEW.measure AND subject_kind = kind
                       AND deprecated_at IS NOT NULL) THEN
            RAISE EXCEPTION 'measure % is deprecated; new values are not accepted', NEW.measure
              USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          IF NEW.qualifier IS NOT NULL AND EXISTS (
               SELECT 1 FROM measure_qualifiers
               WHERE measure_code = NEW.measure AND subject_kind = kind
                 AND qualifier = NEW.qualifier AND deprecated_at IS NOT NULL) THEN
            RAISE EXCEPTION 'qualifier % of % is deprecated; new values are not accepted',
              NEW.qualifier, NEW.measure USING ERRCODE = 'integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER financial_observations_not_deprecated BEFORE INSERT "
        "ON financial_observations FOR EACH ROW "
        "EXECUTE FUNCTION financial_observations_not_deprecated()"
    )


def _refuse_lossy_downgrade() -> None:
    seed = ", ".join(f"('{m}', '{k}', '{q}')" for m, k, q, _ in QUALIFIERS)
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM financial_observations
                     WHERE qualifier IS NOT NULL AND qualifier <> 'unspecified')
             OR EXISTS (SELECT 1 FROM measure_definitions WHERE deprecated_at IS NOT NULL)
             OR EXISTS (SELECT 1 FROM measure_qualifiers WHERE deprecated_at IS NOT NULL)
             OR EXISTS (SELECT 1 FROM measure_qualifiers
                        WHERE (measure_code, subject_kind, qualifier) NOT IN ({seed})) THEN
            RAISE EXCEPTION 'Downgrading 0008 would discard qualifiers or deprecations that '
              'exist in the data; resolve them first (this downgrade never drops them silently)';
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    _refuse_lossy_downgrade()
    op.execute(
        "DROP TRIGGER IF EXISTS financial_observations_not_deprecated ON financial_observations"
    )
    op.execute("DROP FUNCTION IF EXISTS financial_observations_not_deprecated()")
    for table in ("measure_definitions", "measure_qualifiers"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reference_data_append_only()")
    op.drop_constraint(
        op.f("fk_financial_observations_qualifier"), "financial_observations", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_financial_observations_measure"), "financial_observations", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("ck_financial_observations_qualifier"), "financial_observations", type_="check"
    )
    op.drop_column("financial_observations", "qualifier_required")
    op.drop_column("financial_observations", "qualifier")
    op.drop_table("measure_qualifiers")
    op.drop_constraint(
        op.f("uq_measure_definitions_signature"), "measure_definitions", type_="unique"
    )
    op.create_unique_constraint(
        op.f(OLD_SIGNATURE),
        "measure_definitions",
        ["code", "subject_kind", "value_type", "temporal_kind"],
    )
    op.create_foreign_key(
        op.f("fk_financial_observations_measure"),
        "financial_observations",
        "measure_definitions",
        ["measure", "subject_kind", "value_type", "temporal_kind"],
        ["code", "subject_kind", "value_type", "temporal_kind"],
    )
    op.execute(
        "UPDATE measure_definitions SET description = 'Overtime pay' "
        "WHERE code = 'overtime' AND subject_kind = 'income_source'"
    )
    op.drop_column("measure_definitions", "deprecated_at")
    op.drop_column("measure_definitions", "qualifier_required")
