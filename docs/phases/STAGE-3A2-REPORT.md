# Stage 3, step 3-A2 — "Supported by" for Each Item: Report

Date: 2026-11-20 · Status: **Implemented and verified; awaiting review (not committed)** ·
Plan: [STAGE-3A2-SUPPORTED-BY-PLAN.md](STAGE-3A2-SUPPORTED-BY-PLAN.md) (decisions T1–T5
approved) · Baseline: `429b76b` (3-A)

**No migration, no new table or column, no stored state.** The financial domain, documents,
reviews, statements, accounts and both check rule sets are unchanged. Synthetic data only.

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17) | **1132 passed, 0 failed**; coverage 95% |
| Profile integration tests (2 new: declared and documentary kept apart, including an all-rejected payslip; a commitment) | **5 passed** |
| Bank check scenarios, extended with the support assertions | **4 passed** |
| Backend ruff (check and format) and `mypy --strict` | clean (134 source files) |
| Frontend vitest | **158 passed** (154 before, 4 new, including the no-count rule) |
| Frontend `tsc`, eslint, production build | clean; build succeeds (from a copy; see section 3, item 4) |
| Walkthrough (in-app browser, local stack, synthetic) | passed (section 4) |

## 2. What was built

### Derivation (`profile/support.py`, pure)
| Kind | Derived from (existing) |
|---|---|
| **Declared** | the item's current `declared` figures |
| **Documentary (broker-reviewed)** | `document_reviews.income_source_id`, plus the 3-A figure provenance. Per document: the measures saved from it, their period, which were corrected, who reviewed it and when. A document reviewed against the item with every figure rejected appears with `figures_saved: false` (T3) |
| **Corroborating** | `SALARY-CREDIT` findings with status `passed`, outcome `exact`, applicant holds the account. Each carries the account, holders, statement, row and the **fixed scope sentence**: "Confirms that an amount equal to this payslip's net pay was received on {date} into account ending {ending}. It does not confirm contractual basic salary, gross pay, the employer, or that the income will continue." |
| **Findings** | the findings whose `income_source_id` is the item: discrepancies (with acknowledgement), could not run (with reason), passed |
| **Approval** | profile-level `approval: {status: "not_approved"}` until 3-B (T1) |

- **What is never corroboration:** a near match, multiple possible credits, no credit, and
  an exact credit into an account the payslip's applicant doesn't hold all stay findings.
  Tested on real reviewed data.
- **Bank integrity and gap findings** belong to accounts and appear in no item's support
  (T4, tested). **Broker entries** are not a kind of support (T5).
- **Lists only:** no counts, totals or scores in the API. Lists reference figure ids,
  finding keys and document ids already in the profile.
- `rules` stays `profile/1` (T2). The fingerprint covers the additions.
- **The only new query:** the case's document reviews that name an income source.

### Screen
- Each item card has a **"Supported by"** block with five labelled sections, each with a
  one-line note on what that kind can and can't establish:
  - **declared values**, which select the figure;
  - **documentary lines** ("Payslip, May 2026: gross pay, basic pay, income tax, National
    Insurance, pension contribution, net pay (net pay corrected)"), linking to the document;
  - **corroboration lines**, with the scope sentence in full, linking to the statement row;
  - **findings**, with acknowledgement status and notes, and a link to the checks;
  - **approval:** "Not approved".
- An empty kind says **"None recorded"**.
- Wording lives in `lib/profile.ts`. A unit test checks that no wording counts evidence or
  uses strength words ("well evidenced", "strong", "weak", "reliable", "score").

## 3. Differences from the plan, and things found

| # | What | Why |
|---|---|---|
| 1 | Some **check titles** contain a count, for example "Year to date adds up — 3 payslips" | They're shown verbatim from `payslip-checks/1`, and the count names which payslips were compared, not a reliability claim. Rewording them would need a new rule-set version, so they're left as they are. "Supported by"'s own wording contains no counts |
| 2 | A could-not-run reason repeated "Could not run:" | Found in the walkthrough; the prefix is now removed from the reason |
| 3 | A payslip review must keep its pay date, so "all figures rejected" means all **money** figures | An existing review rule; the test was adjusted, not the rule |
| 4 | The first production build failed fetching the Google font (Inter); the rebuild succeeded | Transient network failure; no code change |
| 5 | One documentary period reads "28 Feb 2026" rather than a month | That payslip has only a period end, so a single date is shown. Correct as data |

## 4. Walkthrough (in-app browser, local stack, synthetic only)

| Case | Result |
|---|---|
| `SYN-BANK-001` | **Declared:** none recorded. **Documentary:** four payslips, May to August, each with its measures and month. **Corroborating:** only "Net pay for May 2026 received on 29 May 2026 (£2,805.84) into account ending 4821 (Joint current account, joint: Alex Example and Sam Synthetic)", with the scope sentence. **Findings:** June (more than one possible credit), July (received in an account Alex doesn't hold), August (near match), declared basic could not run, passed checks by title. **Approval:** not approved |
| `SYN-DEMO-002` | Documentary: "Payslip, 28 Feb 2026: … net pay (net pay corrected)". Corroborating: none recorded. Findings: the arithmetic discrepancy, and what couldn't run, each with its reason |
| `SYN-RECON-001` | Declared: "Basic pay £50,000.00, annual". Findings: the missing August payslip ("Pay period not covered — 1 Aug 2026 to 31 Aug 2026"), the acknowledged year-to-date discrepancy with its note, and the declared-basic difference |

## 5. Known limitations
1. **Corroboration exists only for salary credits.** Other kinds arrive with their document
   types (Phase 5).
2. **Evidence without figures** (contracts, letters) can't be attached to an item yet
   (assessment G3, Phase 5).
3. **Approval is always "Not approved"** until 3-B.

## 6. Files
| | Files |
|---|---|
| New (backend) | `profile/support.py` |
| Changed (backend) | `profile/schemas.py` (support and approval shapes), `profile/service.py` (one query; derivation wired in), `tests/integration/test_profile.py`, `tests/integration/test_bank_checks.py`, `openapi.json` |
| Changed (frontend) | profile page ("Supported by" block), `lib/profile.ts`, `lib/types.ts`, `tests/profile.test.ts` |
| Docs | this report; the 3-A2 plan and Stage 3 plan status; the financial domain assessment and roadmap (approved 2026-11-20) |

## 7. Proposed commit (not made: awaiting your approval)

```
Stage 3-A2: "Supported by" per item in five separate kinds; assessment and roadmap
```
