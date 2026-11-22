"""Phase 4: email and password accounts for the pilot (``MEIP_AUTH_MODE=password``).

* ``local_accounts``: one row per email, holding a scrypt password hash, whether it must be
  changed at next sign-in (temporary passwords), and failed-attempt lockout state. Global
  (not tenant data). **The runtime role has no privileges on the table**: it reaches it only
  through two ``SECURITY DEFINER`` functions, so no query can read hashes in bulk.
* ``app_local_account(email)``: the one account's hash and state, for sign-in.
* ``app_local_account_write(...)``: create an account, set a password, or record a sign-in
  attempt.

Firm membership, roles and tenant isolation are unchanged: an account's email is the
identity's subject under the platform's own issuer.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
_TS = sa.DateTime(timezone=True)
FUNCTIONS = (
    "app_local_account(text)",
    "app_local_account_write(text, text, boolean, integer, timestamptz, boolean)",
)


def upgrade() -> None:
    op.create_table(
        "local_accounts",
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", _TS, nullable=True),
        sa.Column("password_changed_at", _TS, nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("email", name=op.f("pk_local_accounts")),
        sa.CheckConstraint(
            "email = lower(email) AND length(email) BETWEEN 3 AND 320", name="email"
        ),
        sa.CheckConstraint("failed_attempts >= 0", name="failed_attempts"),
        sa.CheckConstraint("password_hash LIKE 'scrypt$%'", name="password_hash"),
    )
    op.execute(
        """
        CREATE FUNCTION app_local_account(p_email text)
        RETURNS TABLE (password_hash text, must_change_password boolean,
                       failed_attempts integer, locked_until timestamptz)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
          SELECT a.password_hash, a.must_change_password, a.failed_attempts, a.locked_until
          FROM local_accounts a WHERE a.email = lower(p_email)
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_local_account_write(
          p_email text, p_hash text, p_must_change boolean, p_failed integer,
          p_locked_until timestamptz, p_create boolean)
        RETURNS boolean
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
        BEGIN
          IF p_create THEN
            INSERT INTO local_accounts (email, password_hash, must_change_password,
                                        failed_attempts, locked_until, password_changed_at,
                                        created_at)
            VALUES (lower(p_email), p_hash, p_must_change, 0, NULL, now(), now())
            ON CONFLICT (email) DO NOTHING;
            RETURN FOUND;
          END IF;
          UPDATE local_accounts SET
            password_hash = coalesce(p_hash, password_hash),
            must_change_password = coalesce(p_must_change, must_change_password),
            failed_attempts = p_failed,
            locked_until = p_locked_until,
            password_changed_at = CASE WHEN p_hash IS NULL THEN password_changed_at
                                       ELSE now() END
          WHERE email = lower(p_email);
          RETURN FOUND;
        END
        $$
        """
    )
    for fn in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")


def downgrade() -> None:
    for fn in FUNCTIONS:
        op.execute(f"DROP FUNCTION {fn}")
    op.drop_table("local_accounts")
