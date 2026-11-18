"""The isolation contract for every table in the ``public`` schema.

Every table MUST appear in exactly one of the three maps below.
``test_catalog.py::test_every_table_is_classified`` fails when a migration adds a table that
is not classified here, so a new table cannot silently escape the isolation suite.

When adding a table in a later phase:
* tenant-owned data -> ``TENANT_SCOPED`` (it must carry ``tenant_id`` and the standard
  ``tenant_isolation`` policy); it is then automatically covered by ``test_rls.py``.
* a table needing a different policy -> ``SPECIAL_POLICY`` with the exact expected policy.
* no tenant data and no runtime access -> ``EXEMPT`` with a written justification.
"""

from __future__ import annotations

from dataclasses import dataclass

STANDARD_POLICY = "tenant_isolation"
STANDARD_EXPRESSION = "(tenant_id = app_current_tenant())"


@dataclass(frozen=True)
class Policy:
    name: str
    using: str
    with_check: str | None


# table -> privileges the runtime role holds (anything more is a test failure)
TENANT_SCOPED: dict[str, frozenset[str]] = {
    "memberships": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cases": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "applicants": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "audit_events": frozenset({"SELECT", "INSERT"}),
    # Erasable audit values: append-only for the runtime role (retention deletes as owner).
    "audit_event_details": frozenset({"SELECT", "INSERT"}),
}

# table -> (policy, runtime privileges)
SPECIAL_POLICY: dict[str, tuple[Policy, frozenset[str]]] = {
    # A firm sees only its own tenant row.
    "tenants": (
        Policy("tenant_isolation", "(id = app_current_tenant())", None),
        frozenset({"SELECT"}),
    ),
    # Global identities, visible only where they hold a membership in the current firm.
    # Holds identity only (see EXPECTED_USERS_COLUMNS); writes go through app_register_user.
    "users": (
        Policy(
            "member_of_current_tenant",
            "(EXISTS ( SELECT 1 FROM memberships m WHERE (m.user_id = users.id)))",
            None,
        ),
        frozenset({"SELECT"}),
    ),
}

# table -> justification. The runtime role must hold no privileges on these.
EXEMPT: dict[str, str] = {
    "alembic_version": "Migration bookkeeping; no tenant data; owner role only.",
}

# `users` is visible across firms (via each firm's memberships), so it must never hold
# profile data again. Adding a column here requires a privacy review.
EXPECTED_USERS_COLUMNS = frozenset({"id", "idp_issuer", "idp_subject", "created_at"})

# Tables exercised by the raw-SQL RLS tests in test_rls.py.
RLS_TABLES: tuple[str, ...] = (*TENANT_SCOPED, *SPECIAL_POLICY)
