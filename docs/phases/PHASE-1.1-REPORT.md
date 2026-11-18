# Phase 1.1 — Stable Domain Model: Completion Report

Date: 2026-11-18 · Status: **Implemented; pre-commit review defects D1–D5 fixed** · Design: approved Phase 1.1
architecture proposal (revision 2), with defaults R1–R6 as proposed.

## Steps delivered

| Step | Migration | What changed | Main objects affected |
|---|---|---|---|
| 1.1-A | `0002` | `Actor` (user/operator/system/integration), `TenantScope`, `OperationContext`; audit `on_behalf_of_user_id`, `correlation_id`, stricter actor CHECK; **`audit_event_details`** holds erasable values; `cases.created_by_user_id` nullable | `tenancy/context.py`, `api/deps.py`, `audit/*`, all services |
| 1.1-B | `0003` | Doubled CHECK names renamed; **`ETag`/`If-Match`** on every single-resource read and change (412 stale, 428 missing, 400 malformed); `version` removed from request bodies | all routers, `core/errors.py` |
| 1.1-C | `0004` | Statuses `draft, collecting_evidence, in_review, verified (not storable yet), closed`; `processing`/`approved` retired with data mapping; `closure_reason` + note, CHECK closed ⇔ reason; reopen with note; `porting` purpose; separate `property_use`; loan > value and atypical term are **warnings** | `cases/lifecycle.py`, `cases/*` |
| 1.1-D | `0005` | `party_role`, `on_title`, `is_primary` (one active primary borrower, partial unique index); soft **removal / reinstatement** with reason; `display_order` unique among active only; `employment_status` dropped; runtime role loses DELETE on applicants | `applicants/*`, applicants router |
| 1.1-E | `0006` | `measure_definitions` (35 seeded measures); `income_sources`, `commitments` + `commitment_parties`, `expenditure_items` + `expenditure_parties`; immutable typed **`financial_observations`**; declared figures migrated off `applicants` | new `financial/` module and router |
| 1.1-F | `0007` | `firm_settings` (applicant limit 1–8, four-eyes switch stored for Phase 9); replaces `MEIP_MAX_APPLICANTS_PER_CASE` | new `firm/` module and router |
| 1.1-G | — | Catalog guard extended (reference tables, column-level grants, naming); route-coverage audit test keyed per route; authorization matrix with 37 probes × 4 roles; cross-tenant probes for every parameterised route; module-boundary test | `tests/` |

## Database guarantees added

- **Typed values.** `financial_observations` has one value column per type, with CHECKs that
  exactly the right one is set, in range. A composite FK on
  `(measure, subject_kind, value_type, temporal_kind)` → `measure_definitions` makes a wrong
  type, wrong time semantics or wrong item kind unstorable. `subject_kind` is a stored
  generated column.
- **Case binding.** Every financial row references `(tenant_id, case_id, …)`, including
  applicant references through the new `UNIQUE (tenant_id, case_id, id)` on applicants. An
  observation cannot point at another case's item, and a party cannot be an applicant from
  another case. This is tested at the database level, bypassing the API.
- **Immutability.** The runtime role holds only `INSERT` on observations, plus column-level
  `UPDATE (retired_at, retired_reason)`. A trigger stops a retirement being undone.
- **Lifecycle integrity.** `closed` ⇔ `closure_reason`. Removed applicant ⇔ removal fields,
  never primary. Only a borrower can be primary. `verified` is absent from the status CHECK.
- **Audit.** Actor fields must be mutually consistent. `TRUNCATE … CASCADE` is still
  rejected by the immutability trigger. The permanent `audit_events.changes` holds no direct
  identifiers and no financial values. It is still *pseudonymous* personal data (status and
  role facts about applicant IDs), which the retention policy (D10) must address.
- **Corrections.** At most one observation can supersede any other (partial unique index). A
  correction must match the original's item, measure, source kind and period.

## Tests

- **535 passed** (527, plus 8 regression tests for the review fixes), coverage **95.8 %**.
  ruff and `mypy --strict` are clean on 90 files.
- Isolation suite: **97 passed** (catalog 24, raw-SQL RLS 34, cross-tenant API 27, profile
  isolation 7, constraints 5). New tenant tables are picked up automatically through
  `schema_policy.py`.
- Migrations: up, down to base and up again; ORM-versus-migration drift check; the measure seed
  checked against the code; and migration `0006`'s conversion of declared figures, verified
  on a scratch database seeded at `0005`.

## Defects found and fixed during implementation

1. **The computed column broke updates.** After an UPDATE, SQLAlchemy expired the
   database-computed `subject_kind` and tried to lazy-load it, which async sessions cannot do
   (500 on retirement). Fixed with `eager_defaults`, which fetches it via `RETURNING`.
2. **Decimals could not be audited.** Percentages in audit details caused a 500. Decimals are
   now recorded as exact strings.
3. **Inconsistent decimal rendering.** Created resources echoed `"30"` but reads returned
   `"30.0000"`. Percentages are now normalised to the stored scale at the API edge.
4. A test lock-step that closed a case with raw SQL now must supply `closure_reason`: the new
   CHECK caught it, as intended.

## Breaking API changes (no clients yet)

- `version` moves from request bodies and query strings to the `If-Match` header; stale
  versions return 412, not 409.
- Case statuses and the transition body change: `closure_reason` and `note` replace `reason`.
  `CaseOut` gains `property_use`, `closure_*` and `warnings`.
- `DELETE /applicants/{id}` is replaced by `POST …/removal` and `POST …/reinstatement`.
  `position` becomes `display_order`. Applicants gain `party_role`, `on_title`, `is_primary`,
  `status` and the removal fields, and lose `employment_status` and `declared_*`.
- New: `/api/v1/cases/{id}/financial/*`, `GET /api/v1/financial/measures` and
  `/api/v1/firm/settings`.
- Audit events gain `on_behalf_of_user_id`, `correlation_id` and `details`. Amounts and
  reasons now appear in `details`, not `changes`.
- `MEIP_MAX_APPLICANTS_PER_CASE` is removed; the limit is per firm.

## Decisions applied (defaults from the proposal)

R1 purposes and property uses as proposed; R2 closure reasons as proposed; R4 the applicant
limit counts guarantors; R5 joint commitments stay unallocated until declared or set by the
broker; R6 verification stays in Phase 9. R3 (erasure trigger) and D10 (retention) are not
implemented and are still open.

## Not done / outstanding

- **D10 retention and erasure policy:** still needed before Phase 2 handles real documents.
  Phase 1.1 only made audit values erasable.
- Broker decisions, profile approvals and the `verified` gate are Phase 9. `stale_since`
  arrives with approvals.
- The jobs table, worker role and claim function, documents, extraction and
  `observation_sources` are Phase 2, as designed.
- Applicant aliases (previous names) are Phase 7.
- The Docker stack and a live Entra tenant are still unverified, as in Phase 1.
- Only steps 0 (the Phase 1 baseline) and 1.1-A are committed. Steps 1.1-B to 1.1-G are
  uncommitted.


## Pre-commit review: fixes

A final review before committing found five defects. All are fixed, with regression tests
(`TestReviewFixes` in `test_financial.py`, `TestPrimaryInvariant` in `test_applicants.py`):

| # | Defect | Fix |
|---|---|---|
| D1 | An employer or business `name` was classified as non-personal and kept permanently in `audit_events.changes` (the `"name"` entry was meant for firm names) | `"name"` removed from `NON_PERSONAL_FIELDS`; every name is now erasable detail |
| D2 | A correction could supersede a value from a different source (broker entry retiring a declared value) or a different period | Supersession requires the same item, measure, **source kind** and **period key**; a stock correction keeps the original as-of date |
| D3 | A guarantor changed to borrower, with no primary present, became an active borrower with no primary, and the case could still progress | That applicant becomes primary; progressing to `collecting_evidence`/`in_review` requires an active primary borrower |
| D4 | A removed applicant's income source could still be edited | Edits require the owning applicant to be active (409 otherwise) |
| D5 | The database allowed several observations to supersede the same one | Partial unique index `uq_financial_observations_supersedes` (added to `0006`, uncommitted) |

## Known semantics and caveats (from the review)

- **ETag** is the record's version. It does not change when only derived response fields
  change (`applicant_count`, `warnings`), so use it for concurrency control, not caching.
  `If-Match` is required on modifying requests only. GET ignores it, and `If-Match: *` is
  rejected with 400. Retiring an observation needs no precondition, because a retirement is
  final and enforced once.
- **Downgrades are for development only.** `0002` fails if non-user actors or system-created
  cases exist, `0005` deletes removed applicants, and `0006` drops all financial data.
  Production rollback is by restore.
- **Before Phase 2:**
  - define a current-value rule per period key, and have normalisation runs own
    document-derived observations;
  - make measure definitions append-only, with `deprecated_at`;
  - add a qualifier for component types (allowance and bonus kinds), and a map of which
    measures apply to which income kinds;
  - add a way to withdraw items entered in error;
  - tie audit details to their event's transaction;
  - settle D10 (retention and erasure requirements, including an erasure ledger replayed
    after backup restore).
