# Stage 2-A — Payslip Reconciliation: Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** ·
Plan: [STAGE-2A-RECONCILIATION-PLAN.md](STAGE-2A-RECONCILIATION-PLAN.md) (revision 2,
assumptions A1–A8 accepted) · Baseline: `790922a` (Phase 2.1)

**Synthetic data only.** Every payslip was produced by the generator, in local databases.

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17, Docker) | **991 passed**, 1 deselected, 0 failed; coverage **93.4 %** (reconciliation: rules 86 %, service 90 %). See the note below. |
| Rules unit tests (no database) | **46 passed** |
| Checks integration tests | **21 passed** |
| Backend ruff (check and format) and `mypy --strict` | clean (117 files, tests included; isolation probes, authorization matrix and audit coverage include both new routes) |
| Frontend vitest | **118 passed** (99 before, 19 new) |
| Frontend `tsc --noEmit` and `eslint .` | clean |
| Manual walkthrough | all steps passed (section 4) |

**Note on the full suite.**
- The first full run gave 967 passed and **24 failed**, all in settings, CLI, logging and
  error-handling tests that don't touch reconciliation.
- **Cause:** the local demo's `backend/.env` (created from `.env.example`, as the README
  says, but pointed at port 5433). Pydantic settings read it, so values such as
  `MEIP_BLOB_BACKEND=filesystem` and `MEIP_DOCUMENTS_ENABLED=true` leaked into tests that
  build their own environments.
- With `.env` set aside, those 9 test files plus the checks tests passed: **273 of 273**.
  Together with the first run's 967 passes, that makes 991 passed.
- **This is a pre-existing fragility:** the suite fails for anyone who follows the README's
  local setup. Fixing it (tests ignoring `.env`) is a separate change and is not made here.
- Coverage is from the first full run.

## 2. What was built

### Backend: module `meip/reconciliation/` (three files and a router)
- **`rules.py`**: the six checks as pure functions over frozen data classes, the versioned
  configuration (`RULESET_VERSION = "payslip-checks/1"`, `PARAMETERS`) and the fingerprint.
  No database, no FastAPI.
- **`service.py`**:
  - loads the case's **current** reviewed data (document figures not retired, reviewed pay
    dates, periods and total deductions, line labels, the latest declared basic salary);
  - runs the rules in the request;
  - attaches acknowledgements;
  - records an acknowledgement under the case lock.
- **`models.py`**: `CheckAcknowledgement`. Migration **`0016`** adds `check_acknowledgements`:
  - RLS on `tenant_id`;
  - `SELECT, INSERT` only for the runtime role;
  - `UNIQUE (tenant, case, finding_key)`, and CHECKs on the key format and note length;
  - classified in `schema_policy.py`.
- **`api/routers/checks.py`**: `GET /cases/{id}/checks` (`case:read`) and
  `POST /cases/{id}/checks/{key}/acknowledgement` (`financial:write`); both are in the
  OpenAPI snapshot, the authorization matrix, the cross-tenant probes and the audit coverage.
- **Audit:** `check.acknowledged`. The rule code, rule-set version, key and income source are
  recorded in the permanent row; **the note goes in the erasable details** (`rule_code`,
  `ruleset_version` and `finding_key` are classified as non-personal).

### The six checks (as planned in revision 2)
| Code | Behaviour as built |
|---|---|
| `PAY-ARITH` | Gross − total deductions = net within 1p. Can't run without any one of the three. |
| `PERIOD-GAP` | Judged on **period coverage**: with both period dates, a whole uncovered period is a discrepancy and a smaller misalignment is "could not run"; with period ends only, whole-month (or 7/14/28-day) steps; pay dates are never used (A1). Mixed frequencies: could not run. |
| `PERIOD-DUP` | Same period end, overlapping periods, or the same pay date without period dates. |
| `YTD-GROSS`, `YTD-TAX` | Consecutive payslips in the same tax year, within £1. The checks don't run (each with its reason) across a gap or overlap, when the payslips' consecutiveness can't be told, when the year to date falls, for "taxable pay to date" (gross only, A5), and when a mismatch could be explained by an adjustment line (A4). Tax period 1 is judged by **pay date** (paid 6 April to 5 May for monthly pay), as PAYE assigns it. The earliest payslip with nothing to compare against is mentioned in the passed message, not listed as a failure. |
| `DECLARED-BASIC` | Declared annual basic against the **latest** payslip's basic pay, annualised (A3), with the range across payslips. **Never against gross.** Can't run with no declared basic, no basic line, or a part-period line on the latest payslip. Discrepancy over 2 % of the declared figure, worded as a product assumption. |

### Fingerprints
A SHA-256 over:
- the rule-set version, the rule and **all parameters**;
- the income source;
- for every cited input: its identity and content (observation and field-review IDs, measure,
  qualifier, value, frequency, period, as-of date; for reviewed fields, the code, decision
  and value);
- the source document version, pay date, period and review frequency.

Unit tests pin that a different observation ID, value, period, document version, rule-set
version or parameter each gives a different key.

### Acknowledgement semantics
- An acknowledgement is accepted only for a key the service has **just computed as a
  current discrepancy**. Otherwise the API returns 409 `finding-not-current`; a second
  acknowledgement returns 409 `already-acknowledged`.
- It writes the row and the audit event and nothing else. The finding is still returned as
  `status: "discrepancy"`, with the acknowledgement attached.

### Synthetic series
`payslip_series()` in `tests/synthetic_docs.py`:
- consecutive monthly payslips, both period dates, pay on the last weekday;
- year to date consistent across the tax year, restarting in April;
- nine faults, each at a chosen index (`positions`);
- `scripts/make_synthetic_payslip.py series OUTDIR --faults ytd_break@1,missing_month@3`.

### Frontend
- **Checks card** on the case page:
  - the summary line ("3 discrepancies (1 acknowledged) · 8 passed · 3 could not run");
  - discrepancies first, then acknowledged ones (still marked "Discrepancy · acknowledged",
    with the note);
  - "Could not run" and "Passed" collapsed;
  - it re-runs when a document changes state or a declared salary changes.
- **Evidence chips** link to `/cases/{id}/documents/{doc}?field={ordinal}`; the review page
  selects that field (and its box) on load, for draft and saved reviews.
- **Acknowledge dialog:** the agreed wording, *"This records that you've seen this
  discrepancy. It doesn't change any figures, resolve the discrepancy or approve the
  income."*, and a required "What you found or did" note.
- **Declared basic salary** on each employment income source:
  - labelled "annual, before overtime, bonus and allowances";
  - a change supersedes the previous value through the existing API.
- **Allowlist:** three entries (`GET cases/{id}/checks`, `POST cases/{id}/checks/{64-hex}/acknowledgement`,
  `POST cases/{id}/financial/observations`). The key segment must be 64 lowercase hex
  characters.

## 3. Differences from the plan, and defects found

| # | What | Why |
|---|---|---|
| 1 | **No insert-only trigger** on `check_acknowledgements`; immutability comes from grants (SELECT and INSERT only), like `document_reviews` and `field_reviews` | The note is free text a broker wrote. A trigger blocking DELETE for every role would also block erasure under the retention policy (D10). The runtime role still can't change or delete a row (the catalog test pins the grants). |
| 2 | `DECLARED-PAY` renamed **`DECLARED-BASIC`** | As agreed in revision 2. |
| 3 | **"First period of the tax year" uses the pay date**, not the period start | Found by the unit tests: a May payslip (period from 1 May) was taken as tax month 1. PAYE assigns tax periods by pay date, so month 1 means paid between 6 April and 5 May. |
| 4 | The earliest payslip with no earlier payslip from its tax year is **noted in the passed message**, not listed as "could not run" | Otherwise every series would show two permanent "could not run" items that aren't problems. |
| 5 | Neutral titles for "could not run" items ("Year to date (gross pay) — September 2026 payslip") | The walkthrough showed "Year to date adds up — …" on items that hadn't run. |
| 6 | Per-fault positions in the generator (`positions`, `fault@index`) | Needed to put several faults in one demo series. |

## 4. Manual walkthrough (in-app browser, local stack, synthetic only)

**Setup:**
- case `SYN-RECON-001` (applicant Jordan Sample, one employment income source);
- six months May–October 2026 with three faults:
  - a £375 year-to-date break in June;
  - August missing;
  - an "Arrears Adjustment" line in October, with year to date £150 out;
- the five payslips uploaded and reviewed (accept all, monthly) through the API by a script,
  to keep the walkthrough on the checks.

| Step | Result |
|---|---|
| 1. Checks before any declaration | **2 discrepancies:** "Pay period not covered — 1 Aug 2026 to 31 Aug 2026" (citing July's and September's period dates and pay dates) and "Year to date doesn't add up — June 2026 payslip" ("… Difference £375.00 (allowance £1.00)", citing May's and June's year to date and June's gross pay). **4 could not run:** September's year to date (gross and tax: "a pay period is missing between…"), October's year to date ("may be explained by 'Arrears Adjustment'… Check it by eye") and the declared basic salary (none given). **8 passed.** |
| 2. Declared basic salary through the form: £50,000 | Shown as "£50,000.00 a year"; the card re-ran by itself. **New discrepancy:** "Declared basic salary differs from the payslips — 10.0 % lower … Basic pay was £3,750.00 on all 5 payslips … more than 2 % of the declared figure (a threshold for highlighting differences, not a lender or regulatory standard)". |
| 3. Acknowledge the June finding | The dialog showed the agreed wording; note entered. Afterwards: "Discrepancy · acknowledged" with the note and time; the summary read "3 discrepancies (1 acknowledged)". **The case's observations, income sources and documents (read through the API) were identical before and after.** |
| 4. Evidence chip "June 2026 · Gross Pay To Date" | Opened the June payslip's saved review at `?field=13`, with that row selected and its box highlighted. |
| 5. Isolation, permissions and replacement | Covered by the automated tests (section 1): another firm gets 404, a read-only user can read but not acknowledge (403), and a superseded declared salary or a retired cited figure produces a new, unacknowledged finding (the old key returns 409). |

## 5. Known limitations
1. **Extraction is still the synthetic provider**, so the checks have only run on generated
   payslips. Real layouts will show more "could not run", for example where pay periods
   aren't printed.
2. **The tax period number isn't captured** (A8). It would make coverage and year-to-date
   checks more robust; it needs an extraction schema change.
3. **The adjustment keywords are a heuristic** (A4). They are listed in the versioned
   configuration and are easy to extend.
4. **Findings aren't stored.** Stage 3's approval snapshot must freeze the findings shown at
   approval, including acknowledged discrepancies.
5. **The case reference in headers** still comes from the case list (from Phase 2.1).

## 6. Files
| | Files |
|---|---|
| New (backend) | `src/meip/reconciliation/` (`__init__`, `rules`, `service`, `models`, `schemas`), `src/meip/api/routers/checks.py`, `migrations/versions/0016_check_acknowledgements.py`, `tests/unit/test_reconciliation_rules.py`, `tests/integration/test_checks.py` |
| Changed (backend) | `app.py` (router), `models.py` (registry), `audit/service.py` (action and field classes), `tests/synthetic_docs.py` (series), `scripts/make_synthetic_payslip.py` (series), `tests/isolation/schema_policy.py`, `tests/isolation/test_cross_tenant_api.py`, `tests/integration/test_authorization_matrix.py`, `tests/integration/test_audit.py`, `tests/unit/test_module_boundaries.py`, `openapi.json` |
| New (frontend) | `components/ChecksCard.tsx`, `components/DeclaredSalary.tsx`, `lib/checks.ts`, `tests/checks.test.ts` |
| Changed (frontend) | case page (checks card, declared salary), review page (`?field=`), `lib/allowlist.ts` (3 entries), `lib/types.ts` |
| Docs | this report; the plan (status); ROADMAP (Stage 1 done, Stage 2 in progress); ARCHITECTURE (reconciliation); README (current state, layout, checks convention) |

## 7. Proposed commit (not made: awaiting your approval)

```
Stage 2-A: payslip reconciliation checks with broker acknowledgements
```
