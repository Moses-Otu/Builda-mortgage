# Stage 3, step 3-A2 — "Supported by" for Each Item: Plan

Status: **Approved 2026-11-20; decisions T1–T5 approved as recommended. Implemented (see [STAGE-3A2-REPORT.md](STAGE-3A2-REPORT.md)).** · Date: 2026-11-20 ·
Source: [financial domain assessment, section 3](FINANCIAL-DOMAIN-ASSESSMENT.md) (decision
A1, refined) · Parent: [Stage 3 plan](STAGE-3-BROKER-EXPERIENCE-PLAN.md) · Baseline:
`429b76b` (3-A)

**Goal:** each financial item in the profile states what supports it in five separate
kinds, accurately and without implying that more evidence means more reliable, before 3-B
freezes the profile.

**Constraint (your instruction):** this is not a reason to redesign the evidence model.
- **No migration, no new table, no new column, no new stored state.**
- **No change to observations, reviews, documents, accounts or the check rule sets.**
- Everything is **derived** in the existing profile builder from relationships that already
  exist. The only changes are new read-only output shapes and the screen.

## 1. Each kind, and the existing relationship it comes from

| Kind | Derived from (existing) | Per item, it lists |
|---|---|---|
| **1. Declared by the applicant** | `financial_observations` with `source_kind = 'declared'`, current, for the item (already loaded by 3-A) | measure, value, frequency or as-of date, who recorded it and when. *Never verification* |
| **2. Documentary evidence (extracted, broker-reviewed)** | `document_reviews.income_source_id`: the broker bound the document to this item at review (2-D). Figures and corrections from 3-A's provenance | per reviewed document: file name and type; **the measures it carries** (exactly those saved as current figures from it); **the period** those figures cover; which measures were **corrected**; who reviewed it and when. A document reviewed against the item whose figures were **all rejected** is listed as "reviewed; no figures saved" (decision **T3**) |
| **3. Corroborating evidence** | `SALARY-CREDIT` findings (2B-3) with **status `passed`**. Those are exact matches, unique to the payslip, into an account the payslip's applicant holds. Their structured `relationship` gives the account, holders, statement and row; already in the profile's checks | per corroboration: which payslip (its month), the credit's date and amount, the account (ending, label, sole or joint, holders), the statement and row, and **the scope sentence** (section 2) |
| **4. Reconciliation findings** | findings whose `income_source_id` is the item, from both rule sets (already in the profile) | **discrepancies**, each acknowledged (with the note, who, when) or not; **could not run**, each with its reason; **passed**, by title. Every finding links to its key |
| **5. Broker approval status** | none until 3-B; then the case's current approval | until 3-B: **"Not approved"**. From 3-B: approved (version, by whom, when) or "superseded on reopening" |

**What doesn't count as corroboration:**
- a near match;
- multiple possible credits;
- no credit found;
- an exact credit into an account the payslip's applicant doesn't hold (decision C4).

All of these stay **findings** (kind 4). Corroboration is limited to what the bank checks
already classify as `passed`.

**Commitments and expenditure:** they get the same five kinds. Today they can only have
declared and broker-entered values (no document type evidences them yet), so kinds 2 and 3
show "None recorded". The structure is ready for credit reports in Phase 5, without change.

**Bank statement integrity and gaps** (`BANK-TOTALS`, `BANK-RUNNING`, `BANK-GAP`) are about
accounts, not items. They stay in the profile's checks and on the bank account rows, not in
any item's "Supported by" (decision **T4**).

**Broker entries** (`source_kind = 'broker_entry'`) are none of the five kinds. They keep
their 3-A origin mark (B) on the figure. "Supported by" lists evidence about the item, and a
broker's own entry isn't evidence (decision **T5**).

## 2. Stating scope, not quantity

- **No counts, totals, scores or strength labels** anywhere in "Supported by": not "3
  documents", not "well supported", nothing that ranks items. The API returns **lists**, not
  numbers.
- **Each documentary entry states its own scope:** the measures it carries and the period it
  covers, for example "Payslip, May 2026: gross pay, basic pay, income tax, National
  Insurance, pension contribution, net pay (net pay corrected)".
- **Each corroboration carries a fixed scope sentence,** versioned with the profile rule:

  > Confirms that an amount equal to this payslip's net pay was received on {date} into
  > account ending {ending}. It does not confirm contractual basic salary, gross pay, the
  > employer, or that the income will continue.

- **An empty kind says "None recorded"**, never a judgement such as "unsupported" or
  "weak".
- **Order is by date or measure, never by quantity.**

## 3. Shape (additions to `GET …/profile` only)

```
ProfileOut
  + approval: {status: "not_approved"}            # profile-level (decision T1); 3-B adds
                                                  #   approved / superseded with details
  + support: [ItemSupport]                        # one per item, in item order

ItemSupport
  item_kind, item_id
  declared:      [figure ids]                     # the figures are already in `figures`
  documentary:   [{document_id, document_type, file_name, period_start, period_end,
                   measures[], corrected_measures[], figures_saved: bool,
                   reviewed_by, reviewed_at}]
  corroborating: [{kind: "salary_credit", finding_key, payslip_document_id,
                   credit_date, amount_minor, account_id, account_ending,
                   account_label, joint, holder_ids, statement_document_id,
                   row_number, scope}]
  findings:      {discrepancies: [{key, rule_code, title, acknowledged,
                                   acknowledgement}],
                  not_run: [{key, rule_code, title, reason}],
                  passed: [{key, rule_code, title}]}
```

- **Lists reference what the profile already contains** (figure ids, finding keys,
  document ids) instead of copying it, so there's one source of each fact.
- `rules` stays **`profile/1`** (decision **T2**): no snapshot has been stored yet, so the
  first approved snapshot will already include `support` and `approval`. From 3-B onwards,
  any change to the profile's shape means a new rule version.
- The fingerprint covers the additions automatically, since it's computed over the whole
  profile.

## 4. Screen
- Each item card on the Financial profile gets a **"Supported by"** block with five labelled
  sections, in this order: Declared · Documentary (broker-reviewed) · Corroborating ·
  Findings · Approval.
- **Documentary entries** read as one line each (document, period, measures; corrections
  noted) and link to the document.
- **Corroborating entries** show the payslip month, the credit, the account and holders, and
  the scope sentence in full, and link to the statement at the row (`?row=`, built in 2B-3).
- **Findings** show discrepancies with their acknowledgement status (and note), what couldn't
  run with the reason, and what passed by title, collapsed. Each links to the Checks card.
- **Approval:** "Not approved" until 3-B.
- **Copy rule:** no numbers that count evidence. Tested: a unit test checks that the block's
  wording never contains a count of documents or credits.

## 5. Changes, in full

| Where | Change |
|---|---|
| `profile/schemas.py` | New output models: `ItemSupport`, `DocumentarySupport`, `Corroboration`, `ItemFindings`, `ApprovalStatus`; `ProfileOut` gains `support` and `approval` |
| `profile/service.py` | Derives the five kinds from what 3-A already loads, plus **one query**: the case's `document_reviews` with an `income_source_id`, for documents whose figures were all rejected. Adds the scope sentence constant |
| Frontend | `lib/types.ts`, `lib/profile.ts` (wording, pure and tested), the "Supported by" block on the profile page |
| Tests | Integration: each kind from real reviewed data (section 6). Unit: wording and no-count rule. `openapi.json` regenerated |
| **Unchanged** | Database, migrations, financial domain, documents, reviews, statements, accounts, both rule sets, permissions, routes |

## 6. Tests
**Backend (integration, synthetic):**
- a declared value appears only under declared;
- a reviewed payslip appears under documentary with exactly the measures it carries and its
  period, with corrections named;
- a payslip with every figure rejected is listed with `figures_saved: false`;
- a `passed` exact salary credit appears under corroborating, with the account, holders, row
  and scope sentence;
- **not as corroboration:** a near match, multiple credits, no credit, and an exact credit
  into a partner's sole account. Each appears under findings with its status;
- acknowledged and unacknowledged discrepancies are reported correctly;
- approval is "not_approved";
- bank integrity findings are not in any item's support;
- a commitment shows declared only;
- the fingerprint changes when an acknowledgement is added.

**Frontend (unit):** the wording of each kind; "None recorded" for empty kinds; the scope
sentence; **no count words** ("documents", "credits" after a number); links to documents and
statement rows.

**Every step's gates:** full backend suite, ruff, `mypy --strict`; vitest, `tsc`, eslint,
production build (from a copy, as in 3-A).

**Walkthrough:**
- `SYN-BANK-001`: May is corroborated by the joint account; June has multiple credits
  (finding); July's credit went to Sam's sole account (finding); August is a near match
  (finding).
- `SYN-DEMO-002`: corrected net pay named under documentary.
- `SYN-RECON-001`: declared basic, and the missing August payslip as a finding.

## 7. Left out
| Left out | Why |
|---|---|
| Explicit document-to-item links for evidence with no figures (contracts) | Assessment G3: Phase 5, when such a document type exists |
| Corroboration from anything other than exact salary credits | No other corroborating relationship exists yet. New ones arrive with their document types |
| Any reliability indicator, weighting or score | Against your refinement |
| Editing, retiring or acknowledging from the "Supported by" block | Those actions already exist elsewhere; 3-A2 is read-only |

## 8. Decisions (all approved as recommended, 2026-11-20)

| # | Decision | Recommended |
|---|---|---|
| **T1** | Where approval status lives | **Once, at profile level, and shown in each item's block.** Approval is of the whole profile, not per item, so repeating it per item in the data would suggest otherwise |
| **T2** | Rule version | **Keep `profile/1`.** No snapshot exists yet; the first approved snapshot will include the support. From 3-B onwards, shape changes mean a new version |
| **T3** | A document reviewed against an item with no figures saved (all rejected) | **List it under documentary as "reviewed; no figures saved".** It was reviewed against the item, so leaving it out would hide that it was considered |
| **T4** | Bank integrity and gap findings | **Not in any item's support**; they stay on the accounts and in the checks, since they're about accounts, not items |
| **T5** | Broker entries | **Not one of the five kinds.** They stay labelled (B) on the figure; a broker's entry isn't evidence about the item |
