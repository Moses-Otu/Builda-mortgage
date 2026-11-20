# Stage 3, step 3-B — Profile Approval: Report

Date: 2026-11-20 · Status: **Implemented and verified; awaiting your review (not committed,
not pushed)** · Plan: [STAGE-3B-APPROVAL-PLAN.md](STAGE-3B-APPROVAL-PLAN.md) (revision 2;
V1–V5 and R-a to R-e agreed) · Baseline: `c7dc2bb` (3-A2)

**Synthetic data only.** No approval framework, triggers or extra infrastructure: one table,
one pointer column, CHECKs and the existing case lock.

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17) | **1173 passed, 0 failed**; coverage 96% |
| New: approval integration tests (`test_profile_approval.py`) | **17 passed**, including 3 concurrency tests with real transactions |
| New: verified case refuses every write (`test_authorization_matrix.py`) | **passed** (walks every case-scoped write probe) |
| New unit tests: case lock scan, attestation pins, `profile/1` fixture | **6 passed** |
| Migration `0019`: upgrade, downgrade, upgrade; ORM matches; schema policy | passed (`test_schema.py`, `tests/isolation`) |
| Backend ruff (check and format) and `mypy --strict` | clean (140 source files) |
| Frontend vitest | **175 passed** (158 before, 17 new) |
| Frontend `tsc`, eslint, production build | clean; build succeeds (from a copy) |
| Walkthrough (in-app browser, local stack, synthetic) | passed (section 5) |

## 2. Migration `0019_profile_approvals`

| Object | Detail |
|---|---|
| **`profile_approvals`** (new) | `id`, `tenant_id`, `case_id`, `version` (≥1), `profile_rules`, **`snapshot` (text)**, `snapshot_sha256` (64 hex), `attestation_version`, **`attestation_text`** (non-empty), `note` (1–1,000 characters, optional), `approved_by_user_id`, `approved_at` |
| Keys | unique `(tenant_id, case_id, id)` (the pointer's target); **unique `(tenant_id, case_id, version)`**; foreign keys to the tenant, the case `(tenant_id, case_id)` and the user |
| Security | RLS `tenant_isolation`; **`GRANT SELECT, INSERT` only** to `meip_app`: no `UPDATE`, `DELETE` or `TRUNCATE` |
| **`cases.approval_id`** (new, nullable) | foreign key `(tenant_id, id, approval_id)` → `profile_approvals (tenant_id, case_id, id)`: a case can only point to **its own** approval |
| `ck_cases_status` | now admits `verified` |
| **`ck_cases_verified_has_approval`** | `status <> 'verified' OR approval_id IS NOT NULL`: **a verified case always has a current approval** |
| **`ck_cases_approval_status`** | `approval_id IS NULL OR status IN ('verified', 'closed')`: **a closed, previously verified case keeps its final approval**; a case in progress never holds one, so reopening must clear the pointer |
| Downgrade | refuses while any approval exists; otherwise drops the CHECKs, the pointer and the table, and restores the old status CHECK |

Your clarification is enforced in the database itself:
- closing from verified keeps the pointer;
- reopening has to clear it, or the CHECK refuses the update;
- the approval row always remains.

The ORM declares the pointer's foreign key with `use_alter`, since approvals also point at
their case; that's the only circular reference.

**The local demo database** was upgraded to `0019` for the walkthrough.

## 3. What was built

### Backend
- **`profile/models.py`**: `ProfileApproval`.
- **`profile/attestation.py`**: the wording registry (`attestation/1`), never edited. A unit
  test pins each version's SHA-256.
- **`profile/service.py`**:
  - **`snapshot()`** builds the content (`ProfileSnapshot`), now including **`members`**,
    and returns it with its canonical text; the fingerprint is the SHA-256 of that text;
  - **`readiness()`** computes the blockers (case not in review, documents outstanding,
    unacknowledged discrepancies, nothing recorded at all) and the notices (an item with no
    current figures; no document relied on), and supplies the attestation;
  - **`approval_status()`** gives the current approval, and superseded versions with when,
    taken from the reopening's audit event;
  - **`build_profile()`** adds readiness, approval and fingerprint around the snapshot. They
    are outside the fingerprint (V1).
- **`profile/approvals.py`**:
  - **`approve()`:** one transaction. Lock the case row; rebuild; readiness (409
    `approval-blocked`, with the blockers); fingerprint (409 `profile-changed`); attestation
    (422); insert version n+1 with the same canonical text that was hashed and the
    registry's wording; set status and pointer; one audit event `profile.approved`.
  - **`list_approvals()`** and **`get_approval()`**. Getting an approval checks its hash and
    refuses with 409 `snapshot-integrity` on a mismatch, logging the ids.
- **Reopening** (`cases/service.transition_case`) clears `approval_id` in the same `UPDATE`
  as the status, unless closing, and the status audit event records
  `superseded_approval_id`.
- **The status route** still refuses `in_review → verified`, now with "Approve the profile
  to verify the case".
- **Permission** `profile:approve`: firm admin and adviser.
- **Audit:** the action `profile.approved`, and classified non-personal fields (approval id,
  version, snapshot hash, attestation version, profile rules, superseded approval id).
- **Routes:**
  - `POST /cases/{id}/profile/approvals`, which returns the approval and the case's new
    ETag;
  - `GET /cases/{id}/profile/approvals`;
  - `GET /cases/{id}/profile/approvals/{approval_id}`.

### Frontend
- **Case page:**
  - **status control** (V4): "Start collecting evidence", "Ready for approval", "Back to
    collecting evidence", and "Reopen…", whose dialog needs a reason and explains the
    supersession;
  - a live status badge (green when verified);
  - the fourth step, "Approve the profile", done when verified.
- **Financial profile:**
  - **readiness panel:** blockers with links, notices, and "Approve profile…" for advisers
    and firm admins only (others see who approves);
  - **approve dialog:** the server's attestation text with a required checkbox, notices, an
    optional note, and the fingerprint;
  - **verified banner** ("Approved, version 2, by … on …") and a **versions** list;
  - **`?version=n`** opens a stored version read-only, showing its attestation text, its
    approval as the fifth kind of support, and its own fingerprint.
- `lib/approval.ts` (pure, tested); the allowlist additions; `lib/types.ts`.

## 4. Tests, by the five points of revision 2

| Point | Tests |
|---|---|
| **1. Atomic approval** | `profile-changed` after an acknowledgement; **two approvals at once** give exactly one 201 and one `case_not_in_review`, with one row; **approve against a status change** stays consistent; **a writer holding the case lock**: the approval is still pending after 1 s, then refuses with `profile-changed` once the writer commits; **static scan**: every service function that checks `require_editable` takes the case lock (exemptions listed with reasons) |
| **2. Snapshot completeness** | the stored text hashes to the approved fingerprint; the parsed snapshot equals the live profile minus its three derived fields; all five kinds of support present; **every user id in the snapshot resolves in `members`**; a later member rename doesn't change an approval; **the `profile/1` fixture** (a real approval snapshot from the walkthrough) is unchanged, parses, **re-serialises byte for byte**, and renders in the frontend |
| **3. Readiness** | each blocker refuses, and the profile's readiness agrees; **not blockers:** an income source with no figures, no commitments or expenditure, an unrecognised document; `nothing_recorded` only for a case with no items and no documents; notices listed |
| **4. Immutability and consistency** | `UPDATE` and `DELETE` as `meip_app` → permission denied; CHECKs refuse "verified without an approval" and "an approval while in review"; the composite foreign key refuses another case's approval; duplicate versions refused; a tampered snapshot refuses on read; **a verified case refuses every case-scoped write probe** |
| **5. Attestation** | the stored text equals the registry's for the version; an unknown version or `confirmed: false` → 422; a client-supplied `attestation_text` → 422 (unknown field); the pinned hash test |
| Flow | version 1, `verified`, the pointer set, one audit event with the status diff; reopening needs a note, clears the pointer, records `superseded_approval_id`; version 2; the list shows current and superseded with when; version 1 unchanged; **closing keeps the final approval, and reopening a closed case clears it**; the status route never verifies |
| Access | the matrix (approve: firm admin and adviser; list and get: everyone), cross-tenant probes (404), audit coverage for the approve route, the schema policy |

## 5. Walkthrough (in-app browser, local stack, synthetic)
Case `SYN-BANK-001`:

| Step | Result |
|---|---|
| Profile before | Blockers: "Mark the case ready for approval first", and six unacknowledged discrepancies (acknowledged through the existing checks, with a note) |
| Case page | "Start collecting evidence", then **"Ready for approval"**: badge "In review" |
| Profile | "Nothing outstanding. The profile can be approved." → **"Approve profile…"**: the dialog shows the server's attestation text, `attestation/1` and the fingerprint. Tick, then Approve |
| After | **"Approved, version 1, by A firm member on 20 Nov 2026"**, versions: v1 current. API: case `verified`; **an upload → 409** |
| Reopen | Case page **"Reopen…"**: dialog, with a reason → "In review". The profile: not approved, v1 superseded with time |
| Change and re-approve | One August figure retired as entered in error (31 figures) → approved **version 2**; versions: v2 current, v1 superseded |
| Version 1 | `?version=1`: "Version 1 (superseded)", approved and superseded times, the attestation text, **32 figures as approved**, and v1's own fingerprint (`3333e541…`) |

## 6. Deviations from revision 2

| # | What | Why |
|---|---|---|
| 1 | **`GET cases/{id}` added to the frontend allowlist** (the plan listed only the transitions route) | Status changes need the case's current version as their ETag, and the screens had never read a single case. Read-only; the API's own permissions apply |
| 2 | **"Close case" is exempt from the "verified refuses every write" test** | Closing a verified case is allowed by design (your clarification): it keeps the final approval and changes no profile content. Covered by its own test |
| 3 | **No unique index enforcing "one current approval"** | It isn't needed: the pointer is a single column on the case, so a case can't have two. "Newest" is enforced by the approve route and tests (R-b, agreed) |
| 4 | **The approve route returns the case's new ETag** in a header and as `case_version` | So the screen can make the next status change without re-reading. Small, not in the plan's text |
| 5 | **Superseded times come from the reopening's audit event** | No new column needed; the event already records `superseded_approval_id` |
| 6 | **The `profile/1` fixture is a real walkthrough snapshot** (synthetic case) | It's the exact bytes an approval stores, which is what the promise is about |

## 7. Found along the way
1. **The development log formatter crashes on unhandled errors.** When the demo database
   lacked `0019`, the API's error logging itself failed (`TypeError` in structlog's console
   renderer). It only affects the local console, not responses or tests, and it pre-dates
   this step. Worth a small fix separately.
2. Demo members have no display names, so approvals read "by A firm member". A pilot firm
   should set display names.
3. The profile header still shows figure counts by origin (from 3-A). They describe the
   figures shown, not the support for an item, so 3-A2's no-count rule doesn't apply. Noted
   in case you'd prefer them removed.

## 8. Files
| | Files |
|---|---|
| New (backend) | `migrations/versions/0019_profile_approvals.py`, `profile/models.py`, `profile/attestation.py`, `profile/approvals.py`, `tests/integration/test_profile_approval.py`, `tests/unit/test_case_lock.py`, `tests/unit/test_attestation.py`, `tests/unit/test_snapshot_fixture.py`, `tests/fixtures/profile_snapshot_v1.json` |
| Changed (backend) | `cases/models.py` (storable `verified`, pointer, CHECKs), `cases/lifecycle.py`, `cases/service.py` (reopen supersedes), `tenancy/permissions.py`, `audit/service.py`, `models.py`, `api/deps.py`, `api/routers/profile.py`, `profile/schemas.py`, `profile/service.py`, `tests/isolation/schema_policy.py`, `tests/isolation/test_cross_tenant_api.py`, `tests/integration/test_authorization_matrix.py`, `tests/integration/test_audit.py`, `tests/integration/test_profile.py`, `tests/unit/test_domain_rules.py`, `openapi.json` |
| New (frontend) | `components/CaseStatusControl.tsx`, `components/profile/ApprovalPanel.tsx`, `lib/approval.ts`, `tests/approval.test.ts`, `tests/snapshot-fixture.test.ts` |
| Changed (frontend) | case page, profile page, `lib/case-steps.ts`, `lib/profile.ts`, `lib/types.ts`, `lib/allowlist.ts`, `tests/server.test.ts` |
| Docs | this report; the 3-B and Stage 3 plan status; ROADMAP; ARCHITECTURE; README; retention brief (category 6 now exists) |

## 9. Proposed commit (not made: awaiting your review)

```
Stage 3-B: broker approval of the financial profile (versioned, immutable, verified)
```
