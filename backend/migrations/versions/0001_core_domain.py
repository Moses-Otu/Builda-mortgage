"""Phase 1 core domain: tenants, users, memberships, cases, applicants, audit events.

Also installs: row-level security policies, runtime-role grants, the audit immutability
trigger, and SECURITY DEFINER functions for the two cross-tenant lookups the runtime role
legitimately needs (resolving a login to its memberships, and registering a user).

Revision ID: 0001
Revises:
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "meip_app"
TENANT_TABLES = ("memberships", "cases", "applicants", "audit_events")
DEFINER_FUNCTIONS = (
    "app_memberships_for_subject(text, text)",
    "app_register_user(uuid, text, text)",
    "app_accept_invitation(uuid, text, text, uuid, text)",
)

_TS = sa.DateTime(timezone=True)


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("fca_frn", sa.String(7), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.CheckConstraint(_in("status", ["active", "suspended"]), name="ck_tenants_status"),
        sa.CheckConstraint(
            "fca_frn IS NULL OR fca_frn ~ '^[0-9]{6,7}$'", name="ck_tenants_fca_frn_format"
        ),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("idp_issuer", sa.String(255), nullable=False),
        sa.Column("idp_subject", sa.String(255), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("idp_issuer", "idp_subject", name="uq_users_idp_issuer_idp_subject"),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("accepted_at", _TS, nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_memberships_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_memberships_user_id_users"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_id_user_id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id_id"),
        sa.CheckConstraint(
            _in("role", ["firm_admin", "adviser", "case_administrator", "read_only"]),
            name="ck_memberships_role",
        ),
        sa.CheckConstraint(
            _in("status", ["invited", "active", "deactivated"]), name="ck_memberships_status"
        ),
        sa.CheckConstraint(
            "status <> 'active' OR accepted_at IS NOT NULL",
            name="ck_memberships_activation_requires_acceptance",
        ),
    )
    op.create_index("ix_memberships_tenant_id", "memberships", ["tenant_id"])
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("external_reference", sa.String(100), nullable=True),
        sa.Column("adviser_membership_id", sa.Uuid(), nullable=True),
        sa.Column("property_value_minor", sa.BigInteger(), nullable=True),
        sa.Column("loan_amount_minor", sa.BigInteger(), nullable=True),
        sa.Column("term_months", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("status_changed_at", _TS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_cases"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_cases_tenant_id_tenants"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], name="fk_cases_created_by_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "adviser_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_cases_adviser_membership",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cases_tenant_id_id"),
        sa.CheckConstraint(
            _in("purpose", ["purchase", "remortgage", "further_advance", "product_transfer"]),
            name="ck_cases_purpose",
        ),
        sa.CheckConstraint(
            _in(
                "status",
                [
                    "draft",
                    "collecting_evidence",
                    "processing",
                    "in_review",
                    "approved",
                    "closed",
                ],
            ),
            name="ck_cases_status",
        ),
        sa.CheckConstraint(
            "term_months IS NULL OR term_months BETWEEN 12 AND 480", name="ck_cases_term"
        ),
        sa.CheckConstraint(
            "property_value_minor IS NULL OR property_value_minor >= 0",
            name="ck_cases_property_value",
        ),
        sa.CheckConstraint(
            "loan_amount_minor IS NULL OR loan_amount_minor >= 0", name="ck_cases_loan_amount"
        ),
    )
    op.create_index("ix_cases_tenant_id", "cases", ["tenant_id"])

    op.create_table(
        "applicants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("middle_names", sa.String(200), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("employment_status", sa.String(32), nullable=True),
        sa.Column("declared_gross_annual_income_minor", sa.BigInteger(), nullable=True),
        sa.Column("declared_monthly_commitments_minor", sa.BigInteger(), nullable=True),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_applicants"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_applicants_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"], ["cases.tenant_id", "cases.id"], name="fk_applicants_case"
        ),
        sa.UniqueConstraint("case_id", "position", name="uq_applicants_case_id_position"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_applicants_tenant_id_id"),
        sa.CheckConstraint("position BETWEEN 1 AND 8", name="ck_applicants_position"),
        sa.CheckConstraint(
            "employment_status IS NULL OR "
            + _in(
                "employment_status",
                [
                    "employed",
                    "self_employed",
                    "company_director",
                    "contractor",
                    "retired",
                    "not_employed",
                    "other",
                ],
            ),
            name="ck_applicants_employment_status",
        ),
        sa.CheckConstraint(
            "declared_gross_annual_income_minor IS NULL OR declared_gross_annual_income_minor >= 0",
            name="ck_applicants_declared_income",
        ),
        sa.CheckConstraint(
            "declared_monthly_commitments_minor IS NULL OR declared_monthly_commitments_minor >= 0",
            name="ck_applicants_declared_commitments",
        ),
    )
    op.create_index("ix_applicants_tenant_id", "applicants", ["tenant_id"])
    op.create_index("ix_applicants_case_id", "applicants", ["case_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", _TS, nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_ref", sa.String(255), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_audit_events_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_audit_events_actor_user_id_users"
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'operator', 'system')", name="ck_audit_events_actor_type"
        ),
        sa.CheckConstraint(
            "(actor_type = 'user' AND actor_user_id IS NOT NULL AND actor_ref IS NULL)"
            " OR (actor_type = 'operator' AND actor_user_id IS NULL AND actor_ref IS NOT NULL)"
            " OR (actor_type = 'system' AND actor_user_id IS NULL)",
            name="ck_audit_events_actor_consistency",
        ),
    )
    op.create_index("ix_audit_events_tenant_id", "audit_events", ["tenant_id"])
    op.create_index("ix_audit_events_case_id", "audit_events", ["case_id"])

    _install_security()


def _install_security() -> None:
    # Fail-closed tenant resolver: no/empty setting -> NULL -> every policy comparison false.
    op.execute(
        """
        CREATE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE AS
        $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
        """
    )

    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")

    # tenants: runtime role can read only its own tenant row.
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant_isolation ON tenants USING (id = app_current_tenant())")
    op.execute(f"GRANT SELECT ON tenants TO {APP_ROLE}")

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = app_current_tenant()) "
            "WITH CHECK (tenant_id = app_current_tenant())"
        )

    # users: visible only when they hold a membership in the current tenant. The subquery is
    # itself filtered by the memberships policy, so this inherits tenant scoping.
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY member_of_current_tenant ON users USING ("
        "EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = users.id))"
    )

    op.execute(f"GRANT SELECT ON users TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON memberships TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON cases TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON applicants TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT ON audit_events TO {APP_ROLE}")

    # Audit immutability, enforced for every role including the owner.
    op.execute(
        """
        CREATE FUNCTION audit_events_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS
        $$ BEGIN
             RAISE EXCEPTION 'audit_events is append-only (% rejected)', TG_OP
               USING ERRCODE = 'insufficient_privilege';
           END $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_reject_mutation()"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_reject_mutation()"
    )

    # Cross-tenant operations the runtime role legitimately needs are exposed as narrow
    # SECURITY DEFINER functions rather than broad table privileges. None of them returns
    # personal data held by another firm.
    op.execute(
        """
        CREATE FUNCTION app_memberships_for_subject(p_issuer text, p_subject text)
        RETURNS TABLE (
            membership_id uuid, tenant_id uuid, tenant_name text, role text, status text,
            user_id uuid
        )
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
          -- The caller's own active memberships and pending invitations. Only the firm's
          -- name is disclosed, never another firm's member data.
          SELECT m.id, t.id, t.name, m.role, m.status, u.id
          FROM users u
          JOIN memberships m ON m.user_id = u.id
          JOIN tenants t ON t.id = m.tenant_id
          WHERE u.idp_issuer = p_issuer AND u.idp_subject = p_subject
            AND m.status IN ('active', 'invited') AND t.status = 'active'
          ORDER BY t.name
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_register_user(p_id uuid, p_issuer text, p_subject text)
        RETURNS uuid
        LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
          -- Identity only: users carries no profile data, so nothing about an existing
          -- identity (or which firms it belongs to) is revealed to the caller.
          INSERT INTO users (id, idp_issuer, idp_subject, created_at)
          VALUES (p_id, p_issuer, p_subject, now())
          ON CONFLICT (idp_issuer, idp_subject) DO UPDATE SET idp_subject = users.idp_subject
          RETURNING id
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION app_accept_invitation(
            p_membership_id uuid, p_issuer text, p_subject text, p_audit_id uuid,
            p_request_id text
        ) RETURNS uuid
        LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = public, pg_temp AS
        $$
        DECLARE
          v_tenant uuid;
          v_user uuid;
        BEGIN
          -- Only the invited identity itself can accept, and only a pending invitation.
          UPDATE memberships m
             SET status = 'active', accepted_at = now(), updated_at = now(),
                 version = m.version + 1
            FROM users u, tenants t
           WHERE m.id = p_membership_id
             AND m.user_id = u.id AND u.idp_issuer = p_issuer AND u.idp_subject = p_subject
             AND m.status = 'invited'
             AND t.id = m.tenant_id AND t.status = 'active'
          RETURNING m.tenant_id, m.user_id INTO v_tenant, v_user;

          IF v_tenant IS NULL THEN
            RETURN NULL;
          END IF;

          -- Audited in the same transaction, preserving one-event-per-mutation.
          INSERT INTO audit_events (id, tenant_id, occurred_at, actor_type, actor_user_id,
                                    action, entity_type, entity_id, request_id, changes)
          VALUES (p_audit_id, v_tenant, now(), 'user', v_user,
                  'member.invitation_accepted', 'membership', p_membership_id, p_request_id,
                  jsonb_build_object('status',
                    jsonb_build_object('from', 'invited', 'to', 'active')));
          RETURN v_tenant;
        END
        $$
        """
    )
    for fn in DEFINER_FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO {APP_ROLE}")


def downgrade() -> None:
    for fn in DEFINER_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    op.drop_table("audit_events")
    op.execute("DROP FUNCTION IF EXISTS audit_events_reject_mutation()")
    op.drop_table("applicants")
    op.drop_table("cases")
    # The users policy references memberships, so it must go before that table.
    op.execute("DROP POLICY IF EXISTS member_of_current_tenant ON users")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("tenants")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
