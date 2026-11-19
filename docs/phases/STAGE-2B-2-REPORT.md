# Stage 2-B, step 2B-2 — Confirming Bank Statements; Accounts and Holders: Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** ·
Plan: [STAGE-2B-BANK-STATEMENTS-PLAN.md](STAGE-2B-BANK-STATEMENTS-PLAN.md) (revision 2,
sections 4 and 6) · Baseline: `74e4430` (2B-1)

**Synthetic data only; statements and account routes are refused outside local and test.**

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17, run with a conflicting `.env` present) | **1087 passed**, 1 deselected, 0 failed; coverage 95% |
| New: integrity unit tests | **9 passed** |
| New: confirmation integration tests | **13 passed** (the 13th was added after the full run started and was run separately) |
| Backend ruff (check and format) and `mypy --strict` | clean (125 source files) |
| Frontend vitest | **142 passed** (122 before, 20 new) |
| Frontend `tsc`, eslint, production build | clean; build succeeds |
| Walkthrough (in-app browser, local stack, synthetic) | passed (section 4) |

## 2. What was built

### Confirmation is not validation
- **Validation:** `statements.integrity()` is pure and deterministic. It produces two
  results, each passed, discrepancy or not run:
  - **Totals:** opening + money in − money out = closing, within 1p; printed totals are also
    compared with the rows.
  - **Running balance:** each printed balance must follow from the previous one and the rows
    between, which handles end-of-day-only balances. It names the first row where the
    balance breaks and the amount unaccounted for.

  With no running balances printed, the row-by-row check is **not available**, never
  failed. Any unreadable row means neither check runs. 2B-3's `BANK-TOTALS` and
  `BANK-RUNNING` will reuse this.
- **Confirmation:** the broker:
  - accepts, corrects (with a reason) or rejects each summary field;
  - chooses the account;
  - for a new account, says who holds it.
- **Kept apart:**
  - the screen shows the checks *on the extracted values* before confirming, and *on the
    confirmed values* afterwards (corrections applied);
  - the confirm dialog warns when the balances don't add up;
  - **confirming never clears or hides a discrepancy**;
  - the audit event records the checks' outcome codes at confirmation
    (`integrity_totals`, `integrity_running`).
- **No financial figures** are created by confirming a statement (a test compares the
  observation count before and after).

### Accounts and holders (migration `0018`)
- **`bank_accounts`:**
  - the last 4 digits, the keyed fingerprint (unique per case, nullable) and an optional
    label;
  - never an account number or sort code;
  - RLS, `SELECT, INSERT` only.
- **`bank_account_holders`:**
  - which of the case's applicants hold the account; one is sole, two or more is
    **joint** (derived, never typed);
  - holders are added, never changed or removed.
- **`document_reviews`:** either a payslip review (income source and frequency) **or** a
  statement confirmation (`bank_account_id`), enforced by a CHECK. Field decisions reuse
  `field_reviews` unchanged.
- **The fingerprint suggests and protects; the broker confirms:**

| The statement's fingerprint | The extraction suggests | Refused (409 `account-conflict`) |
|---|---|---|
| matches an account on the case | that account (`existing`) | a new account for it (the error carries `existing_account_id`); any other account |
| matches none | a new account (`new`), listing fingerprinted same-ending accounts as **different**; same-ending accounts never verified by a fingerprint may be chosen | confirming it to an account with a different fingerprint ("different accounts, though both end the same") |
| none (masked number) | `unverified`; the broker chooses an account with the same ending, or a new one | an account with a different ending |

### API
- **`POST …/documents/{id}/review`:**
  - one route for both document types;
  - a statement takes `decisions` plus `bank_account_id` **or** `new_account: {holder_ids,
    label}`;
  - **422** for a statement sent with an income source or frequency, for a payslip sent
    without an income source or with account fields, and for missing, duplicate or invalid
    decisions (a correction needs a reason; the account ending is 4 digits; a statement
    saves no figures);
  - **409** on an account conflict or when the statement is already confirmed.
- **`GET …/documents/{id}/extraction`** for statements adds `integrity` (on the extracted
  values) and `account_match`.
- **`GET …/documents/{id}/review`** adds `bank_account` and `integrity` (on the confirmed
  values). `income_source_id` and `frequency` are now nullable.
- **New routes:**
  - `GET /cases/{id}/bank-accounts` (`document:read`);
  - `POST /cases/{id}/bank-accounts/{account_id}/holders` (`financial:write`; audited as
    `bank_account.holder_added`; 409 if already a holder, 422 if not an active applicant on
    the case).
  - Both are behind the documents switch and the statements gate, and are in the
    authorization matrix, the cross-tenant probes and the audit coverage.

### Frontend
- **Upload:** a document type choice (Payslip or Bank statement).
- **Case page:** statements show "Ready to confirm" or "Confirmed", with a **Confirm**
  button.
- **Statement confirmation screen**, `components/statement/StatementReview.tsx`:
  - the PDF, with boxes for every summary field and for each transaction row and its amount
    cells;
  - a **balance checks** panel (totals and running balance, plainly worded);
  - the summary decisions, reusing the payslip field rows; period start and end are labelled
    as such;
  - an **account picker** driven by the server's suggestion:
    - **same account:** that account, preselected;
    - **new:** a warning about same-ending accounts, and holder checkboxes;
    - **unverified:** a warning, with a choice among same-ending accounts;
  - the **transactions table**, verbatim:
    - the break row outlined in red;
    - unreadable rows shown with their raw text;
    - brought and carried forward rows in italics;
    - two-way linking with the PDF;
  - a confirm dialog with the integrity warning;
  - a read-only confirmed view with an **account card** (holders, joint, "Add a holder").
- **Shared logic:** `lib/statements.ts` (account choice, the request body, evidence,
  wording). `lib/decisions.ts` gains statement field kinds, signed money for balances, and
  `buildDecisions`.
- **Allowlist:** `GET cases/{id}/bank-accounts` and `POST cases/{id}/bank-accounts/{id}/holders`.

## 3. Differences from the plan, and things found

| # | What | Why |
|---|---|---|
| 1 | Integrity is built now (in `documents/statements.py`), not in 2B-3 | The confirm dialog and the audit event need it at confirmation (plan §6). 2B-3 reuses the same function, so there's one definition. |
| 2 | The audit records **codes** (`integrity_totals`, `integrity_running`, `fingerprint_status`, `account_created`, `holder_count`) | Proves what was shown without storing amounts. |
| 3 | A new account confirmed from an **unverified** statement has no fingerprint, and accounts are insert-only | A later statement that prints the full number still suggests "new". The broker may confirm it to that same-ending, never-verified account (the server allows it and the screen offers it), but the account stays unverified. Fingerprinted same-ending accounts are never offered: they are different accounts. Recorded as a limitation (section 5). |
| 4 | The 2B-1 test "statements can't be reviewed as payslips" was replaced | Statements can now be confirmed. A statement sent with an income source gets 422, "confirmed to a bank account". |
| 5 | Walkthrough fixes | Period start and end were both labelled "Statement period" (now suffixed, display only); the warning sentence was capitalised. |
| 6 | One audit test (invitation acceptance) failed once in a partial run and passed on rerun | It picks the "latest" event by timestamp, and two events can share one under load. Pre-existing and unrelated; noted, not changed. |

## 4. Walkthrough (in-app browser, local stack, synthetic only)
Case `SYN-RECON-001`; applicants Jordan Sample and (added for this) Riley Sample.

| Step | Result |
|---|---|
| Case page | The type choice is shown; three statements are "Ready to confirm", with "Confirm" buttons; the dollar statement still shows its refusal |
| June statement (two pages) | The balance checks both add up; 9 rows verbatim (page-break rows included); "A new account ending 4821"; holders: Jordan (filer, preselected) and Riley, ticked |
| Confirm | The dialog: "The statement's balances add up … To a new account ending 4821, held by Jordan Sample and Riley Sample". Afterwards: "Confirmed bank statement", an account card marked **Joint**, "no figures saved" |
| July statement | Suggested **"Same account as an earlier statement"**; confirmed; the account now lists 2 statements |
| August statement (a dropped row) | **"Totals: Doesn't add up … £3.80 unaccounted for"** and **"Running balance breaks at row 3 (05 Aug) … £3.80 unaccounted for: a row may be missing or misread"**; the row is outlined in red. That is the removed café payment |
| Confirm August | The dialog warned and said the discrepancy stays visible; after confirming, the checks *on the confirmed values* still say it doesn't add up |
| Audit (API) | Three `document.reviewed` events: `account_created` true then false and false; `fingerprint_status` new, matched, matched; integrity passed, passed, then **discrepancy/discrepancy** |

## 5. Known limitations
1. **An account first confirmed from a masked statement stays unverified.** Accounts are
   insert-only, so a later statement printing the full number can't attach its fingerprint,
   though the broker can confirm it to that account. Attaching the fingerprint later needs a
   deliberate, audited action; proposed for later if pilots need it.
2. **Holders can't be removed.** A mistaken account is replaced by confirming the next
   statement to a new account (as planned).
3. **Synthetic only;** real statement extraction accuracy is unknown (plan §3.3).
4. The `SALARY-CREDIT`, `BANK-GAP` and Checks-card parts are 2B-3.

## 6. Files
| | Files |
|---|---|
| New (backend) | `documents/statement_review.py`, `api/routers/bank_accounts.py`, `migrations/versions/0018_bank_accounts.py`, `tests/unit/test_statement_integrity.py`, `tests/integration/test_statement_review.py` |
| Changed (backend) | `documents/statements.py` (integrity), `documents/models.py`, `documents/review.py`, `documents/schemas.py`, `documents/service.py`, `reconciliation/service.py` (skips statement confirmations), `audit/service.py`, `models.py`, `app.py`, `tests/integration/test_statements.py`, `test_audit.py`, `test_authorization_matrix.py`, `tests/isolation/test_cross_tenant_api.py`, `schema_policy.py`, `openapi.json` |
| New (frontend) | `components/statement/StatementReview.tsx`, `lib/statements.ts`, `tests/statements.test.ts` |
| Changed (frontend) | case page (type choice, Confirm), review route (screen by type), `components/review/SavedReview.tsx`, `lib/decisions.ts`, `lib/types.ts`, `lib/allowlist.ts`, `lib/api.ts`, `lib/labels.ts` |
| Docs | this report; plan status; ROADMAP; ARCHITECTURE; README; retention brief (category 3a) |

## 7. Proposed commit (not made: awaiting your approval)

```
Stage 2B-2: confirm bank statements to accounts with holders; validation kept separate
```
