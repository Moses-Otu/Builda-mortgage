# Stage 3, step 3-B — Profile Approval: Plan (revision 2)

Status: **Revision 2 approved for implementation (2026-11-20), with R-a to R-e agreed.
Implemented: see [STAGE-3B-REPORT.md](STAGE-3B-REPORT.md).** · Date: 2026-11-20 · Parent:
[Stage 3 plan](STAGE-3-BROKER-EXPERIENCE-PLAN.md) (sections 3 and 4; decisions S1–S4) ·
Baseline: `c7dc2bb` (3-A2)

**Goal:** a broker approves exactly the profile they reviewed. The approval is a frozen,
versioned record that nothing later can change. The case becomes `verified`, and
reopening it, with a reason, supersedes the approval.

**Revision 2** tightens five points. The design and scope are unchanged: no new approval
framework and no new infrastructure.

| # | Your point | Where | In short |
|---|---|---|---|
| 1 | Atomic approval | §4 | One transaction under the case row lock, which every user write already takes; the profile is rebuilt **inside** the lock and compared with the fingerprint the broker saw. The background worker is the one writer without the lock, and it can't change an approvable profile |
| 2 | Snapshot completeness | §3 | The snapshot is the **whole** profile: every figure with full provenance, the five kinds of support, every finding with its evidence and acknowledgements, the inventory. Two additions make it **self-contained**: a `members` list (names for every user referenced) and stating that measure codes never change meaning. A stored `profile/1` example becomes a permanent test fixture |
| 3 | Readiness semantics | §5 | "No figures" is **no longer a blocker**. The absence of a position (no commitments, an item with no figures) never blocks. The blockers are: case not in review, documents outstanding, discrepancies unacknowledged, and a profile with **nothing recorded at all** |
| 4 | Database immutability | §2, §6 | Approvals: `SELECT` and `INSERT` only for the runtime role, enforced by the schema policy test. Pointer and status: two CHECKs make an inconsistent combination **impossible to commit**, and the case row lock serialises every writer of either |
| 5 | Attestation history | §2, §7 | The approval row stores the **exact wording** the broker saw, plus its version. The server supplies the text; a client can't send it. Published wordings are pinned by a test so a version's text can never change |

**What carries over unchanged:**
- **S2:** outstanding reviews and unacknowledged discrepancies block approval.
- **S3:** firm admins and advisers approve, through `profile:approve`.
- **V1–V5:**
  - V1: approval facts and readiness stay outside the fingerprint and snapshot;
  - V2: the snapshot is stored as exact text;
  - V3: closing keeps the pointer and reopening clears it;
  - V4: the screens offer forward steps, going back, and reopening;
  - V5: one readiness function.

**Not changed:**
- the financial domain, documents, reviews, statements, accounts and both check rule sets;
- the case lifecycle's transitions;
- the reopening rule (a note is required, and the `case:close` permission).

## 1. Using what exists

| Need | Existing piece reused |
|---|---|
| What is approved | the 3-A and 3-A2 profile builder (`build_profile`) |
| "Approve what you saw" | the profile's `fingerprint` (3-A) |
| Serialising with every other change | the **case row lock** (`get_case(…, for_update=True)`). Verified for this revision: every user write that can change a profile input takes it before writing (section 4.2) |
| No edits while verified | `require_editable`, already on every write path; `verified` is already outside `EDITABLE_STATUSES` |
| Reopening needs a reason | `transition_case` (a note is required, plus `case:close`) |
| `verified` held back until an approval exists | `STORABLE_STATUSES` and `lifecycle.NOT_YET_AVAILABLE` (Phase 1.1) |

## 2. The record (migration `0019`)

### 2.1 `profile_approvals` (new)
- RLS by tenant.
- **The runtime role (`meip_app`) is granted `SELECT` and `INSERT` only**: no `UPDATE`, no
  `DELETE`, no `TRUNCATE`.
- Only the owner role, through the separate erasure process, can remove a row (retention
  brief, category 6).

| Column | Holds |
|---|---|
| `id`, `tenant_id`, `case_id` | as everywhere; foreign key to the case |
| `version` | 1, 2, 3…; **unique per case** |
| `profile_rules` | `profile/1` |
| `snapshot` | **text**: the profile's canonical JSON, byte for byte as hashed (V2) |
| `snapshot_sha256` | CHECK 64 lowercase hex characters |
| `attestation_version` | for example `attestation/1` |
| `attestation_text` | **the exact wording the broker saw and accepted**, stored with the approval (point 5) |
| `note` | optional, 1 to 1,000 characters |
| `approved_by_user_id`, `approved_at` | who and when |

### 2.2 `cases` gains `approval_id`
- A foreign key on `(tenant_id, id, approval_id)` points to the approval with the same
  tenant **and the same case**, so a case can never point at another case's approval.
- The status CHECK admits `verified`.
- **Two CHECKs make pointer and status consistent in every committed row** (point 4; V3):
  - `verified` needs an approval: `status <> 'verified' OR approval_id IS NOT NULL`;
  - an approval only on a verified case, or one closed from verified:
    `approval_id IS NULL OR status IN ('verified', 'closed')`.
- `cases` already grants `UPDATE` to the runtime role, so no grant changes.
- The downgrade refuses while any approval exists.

### 2.3 What the database guarantees on its own

Even if the application had a bug:

| Guarantee | By |
|---|---|
| An approval row is never changed or deleted by the application | grants (`SELECT`, `INSERT`); checked by the schema policy test and by a test that runs `UPDATE` and `DELETE` as `meip_app` |
| A case is never `verified` without an approval | CHECK |
| A case in progress (draft, collecting, in review) never keeps an approval | CHECK. So reopening **must** clear the pointer in the same `UPDATE`; forgetting would fail the CHECK |
| A case never points at another case's or firm's approval | composite foreign key and RLS |
| Two approvals never share a version | unique `(tenant_id, case_id, version)` |

**What the database doesn't enforce:** that the pointer is the *newest* version. Only the
approve route sets it, to the row it has just inserted, under the case lock. A database
rule for "newest" would need a trigger, which this plan avoids (left out, section 10). A test
covers it.

## 3. The snapshot: complete and self-contained (point 2)

### 3.1 What it contains
The snapshot is **the whole profile as the broker saw it**, minus only the two derived
state fields (`approval`, `readiness`; V1):

| Part | Contents |
|---|---|
| Case | id, reference, purpose, status at approval |
| Applicants | every applicant on the case, removed ones included (so holder and party ids resolve): id, order, role, primary, status, names. No date of birth |
| Items | income sources, commitments (with parties), expenditure (with parties) |
| **Figures, with full provenance** | value, measure, qualifier, period, frequency, tax year, as-of date, origin (E/C/D/B), recorded by (id **and name**) and when. For document figures: document id, version id, type, file name, **SHA-256 of the original**, the extracted field (printed label, raw text, extracted value, **page, label box, value box, page size and unit**), the decision and **correction reason**, extraction run, provider, schema version, **reviewed by (id and name) and when**. **History:** every value it replaced. **Cited by:** finding keys |
| **Support, all five kinds** | per item: declared (figure ids), documentary (document, period, measures, corrected measures, whether figures were saved, reviewer), corroborating (finding key, credit, account, holders, statement and row, **the scope sentence as worded then**), findings (discrepancies with acknowledgements, could not run with reasons, passed). **Approval, kind 5,** is the approval row itself (V1) |
| **Checks** | both rule sets' **versions and parameters**; every finding: key, status, **title and message as worded then**, amounts, **cited evidence** (document, field or row, values), match outcome, relationship, and acknowledgement (note, by, when) |
| Bank accounts | ending, label, sole or joint, holders, statements confirmed to each |
| Document inventory | every document: type, status, failure code, file name, size, **SHA-256**, page count, filed under, uploaded by and when |
| **Members** (new, point 2) | `[{user_id, name}]` for every user the snapshot references: reviewers, recorders, acknowledgers, uploaders. A user id inside the snapshot always resolves to a name **as it was at approval** |

### 3.2 Why it stays interpretable after the live case changes
- **Copied, not referenced.** Names, values, wording (finding titles and messages, the
  corroboration scope sentence), parameters and provenance are copied into the snapshot.
  Later edits, renamed members, reworded checks or a new rule-set version can't change what
  an old approval says.
- **Codes keep their meaning.** Measure and qualifier codes have meanings that are fixed for
  ever (the measure catalogue only adds or deprecates, never edits). Rule codes are
  versioned with their rule set, which the snapshot records.
- **Shape is versioned.** `profile_rules` says how to read it. A later `profile/2` adds a
  reader; it never rewrites stored snapshots. **A stored `profile/1` snapshot is kept in the
  repository as a test fixture**, and the screen must keep rendering it. This guards the
  promise in code, not just in prose.
- **Originals stay outside it.** The snapshot holds each original's SHA-256, not the file.
  An old approval is interpretable without the files; the evidence pack (3-C) needs them and
  checks them against these hashes.
- **The hash is checked whenever an approval is read** (section 8).

## 4. Atomic approval (point 1)

### 4.1 The transaction
`POST /cases/{id}/profile/approvals` (`profile:approve`) runs as **one database
transaction**:

1. **Lock the case row** (`SELECT … FOR UPDATE`) as the first statement. From here until
   commit, no other user write to this case can start its change (section 4.2).
2. **Rebuild the profile inside the lock**, and compute its fingerprint and readiness.
3. **Refuse if readiness has blockers** → 409 `approval-blocked`, with the blockers.
4. **Refuse if the rebuilt fingerprint isn't the one the broker sent** → 409
   `profile-changed`. The broker approved a screen that no longer matches the data, so it
   reloads.
5. **Refuse a bad attestation:** the version isn't the current one, or `confirmed` isn't
   true → 422.
6. **Insert the approval:** version = the case's highest version + 1, read inside the lock.
   The snapshot is **the same canonical text that produced the fingerprint**, so what's
   stored is exactly what was compared. The attestation text comes from the server's
   registry.
7. **Update the case:** `status = 'verified'`, `approval_id =` the new row, bumping the case
   version.
8. **Write one audit event,** `profile.approved`.
9. **Commit.** Any failure rolls back everything: there's never an approval without the
   case update, or a case update without the approval.

### 4.2 Why a stale profile can't be approved
- **Every user write that can change a profile input already takes the same case row
  lock** before writing (verified in the code for this revision):
  - applicants: add, update, remove, reinstate;
  - case details and status transitions;
  - uploads (`store_upload`);
  - payslip reviews and statement confirmations;
  - bank account holders;
  - financial items and figures (create, update, withdraw, retire, supersede);
  - acknowledgements.
- **So once step 1 holds the lock:**
  - a change that committed earlier is visible to the rebuild;
  - a change that hasn't started yet waits until the approval commits. It then finds the
    case `verified`, and `require_editable` refuses it.

  There's no window in which the profile can change between rebuilding and approving.
- **The fingerprint check catches everything before the lock:** anything that changed
  between the broker loading the screen and pressing Approve.
- **The one writer without the case lock is the background worker,** which moves a document
  from *received* or *ready* to *ready*, *extracted*, *failed* or *unrecognised*. Any
  document in *received* or *ready* is itself a blocker (section 5), so:
  - **if the rebuild sees a processing document,** approval is refused;
  - **if it sees none,** the worker has nothing to change on this case: new uploads need
    the lock, and are refused once verified.
- **Two approvals at once:** the second waits for the lock, then finds the case `verified`
  and is refused (`case_not_in_review`). The unique version is a second barrier.
- **Approve and reopen at once:** both take the lock, so one goes first. A reopen that wins
  makes the approval's status check fail; an approval that wins is then superseded by the
  reopen, which clears the pointer in the same `UPDATE` as the status.

**Accepted limits (trade-offs):**
- **Member display names aren't locked.** A member renaming themselves during the
  transaction could make the snapshot show either name, or change the fingerprint and give
  a harmless `profile-changed`. Names are presentation, and the snapshot records the one it
  read.
- **The transaction runs at PostgreSQL's default isolation (read committed),** relying on
  the lock rather than repeatable read. Section 4.2 shows the lock already excludes every
  concurrent profile change. Repeatable read would add serialisation retries for no gain.
- **A new writer added later must take the case lock.** A test enforces this: it scans the
  services for case-scoped writes that call `require_editable` without the lock.

## 5. Readiness semantics (point 3)

`readiness: {ready, blockers[], notices[]}` is computed by one function (V5). The profile
shows it; the approve route enforces it.

### 5.1 Blockers: approval refused

| Code | When | Why it blocks |
|---|---|---|
| `case_not_in_review` | the case isn't `in_review` | approval follows the broker marking the case ready |
| `documents_outstanding` | a document is **received or ready** (still processing), or **extracted** and not yet reviewed or confirmed (payslips and statements) | **genuine outstanding work:** evidence uploaded but not yet reviewed can't be attested |
| `discrepancies_unacknowledged` | any current discrepancy, from either rule set (item- or account-level), has no acknowledgement | the attestation says every discrepancy has been acknowledged |
| `nothing_recorded` | the case has **no current item and no document at all** | there's nothing to attest. This is the only "emptiness" that blocks |

### 5.2 Never blockers (they may legitimately be true)
- **No commitments, or no expenditure:** many applicants have none.
- **An item with no current figures,** for example an income source declared but not yet
  evidenced, or one whose payslip figures were all rejected.
- **An item supported only by declarations,** or with no corroboration.
- **"Could not run" findings:** they're listed and copied into the snapshot.
- **Failed or unrecognised documents:** listed in the inventory as not relied on.
- **A case with items but no figures at all** (everything declared as items with no
  values): the broker may still attest the position as recorded.

### 5.3 Notices: shown, never blocking
Factual statements shown on the approve dialog, so the broker sees them before attesting.
No counts, scores or judgements (3-A2's rule):
- "Employment income (Alex Example): no current figures."
- "No document has been relied on; every figure is declared or entered by the broker."

## 6. Reopening and superseding
- **Reopening** a verified case uses the existing transition (to `in_review` or
  `collecting_evidence`; note required; `case:close`). It takes the case lock, as it already
  does, and **clears `approval_id` in the same `UPDATE` as the status**. The CHECK makes
  this mandatory.
- The `case.status_changed` audit event gains **`superseded_approval_id`** (classified,
  non-personal).
- **Closing a verified case keeps the pointer.** **Reopening a closed case clears it**
  (V3).
- The approval rows always remain. The next approval is version n+1.

## 7. Attestation history (point 5)
- **A registry in code:** `attestation/1` → its exact text. A published version's text **is
  never edited.** New wording means a new version (`attestation/2`), and the registry keeps
  every version.
- **A test pins each published version** to the SHA-256 of its text, so editing a published
  wording fails the build.
- **The server supplies the text.** `GET …/profile` returns `readiness.attestation:
  {version, text}` for the current version. The dialog shows exactly that text. The approve
  request sends only `attestation_version`.
- **On approval, the server stores the registry's text for that version** in
  `attestation_text`, verbatim, alongside the version. The record of what the broker agreed
  to is in the database row itself: it survives code changes, and a later wording can't
  rewrite it.
- **The audit event** records the attestation version and the snapshot hash.

## 8. Reading approvals
| Route | Permission | Returns |
|---|---|---|
| `GET /cases/{id}/profile/approvals` | `document:read` | every version, newest first: version, approved by (name) and when, snapshot hash, attestation version, note, current or superseded (with when) |
| `GET /cases/{id}/profile/approvals/{approval_id}` | `document:read` | one approval: the **attestation text as stored**, and the **snapshot parsed after checking its hash**. A mismatch refuses with 409 `snapshot-integrity` and is logged |

**The live profile's `approval`:**
- on a verified case: `{status: "approved", version, approved_by, approved_at}`;
- after reopening: `{status: "not_approved", superseded: [{version, superseded_at}]}`;
- otherwise: `{status: "not_approved"}`.

## 9. Screens
- **Case page:**
  - **status control** (V4): draft → "Start collecting evidence"; collecting → "Ready for
    approval"; in review → "Back to collecting evidence"; verified → "Reopen…" (a reason is
    required, and the dialog explains that the approval is superseded);
  - the fourth step becomes "Approve the profile".
- **Financial profile:**
  - **Readiness panel:** each blocker in plain words, with links; then the notices; then
    **"Approve profile…"**, only for roles with `profile:approve`.
  - **Approve dialog:** the attestation text **from the server**, a required checkbox, the
    notices, an optional note, and the shortened fingerprint. On `profile-changed`, the
    screen reloads and says so.
  - **Verified:** a banner with version, approver and date; a **Versions** list that opens
    any version read-only (`?version=n`), with the attestation text it was approved under.
- **Allowlist:**
  - `GET` and `POST cases/{id}/profile/approvals`;
  - `GET cases/{id}/profile/approvals/{id}`;
  - `POST cases/{id}/transitions`: new to the allowlist, since the screens have never
    changed a status. The route exists since Phase 1.1, with its own permission checks.

## 10. Tests

**Atomicity (point 1):**
- `profile-changed` for each kind of change after loading: a figure, an acknowledgement, a
  document, a status or a holder;
- **concurrency:** two approvals at once (exactly one succeeds; the other gets
  `case_not_in_review`), approve against reopen, and approve against a review in progress
  (each run as two real transactions with the case lock held);
- **a static test:** every case-scoped write that calls `require_editable` also takes the
  case lock.

**Completeness (point 2):**
- the snapshot text equals the canonical profile text and hashes to `snapshot_sha256`;
- it contains all five kinds of support, the findings with evidence and acknowledgements,
  the provenance down to page and box, and `members` resolving every referenced user id;
- **a stored `profile/1` fixture** keeps parsing and rendering;
- an old approval is unchanged after reopening, editing, renaming a member, and changing an
  acknowledgement.

**Readiness (point 3):**
- each blocker refuses, and the profile's readiness agrees;
- **not blockers:** no commitments, an item without figures, declared-only items, failed
  and unrecognised documents, "could not run" findings;
- `nothing_recorded` only for a case with no items and no documents;
- notices are listed and never block.

**Immutability (point 4):**
- `UPDATE` and `DELETE` on approvals as `meip_app` fail;
- the CHECKs refuse each inconsistent pair (`verified` without an approval; an approval
  while in progress);
- the composite foreign key refuses another case's approval;
- the unique version is enforced;
- the pointer always names the newest version after approve, reopen and re-approve.

**Attestation (point 5):**
- the stored text equals the registry text for the version sent;
- a client can't supply text;
- an unknown or old version is refused;
- the pinned hash test.

**Also:**
- the approval flow: version 1, verified, the pointer set, one audit event; reopen
  supersedes; version 2;
- **every write route refuses on a verified case** (walking the authorization matrix's
  write probes);
- permissions (`profile:approve`: firm admin and adviser; case administrator and read-only
  refused), cross-tenant probes, audit coverage, schema policy;
- frontend unit tests: the status buttons, readiness and notice wording, approval text, the
  request body, the allowlist;
- the usual gates: full suites, ruff, `mypy --strict`, vitest, `tsc`, eslint, production
  build.

**Walkthrough (`SYN-BANK-001`):**
1. "Ready for approval".
2. Blockers list the unacknowledged discrepancies; acknowledge them.
3. Approve version 1, with the attestation shown. The case is verified, and an upload is
   refused.
4. Reopen with a reason. Retire a figure, then approve version 2.
5. Version 1 opens unchanged, with its attestation text, labelled superseded.

## 11. Decisions
**Agreed:** V1–V5 (revision 1). This revision adds no new decision points; it settles the
five points above within that design.

**Remaining trade-offs for your review:**

| # | Trade-off | Chosen | Alternative |
|---|---|---|---|
| R-a | Isolation level | read committed plus the case lock (section 4.2) | repeatable read, with retries on serialisation failures |
| R-b | "Pointer is the newest version" | enforced by the approve route and a test | a database trigger (new infrastructure; avoided) |
| R-c | Member names at approval | read as they are, unlocked; a concurrent rename can give a harmless `profile-changed` | locking membership rows (no benefit for presentation data) |
| R-d | `nothing_recorded` blocks a case with no items and no documents | blocks, since there's nothing to attest | no emptiness blocker at all, relying on the attestation |
| R-e | Notices before attesting | shown, never blocking | none (simpler, but the broker could miss an item with no figures) |

## 12. Left out
| Left out | Why |
|---|---|
| A comparison between versions | each version opens in full; a comparison isn't needed to approve or supersede |
| Four-eyes approval, approval expiry, notifications | no requirement yet |
| Closing from the UI | V4 |
| A trigger for "newest version" | R-b |
| The evidence pack | 3-C |
