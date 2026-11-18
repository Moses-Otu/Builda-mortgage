# Phase 1.2 — Pre-Phase-2 Hardening: Completion Report

Date: 2026-11-18 · Status: **Implemented; review fixes M1–M5 applied** · Plan:
[PHASE-1.2-PLAN.md](PHASE-1.2-PLAN.md) (revision 2, approved) · Baseline: `6a6f486`

**Results (after the review fixes, PostgreSQL 17.4):** 632 tests pass, branch coverage
96.1 %. The isolation suite passes alone (105),
and ruff and `mypy --strict` are clean (92 files). Migrations `0008`–`0011` pass the
up/down/up round trip and the ORM drift check, which now also compares the `xid8` column. The
seeded backfill and duplicate-stop migrations are tested on scratch databases.

Scope stayed as approved: no Phase 2 code, no document processing, no workflow engine, and
no change to existing permissions (the new withdrawal routes use the existing
`financial:write` permission).

## Per item

### 1. Immutable measure meaning (`0008`)

| Aspect | Delivered |
|---|---|
| Migration | `deprecated_at` and `qualifier_required` added to definitions. The FK target is renamed to `uq_measure_definitions_signature` (the old key had a hash-truncated name). `overtime`'s description is clarified to include premium rates *before* the trigger exists. |
| Constraints | Trigger `reference_data_append_only` on `measure_definitions` and `measure_qualifiers`: no DELETE; UPDATE only to set `deprecated_at` once (a `to_jsonb` comparison of every other column) |
| Indexes | — |
| Security | Runtime role: `SELECT` only. The owner could disable the trigger in a migration, which is a reviewed, operator-only path. |
| Deprecation | Trigger `financial_observations_not_deprecated` (BEFORE INSERT) rejects new values for a deprecated measure **or qualifier**, so deprecation is database-enforced (review fix M1). |
| API | Measures list gains `qualifier_required`, `qualifiers` and `deprecated_at`; deprecated measures and qualifiers are hidden unless `include_deprecated`. New values for either get 422; the service reads qualifiers from the reference table, not a code constant. |
| Tests | `TestMeasureGovernance`: six owner-role attempts to change or delete meaning are rejected; deprecation once, never undone or repeated; 422 for new values; list behaviour. The seed test now covers `qualifier_required` and qualifiers. |

### 2. Component qualifiers (`0008`)

| Aspect | Delivered |
|---|---|
| Lists | allowance: car, shift, location, travel, other, unspecified. bonus: guaranteed, discretionary, performance, other, unspecified. **Overtime premium is not an allowance:** it belongs to the `overtime` measure, whose description now says so, avoiding double counting. |
| Migration | New `measure_qualifiers`, seeded. Observations gain `qualifier` and `qualifier_required`. Existing allowance and bonus values are backfilled with `unspecified` (not a guessed `other`). |
| Constraints | `qualifier_required` is part of the composite FK to the definition. `CHECK (qualifier_required = (qualifier IS NOT NULL))`. FK `(measure, subject_kind, qualifier)` → `measure_qualifiers`. |
| Documented | The labels overlap conceptually; a qualifier is the single label the source gives, not a classification; no rule may treat qualifiers as exhaustive or disjoint; future dimensions will be separate named attributes (`financial/measures.py` docstring and ARCHITECTURE.md) |
| Tests | `TestQualifiers`: seven API cases, four raw-SQL cases (missing, unlisted, not allowed, inconsistent with the definition), two qualifiers coexisting in one period; the backfill on a scratch database seeded at `0007` |

### 3. Applicability (application-enforced, no migration)

| Aspect | Delivered |
|---|---|
| Design | `MEASURE_APPLICABILITY` plus the single function `ensure_applicable`. The `other` kinds accept every measure. Documented in the code and ARCHITECTURE.md as **application-enforced, not a database guarantee**. |
| Write paths | Recording a value, and kind changes on income sources and commitments (409 if any active value would stop applying). Expenditure has one measure that suits all categories. |
| Tests | Unit: every measure is mapped, kinds are real, `ensure_applicable` cases. **Static AST guard:** every function anywhere in `src/` that constructs `FinancialObservation` must call `ensure_applicable`, and both kind-change paths must call the validated helper. API tests in `TestApplicability`. |

### 4. Supersession relationship (`0009`)

| Aspect | Delivered |
|---|---|
| Definition | B supersedes A when it has the same item, measure, qualifier, source kind and period key. A is retired as `superseded` in the same transaction. Each value has at most one successor, and every superseded value has exactly one. Chains are linear and never deleted. |
| Constraints | `financial_observations_supersession_check` (BEFORE INSERT): the predecessor must already be retired as `superseded` with an identical key. `financial_observations_superseded_has_successor`: a **deferred constraint trigger** checking at commit that every row retired as `superseded` has a successor. Plus the existing `uq_financial_observations_supersedes`. |
| Service | Under the case lock: retire, flush, insert, audit, all in one transaction. The explicit flush is required by the triggers and the current-value index. |
| API | `superseded_by_observation_id` added, so chains can be walked both ways |
| Security | The trigger functions run with the caller's rights, so RLS applies to their lookups |
| Tests | `TestSupersession`: a three-step chain walked both ways with all values readable; raw SQL rejects a successor of an active value, a mismatched key, and "superseded" without a successor (at commit); three concurrent corrections give 201/409/409 and never fork |

### 5. Current-value uniqueness (`0009`)

| Aspect | Delivered |
|---|---|
| Index | `uq_financial_observations_current_value`: `UNIQUE NULLS NOT DISTINCT` (item, measure, qualifier, source kind, frequency, period start, period end, tax year, as-of date) `WHERE retired_at IS NULL AND source_kind IN ('declared','broker_entry')`. Requires PostgreSQL 15+. |
| Migration | Stops with the offending keys if duplicates exist, without guessing (tested on a seeded scratch database) |
| Concurrency | The case lock serialises writers. The service returns 409 with `current_observation_id`. The index is the backstop: a uniqueness violation maps to 409. |
| Phase 2 | Document-derived values are excluded; they will follow processing-run semantics, recorded as a Phase 2 acceptance criterion |
| Tests | `TestCurrentValue`: 409 with the existing ID; different periods and sources coexist; raw-SQL uniqueness, with retired duplicates allowed; four concurrent duplicates give one 201 and three 409s |

### 6. Withdrawal (`0010`)

| Aspect | Delivered |
|---|---|
| Migration | `withdrawn_at`, `withdrawn_by_user_id` and `withdrawal_reason` (`entered_in_error`, `duplicate`) on the three item tables, with a CHECK that time and reason are set together. `retired_reason` gains `item_withdrawn`. |
| Constraints | `*_frozen_after_withdrawal` (BEFORE UPDATE) freezes withdrawn rows, which makes withdrawal irreversible. `financial_observations_item_not_withdrawn` (BEFORE INSERT) blocks new values for a withdrawn item and locks the item row `FOR SHARE`, so an insert racing a withdrawal waits and then fails. `*_withdrawn_has_no_active_values` (deferred) refuses to commit a withdrawal that leaves active values. `*_parties_frozen_after_withdrawal` freezes parties (review fix M2). |
| Atomicity | One transaction under the case lock: mark withdrawn, retire the active values, one `financial_item.withdrawn` audit event (listing the retired value IDs) |
| API | `POST …/{item}/withdrawal` for all three item types (with `If-Match`); `include_withdrawn` on lists; parties kept |
| Tests | `TestWithdrawal`: values retired and one audit event; edits, new values and a second withdrawal all 409; raw-SQL un-withdraw and insert both rejected; raw withdrawal leaving active values fails at commit; **an insert racing an uncommitted withdrawal on a second connection waits, then fails**; parties frozen; **atomicity: with the audit write forced to fail, the item stays active and its values stay current** |

### 7. Audit-detail transaction binding (`0011`)

| Aspect | Delivered |
|---|---|
| Migration | `audit_events.txid xid8`, added with a constant `'0'` default (no UPDATE on the append-only table; existing rows can never match), then defaulting to `pg_current_xact_id()` |
| Constraints | `audit_event_details_same_transaction` (BEFORE INSERT, row level) |
| Grants | The runtime role's INSERT on `audit_events` is now column-level, excluding `txid` |
| Bypass analysis, verified | INSERT / INSERT…SELECT / ON CONFLICT: trigger. **COPY: refused outright** by PostgreSQL on row-level-secured tables for the runtime role (stronger than planned; tested). UPDATE/DELETE: not granted. Setting `txid`: permission denied (tested). Disabling triggers: the runtime role owns nothing, and `session_replication_role` needs superuser (catalog test). Definer functions: none writes details (catalog test). Savepoints: allowed within the same transaction (tested). The owner role remains a trusted, operator-only path. |
| Tests | `TestAuditDetailBinding` (5 tests); catalog tests for the column-level INSERT set, all eighteen integrity triggers present and enabled, no definer function writing details, and no trigger-disable privilege |

### 8. Retention and erasure requirements brief

[RETENTION-AND-ERASURE-REQUIREMENTS.md](../compliance/RETENTION-AND-ERASURE-REQUIREMENTS.md)
covers the categories you listed: originals and versions, OCR and provider outputs, extracted
fields, observations, reconciled values, approvals, audit events and details, logs and error
traces, backups and blob soft-delete, and third-party copies. It also covers case and
applicant records. The brief has 14 technical requirements and nine questions for legal and
the DPO. It invents no periods, draws no legal conclusions, and does not treat key destruction
alone as erasure.

## Defects and surprises found while building

1. **COPY on RLS tables:** I planned to test COPY as a trigger-covered path, but PostgreSQL
   refuses it entirely for the runtime role. The test now asserts the refusal.
2. **Reference data is permanent in tests.** A test that adds a measure can't remove it
   (append-only). Test measures are prefixed `test_`, and the seed comparison ignores them.
3. **Downgrades are data-dependent,** as documented. Scratch migration tests now recreate
   their database instead of downgrading through `0002`.
4. **The `xid8` column was invisible to the drift check** (unknown type). It is now
   registered, so the check compares it.

## Final review and fixes (M1–M5)

An adversarial review before commit, using raw SQL as the runtime role and two concurrent
connections, found that the first version of this report overstated what the database
enforced:

| # | Found | Fixed |
|---|---|---|
| M1 | Deprecated *qualifiers* still accepted new values (the service checked a code constant; the database checked nothing), and measure deprecation was service-only although documented as database-enforced | BEFORE INSERT trigger for measures and qualifiers; the service reads the reference table; tests through the API and raw SQL |
| M2 | The runtime role could withdraw an item without retiring its values; an insert could race an uncommitted withdrawal without waiting; parties of withdrawn items could change | `FOR SHARE` lock in the insert trigger; deferred "no active values" constraint trigger; party freeze triggers; tests including a two-connection race |
| M3 | Downgrades silently discarded data: 0010 reinstated withdrawn items that had no values (contrary to its own comment), and 0008 dropped qualifiers and deprecations | Both downgrades now refuse while such data exists (tested). 0011's downgrade drops `txid`, which carries nothing after commit (documented). |
| M4 | Documentation: "database-enforced unless stated" was inaccurate; the minimum PostgreSQL version was stated as both 15+ and 16+ | ARCHITECTURE.md states where each rule is enforced; 15+ is required, 17 is tested |
| M5 | — | All gates rerun (results above) |

A follow-up checklist added tests with **real concurrent transactions that bypass the
service's case lock**: the withdrawal race in both orders (insert first: a correct withdrawal
waits and retires the new value, a careless one is refused at commit; withdrawal first: the
insert waits and fails), two raw corrections of the same value (one wins, the other is
refused, one successor), and two raw duplicate current values (the second waits on the index
and fails). It also checks that refused downgrades leave the revision and the protected data
unchanged, and that the runtime role cannot insert, update, delete or truncate measure or
qualifier definitions.

Still application-only, by design: applicability, and the single withdrawal audit event.

## API changes (pre-release)

- Observations: `qualifier` (required for allowance and bonus) and
  `superseded_by_observation_id`; a second current value gives 409 with
  `current_observation_id`.
- Items: `withdrawn_at` and `withdrawal_reason`; three withdrawal endpoints; lists hide
  withdrawn items by default.
- Measures: `qualifier_required`, `qualifiers` and `deprecated_at`; `include_deprecated`.

## Outstanding (outside this scope)

- **"Current figure" is a Phase 2–3 design requirement, not a column.** The current-value
  rule only stops duplicate *entries* of the same fact; it does not say which figure is the
  applicant's income. Several payslips, a P60 and a broker declaration are all legitimately
  "current" for their own period and source. Phase 2–3 must keep distinct: the latest
  declared figure, the latest extracted figure, the broker-adopted figure, an annualised
  calculation, the historical figures behind an average, and the figures included in an
  assessment. There will be **no universal `is_current` flag** on observations. Those
  concepts arrive with processing runs, evidence versions, reconciliation results,
  broker-adopted values and assessment snapshots, and reconciliation selects evidence by
  explicit, recorded rules, never by "latest row wins".
- **Deferred review items:** column-level INSERT on observations (the runtime role can insert
  a value already retired); `current_observation_id` on the index-race 409; TRUNCATE
  coverage on reference tables; a parameterised runtime role name (migrations assume
  `meip_app`); a separate permission for withdrawal; a static guard that also sees Core
  inserts; test-only reference data accumulating in the test database.

- **D10:** decisions in the requirements brief (section 4) are still needed before Phase 2
  handles real documents.
- The Docker stack and a live Entra tenant are not yet verified.
- Phase 2 must implement processing-run ownership of document-derived values (acceptance
  criterion recorded) and call `ensure_applicable` from its normaliser (enforced by the static
  guard).
- Nothing is committed: Phase 1.2 is uncommitted on top of `6a6f486`.
