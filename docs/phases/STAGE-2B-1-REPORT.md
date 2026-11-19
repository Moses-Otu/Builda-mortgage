# Stage 2-B, steps 2B-0 and 2B-1 — Test Environment Fix; Reading Bank Statements: Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** ·
Plan: [STAGE-2B-BANK-STATEMENTS-PLAN.md](STAGE-2B-BANK-STATEMENTS-PLAN.md) (revision 2,
C1–C7 approved) · Baseline: `85324f4` (Stage 2-A)

**Synthetic data only. Statements are refused in code outside local and test.** Everything
below proves the pipeline against the **synthetic generator's ground truth**. It says nothing
about how accurately real bank statements would be read (plan, section 3.3).

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17), **run with a conflicting `backend/.env` present** | **1,046 passed, 3 failed** on the full run; coverage **94.6 %**. All 3 then **passed** when rerun on their own (**1,049 of 1,049**); see the note below |
| Statement unit tests (no database) | **40 passed** |
| Statement integration tests | **13 passed** |
| Migration checks (models match migrations; upgrade, downgrade, upgrade; downgrade refusals) | passed |
| Backend ruff and `mypy --strict` | clean (120 source files) |
| Frontend vitest | **122 passed** (118 before, 4 new) |
| Frontend `tsc --noEmit` and eslint | clean |
| *Note on the 3 failures* | (1) `test_document_type_is_required_and_limited` still treated `bank_statement` as invalid. Updated to use types that remain invalid (`p60`, `credit_report`). (2) A CLI subprocess hit its 60-second timeout, and (3) an extraction-retry test found no job to claim. Both ran while I was migrating and driving the demo stack on the same database server, both pass on rerun, and neither involves statement code. They're reported as timing flakiness under load, not as fixed. |
| Real worker on the local stack | a two-page statement extracted (9 rows, 7 summary fields); a dollar statement refused with `statement_foreign_currency`; shown on the case page as below |

## 2. Step 2B-0: tests ignore a developer's `.env`
- `Settings` reads `env_file` from `MEIP_ENV_FILE`. The default is still `.env`, so nothing
  changes outside tests; empty means no file.
- `tests/__init__.py` sets `MEIP_ENV_FILE=""` before anything imports `meip` (pytest imports
  the package before any conftest), so the test process and every subprocess it starts
  ignore a local `.env`.
- Two existing helpers that start subprocesses with a "clean" `MEIP_*` environment
  (`test_cli.py`, `test_processing.py`) were stripping the new variable too; they now keep
  `MEIP_ENV_FILE`.
- A new test proves both directions: with no setting, a temporary `.env` is read; with it
  empty, the same `.env` is ignored.
- **Verified:** the 24 tests that failed in the 2-A report pass with the conflicting demo
  `.env` present, and so does the whole suite (section 1).

## 3. Step 2B-1: what was built

### Upload and processing
- **Document type** `bank_statement`. The upload route refuses it with 404
  `feature-disabled` unless `Settings.bank_statements_allowed` (local or test only),
  **before reading the body**.
- **Page limit:** 20 pages for statements, 10 for payslips; the limit is passed to the
  sandboxed PDF check.
- **Extraction branches by type.** A payslip is extracted only as a payslip, and a statement
  only as a statement:
  - uploaded as the wrong type, a document is `unrecognised`;
  - a refusal sets `failed` with one of three new failure codes;
  - in both cases the run is stored with its markers and no rows.
- **Payslip review refuses statements** (409) until confirmation exists in 2B-2.

### `documents/statements.py` (`statement-rules/1`, `bank_statement/1.0.0`)
- **Classifier:** a statement marker plus a transactions header. Tested both ways (no payslip
  layout is a statement; no statement is a payslip).
- **Summary fields**, stored as `extracted_fields` with label and value boxes:
  - `account_ending` (last 4 digits; **raw text stored masked as `****4821`**);
  - `statement_period_start` and `statement_period_end`;
  - `opening_balance` and `closing_balance`;
  - `total_paid_in` and `total_paid_out`.
- **Table rows**, stored as `extracted_transactions`:
  - columns are found from the header (date, description, paid out, paid in, balance);
  - **dates** printed without a year take it from the statement period;
  - **descriptions** are kept verbatim, multi-line ones joined with line breaks, with
    truncation over 500 characters flagged;
  - **money in and money out** stay in separate columns;
  - **running balances** are stored when printed; end-of-day-only balances and statements
    with no balance column are supported;
  - **brought and carried forward rows**, including those at page breaks, are kept as their
    own kinds.
- **Provenance:** each row's `cells` holds the raw text, page and box of the date, **every
  description line**, paid out, paid in and balance, plus the row's own box.
- **Money as statements print it:** `£1,200.00`, `1,200.00`, `-£500.00`, `£-500.00`,
  `(500.00)`, and overdrawn `OD`/`DR` (`CR` is in credit).
- **Refuse rather than guess:**
  - more than one account number;
  - a foreign currency;
  - no transactions table.
  An unreadable row is kept as `unparsed`, with its raw text and box.

### Account identity (decision C2)
- The full account number and sort code are read **in memory only**. The worker computes
  `HMAC-SHA256(key, "sortcode:account")` and stores it on the extraction run
  (`account_fingerprint`), then discards both values.
- `MEIP_ACCOUNT_FINGERPRINT_KEY` is optional in local and test (a fixed development key) and
  required anywhere else.
- **Tested:**
  - the same account gives the same fingerprint;
  - **same last 4 digits with a different sort code gives a different fingerprint**;
  - a masked number gives none;
  - no column of any statement table, the documents table or the audit tables contains the
    account number or the sort code.
- **The raw provider output does contain them**, as decided (C1). A test asserts this too,
  so the fact can't be forgotten.

### Migration `0017`
- `documents`: new document type and failure codes.
- `extraction_runs`: new classification, and `account_fingerprint` (CHECK: 64 hex
  characters).
- `extracted_fields.field_code`: the statement codes.
- **`extracted_transactions`:**
  - CHECKs: kind; amounts not negative; a transaction has an amount; only transactions
    have amounts; unparsed rows have no balance; `cells` is an object; the row box lies
    inside the page;
  - RLS, `SELECT, INSERT` only;
  - classified in the isolation catalog.
- The downgrade refuses while any statement exists.

### Synthetic generator: `bank_statement_series()`
- **Consistent with `payslip_series()`:** salary credits are the net pay on the pay date.
- **Layouts:** standard, end-of-day balances, no balance column, two pages (with carried and
  brought forward rows).
- **Account variants:** different sort code with the same last 4 digits, masked number,
  overdrawn opening.
- **Eleven faults:**
  - salary credits: missing, short, near, duplicate amount, late, outside the window;
  - statement rows: missing row, unreadable row;
  - whole statements: statement gap, multiple accounts, foreign currency.
- **Ground truth** is what was actually printed, including the page-break rows.

### Frontend (small, so the case page stays correct)
- Statements show a "Bank statement" badge and status **"Extracted"**, and **no Review
  button** (the payslip screen can't confirm a statement).
- The three refusal codes have plain-English messages.
- The next-step strip counts payslips only.

### Docs
- **Retention brief:** categories 2a (statement originals and raw output) and 3a (extracted
  transactions), and requirement R8a (DPIA, retention, key management before real
  statements).
- **ROADMAP:** 2B progress and the real-statement gate in Stage 4.
- **ARCHITECTURE** and **README:** the statement sections.

## 4. Differences from the plan

| # | What | Why |
|---|---|---|
| 1 | **`bank_accounts`, `bank_account_holders` and `document_reviews.bank_account_id` move to 2B-2** (migration `0018`), instead of `0017` | They're first used by confirmation. Creating them now would mean tables with no code, designed without the review that writes them. The fingerprint they need is already stored on each extraction run. |
| 2 | **The fingerprint lives on `extraction_runs.account_fingerprint`** | It's a property of what was extracted from one document version; 2B-2 copies it to the account the broker confirms. |
| 3 | The statement period fields are `statement_period_start` and `statement_period_end` | Kept apart from payslips' `pay_period_*`, which mean something different. |
| 4 | Payslip review refuses statements (409) | Without it, the payslip review would have accepted a statement and tried to save its balances as income. |
| 5 | Statement money parsing accepts `£-500.00`, `OD`, `DR` and `CR` | Found by the overdrawn-balance test: `£-500.00` wasn't parsed. |

## 5. Known limitations
1. **Synthetic only, and accuracy on real statements is unknown.** The parser is tuned to
   one generated layout family. Real statements need a table-capable provider and an
   evaluation corpus (plan, section 3.3); nothing here claims real-world accuracy.
2. **Statements can't be confirmed or checked yet:** that's 2B-2 and 2B-3.
3. **Holder names are printed on the statement but never extracted.** Holders will be the
   case's applicants, chosen by the broker in 2B-2.
4. **Sort codes are recognised only as six digits** (with separators); other national
   formats are out of scope (GBP only).

## 6. Files
| | Files |
|---|---|
| New | `src/meip/documents/statements.py`, `migrations/versions/0017_bank_statements.py`, `tests/unit/test_statements_unit.py`, `tests/unit/test_env_file.py`, `tests/integration/test_statements.py`, this report |
| Changed (backend) | `core/config.py` (env file, fingerprint key, gate), `documents/models.py`, `documents/processing.py`, `documents/pdfcheck.py`, `documents/service.py`, `documents/schemas.py`, `documents/review.py`, `documents/extraction.py` (shared row grouping), `api/routers/documents.py`, `audit/service.py` (count fields), `models.py`, `tests/__init__.py`, `tests/conftest.py` (upload type), `tests/synthetic_docs.py`, `tests/integration/test_cli.py`, `tests/integration/test_processing.py`, `tests/isolation/schema_policy.py`, `tests/unit/test_module_boundaries.py`, `tests/integration/test_documents.py`, `openapi.json` |
| Changed (frontend) | case page (statement badge and status, no review), `lib/failures.ts`, `lib/case-steps.ts`, `tests/ui.test.ts` |
| Docs | ROADMAP, ARCHITECTURE, README, retention brief, the plan (status) |

## 7. Proposed commits (not made: awaiting your approval)
Two commits, as the plan says, with 2B-0 on its own first:

```
Stage 2B-0: tests ignore a developer's backend/.env (MEIP_ENV_FILE)
Stage 2B-1: upload, classify and extract bank statements with provenance and keyed account fingerprints
```
