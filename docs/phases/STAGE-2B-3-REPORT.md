# Stage 2-B, step 2B-3 — The Bank Checks (`bank-checks/1`): Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** ·
Plan: [STAGE-2B-BANK-STATEMENTS-PLAN.md](STAGE-2B-BANK-STATEMENTS-PLAN.md) (revision 2,
sections 4.3, 5 and 7) · Baseline: `8362cd1` (2B-2)

**Synthetic data only; bank statements are refused outside local and test.** With 2B-3,
Stage 2-B (and so Stage 2, the reconciliation engine) is complete.

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17) | **1120 passed, 1 failed** (the module-boundary rule, item 9 below: fixed, and its 12 tests rerun and passed); coverage 95% |
| Rerun after the last wording fixes: bank unit and integration tests, payslip checks, schema snapshot | **69 passed** |
| New: bank rule unit tests (`tests/unit/test_bank_checks.py`) | **29 passed** |
| New: bank check integration tests (`tests/integration/test_bank_checks.py`) | **4 passed** |
| Backend ruff (check and format) and `mypy --strict` | clean (128 source files) |
| Frontend vitest | **146 passed** (142 before, 4 new) |
| Frontend `tsc`, eslint, production build | clean; build succeeds |
| Walkthrough (in-app browser and API, local stack, synthetic) | passed (section 4) |

## 2. What was built

### The rule set (`reconciliation/bank_rules.py`)
Pure functions over plain data, versioned separately from `payslip-checks/1`, computed in the
request, nothing stored. The parameters, returned with every response, are:

```
bank-checks/1
  balance_allowance_pence      = 1      (the same constant as the confirmation screen)
  match_window_days_before     = 3      initial product assumption
  match_window_days_after      = 5      initial product assumption
  exact_match_tolerance_pence  = 0      exact means to the penny
  near_match_band_pct          = 10.0   initial product assumption (decision C3)
  near_matches_shown           = 3
```

| Code | One finding per | Passed | Discrepancy | Not run |
|---|---|---|---|---|
| `BANK-TOTALS` | confirmed statement | opening + in − out = closing; printed totals match the rows | the difference, citing the summary values | opening or closing missing or rejected; any unreadable row |
| `BANK-RUNNING` | confirmed statement | every printed balance follows from the rows before it (end-of-day balances handled) | the first row where it breaks and the amount, citing that row | **no running balances printed: "isn't available", never failed**; any unreadable row |
| `BANK-GAP` | consecutive pair of statements of one account | no uncovered days | the uncovered days and both statements | a statement without a confirmed period |
| `SALARY-CREDIT` | reviewed payslip | an **exact** credit, unique to this payslip, in an account the payslip's applicant holds | **exact** into an account the applicant doesn't hold (C4) · **multiple** (C5) · **near**, never treated as received · **none**, listing the accounts searched | no reviewed net pay or pay date; no confirmed statement covers the window; an unreadable row in the window |

- **Integrity is one definition.** `BANK-TOTALS` and `BANK-RUNNING` call
  `statements.integrity`, the function the confirmation screen and the audit event use since
  2B-2.
- **Continuity on the broker's word is said.** When either statement in a pair had no
  fingerprint (a masked account number), `BANK-GAP` adds: "these statements are linked to
  the account by your confirmation: the account couldn't be verified from the documents."
- **One credit can't confirm two payslips.** Candidates are assigned across all payslips
  together. A credit that is the only exact candidate for two payslips makes both "multiple".
- **Matching never uses the description** (a unit test changes only the description and gets
  the same outcome). Descriptions are shown verbatim in messages and evidence.
- **The relationship is part of the finding**, as plan section 4.3 required:
  - the message names the account (ending, label), whether it's sole or joint with its
    holders, and who the statement was filed under;
  - a structured `relationship` gives the payslip applicant, account, joint, holders,
    whether the applicant holds the account, the filing applicant, and the statement and
    row.
- **Keys.** SHA-256 over the rule set, rule, parameters, subject, context and every cited
  input. For `SALARY-CREDIT` the context includes the matched account's holders and the
  statements searched. Adding a holder, or a statement covering the window, gives a new
  finding, so an acknowledgement never carries over to changed inputs.
- **No bank findings without a confirmed statement.** A case with only payslips sees exactly
  what it saw in 2-A, not a "not run" salary finding per payslip.

### API (a small, documented change)
- **`GET /cases/{id}/checks`** returns both rule sets:
  - `ruleset_versions: ["payslip-checks/1", "bank-checks/1"]` replaces `ruleset_version`;
  - `parameters` is keyed by version;
  - each finding gains `ruleset_version`, and bank findings gain `bank_account_id`,
    `document_id`, `match_outcome` and `relationship`;
  - `income_source_id` and `applicant_id` are now nullable (statement-level findings have
    none).
- **Evidence** gains two kinds:
  - `statement`: a confirmed summary value, by field ordinal;
  - `transaction`: a printed row, by `row_number`, with the description verbatim in
    `value_text`.
- **Acknowledgements** work unchanged for bank findings and record the finding's own rule set
  (`check_acknowledgements.ruleset_version = 'bank-checks/1'`, and in the audit event, with
  `bank_account_id`). Acknowledging still changes nothing else (tested by counting figures,
  reviews and holders).
- `openapi.json` regenerated.

### Frontend
- **Checks card:** a **Payslips** section and a **Bank statements** section, each with its
  rule-set version (tooltip: what it checks), its own counts, and the same open,
  acknowledged, could-not-run and passed grouping. The overall count spans both.
- **Salary outcome badges:** "Near match · not confirmed", "More than one possible credit",
  "No credit found" (exact needs none; the status says it).
- **Evidence chips** link to the statement:
  - `?row=<n>` opens it with that row selected and its boxes highlighted;
  - `?field=<ordinal>` selects a summary value.
  
  Statement chips read "June 2026 statement · Credit, 30 Jun 2026"; period values say
  "(start)" or "(end)".
- Shared logic in `lib/checks.ts` (`ofRuleset`, `outcomeLabel`, evidence links) and
  `lib/statements.ts` (`linkedOrdinal`).

## 3. Differences from the plan, and things found

| # | What | Why |
|---|---|---|
| 1 | No bank findings at all for a case without a confirmed statement | Otherwise every payslip-only case (and every case outside local and test, where statements are refused) would show a "not run" salary finding per payslip. Noise, not information |
| 2 | Coverage is required for every salary outcome, including exact | An exact credit with part of the window uncovered might have a twin on the uncovered days (that would be "multiple"). Stricter, and consistent with "never silently confirm" |
| 3 | The window counts as covered by one account's statements together (for example June and July) | A window that crosses a month end is the normal case for month-end pay; requiring one statement to cover it would make most salaries "not run" |
| 4 | An unreadable row makes the salary check not run only when it's in, or may be in, the window | An unreadable row without a date can't be placed, so it counts as possibly in the window |
| 5 | `BANK-GAP` gives no finding for an account with a single statement | Nothing to compare. A missing earlier or later month shows as a salary "not run" instead |
| 6 | Wording found in the walkthrough | A labelled joint account read "(Joint current account) (joint: …)": now "(Joint current account; joint: …)". Period chips both read "Statement period": now "(start)" and "(end)" |
| 7 | A test scenario was wrong, not the code | Removing June's statement makes May's salary "not run", because its window runs to 3 June. The integration test now removes August instead |
| 8 | Three 2-A integration tests failed once when run together with the new ones (a payslip still "ready" after the worker loop) and passed on rerun | The same worker timing flakiness under load noted in earlier reports; it happens in upload setup, before any checks code. Not changed |
| 9 | The module-boundary test refused `reconciliation` importing `applicants` | The bank checks read applicants' names for the relationship messages. Read-only, like `documents` and `financial` already do, and no cycle (nothing imports `reconciliation`). The allowlist now includes `applicants`, with a comment |

## 4. Walkthrough (local stack, synthetic only)
Case `SYN-BANK-001`: Alex Example (payslips, May to August, all reviewed) and Sam Synthetic.

| Account | Statements | Set up with |
|---|---|---|
| Joint current account, Alex and Sam, ending **4821**, sort code 12-34-56 | May, June, July | May salary arrives; June's arrives with a second credit of the same amount 2 days earlier; a row missing from July |
| Sam's current account, sole, **also ending 4821**, sort code 60-71-88, **no running balances** | July, August, September, November | July salary arrives here; August's arrives £1.50 short; October missing |

| Check | Result |
|---|---|
| Second account with the same ending | Suggested as a **new** account at confirmation (different fingerprint), never merged |
| `BANK-TOTALS` / `BANK-RUNNING` | July joint: both discrepancies, "breaks at row 3 (05 Jul) … £3.80 unaccounted for". Sam's four: totals passed, running "isn't available" (not run). The rest passed |
| `BANK-GAP` | Sam's account: "No statement covers 1 Oct 2026 to 31 Oct 2026 (31 days)". Four other pairs passed |
| May salary | **Passed**: "in account ending 4821 (joint: Alex Example and Sam Synthetic), statement filed under Alex Example" |
| June salary | **Multiple**: both credits listed verbatim, 'TRANSFER FROM J SAMPLE' and 'SERIES EMPLOYER LTD / SALARY' |
| July salary | **Exact, but a discrepancy (C4)**: "held only by Sam Synthetic … Alex Example (the payslip's applicant) isn't a recorded holder of this account" |
| August salary | **Near, not confirmed**: "£2,804.34 on 31 Aug 2026 … £1.50 less. A near match (within 10.0%) is not treated as received" |
| Checks card | Payslips (8 passed, 1 not run) and Bank statements (6 discrepancies, 13 passed, 4 not run) sections; outcome badges; evidence chips |
| Evidence link | "June 2026 statement · Credit, 30 Jun 2026" opened the confirmed June statement with row 8 (the salary) selected |

## 5. Known limitations
1. **Synthetic only.** Real statements are refused until the gate in plan section 2.3; real
   extraction accuracy is unknown.
2. **Two accounts with the same ending are told apart in messages only by their label.**
   Every finding carries `bank_account_id`; a broker who doesn't label accounts sees
   "account ending 4821" twice. Labelling is optional at confirmation.
3. **Salary credits only.** No other income (benefits, rent), no commitments, no
   categorisation (scope, plan section 9).
4. **The window and the near band are product assumptions** (versioned), not lender rules.
5. Carried over from 2B-2: an account first confirmed from a masked statement stays
   unverified; holders can't be removed.

## 6. Files
| | Files |
|---|---|
| New (backend) | `reconciliation/bank_rules.py`, `tests/unit/test_bank_checks.py`, `tests/integration/test_bank_checks.py` |
| Changed (backend) | `reconciliation/service.py` (loads confirmed statements, accounts and holders; both rule sets; evidence), `reconciliation/schemas.py`, `api/routers/checks.py` (description), `tests/integration/test_checks.py` (response shape), `tests/unit/test_module_boundaries.py` (allowlist), `openapi.json` |
| Changed (frontend) | `components/ChecksCard.tsx`, `components/statement/StatementReview.tsx` (`?row=` and `?field=` links), `lib/checks.ts`, `lib/statements.ts`, `lib/types.ts`, `tests/checks.test.ts` |
| Docs | this report; plan status; ROADMAP (Stage 2 done); ARCHITECTURE; README |

## 7. Proposed commit (not made: awaiting your approval)

```
Stage 2B-3: bank checks (bank-checks/1) with salary matching and relationships
```
