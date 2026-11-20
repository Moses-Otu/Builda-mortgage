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
    # Stage 3-B: approvals are insert-only for the runtime role.
    "profile_approvals": frozenset({"SELECT", "INSERT"}),
    "cases": frozenset({"SELECT", "INSERT", "UPDATE"}),
    # No DELETE: applicants are removed by status, never deleted by the application.
    "applicants": frozenset({"SELECT", "INSERT", "UPDATE"}),
    # INSERT is column-level (every column except txid): see COLUMN_INSERTS.
    "audit_events": frozenset({"SELECT"}),
    # Erasable audit values: append-only for the runtime role (retention deletes as owner).
    "audit_event_details": frozenset({"SELECT", "INSERT"}),
    "income_sources": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "commitments": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "expenditure_items": frozenset({"SELECT", "INSERT", "UPDATE"}),
    # Party links are replaced as a set when the broker corrects who is liable.
    "commitment_parties": frozenset({"SELECT", "INSERT", "DELETE"}),
    "expenditure_parties": frozenset({"SELECT", "INSERT", "DELETE"}),
    # Immutable values: INSERT only, plus column-level UPDATE of retirement (see below).
    "financial_observations": frozenset({"SELECT", "INSERT"}),
    # One row per firm, created at onboarding by the owner role.
    "firm_settings": frozenset({"SELECT", "UPDATE"}),
    # Uploads: no UPDATE or DELETE in step 2-A (processing states arrive in later steps).
    "documents": frozenset({"SELECT", "INSERT"}),
    # Immutable originals: insert-only, and a trigger refuses changes for every role.
    "document_versions": frozenset({"SELECT", "INSERT"}),
    # Background jobs: enqueued with the upload, finished by the worker (same runtime role).
    "jobs": frozenset({"SELECT", "INSERT", "UPDATE"}),
    # Extraction results: written once by the worker, never changed or deleted.
    "extraction_runs": frozenset({"SELECT", "INSERT"}),
    "extracted_fields": frozenset({"SELECT", "INSERT"}),
    # Bank statement rows (Stage 2-B): written once by the worker, never changed or deleted.
    "extracted_transactions": frozenset({"SELECT", "INSERT"}),
    # Bank accounts confirmed by brokers, and their holders (Stage 2-B): written once; a
    # holder is added as a new row, never changed or removed by the application.
    "bank_accounts": frozenset({"SELECT", "INSERT"}),
    "bank_account_holders": frozenset({"SELECT", "INSERT"}),
    # Broker reviews and their per-field decisions: written once, never changed or deleted.
    "document_reviews": frozenset({"SELECT", "INSERT"}),
    "field_reviews": frozenset({"SELECT", "INSERT"}),
    # Acknowledgements of reconciliation discrepancies: written once, never changed by the
    # application (the owner role can erase them under the retention policy).
    "check_acknowledgements": frozenset({"SELECT", "INSERT"}),
}

# table -> columns the runtime role may UPDATE through column-level grants only.
COLUMN_UPDATES: dict[str, frozenset[str]] = {
    "financial_observations": frozenset({"retired_at", "retired_reason"}),
    # Processing outcome only; uploads never change.
    "documents": frozenset({"status", "failure_code", "page_count", "updated_at", "version"}),
}

# Platform reference data: no tenant_id, no RLS, read-only for the runtime role, changed
# only by migrations.
# table -> columns the runtime role may INSERT through column-level grants only.
COLUMN_INSERTS: dict[str, frozenset[str]] = {
    "audit_events": frozenset(
        {
            "id",
            "tenant_id",
            "occurred_at",
            "actor_type",
            "actor_user_id",
            "actor_ref",
            "on_behalf_of_user_id",
            "action",
            "entity_type",
            "entity_id",
            "case_id",
            "request_id",
            "correlation_id",
            "changes",
        }
    ),
}

REFERENCE: dict[str, str] = {
    "measure_definitions": "Value-type definition of each financial measure.",
    "measure_qualifiers": "Labels a qualified measure (allowance, bonus) may carry.",
}

# Integrity triggers that must exist and be enabled (name -> table).
EXPECTED_TRIGGERS: dict[str, str] = {
    "audit_events_immutable": "audit_events",
    "audit_events_no_truncate": "audit_events",
    "audit_event_details_same_transaction": "audit_event_details",
    "financial_observations_retire_once": "financial_observations",
    "financial_observations_supersession_check": "financial_observations",
    "financial_observations_superseded_has_successor": "financial_observations",
    "financial_observations_item_not_withdrawn": "financial_observations",
    "financial_observations_not_deprecated": "financial_observations",
    "income_sources_withdrawn_has_no_active_values": "income_sources",
    "commitments_withdrawn_has_no_active_values": "commitments",
    "expenditure_items_withdrawn_has_no_active_values": "expenditure_items",
    "commitment_parties_frozen_after_withdrawal": "commitment_parties",
    "expenditure_parties_frozen_after_withdrawal": "expenditure_parties",
    "document_versions_immutable": "document_versions",
    "document_versions_no_truncate": "document_versions",
    "income_sources_frozen_after_withdrawal": "income_sources",
    "commitments_frozen_after_withdrawal": "commitments",
    "expenditure_items_frozen_after_withdrawal": "expenditure_items",
    "measure_definitions_append_only": "measure_definitions",
    "measure_qualifiers_append_only": "measure_qualifiers",
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
