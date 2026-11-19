# Stage 2-B — Bank Statements: Plan (revision 2)

Status: **Revision 2 approved; decisions C1–C7 approved as recommended (2026-11-19). Built step
by step, each reviewed before the next: 2B-0 and 2B-1 implemented (see
[STAGE-2B-1-REPORT.md](STAGE-2B-1-REPORT.md)); 2B-2 implemented (see
[STAGE-2B-2-REPORT.md](STAGE-2B-2-REPORT.md)); 2B-3 not started.** · Date: 2026-11-19 · Roadmap: [Stage 2](../ROADMAP.md) · Baseline:
`85324f4` (Stage 2-A)

**Revision 2** applies your eight refinements. The three-step delivery and the scope
boundaries are unchanged.

| # | Refinement | Section |
|---|---|---|
| 1 | What is extracted, separated from what the preserved original holds; a privacy gate before any real statement | 2 |
| 2 | Salary matching with four distinct outcomes; explicit, versioned tolerances | 5.3 |
| 3 | Applicant, account holder, joint-account and matched-transaction relationships kept and explained | 4 |
| 4 | Accounts told apart without assuming the last four digits are unique | 4 |
| 5 | Field-level provenance for every statement value; synthetic validation separated from real-world accuracy | 3 |
| 6 | Row-by-row balance check "not available" (not failed) without running balances | 5.2 |
| 7 | Broker confirmation separated from mathematical validation | 6 |
| 8 | Scope boundaries retained | 9 |

The remaining decisions are in section 10.

**Goal:** bank statements as the second evidence source. The platform reads current-account
statements with full provenance and checks that each statement is mathematically complete.
It also checks whether **each reviewed payslip's net pay can be seen arriving** in an account
on the case, and explains which applicant, account and transaction that involves. The checks
decide nothing. Acknowledging is not resolving or approving, and nothing becomes a financial
figure.

**Synthetic data only. Bank statements are refused outside local and test environments in
2-B (section 2.3).**

## 1. Delivery (unchanged: three steps, each reviewed before the next)

| Step | Delivers |
|---|---|
| **2B-0** (small, separate) | The test-environment fix (section 8) |
| **2B-1** | Upload, classify and extract statements with field-level provenance; account fingerprints; the statement generator |
| **2B-2** | Broker confirmation: summary fields, the account and its holders |
| **2B-3** | The bank checks (`bank-checks/1`) and their place on the Checks card |

## 2. Privacy: structured data versus the preserved original (refinement 1)

### 2.1 Where each kind of statement data lives

| Data | Original PDF (blob, immutable) | Raw provider output (`extraction_runs.raw_output`) | Structured fields (database columns) |
|---|---|---|---|
| Full account number, sort code | **Yes**, as printed | **Yes**: the raw output is all the document's text | **No.** Only the last 4 digits, and a keyed fingerprint (section 4.1) |
| Account holder name(s), address | **Yes** | **Yes** | **No.** Never extracted. Holders are recorded as the case's **applicants**, chosen by the broker (section 4.2) |
| Bank name, branch | Yes | Yes | No |
| Period, opening and closing balance | Yes | Yes | Yes, with page and box |
| Every transaction (date, description, money in and out, balance) | Yes | Yes | **Yes**, descriptions verbatim, every value with page and box (section 3) |

**Consequences, stated plainly:**
- Not extracting a value into a column **does not remove it from the system**. The original
  is preserved, immutable and downloadable by anyone with `document:read` on the case. The
  raw provider output holds the same text.
- So for statements, data minimisation is achieved only in the **structured** layer.
  Protection of the original and the raw output rests on:
  - access control (RLS, permissions);
  - the original-download access log (R17);
  - storage hardening (R15, R16);
  - retention and erasure (D10).
  None of the last three is done yet.
- **The raw provider output is classified with the original** for retention and erasure:
  same sensitivity, same retention, erased together. Whether to also *redact* account
  identifiers from it is decision **C1** (section 10).

### 2.2 Added to the retention brief (in this stage's docs, not built)
- New data class **"bank statement originals and raw output"**: the highest sensitivity in
  the system (full account identifiers, and a transaction history that can reveal health,
  religion, gambling or relationships).
- New data class **"extracted transactions"**: structured, verbatim descriptions; separate
  retention period; erasable per case.
- **Account fingerprint key:** a secret; rotating it orphans existing fingerprints (section
  4.1). Key management goes on the Stage 4 list (Key Vault).

### 2.3 Gate before any real statement (fail-closed in code)
- **In 2-B, the API refuses `document_type=bank_statement` outside `local` and `test`**,
  whatever other settings say (404 `feature-disabled`, like documents today). Real
  statements stay impossible until a later, explicit change.
- **That change needs, at least:**
  - a **DPIA** covering bank statements;
  - the **retention decision (D10)** including both new data classes;
  - the **download access log (R17)**;
  - **storage identity and hardening (R15, R16)**;
  - **provider terms** for a real extraction provider;
  - **fingerprint key management**.
- These are recorded in the ROADMAP's Stage 4 gate; none is built here.

## 3. Statement extraction with field-level provenance (refinement 5)

**Schema `bank_statement/1.0.0`**, deterministic, from the layout provider's lines (the same
`LayoutProvider` interface as payslips).

### 3.1 Statement-level values (reuse `extracted_fields`, section `summary`)
Each value has its label and value boxes, page, page size and raw text, exactly like payslip
fields. The review screen, evidence boxes and field decisions work unchanged.

| Code | Value |
|---|---|
| `account_ending` | the last 4 digits as printed (text) |
| `period_start`, `period_end` | dates |
| `opening_balance`, `closing_balance` | signed money (overdrawn balances are negative) |
| `total_paid_in`, `total_paid_out` | money, when the statement prints them |

The full account number and sort code are read **only in memory** to compute the
fingerprint (section 4.1). They are never written to a column.

### 3.2 Transactions (one new table, `extracted_transactions`)
One row per printed row, per extraction run, insert-only, RLS, linked to its run within one
tenant and case.

| Column | |
|---|---|
| `row_number`, `kind` | order on the statement; `transaction`, `brought_forward`, `carried_forward` or `unparsed` |
| `posted_on` | date as printed (with the statement year applied), nullable for `unparsed` |
| `description` | the **original text, verbatim**: multi-line descriptions joined with line breaks, never cleaned or truncated beyond 500 characters (truncation flagged) |
| `paid_in_minor`, `paid_out_minor` | money in and money out, **kept as two columns** as printed (never merged into one signed amount in storage) |
| `balance_minor` | running balance when printed on that row, else null |
| `cells` | JSONB: for **each** value (date, each description line, paid in, paid out, balance), its raw text, page and box. Shape validated in code and by tests; boxes must lie inside the page |
| `page_number`, `row_box`, `page_width`, `page_height`, `unit` | the row's own provenance |

- **Unparsed rows are kept, never dropped:** a row the parser can't read is stored as
  `unparsed` with its raw text and box. The balance checks then say they can't be relied on
  (section 5.2) rather than silently passing.
- **The parser refuses rather than guesses:**
  - more than one account on a statement;
  - a foreign-currency column;
  - no recognisable transactions header.
  Each gives the extraction an outcome with a reason, and nothing is stored as
  transactions.

### 3.3 Synthetic validation is not real-world accuracy
- **What 2-B proves:** the pipeline (parser, storage, provenance, checks) produces exactly
  the ground truth that the **synthetic generator** defines. That includes multi-page tables,
  brought-forward rows, multi-line descriptions, end-of-day-only balances, a missing balance
  column, overdrawn balances and every fault.
- **What 2-B does not prove:** accuracy on real bank statements. The synthetic provider
  returns the generator's own layout. Real statements vary by bank and month, and often
  arrive scanned.
- **Real-world accuracy needs:**
  - a real, table-capable provider (for example Azure Document Intelligence);
  - an evaluation corpus;
  - per-field measurement (dates, amounts, descriptions, balances, row completeness).
  This is a separate, later activity behind the gate in 2.3. No accuracy claim is made in
  2-B, and the report will say so.

### 3.4 Classification and limits
- **Classifier `statement-rules/1`:** a statement marker ("statement of account", "opening
  balance" or "balance brought forward"), a transactions header, and no payslip markers.
  Payslip classification is unchanged; a statement is never a payslip and vice versa
  (tests).
- **Page limit: 20 for statements** (10 stays for payslips). Remaining decision C7.

### 3.5 Synthetic generator
`bank_statement_series()` produces monthly statements consistent with a `payslip_series()`:
the salary credit is the net pay, on the pay date or the next working day. It supports
**account variants**:
- two accounts that share their last 4 digits but have different sort codes;
- a joint account;
- an account with only a masked number printed.

It also has **layout variants** (multi-page, end-of-day balances, no balance column,
overdrawn) and these **faults**:
- `missing_credit`, `short_credit` (−£100), `near_credit` (−£1.50);
- `duplicate_amount_credit` (two credits of the net pay in the window);
- `late_credit` (+4 days: still exact), `outside_window` (+7 days);
- `missing_row` (dropped from the PDF), `unparsed_row`, `statement_gap`.

## 4. Accounts, holders and applicants (refinements 3 and 4)

### 4.1 Telling accounts apart without storing them
- At extraction, when the statement prints a full sort code and account number, the
  service computes an **account fingerprint**: an HMAC-SHA256 of the normalised sort code
  and account number under a **server-side secret key**.
  - **Keyed, not a plain hash:** a plain hash of 14 digits could be reversed by trying
    every account number.
  - Only the fingerprint and the last 4 digits are stored.
- **The last 4 digits are never used to decide that two statements are the same account.**
  They're a display label only.
- **The broker confirms the account at review (section 4.2).** The fingerprint only
  suggests and protects:
  - **matching fingerprint on the case:** "same account as the June statement", offered as
    the default;
  - **same last 4 but a different fingerprint:** shown as a **different account**, never
    merged; the screen says why ("ends 4821 like account A, but the sort code or number
    differs");
  - **choosing an account whose fingerprint differs from this statement's:** refused
    (409);
  - **no fingerprint** (masked number printed): the broker chooses or creates the account,
    with a note that it couldn't be verified from the document. `BANK-GAP` then explains
    that continuity relies on the broker's assignment.
- Remaining decision C2 (keyed fingerprint and its key).

### 4.2 Structure (minimal)
| Table | Holds |
|---|---|
| `bank_accounts` (new) | per case: `id`, `account_ending` (4 digits), `fingerprint` (nullable; unique per case when present), a broker label (optional, e.g. "Current account"), created by and when. Insert-only |
| `bank_account_holders` (new) | `(account_id, applicant_id)`: the applicants who hold the account, as stated by the broker at confirmation; one row is a sole account, two or more a **joint account** (derived, never typed in). Insert-only |
| `document_reviews` (changed) | gains `bank_account_id`. A CHECK makes a review **either** a payslip review (income source and frequency set, no account) **or** a statement review (account set, no income source or frequency). Field decisions keep using `field_reviews` unchanged |

- The **filing applicant** (who the statement was uploaded under, `documents.applicant_id`)
  stays separate from the **account holders**. A joint statement can be filed under either
  holder.
- **Holders are fixed when the account is first confirmed.** Adding a holder later is a
  broker action on the account, audited. Removing one is out of scope; a mistaken account
  is replaced by confirming the next statement to a new account.

### 4.3 Case-level matching, with the relationship explained
Salary matching (section 5.3) searches **every confirmed statement on the case**. Every
`SALARY-CREDIT` finding records and shows:
- the **payslip applicant** (who the payslip is for);
- the **account**: its ending, the label, **sole or joint**, and the holders;
- the **filing applicant** of the statement;
- the **matched transaction:** date, verbatim description, amount, row, and the statement it
  is on.

**The explanation is part of the message**, for example:
- *"Received in account ending 4821 (joint: Alex Example and Sam Synthetic), statement filed
  under Sam Synthetic."*
- *"Received in account ending 7710, held only by Sam Synthetic. Alex Example (the payslip's
  applicant) isn't a recorded holder of this account."*

How the second case (money into an account the payslip's applicant doesn't hold) is
classed is decision C4.

## 5. The checks (rule set `bank-checks/1`, separate from `payslip-checks/1`)

These are pure functions as in 2-A, computed on request, with the same fingerprinting of
findings, and statuses `passed`, `discrepancy` or `not_run` (always with a reason). Inputs
are **confirmed** statements (summary fields as accepted or corrected, the account and its
holders, the extracted rows) and reviewed payslips.

### 5.1 Versioned configuration

```
RULESET = bank-checks/1
  balance_allowance_pence       = 1
  match_window_days_before      = 3      # initial product assumption
  match_window_days_after       = 5      # initial product assumption
  exact_match_tolerance_pence   = 0      # exact means to the penny
  near_match_band_pct           = 10.0   # initial product assumption (decision C3)
```

Changing any value means a new version (`bank-checks/2`). Each finding returns the
parameters it used.

### 5.2 Statement integrity (refinement 6: two checks, not one)

| Code | Check | Discrepancy | Not run (with the reason) |
|---|---|---|---|
| `BANK-TOTALS` | opening balance + money in − money out = closing balance (within 1p); and, when printed, totals in and out match the rows | names the difference | opening or closing balance missing or rejected; or any `unparsed` row ("row 23 couldn't be read, so the totals can't be relied on") |
| `BANK-RUNNING` | each printed running balance follows from the previous printed balance and the rows between them. **End-of-day-only balances are handled:** rows without a balance are summed until the next printed one | names the first row where the balance stops following, and the amount unaccounted for (a likely missing or misread row) | **the statement prints no running balances:** "this statement doesn't show running balances, so the row-by-row check isn't available; the totals are checked by `BANK-TOTALS`". Also not run with any `unparsed` row. **Never reported as failed for a missing balance column** |
| `BANK-GAP` | consecutive statements **for the same confirmed account** have no uncovered days between their printed periods | the uncovered days, citing both statements | a statement without a period; continuity based on a broker assignment without a fingerprint is still checked, and the message says so |

### 5.3 `SALARY-CREDIT`: four distinct outcomes (refinement 2)
For each reviewed payslip with net pay and a pay date, the **window** runs from 3 days
before to 5 days after the pay date. The check looks at credits (money in) in that window
on any confirmed statement on the case.

Candidates are assigned across **all** payslips together, so one credit can't confirm two
payslips:

| Outcome | Rule | Status | Shown as |
|---|---|---|---|
| **Exact match** | exactly one credit in the window equals the net pay to the penny, and that credit isn't also the only candidate for another payslip | **passed** (but see C4 for an account the applicant doesn't hold) | "Received: £2,805.84 on 30 Jun, 'SERIES EMPLOYER LTD SALARY', account ending 4821 (joint: …)" |
| **Multiple possible matches** | more than one credit in the window equals the net pay, or one credit is the only exact candidate for two payslips | **discrepancy** (never passed) | "2 credits of £2,805.84 between 27 Jun and 5 Jul (28 Jun, 'TRANSFER FROM J SAMPLE'; 30 Jun, 'SERIES EMPLOYER LTD'): it can't be told which, if either, is this salary." Both cited |
| **Near match** | no exact credit, but at least one within the **near band** (±10 % of net pay) | **discrepancy, labelled "near match: not confirmed"** | "No credit of exactly £2,805.84. Closest: £2,804.34 on 30 Jun, £1.50 less." The closest (up to 3) cited. **A near match is never treated as received** |
| **No plausible match** | the window is fully covered by confirmed statements of at least one account, with no credit within the near band | **discrepancy** | "No credit within 10 % of £2,805.84 between 27 Jun and 5 Jul in accounts ending 4821, 7710." The accounts searched are listed |
| *(not run)* | no confirmed statement covers the whole window; or the payslip has no reviewed net pay or pay date; or a covering statement has `unparsed` rows in the window | not_run | e.g. "No confirmed statement covers 27 Jun to 5 Jul." |

Further rules:
- **Matching never uses the description** (B9, retained). The description is always shown
  verbatim so the broker can judge.
- **No tolerance is applied silently:** the exact tolerance is 0p and the near band is a
  named, versioned parameter that the message quotes.

## 6. Confirmation is not validation (refinement 7)

| | What it is | Recorded as |
|---|---|---|
| **Broker confirmation** (2B-2) | The broker attests that the **extracted information matches the document**: each summary field accepted, corrected (with a reason) or rejected; **which account** it is; **who holds it** | the statement review: field decisions, account and holders; audited (`document.reviewed`) |
| **Mathematical validation** (2B-3) | Whether the statement's numbers are internally consistent and complete | `BANK-TOTALS`, `BANK-RUNNING`, `BANK-GAP` findings, computed on request |

- **A broker can confirm a statement whose balances don't add up** (B3, retained). The
  confirm dialog shows the current integrity result first: *"Balances don't add up: the
  running balance breaks after 14 Jul (£120.00 unaccounted for). Confirming records that the
  extracted information matches the document; the discrepancy stays visible in the
  checks."*
- **Confirmation never clears, hides or changes a validation finding.** The discrepancy
  stays on the Checks card until the inputs change, and can only be acknowledged, as in
  2-A.
- **The audit event records the integrity status shown at confirmation** (codes only, for
  example `totals: passed, running: discrepancy`), so it's provable that the broker confirmed
  knowingly.

## 7. API and screens

**Upload:**
- `document_type=payslip|bank_statement` (statements in local and test only, section 2.3).

**Statement review:**
- `POST …/documents/{id}/review` takes a statement body: `decisions[]` for the summary fields,
  plus **either** `bank_account_id` (an existing account) **or** `new_account` (`holder_ids`,
  at least one applicant on the case, and an optional label).
- It is all or nothing and once only.
- **No figures are saved** (no financial observations).

**Read routes:**
- `GET …/extraction` for a statement adds `transactions[]`, each with its `cells`
  provenance.
- `GET /cases/{id}/bank-accounts` lists the accounts with holders and statements.

**Holders:**
- `POST /cases/{id}/bank-accounts/{account_id}/holders` adds a holder; audited.

**Checks:**
- `GET …/checks` returns both rule sets: `ruleset_versions` replaces `ruleset_version`.
  This is a small, documented API change; the frontend is updated with it.
- Each finding gains `ruleset_version` and, for `SALARY-CREDIT`, a structured
  `relationship` (payslip applicant, account ending, sole or joint, holders, filing
  applicant, matched row) and `match_outcome` (`exact`, `multiple`, `near`, `none`).

**Screens:**
- **upload:** a type choice;
- **statement review:**
  - the PDF with a box per cell;
  - the summary decisions;
  - account confirmation (suggested account, or a new one with holders chosen from the
    case's applicants);
  - the integrity banner;
  - the transactions table, verbatim, with two-way box linking;
- **saved view:** read-only;
- **Checks card:** a *Bank statements* section; chips open the statement at the row
  (`?row=<n>`).

**Allowlist additions:**
- `GET cases/{id}/bank-accounts`;
- `POST cases/{id}/bank-accounts/{id}/holders`.

## 8. Test-environment fix (2B-0, minimal)
- **Problem (2-A report):** the settings read `backend/.env`. A developer following the
  README has one, and its values leak into tests that build their own environments. That
  caused 24 false failures.
- **Fix:** `env_file` reads `MEIP_ENV_FILE` (default `.env`, so nothing changes for anyone
  else). `tests/conftest.py` sets `MEIP_ENV_FILE` to empty before anything is imported, so
  the test process and its subprocesses ignore `.env`.
- **Test:** the suite passes with a `.env` that sets conflicting values.
- About ten lines, committed on its own before 2B-1.

## 9. Scope (refinement 8: retained)
- A **separate rule set**, `bank-checks/1`. `payslip-checks/1` and its acknowledgements are
  untouched.
- **No financial observations from transactions:** statements are evidence only. Which
  figure counts is Stage 3.
- No spending categorisation, recurring-commitment detection or affordability.
- No Open Banking or CSV imports.
- No real or scanned statements, and no real provider (section 3.3).
- No matching of other income (benefits, rent) or declared commitments.
- No per-transaction broker decisions.

## 10. Decisions (all approved as recommended, 2026-11-19)

| # | Decision | Options | Approved |
|---|---|---|---|
| **C1** | Raw provider output for statements (it contains the full account number and sort code) | (a) keep in full, classed and erased with the original; (b) redact account numbers and sort codes before storing | **(a).** The original holds the same data and stays anyway. Redaction depends on recognising every format and would make the stored output differ from what the provider returned (reproducibility). Revisit with a real provider |
| **C2** | Account fingerprint | (a) HMAC-SHA256 with a server-side key (`MEIP_ACCOUNT_FINGERPRINT_KEY`, required when statements are enabled; a generated local key in development); (b) no fingerprint, broker assignment only | **(a)**, with the broker still confirming. It protects against merging two accounts that share their last 4 digits, without storing account numbers. Key rotation and Key Vault go on the Stage 4 list |
| **C3** | Near-match band | e.g. ±5 %, **±10 %**, or a fixed £ amount | **±10 % of net pay**, a versioned product assumption. Wide enough to surface plausible candidates (rounding, a deduction change), never treated as received |
| **C4** | Exact match in an account the **payslip's applicant doesn't hold** (for example, a partner's sole account) | (a) passed, with the relationship explained; (b) **discrepancy** ("received in an account Alex Example doesn't hold"), acknowledgeable | **(b).** The money arrived, but not to the applicant. A broker should look and note why, rather than see a pass |
| **C5** | Multiple possible matches | (a) **discrepancy** (unconfirmed, needs judgement); (b) passed (the amount was seen) | **(a).** Consistent with "never silently confirm" |
| **C6** | Bank statements outside local and test in 2-B | (a) **refused in code**; (b) allowed with documents enabled | **(a).** Real statements need the gate in section 2.3 first |
| **C7** | Page limit for statements | 10 (as payslips); **20** | **20 for statements only;** the PDF check stays time-limited in its own process |

Already agreed in principle and kept as written:
- bank statements as the source (B1);
- confirming with a balance discrepancy (B3);
- no observations (B4);
- the 3 + 5 day window (B5);
- a separate rule set (B6);
- case-level matching (B7);
- descriptions not used for matching (B9);
- one account per statement (B10), GBP only (B11), periods as printed (B12).

B2 (last 4 digits only) is replaced by section 4.1.

## 11. Tests and verification
- **2B-0:** the suite passes with a conflicting `.env` present.
- **2B-1:**
  - extraction ground truth for every layout, account variant and fault, **cell by cell**
    (text, value, page, box);
  - unparsed rows kept;
  - refusals (multiple accounts, foreign currency, no header);
  - the classifier both ways;
  - fingerprints: the same account gives the same fingerprint; same last 4 with a
    different sort code gives a different fingerprint; no plain account number or sort code
    in any column (a test scans every row of every statement table);
  - statements refused outside local and test;
  - the isolation catalog (the new tables);
  - log safety (no descriptions, amounts or account digits in logs).
- **2B-2:**
  - confirmation with each account path (suggested, new, a mismatched fingerprint refused,
    no fingerprint);
  - holders limited to the case's applicants;
  - confirmation with a balance discrepancy allowed, with the integrity status in the audit
    event;
  - a statement review never creates financial observations;
  - authorization and isolation.
- **2B-3:**
  - every outcome of every check, including end-of-day balances, a missing balance column
    (not run, not failed), the four matching outcomes, one credit eligible for two payslips,
    the window edges (−3 and +5 days), and cross-applicant and joint relationships (C4);
  - fingerprints of findings;
  - acknowledging changes nothing else;
  - another firm gets 404.
- **Gates every step:**
  - backend: the full suite, ruff, `mypy --strict`;
  - frontend: vitest, `tsc`, eslint and a production build.
- **Walkthrough after 2B-3 (synthetic):** two applicants. Alex's salary goes into a joint
  account in May and June, and into Sam's sole account in July. Alongside:
  - a near credit;
  - a duplicate-amount credit;
  - a missing row;
  - a statement without running balances;
  - a missing statement;
  - two accounts that share their last 4 digits.
  Each expected finding, relationship explanation and evidence link is checked. Confirming
  a broken statement keeps the discrepancy visible.

## 12. Deliverables
- **2B-0:** the env-file fix and its test.
- **2B-1:**
  - document type and classifier;
  - statement extraction;
  - migration `0017` (`extracted_transactions`, `bank_accounts`, `bank_account_holders`,
    `document_reviews.bank_account_id` and its CHECK);
  - the fingerprint;
  - the generator;
  - tests.
- **2B-2:** statement confirmation (API and screen), account and holder handling, tests.
- **2B-3:** `bank-checks/1`, the checks API change, the Checks card section, row links, tests,
  the walkthrough.
- **Docs:**
  - a report per step;
  - ROADMAP (the Stage 4 gate additions in 2.3);
  - ARCHITECTURE;
  - README;
  - the retention brief (the two new data classes and the fingerprint key).
