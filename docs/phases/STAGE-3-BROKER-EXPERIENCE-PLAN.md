# Stage 3 — Broker Experience: Plan

Status: **Approved; decisions S1–S4 approved as recommended (2026-11-19). Built step by step, each reviewed before the next: 3-A implemented (see [STAGE-3A-REPORT.md](STAGE-3A-REPORT.md)); 3-A2 added 2026-11-20 by the [financial domain assessment](FINANCIAL-DOMAIN-ASSESSMENT.md) (decision A1); 3-A2 implemented (see [STAGE-3A2-REPORT.md](STAGE-3A2-REPORT.md)); 3-B and 3-C not started.** · Date: 2026-11-19 ·
Roadmap: [Phase 3](../ROADMAP.md) · Baseline: `6aa3be5` (Stage 2 complete)

**Goal:** turn reviewed evidence into a **broker-approved financial profile** that can be
trusted and defended:
- every figure traces to its source;
- the approval is an immutable, versioned snapshot;
- the evidence pack reproduces exactly what was approved.

**Synthetic data only**, as in every step before Phase 4's safeguards.

**Product principle (2026-11-20):** documents are evidence; the financial profile is the
product. The profile is organised by financial position (item), and each item states what
supports it, by kind (step 3-A2).

## 0. Your three principles, and where each is met

| # | Principle | How | Section |
|---|---|---|---|
| 1 | **Approval is immutable.** New documents or changed records never alter it; a later approval is a new version | The approval stores a full copy of the profile, plus its SHA-256, in an insert-only table. A verified case can't be edited at all: changing anything means reopening (with a reason), which supersedes the approval; the next approval is version n+1 | 3, 4 |
| 2 | **Every figure has provenance.** Where it came from, which document, extracted, corrected or entered, who reviewed it, who approved it | One profile builder attaches provenance to every figure from links that already exist (figure → field decision → extracted field → page and box → document version and hash). Clicking a figure opens the document at that field | 2 |
| 3 | **The pack reflects the approved snapshot**, not whatever is in the database at download time | The pack is built **only** from the stored snapshot, plus originals checked against the hashes in the snapshot. Generation is deterministic: the same approval always gives byte-identical files | 5 |

## 1. Delivery: four steps, each reviewed before the next

| Step | Delivers | Migration |
|---|---|---|
| **3-A Profile and provenance** | The live profile (`GET …/profile`) and a **Financial profile** screen: every current figure, grouped by applicant and item, each with its provenance; clicking opens the evidence | none |
| **3-A2 "Supported by" for each item** | Each item states its support in five separate kinds: declared, documentary (broker-reviewed), corroborating (for example an exact salary credit, which confirms receipt only), findings with acknowledgement status, and approval status. No count or score implies reliability; each piece of evidence states what it does and doesn't establish. Derived; in the snapshot. Detail: [assessment, section 3](FINANCIAL-DOMAIN-ASSESSMENT.md); [plan](STAGE-3A2-SUPPORTED-BY-PLAN.md) approved 2026-11-20 | none |
| **3-B Approval** | Approving the profile freezes it as version n: `profile_approvals`, case status `verified`, reopening supersedes, version history | `0019` |
| **3-C Evidence pack** | `GET …/approvals/{id}/pack`: a ZIP with a PDF summary, the snapshot JSON, the original documents and a manifest of hashes; audited download | none |

One new domain package, `profile`. The architecture plan's separate `review` and `evidence`
modules are merged into one, since they share one data structure.

## 2. The profile and its provenance (3-A)

### 2.1 What the profile is
Following the decision of 2026-11-18, there is **no "current figure" flag**. The profile is
built by one explicit, versioned rule, `profile/1`:

> **Every observation on the case that is not retired**, grouped by applicant → item (income
> source, commitment, expenditure) → measure and period. Declared, broker-entered and
> document figures are listed side by side and labelled. Nothing is chosen, averaged,
> annualised or summed.

The broker doesn't "pick the income figure". That is assessment, which stays out of scope
(roadmap "Later": indicative affordability). To keep a figure out of the profile, the broker
retires it as entered in error, an existing audited action. See decision **S1**.

The profile also carries:
- **applicants:** name, role, order;
- **items:** income sources, commitments and expenditure, with kind, name, basis and dates;
- **bank accounts:** ending, label, holders, and the statements confirmed to each;
- **checks:** both rule sets, their versions and parameters, every finding, and every
  acknowledgement with its note, author and time;
- **document inventory:** every document on the case, including unrecognised, failed or
  unreviewed ones, with status, type, file name, size, SHA-256 and who it's filed under. The
  inventory shows what was *not* relied on as well as what was.

### 2.2 Provenance per figure

| Field | Document figure (`source_kind = document`) | Declared or broker entry |
|---|---|---|
| **Origin** | **Extracted** (accepted as read) or **Corrected** (with the extracted value, the corrected value and the reason) | **Declared** (what the applicant says) or **Broker entry** |
| **Document** | document id, file name, type, version, SHA-256 of the original | — |
| **Location** | page, label box and value box, printed label and raw text | — |
| **Extraction** | run id, provider, schema version | — |
| **Reviewed by / recorded by** | the reviewing member (display name, else email) and time | the recording member and time |
| **History** | the figure it supersedes, if any (value, reason and who), followed back to the first | same |
| **Cited by** | the keys of check findings that cite it | same |

All of it comes from links that already exist; nothing new is stored in 3-A.

### 2.3 API and screen
- **`GET /cases/{id}/profile`** (`case:read` and `document:read`, since it includes document
  details) returns the profile and its **`fingerprint`**: SHA-256 over its canonical JSON.
- **Financial profile screen** (`/cases/{id}/profile`), linked from the case page and added
  as a fourth step on the step strip, "Approve profile":
  - **per applicant**, a table per income source: one row per period, one column per measure
    (gross, net, basic, tax, NI, pension…);
  - **declared and broker-entered figures** listed under the source, labelled;
  - **commitments and expenditure** shown if any exist;
  - every value carries a small origin mark: **E** extracted, **C** corrected, **D** declared,
    **B** broker entry;
  - **clicking a value** opens a provenance panel (section 2.2) with **"Open evidence"**,
    which goes to the document screen at that field (`?field=`, already built);
  - a **checks summary** (counts, with a link to the Checks card), and the **document
    inventory**.

## 3. Approval (3-B)

### 3.1 The record
`profile_approvals` (new, insert-only for the runtime role, RLS):

| Column | Holds |
|---|---|
| `id`, `tenant_id`, `case_id` | as everywhere |
| `version` | 1, 2, 3… per case (unique per case) |
| `snapshot` | JSONB: the profile **exactly as the broker saw it** |
| `snapshot_sha256` | the hash of the snapshot's canonical JSON: the fingerprint the broker approved |
| `profile_rules` | `profile/1` |
| `attestation_version` | which wording the broker accepted (section 3.3) |
| `note` | optional, up to 1,000 characters |
| `approved_by_user_id`, `approved_at` | who and when |

`cases` gains `approval_id`, the current approval:
- a CHECK makes **`status = 'verified'` exactly when `approval_id` is set**, as the Phase 1.1
  design anticipated;
- the status CHECK now admits `verified`.

### 3.2 Approving
`POST /cases/{id}/profile/approvals` takes `{fingerprint, attestation_version, note?}` and
requires a new permission, `profile:approve` (decision **S3**). In one transaction, under
the case lock (the same lock reviews and acknowledgements take):

1. The case must be `in_review`.
2. The profile is rebuilt, and **its fingerprint must equal the one the broker sent**.
   Otherwise 409 `profile-changed`: something changed since the screen loaded, so reload. The
   broker approves exactly what they saw, never a newer version.
3. **Preconditions** (decision **S2**); each failure is a 409 that lists what's outstanding:
   - no document is still processing or awaiting review or confirmation (unrecognised and
     failed documents are allowed, and listed as not used);
   - every current discrepancy is acknowledged ("could not run" findings are allowed, and
     listed);
   - at least one current figure.
4. The approval row is written with version n+1, the case moves to **`verified`** with
   `approval_id` set, and one audit event `profile.approved` records the version, snapshot
   hash and counts. The note goes to the erasable details.

The existing status route keeps refusing `in_review → verified`, now with "Approve the
profile to verify the case": approving is the only way in.

### 3.3 The attestation
The approve dialog shows the wording, versioned as `attestation/1`, and requires a
checkbox:

> I have reviewed the evidence and the figures in this profile, and I confirm that they
> reflect the documents and information provided. The checks listed have been reviewed and
> every discrepancy has been acknowledged. This approval records the evidence position on
> this date; it is not a lending decision, an affordability assessment or advice.

### 3.4 Immutability and versions
- **The snapshot is a copy**, not a pointer to live rows, so nothing done later (a new
  document, a retired figure, an added holder) can change it. The runtime role has only
  `SELECT` and `INSERT` on `profile_approvals`.
- **The hash is checked on every read** that relies on it (the pack, the approval view). A
  mismatch refuses the operation and is logged; it should be impossible without the owner
  role.
- **A verified case is read-only.** Every change goes through `require_editable`, which
  already excludes `verified`: uploads, reviews, figures, acknowledgements, holders,
  applicants and case details. Tests confirm every write route refuses.
- **Reopening** (`verified → in_review` or `collecting_evidence`, which already needs a
  reason) clears `approval_id`. The approval row stays as history; its audit event records
  `superseded_approval_id`. The next approval is version n+1.
- **Version history:** `GET …/profile/approvals` lists every version (current or
  superseded, by whom, when, hash). `GET …/approvals/{id}` returns one snapshot. The profile
  screen shows the approved snapshot, read-only, with a banner: "Approved, version 2, by Alex
  Broker on 19 Nov 2026".

## 4. What a verified case looks like
- **Case page:** a "Verified" badge and a profile card with the version, approver, date and
  **Download evidence pack**.
- **Case status control (new in the UI; the API route exists since Phase 1.1):**
  - "Ready for approval" moves the case to `in_review`, which approving requires;
  - "Reopen", with a required reason, moves a verified case back.
  
  Today the screens have no way to change a case's status.
- **Superseded versions** stay viewable, and their packs downloadable, clearly labelled
  "superseded" on screen. The pack itself carries its version and date (section 5.3).

## 5. The evidence pack (3-C)

### 5.1 Contents (decision **S4**)
`GET /cases/{id}/profile/approvals/{approval_id}/pack` (`document:read`) returns
`evidence-pack-<case ref>-v<version>.zip`:

| File | What it is |
|---|---|
| `summary.pdf` | The human-readable pack, below |
| `snapshot.json` | The approved snapshot, byte-for-byte as hashed |
| `originals/<n>-<file name>` | Each original document **relied on**: reviewed payslips and confirmed statements |
| `manifest.json` | Every file's SHA-256, the snapshot hash, approval id and version, `profile/1`, `attestation/1` and the pack generator version |

**`summary.pdf`** sections:
1. **Cover:** firm, case reference, applicants, approval version, approver, date, the
   attestation text, the snapshot hash. "Synthetic data" is marked while that's all the
   platform holds.
2. **Figures**, per applicant and item, each with its origin (E/C/D/B) and its evidence: the
   document, page and printed label. For a correction, the extracted value and the reason.
3. **Checks:** every finding by rule set; discrepancies with their acknowledgement notes;
   what couldn't run, and why.
4. **Bank accounts and statements:** accounts (ending, sole or joint, holders) and the
   statements confirmed to each.
5. **Document inventory:** every document, including those not relied on and why (failed,
   unrecognised).
6. **Footer on every page:** case reference, version, page n of m, "Not a lending
   decision".

### 5.2 Built only from the snapshot
- The generator reads **the approval row and the original files, nothing else**. Applicant
  names, reviewer names, findings and acknowledgements all come from the snapshot.
- **Each original is checked against the SHA-256 in the snapshot.** If one is missing,
  changed or erased, the pack is refused (409 `original-unavailable`, naming the document),
  never produced partially.
- **Deterministic:** fixed ZIP entry times (the approval time), sorted entries, and
  ReportLab's invariant mode. The same approval gives the same pack, byte for byte, today or
  next year. A test downloads it twice, across a later reopen and change, and compares.
- **Nothing is stored.** The pack is generated on request, so there's no new data category
  to retain or erase, and no stale file.

### 5.3 Download
- **Audited:** `evidence_pack.downloaded` records the approval id, version, number of files
  and the pack's SHA-256. A pack exports every relied-on original at once, so it's logged
  even though single downloads aren't yet (retention brief R17).
- **Dependency:** ReportLab moves from development to runtime dependencies. It already
  draws the synthetic documents in tests, so this adds no new library.

## 6. Scope

**In:**
- the profile view with provenance;
- approval, versions and reopening;
- the evidence pack;
- `verified` made storable;
- the new permission;
- tests, docs and the walkthrough.

**Left out (with the four-question test applied):**

| Left out | Why |
|---|---|
| Choosing or adopting one figure per measure; annualising, averaging, totals | Assessment, not evidence. Regulatory drift; roadmap "Later" |
| Four-eyes approval (a second broker) | Not needed for the MVP; one attributable approval meets principle 1. Add if pilots ask |
| Images of the evidence region inside the PDF | Page and label references, plus the originals in the ZIP, identify the location. Crops need Pillow and rendering; later if wanted |
| Storing packs, share links, e-signature, lender-specific formats | Generated on request is simpler and always matches the snapshot |
| Approving per applicant | One profile per case; the snapshot covers all applicants |
| Staleness timers, notifications | No requirement yet |
| Partial packs, or packs from live data | Against principle 3 |

## 7. Decisions (all approved as recommended, 2026-11-19)

| # | Decision | Options | Recommended |
|---|---|---|---|
| **S1** | What the profile contains | (a) **every current (non-retired) figure**, labelled by origin; the broker excludes a figure by retiring it; (b) the broker selects the figures to include | **(a).** One explicit rule, no new "adopted" state, and the snapshot shows everything the case relies on. (b) is assessment and adds a selection model |
| **S2** | What blocks approval | (a) **documents not yet reviewed or confirmed, and unacknowledged discrepancies, both block**; (b) only unreviewed documents block; discrepancies need only a warning | **(a).** It makes acknowledging meaningful and matches the attestation ("every discrepancy has been acknowledged"). Acknowledging is not resolving, so it's never a dead end |
| **S3** | Who may approve | (a) **firm admin and adviser** (new `profile:approve`); (b) also the case administrator; (c) firm admin only | **(a).** Approval is the adviser's professional act; a case administrator prepares the evidence but doesn't attest it. Read-only stays read-only |
| **S4** | Pack format | (a) **ZIP: PDF summary, snapshot JSON, originals, manifest**; (b) PDF summary only; (c) ZIP with an HTML summary (no PDF library) | **(a).** The PDF is for people, the JSON and manifest prove integrity, and the originals are the evidence. ReportLab is already in the project |

Recorded as proposed; changeable on review, but not open questions: the attestation wording
(3.3); superseded versions viewable and downloadable; the profile and pack also readable by
read-only members (`case:read` and `document:read`).

## 8. Tests and verification

**3-A:**
- every origin (extracted, corrected, declared, broker entry) with its provenance, down to
  page and box;
- the supersession chain;
- retired figures excluded;
- withdrawn items excluded;
- the inventory includes failed and unrecognised documents;
- the fingerprint is stable, and changes with any figure, acknowledgement or document;
- `document:read` required;
- another firm gets 404.

**3-B:**
- approve succeeds, giving version 1, a `verified` case and the pointer set; the CHECK
  refuses `verified` without an approval, and an approval pointer without `verified`;
- `profile-changed` when anything changed after loading;
- each precondition refuses with what's outstanding;
- **every write route refuses on a verified case**;
- reopen clears the pointer and keeps the row; the next approval is version 2;
- the snapshot is unchanged by later edits;
- the runtime role can't `UPDATE` or `DELETE` approvals;
- the hash is checked;
- the new permission is in the authorization matrix (case administrator and read-only
  refused), with cross-tenant probes and audit coverage.

**3-C:**
- the ZIP has the expected files;
- the manifest hashes match;
- originals are byte-identical;
- `snapshot.json` hashes to `snapshot_sha256`;
- **the same pack, byte for byte, before and after a reopen and change**;
- a missing or altered original gives 409 and no partial pack;
- the PDF opens (pypdfium2) and contains the expected figures, findings and inventory;
- the download is audited;
- `document:read` required;
- another firm gets 404.

**Every step:**
- backend: full suite, ruff, `mypy --strict`, module boundaries (`profile` added);
- frontend: vitest, `tsc`, eslint, production build.

**Walkthrough (synthetic, case `SYN-BANK-001` from 2B-3):**
1. Open the profile and click figures through to their evidence.
2. Approval is refused with six unacknowledged discrepancies; acknowledge them.
3. Approve version 1. The case is verified and the upload is refused.
4. Download the pack and check its contents and hashes.
5. Reopen with a reason, retire a figure, then approve version 2.
6. Version 1's pack is still byte-identical.

## 9. Deliverables
- **3-A:** the `profile` package (builder, provenance, fingerprint), `GET …/profile`, the
  profile screen and provenance panel, the step strip's fourth step, tests.
- **3-B:** migration `0019` (`profile_approvals`; `cases.approval_id`; status and pointer
  CHECKs), the approve, list and get routes, `profile:approve`, reopen supersession, the
  approve dialog with the attestation, the case status control ("Ready for approval",
  "Reopen" with a reason), the verified view and version history, tests.
- **3-C:** the pack generator (ZIP, PDF, manifest), the download route and audit, the
  download button, tests.
- **Docs:**
  - a report per step;
  - ROADMAP;
  - ARCHITECTURE: the `profile` module replaces the planned `review` and `evidence`
    modules;
  - README;
  - retention brief:
    - category 6 now exists: the snapshot copies names, figures, reasons and notes, and
      must be erased with the case, never separately;
    - exported packs leave the platform's control;
    - the pack download log.
