# Retention and Erasure: Technical Requirements Brief

Audience: Data Protection Officer, legal advisers, product owner · Date: 2026-11-18 · Status:
**For decision (D10)** — blocks Phase 2 from handling real borrower documents.

## Purpose and limits of this brief

This brief describes **what data the platform holds, where, and what any retention and
erasure policy will have to make technically possible.** It does not set retention periods,
determine lawful bases or controller/processor roles, or reach legal conclusions; those are
the decisions listed in section 4.

Two statements are deliberate:

- **No retention periods are proposed here.** Any period in the platform will come from your
  decision, and from each firm's own regulatory record-keeping obligations, which legal must
  confirm.
- **Destroying encryption keys is not assumed, on its own, to satisfy erasure obligations.**
  Per-case keys may be used as defence in depth (for example to make backup copies unreadable
  sooner), but whether that counts as erasure is a legal question.

Working assumption to confirm: **[A]** the broker firm is the controller of borrower data and
the platform operator is its processor.

Status markers used below: **Exists** (in the code today, Phase 1.2), **Planned** (designed,
built in a later phase), **External** (held by a third party).

## 1. Data inventory

| # | Category | Status | Where | Personal data it contains | Created by | Deleted by today |
|---|---|---|---|---|---|---|
| 1 | Original documents and document versions | Planned (Phase 2) | Private blob storage (UK region), referenced by `document_versions` | Full documents: payslips, P60s, SA302s, bank statements, credit reports | Upload | Nothing yet |
| 2 | OCR / provider raw outputs, rendered page images | Planned (Phase 2–3) | Blob storage, referenced by `extraction_runs` | Full text and layout of the documents | Extraction jobs | Nothing yet |
| 3 | Extracted fields | Planned (Phase 3) | `extracted_fields` | Raw text and parsed values per field, with page and region | Extraction runs | Nothing yet |
| 4 | Normalised financial observations, items, parties | **Exists** | `financial_observations`, `income_sources`, `commitments`, `commitment_parties`, `expenditure_items`, `expenditure_parties` | Amounts, periods, employer/creditor names, masked account references, links to applicants | Brokers (declared/broker entries); later normalisation | Nothing: values are retired or items withdrawn, never deleted (by design) |
| 5 | Reconciled values and discrepancies | Planned (Phase 7) | `reconciliation_runs`, `reconciled_values`, `discrepancies` | Derived figures per applicant item | Reconciliation runs | — |
| 6 | Broker decisions and profile approvals | Planned (Phase 9) | `broker_decisions`, `profile_approvals` (including a JSON snapshot and hash) | Overrides with reasons; a full snapshot of the approved profile | Brokers | — |
| 7a | Audit events (permanent skeleton) | **Exists** | `audit_events` (append-only; triggers block UPDATE, DELETE and TRUNCATE for every role) | No direct identifiers or financial values, but **pseudonymous personal data**: actions, statuses and roles about applicant and user IDs, timestamps, request IDs | Every mutation | Nothing, by design |
| 7b | Audit details (erasable values) | **Exists** | `audit_event_details` (runtime role: insert-only; bound to the event's transaction) | Amounts, reasons, notes, names (employer/business), party lists | Every mutation with values | Nothing yet; designed to be deletable by the owner-run retention process |
| 8 | Case and applicant records | **Exists** | `cases`, `applicants` (soft-removed, never deleted by the application), `memberships` | Names, dates of birth, notes (closure, removal), external references; firm contact details for members | Brokers | Nothing: removal is a status, not deletion |
| 9 | Application logs and error traces | **Exists** | Platform log store (Azure, planned) | By design IDs and route templates only; exceptions logged as type and stack frames without messages. **Gap:** an error after a response has started streaming is logged by the web server with its message | Every request | Log retention setting (not yet defined) |
| 10 | Database backups | **Exists** (when deployed) | Azure PostgreSQL point-in-time restore | Everything in categories 3–8 as at each point in time, including data later erased | Platform | Backup expiry only |
| 11 | Blob soft-delete and versioning | Planned (Phase 2) | Blob storage | Deleted or overwritten documents and outputs, for the soft-delete window | Platform | Soft-delete expiry |
| 12 | Third-party processing copies | External (Phase 3) | OCR / document-intelligence provider; LLM provider | Document content sent for processing; temporary result storage; possible abuse-monitoring logs | Extraction calls | **Per provider terms — to be confirmed from current contracts, not assumed here** |

## 2. Relationships that constrain erasure

- **Dependency order.** Observations reference items; items and parties reference applicants
  and cases; corrections reference earlier observations (`supersedes_observation_id`); audit
  details reference audit events. Erasure must delete children before parents, or tombstone
  parents, in a defined order.
- **Immutability by design.** Values are retired, not deleted; withdrawn items are frozen;
  audit events cannot be changed by any role through normal SQL. Erasure therefore has to be a
  separate, privileged, audited process run by the owner role. It is not something the
  application does.
- **Shared items.** A joint commitment can involve several applicants. Erasing one applicant
  must not silently delete a liability that still concerns another (the
  liability-versus-allocation model in Phase 1.1).
- **Approvals.** An approval snapshot (Phase 9) copies the values that were approved. Erasing
  the underlying observations without the snapshot (or the reverse) would leave either orphan
  personal data or an unverifiable approval.

## 3. Technical requirements any policy must satisfy

| # | Requirement | Why |
|---|---|---|
| R1 | **Defined scope units:** whole case; one applicant within a live case; one firm (offboarding) | Different triggers (retention expiry, data subject request, firm exit) need different scopes |
| R2 | **Deletion in dependency order** across categories 1–8, with tombstones where a parent row must remain for integrity | Foreign keys and the correction chain |
| R3 | **An erasure event of its own** (who, when, what scope, which categories), retained after the data is gone | Demonstrating that erasure happened |
| R4 | **Proof of erasure:** counts per category before and after, recorded with the erasure event | Accountability |
| R5 | **Legal hold:** a flag that blocks erasure for a case or firm, with who set it and why | Disputes, regulatory requests |
| R6 | **Erasure ledger replayed after any backup restore** (list of erased scope IDs), before the system serves traffic | A restore must not resurrect erased data |
| R7 | **Backup window documented and accepted:** erased data persists in backups until they expire | Point-in-time restore cannot selectively delete |
| R8 | **Blob soft-delete and version cleanup** included in erasure, not left to expiry by default | Documents are the most sensitive data |
| R9 | **Provider-side deletion:** request or confirm deletion at OCR/LLM providers per their terms; record provider, region and request IDs on every extraction run so this is traceable | Third-party copies |
| R10 | **Log retention window** defined; logs are designed not to contain personal data, but the one known gap (category 9) must be closed or covered by the window | Logs outlive cases if unmanaged |
| R11 | **Audit skeleton decision implemented:** either keep the pseudonymous skeleton (with erasable details deleted) or pseudonymise further (for example replacing applicant IDs) | The skeleton is still personal data |
| R12 | **Re-identification check:** after erasure, nothing left (audit skeleton, logs, metrics) can be joined back to the person through a remaining identifier | Pseudonymous data plus a key is identifiable |
| R13 | **Who can trigger erasure** (firm administrator, platform operator, both) and with what confirmation | Irreversible and high impact |
| R14 | **Tests** for each requirement, including restore-and-replay | Erasure must be verifiable, not assumed |

## 4. Decisions needed (questions for legal / DPO)

1. **Roles:** is the firm the controller and the platform the processor for all categories,
   including audit data the platform needs for its own security?
2. **Retention periods** per category, and the event that starts each clock (case closed,
   firm offboarded, document superseded, …). Do they differ by firm (for example by the firm's
   own regulatory obligations)?
3. **Audit skeleton:** may the pseudonymous skeleton outlive the case's personal data? For how
   long? Must it be further pseudonymised?
4. **Trigger:** is erasure at retention expiry automatic, or only after a firm administrator
   confirms (open decision R3 from Phase 1.1)?
5. **Individual erasure within a live case** (one applicant leaves and asks for erasure): what
   must happen to joint liabilities, shared documents and approvals that involved them?
6. **Backups:** is the backup retention window acceptable as the period during which erased
   data still exists, provided the ledger is replayed on restore?
7. **Third-party providers:** which provider terms (training exclusion, result retention,
   abuse monitoring) are acceptable, and must provider-side deletion be requested per case?
8. **Approvals:** after erasure, may the platform retain the *fact* that a profile was
   approved (who, when, hash) without the values?
9. **Proof:** what evidence of erasure do firms need to give data subjects or regulators?

## 5. What exists today to support this

- Personal values in audit records are already separated into erasable `audit_event_details`,
  bound to their event's transaction, while the permanent audit skeleton holds no direct
  identifiers or financial values.
- Every financial value, item, applicant and case has a stable ID and a tenant and case
  binding enforced by the database, which makes scoped deletion precise.
- Logs are designed to carry identifiers only.

Nothing in the platform performs erasure yet. The process will be designed and built after
the decisions above are made.
