# Stage 3, step 3-A — The Financial Profile and Provenance: Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** ·
Plan: [STAGE-3-BROKER-EXPERIENCE-PLAN.md](STAGE-3-BROKER-EXPERIENCE-PLAN.md) (section 2;
decisions S1–S4 approved) · Baseline: `6aa3be5` (Stage 2 complete)

**Read-only, no migration, synthetic data only.**

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17) | **1128 passed, 2 failed**; coverage 95%. The two failures (`test_processing.py` and one `test_statements.py` refusal case) were the worker timing flakiness noted since 2B-1 (a document still "received" when the worker loop ended). The run took 9 hours because the machine slept. Both files were rerun: **33 passed** |
| New: profile integration tests (`tests/integration/test_profile.py`) | **3 passed** |
| Authorization matrix and cross-tenant probes (with the profile route) | **239 passed** |
| Module boundaries (`profile` added) | **13 passed** |
| Backend ruff (check and format) and `mypy --strict` | clean (133 source files) |
| Frontend vitest | **154 passed** (146 before, 8 new) |
| Frontend `tsc`, eslint, production build | clean; build succeeds (built in a copy, see section 3, item 5) |
| Walkthrough (in-app browser, local stack, synthetic) | passed (section 4) |

## 2. What was built

### The profile (`profile` package, rule `profile/1`)
- **One builder**, `profile.service.build_profile`. It collects every observation that isn't
  retired, from items that aren't withdrawn (decision S1). Nothing is chosen, summed,
  averaged or annualised. The same builder will produce the approval snapshot in 3-B, so
  what a broker sees is what gets approved.
- **Origin** of each figure:
  - **extracted:** a document figure the broker accepted as read;
  - **corrected:** a document figure the broker corrected, with a reason;
  - **declared:** what the applicant says;
  - **broker entry:** entered by the broker.
- **Provenance** comes entirely from links that already existed, so nothing new is stored:

| For | The profile gives |
|---|---|
| Every figure | measure, qualifier, value, period, frequency, tax year, as-of date, origin, who recorded it and when |
| A document figure | document, version, type, file name and **the original's SHA-256**; the extracted field (printed label, raw text, extracted value, **page and boxes**); the decision and the **correction reason**; extraction run, provider and schema; **who reviewed it and when** |
| Its history | every value it replaced, newest first (value, origin, who, when, why retired) |
| Checks | the keys of every finding, from either rule set, that cites it |

- **Around the figures:** the case, its applicants, and the items (income sources,
  commitments, expenditure). Also the bank accounts with their holders and statements, both
  rule sets' checks with their acknowledgements, and **every document on the case**,
  including unrecognised and failed ones.
- **Applicants carry only what the profile needs:** no date of birth. The snapshot will
  copy this, so less personal data is copied.
- **Names** of reviewers and recorders come from the member's display name, else their
  email, else "A firm member".
- **`fingerprint`:** SHA-256 over the profile's canonical JSON (sorted keys, no whitespace).
  It's stable across reads and changes with any figure, decision, acknowledgement, document
  or item. Approving (3-B) will require it.

### API
- **`GET /api/v1/cases/{id}/profile`** needs `document:read`, since it includes document
  details. Every role has it. Another firm gets 404.
- It's in the authorization matrix, the cross-tenant probes and the OpenAPI snapshot.
- `documents.service._field_out` became `field_out` (now shared).

### Frontend
- **Financial profile screen** (`/cases/{id}/profile`):
  - **per applicant**, a table per income source: one row per pay period, one column per
    measure in payslip reading order (earnings, deductions, net, year to date);
  - figures without a period (a declared annual salary, year-to-date "as of" values) are
    listed under the table;
  - every value carries an **origin mark** (E, C, D or B, with a tooltip) and counts at the
    top;
  - commitments and expenditure (when any), bank accounts, a checks summary, and the
    **document inventory**.
- **Source panel:** selecting a value shows where it came from in a sentence, plus:
  - for a correction, the value as read and the reason;
  - who reviewed it and when;
  - the extraction and the original's hash;
  - **"Open evidence"**, to the document with that field selected (`?field=`);
  - the values it replaced;
  - how many checks cite it.
- **Case page:** a **Financial profile** button, and a fourth step, "Check the profile",
  which becomes current once everything uploaded is reviewed. 3-B will make it
  "Approve the profile".
- **Pure logic** in `lib/profile.ts` (period table, value formatting, origin wording,
  evidence links), unit-tested.
- **Allowlist:** `GET cases/{id}/profile` only.

## 3. Differences from the plan, and things found

| # | What | Why |
|---|---|---|
| 1 | Year-to-date figures are listed under the table as "as of" values, not in a column | They're recorded against a date (`as_of_date`), not a pay period. Putting them in a period's row would mean inferring which period they belong to, which is a choice the profile doesn't make |
| 2 | Removed applicants aren't shown as cards, but stay in the profile data | The snapshot needs to know who they were; the screen shows the active case |
| 3 | The profile needs `document:read`, not only `case:read` | It includes document details and provenance. Every role holds both today, so nothing changes for anyone |
| 4 | Wording found in the walkthrough | The source panel said "Corrected. Corrected by the broker…"; the repeated label is gone |
| 5 | **The production build broke the running dev server** | Running `next build` in place (as in 2B-3) overwrote the dev server's `.next`, so new routes failed with a missing chunk. I restarted the dev server with a clean `.next`, and now build from a copy of the frontend instead. Nothing in the code is affected |
| 6 | Most demo members have no display name or email | So reviewers show as "A firm member". Correct, but a pilot firm should set display names |
| 7 | Two processing tests failed once in the full run, in upload processing, before any profile code | The known worker timing flakiness. Both files pass on rerun; not changed |

## 4. Walkthrough (in-app browser, local stack, synthetic only)

| Case | Checked | Result |
|---|---|---|
| `SYN-DEMO-002` | Profile and corrected net pay | 6 extracted and 1 corrected (counts at the top); the period table; the corrected net pay: "From payslip-earnings.pdf, page 1, 'Take Home Pay' (read as 2,849.35)", with the reason "Matches the salary credit on the bank statement", the reviewer and the original's hash. Cited by 1 check. The failed upload is in the inventory |
| `SYN-DEMO-002` | "Open evidence" | Opened the payslip with "Take Home Pay" (field 12) selected and outlined |
| `SYN-RECON-001` | Declared figure | Basic pay £50,000.00, annual, "Declared by the applicant", recorded by and when, cited by 1 check. The table shows May, June, July, September and October: **the missing August payslip is visible** |
| `SYN-RECON-001` | Case page | "Financial profile" button; the step strip shows "Check the profile" as the current step |
| API, `SYN-BANK-001` | Profile | 32 extracted figures, 11 documents, 2 accounts; page and boxes for each figure |

## 5. Known limitations
1. **Read-only.** Approval is 3-B; the evidence pack is 3-C.
2. **No editing from the profile.** Retiring or correcting a figure still happens in the
   existing places (review, financial endpoints). The profile shows the result.
3. **Commitments and expenditure** have no screen to create them yet (API only); the
   profile shows them when they exist.
4. **Synthetic only**, as everywhere before the Stage 4 gates.

## 6. Files
| | Files |
|---|---|
| New (backend) | `profile/__init__.py`, `profile/schemas.py`, `profile/service.py`, `api/routers/profile.py`, `tests/integration/test_profile.py` |
| Changed (backend) | `app.py` (router), `documents/service.py` (`field_out` public), `tests/unit/test_module_boundaries.py` (`profile` rule), `tests/integration/test_authorization_matrix.py`, `tests/isolation/test_cross_tenant_api.py`, `openapi.json` |
| New (frontend) | `app/cases/[caseId]/profile/page.tsx`, `lib/profile.ts`, `tests/profile.test.ts` |
| Changed (frontend) | case page (button, four-step strip), `lib/case-steps.ts`, `lib/types.ts`, `lib/allowlist.ts`, `tests/ui.test.ts` |
| Docs | this report; plan status; ROADMAP; ARCHITECTURE (`profile` module); README |

## 7. Proposed commit (not made: awaiting your approval)

```
Stage 3-A: financial profile with per-figure provenance
```
