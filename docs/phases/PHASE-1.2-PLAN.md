# Phase 1.2 — Pre-Phase-2 Hardening: Implementation Plan (revision 2)

Status: **Approved and implemented (see PHASE-1.2-REPORT.md)** · Date: 2026-11-18
· Baseline: commit `6a6f486`

Revision 2 incorporates the clarifications on P1–P4 and additional requirements 1–4. Scope is
unchanged: no Phase 2 functionality, no document processing, no workflow engine, and no change
to existing permissions.

Tags: **[V]** verified in the code or database, **[R]** recommendation.

**Facts this plan relies on [V]:**
- PostgreSQL 17 locally; `NULLS NOT DISTINCT` (15+) and `pg_current_xact_id()` (13+) are
  available.
- The runtime role `meip_app` holds table-level `INSERT, SELECT` on `audit_events` and
  `audit_event_details`, and nothing else on them.
- `app_accept_invitation` is the only `SECURITY DEFINER` function that writes audit rows, and
  it writes no details.
- Only superusers can change `session_replication_role`; `meip_app` cannot. The runtime role
  owns no tables, so it cannot disable triggers.
- `overtime` is already a separate measure.
- Supersession currently retires the old value and inserts the new one in a single ORM flush
  (`financial/service.py`, `record_observation`).

| # | Item | Migration |
|---|---|---|
| 1 | Measure definitions immutable in meaning; deprecation | `0008` |
| 2 | Component qualifiers | `0008` |
| 3 | Measure applicability (application-enforced) | none |
| 4 | Supersession relationship, enforced in the database | `0009` |
| 5 | Current-value uniqueness | `0009` |
| 6 | Irreversible, atomic withdrawal of items | `0010` |
| 7 | Binding audit details to their event's transaction | `0011` |
| 8 | Retention and erasure requirements brief | none (documentation) |

---

## 1. Measure definitions: immutable meaning

**Rule.** A measure code means exactly what its definition said when it was introduced, for
ever. Deprecation stops *new* values being recorded. It never changes how existing values are
read. A new or refined meaning needs a new code.

**Migration `0008`:**
- `measure_definitions` gains `deprecated_at timestamptz NULL` and `qualifier_required
  boolean` (see item 2). `qualifier_required` is set during the migration, before the trigger
  is created.
- Trigger `measure_definitions_immutable` (`BEFORE UPDATE OR DELETE`):
  - rejects every `DELETE`;
  - rejects every `UPDATE` except one that changes only `deprecated_at`, and only from null
    to a value.

  `INSERT` stays allowed, so a later migration can add measures. The same trigger function
  protects `measure_qualifiers` (item 2).
- The composite foreign keys from observations already block changing `value_type` and
  `temporal_kind`, and deleting a referenced measure [V]. The trigger extends this to
  `description`, `qualifier_required` and unreferenced rows, so nothing depends on whether a
  measure happens to be in use.

**Constraints and indexes:** no new indexes.

**Security:** runtime grants are unchanged (`SELECT` only). The owner role can still disable
the trigger in a migration; that is the documented, reviewed way to change reference data,
and no migration should ever do it.

**API:**
- `GET /api/v1/financial/measures` gains `deprecated_at`, `qualifier_required` and the
  allowed qualifiers, and hides deprecated measures unless `include_deprecated=true`.
- Recording a value for a deprecated measure returns 422.

**Tests:**
- As the owner, on referenced *and* unreferenced measures:
  - update `description`, `value_type`, `temporal_kind` or `qualifier_required` → rejected;
  - delete → rejected;
  - set `deprecated_at` → allowed once; set it again or clear it → rejected.
- New values for a deprecated measure get 422; existing ones are still returned unchanged.
- The seed test checks definitions, qualifiers and `qualifier_required` against the code.

## 2. Component qualifiers

**Lists (P1, approved), with one change for overtime:**
- `allowance`: `car`, `shift`, `location`, `travel`, `other`, `unspecified`.
- `bonus`: `guaranteed`, `discretionary`, `performance`, `other`, `unspecified`.

**Overtime premium — recommendation: not an allowance.** Overtime pay, including enhanced
rates and premiums, is already the separate `overtime` measure [V]. An `overtime_premium`
allowance would let the same pay be recorded twice and double-counted in reconciliation. So
it is dropped from the allowance list, and the measure description of `overtime` will say it
includes premium rates. If basic-rate and premium-rate overtime ever need separating, that's a
qualifier on `overtime` (a new, append-only migration), not an allowance.

**`unspecified` is new:** it means "the source doesn't say", as opposed to `other`, which means
"a known type not on the list". Existing development values are backfilled with
`unspecified`, which is honest, rather than `other`.

**Overlap and future dimensions (documented in the code and the architecture doc):**
- The bonus categories are **not mutually exclusive.** A performance bonus can be guaranteed
  or discretionary.
- The qualifier records the **single label the evidence or the declarant gives**. It is not a
  complete classification.
- **No rule may assume the qualifiers partition the measure.** Reconciliation (Phase 7) must
  not treat them as exhaustive or disjoint.
- **Future dimensions** (for example contractual basis versus trigger) are added as separate,
  explicitly named typed attributes in a later reviewed migration. They are never packed into
  `qualifier` and never replace it, so today's values stay correctly interpreted.

**Migration `0008`:**
- New reference table `measure_qualifiers (measure_code, subject_kind, qualifier,
  description)`:
  - PK `(measure_code, subject_kind, qualifier)`;
  - FK `(measure_code, subject_kind)` → `measure_definitions`;
  - append-only, via the trigger from item 1.
- `financial_observations` gains:
  - `qualifier varchar(32) NULL`;
  - `qualifier_required boolean NOT NULL`, added to the existing composite FK to the
    definition. It becomes `(measure, subject_kind, value_type, temporal_kind,
    qualifier_required)`, with the matching unique key on the definitions table;
  - `CHECK (qualifier_required = (qualifier IS NOT NULL))`;
  - FK `(measure, subject_kind, qualifier)` → `measure_qualifiers`. `MATCH SIMPLE` skips it
    when `qualifier` is null, which is exactly the "not required" case.
- Backfill: `qualifier_required` from each value's definition, and `qualifier =
  'unspecified'` where required.

**Security:** reference tables are `SELECT` only for the runtime role, and the catalog guard's
`REFERENCE` class covers them.

**Tests:**
- Through the API, and with raw SQL as `meip_app`:
  - allowance or bonus without a qualifier → rejected;
  - with a qualifier that isn't on the list → rejected;
  - a qualifier on any other measure → rejected.
- The backfill is tested on a scratch database seeded at `0007`.

## 3. Measure applicability (application-enforced invariant)

**Status: documented as an application-enforced invariant, not a database guarantee.**
Observations don't store their item's kind, so the database can't check it without
denormalising. The docs and the code comment will say so, and the Phase 2 normaliser must call
the same function.

**Design:**
- `MEASURE_APPLICABILITY` in `financial/measures.py` maps each measure to the item kinds it
  applies to. The `other` income kind accepts every income measure.
- One function, `ensure_applicable(measure, subject_kind, item_kind)`, is the **only**
  validation path. It is called by:
  - recording a value (`record_observation`);
  - changing an income source's `kind` (409 if active values would stop applying);
  - changing a commitment's `kind` (same).
- Expenditure has a single measure that applies to every category.

**Migration:** none.

**Tests:**
- Every seeded measure appears in the map (unit test).
- Each write path is tested for both acceptance and rejection.
- A static test fails if any module other than `financial/service.py` writes
  `FinancialObservation` rows, or calls the kind setters without going through
  `ensure_applicable`. It uses the same `ast` import-scanning approach as the boundary test.

## 4. Supersession relationship (requirement 1)

**Definition:**
- Observation **B supersedes A** when B is a correction of the same statement: same item,
  measure, qualifier, source kind and period key (frequency, period start, period end, tax
  year, as-of date).
- A is retired with reason `superseded` *in the same transaction* in which B is inserted.
- Each observation is superseded **at most once** (unique index `uq_financial_observations_
  supersedes`, already present).
- A row retired as `superseded` always has **exactly one** successor.
- Chains are linear: A ← B ← C.
- No row is ever deleted or edited, so the full history stays reconstructable by following
  `supersedes_observation_id` backwards, or the derived successor forwards.

**Migration `0009`:**
- Trigger `financial_observations_supersession_check` (`BEFORE INSERT`, when
  `supersedes_observation_id` is set) rejects the insert unless the referenced row has the
  same item, measure, qualifier, source kind and period key (`IS NOT DISTINCT FROM`), and is
  already retired as `superseded`. The rule is enforced in the database, not only in the
  service.
- A deferred constraint trigger, `financial_observations_superseded_has_successor`
  (`DEFERRABLE INITIALLY DEFERRED`, on update of `retired_reason`), checks at commit that
  every row newly retired as `superseded` has a successor. A transaction can therefore never
  "supersede" a value without recording its replacement.

**Service:**
- Order inside the case lock: retire A, flush, insert B, flush, write the audit record, all in
  one transaction.
- The explicit first flush is needed because the current-value index (item 5) would reject B
  while A is still active.

**API:** `ObservationOut` gains `superseded_by_observation_id`, derived from the reverse
reference, so a client can walk the chain in both directions.

**Security:** trigger functions run with the caller's rights (not `SECURITY DEFINER`), so RLS
still applies to every lookup they make.

**Tests:**
- Through the API: correct, then correct the correction; check both directions of the chain
  and that all values remain readable.
- With raw SQL as `meip_app`:
  - inserting a successor whose key differs from its predecessor's → rejected;
  - inserting a successor while the predecessor is still active → rejected;
  - retiring as `superseded` with no successor → rejected at commit.
- Concurrency: two simultaneous corrections of the same value give exactly one success; the
  other gets 409 or 412, never 500 and never a fork.

## 5. Current-value uniqueness (P3, database-enforced)

**Rule:** for `declared` and `broker_entry` values, at most one **active** observation per
key (item, measure, qualifier, source kind, frequency, period start, period end, tax year,
as-of date).

Values extracted from documents (Phase 2) are deliberately outside this rule. They will follow
processing-run semantics: each run owns its values, and reprocessing retires the previous
run's values in the same transaction. That rule is recorded as a Phase 2 acceptance criterion,
not built now.

**Migration `0009`:**
- Pre-check: stop with a clear message listing the duplicate keys if any exist. No guessing
  which to keep; this affects development databases only.
- Index `uq_financial_observations_current_value`:

  ```
  UNIQUE NULLS NOT DISTINCT (income_source_id, commitment_id, expenditure_item_id,
                             measure, qualifier, source_kind, frequency, period_start,
                             period_end, tax_year, as_of_date)
  WHERE retired_at IS NULL AND source_kind IN ('declared', 'broker_entry')
  ```

**Atomicity under concurrent writes:**
- Every write takes the case row lock first, so concurrent writers on a case are serialised.
- The service checks for an existing current value and returns **409** with that value's ID.
- If a path ever skipped the service, the unique index would still reject it, and the
  existing `IntegrityError` handler would map that to 409.
- Supersession (item 4) is the only way to replace a current value.

**Tests:**
- Through the API: a second current value gets 409 with the existing ID; supersession still
  works; different periods and different sources coexist.
- With raw SQL: a duplicate active row raises a unique violation, and a retired duplicate is
  allowed.
- Concurrency: parallel identical recordings give exactly one 201.
- The migration stops on a scratch database seeded with duplicates.

## 6. Withdrawing items (P2: irreversible and atomic)

**Rule:**
- Withdrawal means an item **should never have existed** (entered in error, or a duplicate).
- It is not ending: a settled loan or a past job stays a true record.
- It cannot be undone; a correction means creating a new item.
- Nothing is deleted.

**Migration `0010`:**
- `income_sources`, `commitments` and `expenditure_items` each gain `withdrawn_at`,
  `withdrawn_by_user_id` (convenience only; the audit log is authoritative) and
  `withdrawal_reason` (`entered_in_error` or `duplicate`), plus a CHECK that all three
  fields are set together.
- Trigger `financial_items_frozen_after_withdrawal` (`BEFORE UPDATE`, on all three tables)
  rejects any update to a row that is already withdrawn. Irreversibility and "no edits after
  withdrawal" are therefore enforced in the database.
- The observation insert trigger from item 4 also rejects new values for a withdrawn item.
- The `retired_reason` CHECK gains `item_withdrawn`.

**Atomicity:** one transaction, under the case lock and with `If-Match`:
1. mark the item withdrawn;
2. retire its active observations with reason `item_withdrawn`;
3. write exactly one `financial_item.withdrawn` audit event, with the reason and the IDs of
   the retired values.

Either all three persist or none do.

**API:**
- `POST …/income-sources/{id}/withdrawal`, `…/commitments/{id}/withdrawal` and
  `…/expenditure/{id}/withdrawal`, each with `FinancialWriteCtx` and `If-Match`.
- Lists hide withdrawn items unless `include_withdrawn=true`.
- Party links are kept.

**Security:** existing grants are enough (table `UPDATE` on items; column `UPDATE` on
observation retirement). No new definer functions. The new routes join the authorization
matrix, the audit route coverage and the cross-tenant probes.

**Tests:**
- Withdrawal retires every active value and writes one audit event.
- Edits, new values and a second withdrawal return 409 through the API and are rejected
  through raw SQL.
- Atomicity: with the audit write forced to fail, the item stays active and its values stay
  current.
- Hidden and shown by `include_withdrawn`; parties kept.

## 7. Binding audit details to their event's transaction (requirement 3)

**Migration `0011`:**
- `audit_events.txid xid8 NOT NULL DEFAULT pg_current_xact_id()`. Existing rows are
  backfilled with `'0'::xid8`, which matches no live transaction, so they can never receive
  details.
- Trigger `audit_event_details_same_transaction` (`BEFORE INSERT`, row level) rejects any row
  whose event's `txid` differs from `pg_current_xact_id()`.
- **Runtime grants on `audit_events` change from table-level `INSERT` to column-level
  `INSERT` on every column except `txid`.** The runtime role can then never supply a `txid`
  itself, so the default (the real transaction) always applies.

**Every write path, analysed:**

| Path | Covered by |
|---|---|
| `INSERT`, `INSERT … SELECT`, `INSERT … ON CONFLICT` | The row-level trigger |
| `COPY FROM` | The row-level trigger, which also fires for `COPY` |
| `UPDATE`/`DELETE` of details | Not granted to the runtime role |
| `UPDATE` of an event's `txid` | Not granted, and the immutability trigger rejects all updates |
| A new event with a forged `txid` | Prevented by the column-level grant |
| Disabling triggers | The runtime role owns nothing; `session_replication_role` needs superuser [V] |
| `SECURITY DEFINER` functions | Only `app_accept_invitation` writes audit rows, and it writes no details [V]; the catalog guard fails if a definer function ever references `audit_event_details` |
| Savepoints | `pg_current_xact_id()` returns the top-level transaction ID; tested explicitly |
| The owner role | Can bypass, by design (migrations and erasure); documented as a trusted, operator-only path |

**Tests:**
- As `meip_app` with raw SQL:
  - details for an event from an earlier transaction → rejected;
  - an explicit `txid` on an event insert → permission denied;
  - `COPY` into details for an old event → rejected;
  - details inside a savepoint in the same transaction → allowed.
- Every existing mutation route still writes its details.
- Catalog guard: the trigger exists and is enabled; `audit_events` has column-level INSERT
  without `txid`; no definer function references `audit_event_details`.

## 8. Retention and erasure requirements brief (requirement 4)

`docs/compliance/RETENTION-AND-ERASURE-REQUIREMENTS.md`, written for the DPO and legal
advisers.

**For each data category below, the brief states:**
- where the data lives (table, blob store, log, backup or provider);
- what personal data it contains;
- what currently creates and deletes it;
- the technical erasure mechanism available;
- the open question for legal or the DPO.

**Data categories covered:**
1. Original documents and document versions (Phase 2 blob storage).
2. OCR and provider raw outputs, and rendered page images.
3. Extracted fields.
4. Normalised financial observations, items, parties and declared values.
5. Reconciled values and discrepancies (Phase 7).
6. Broker decisions and profile approvals, including snapshots (Phase 9).
7. Audit events (the pseudonymous skeleton) and audit details.
8. Application logs and error traces.
9. Database backups (point-in-time recovery) and blob soft-delete or versioning.
10. Third-party processing copies at OCR and AI providers, including temporary result storage
    and abuse-monitoring retention, to be confirmed from the providers' current contracts.

**Technical requirements any policy must meet:**
- deletion in dependency order;
- an erasure event of its own;
- an erasure ledger, replayed after any restore;
- a legal-hold flag;
- erasing one applicant within a live case versus erasing a whole case;
- a log retention window;
- provider-side deletion;
- proof of erasure.

The brief invents no retention periods and draws no legal conclusions. It states explicitly
that destroying encryption keys is not assumed, on its own, to satisfy erasure.

---

## Migration summary

| Migration | Adds | Downgrade |
|---|---|---|
| `0008` | `deprecated_at`, `qualifier_required`, `measure_qualifiers` and seed, observation `qualifier` + foreign keys + CHECK, immutability trigger, backfill | Drops additions (dev only) |
| `0009` | Duplicate pre-check, current-value index, supersession check trigger, deferred successor trigger | Drops index and triggers |
| `0010` | Withdrawal columns, CHECKs, frozen-after-withdrawal trigger, `item_withdrawn` reason | Fails if any item is withdrawn (dev only) |
| `0011` | `audit_events.txid`, binding trigger, column-level INSERT grants | Restores the table-level grant and drops the column |

Downgrades remain development-only, as documented in Phase 1.1.

## Acceptance criteria

1. Measure and qualifier definitions can only be added or deprecated once; deprecated measures
   reject new values; historical values are unchanged.
2. Qualifier rules hold through the API and raw SQL. The overlap and future-dimension policy is
   documented. The `overtime` description covers premium rates.
3. Applicability goes through `ensure_applicable` on every write path, enforced by a static
   test, and is documented as application-enforced.
4. Supersession rules hold in the database (same key; predecessor retired in the same
   transaction; exactly one successor). Chains can be walked in both directions. Concurrent
   corrections never fork.
5. Current-value uniqueness is enforced by the database for declared and broker values;
   concurrent duplicates give one 201 and one 409.
6. Withdrawal is atomic, irreversible and enforced by the database; withdrawn items accept no
   edits and no values.
7. Audit details cannot be attached outside their event's transaction through any runtime
   write path in the table above, each tested.
8. The brief covers all ten categories and the requirements list.
9. The catalog guard, authorization matrix, audit route coverage and cross-tenant probes
   include every new table, trigger, grant and route.
10. Migration round trip, seeded-data migration tests, ORM drift test, ruff, `mypy --strict`,
    the full suite (coverage ≥ 85 %) and the isolation suite all pass. A Phase 1.2 report lists
    anything outstanding.

## Risks

| Risk | Mitigation |
|---|---|
| More logic in database triggers is harder to test than service code | Each trigger gets direct raw-SQL tests as the runtime role, and the catalog guard asserts every trigger exists and is enabled |
| The deferred successor check surfaces errors only at commit | The service always inserts the successor in the same transaction; commit-time failures map to 409 |
| Qualifier lists turn out incomplete for real payslips | `other` and `unspecified`; adding a value is an append-only migration |
| Column-level grants on `audit_events` are easy to undo by accident | The catalog guard asserts the exact column set |
| The applicability map is wrong for an edge case | The `other` income kind accepts everything; the map is revisited with real documents in Phase 2 |

With your approval, I'll implement items 1–8 in migration order `0008` → `0011`, run every
gate, and finish with a Phase 1.2 report. I won't start Phase 2.
