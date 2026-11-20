# Financial Domain Assessment: Does the Foundation Support the Broader Vision?

Date: 2026-11-20 · Status: **Approved 2026-11-20: A1 (with your refinement), A2, R1 and A3.
No code changes.** · Baseline: `429b76b` (Stage 3-A)

**The principle tested:** documents are evidence; the financial profile is the product. The
profile must be independent of the documents that populate it. For example, three payslips,
a P60, salary credits on a statement and a contract are four kinds of evidence about **one**
employment income, not four unrelated figures.

**Conclusion in one line:** the core domain already follows this principle and needs **no
redesign**. Four things are shaped by the first two document types; three can wait until a
third type arrives, and one, the per-item evidence view, belongs in Stage 3.

## 1. What already fits the vision

| Layer of the vision | What exists | Why it fits |
|---|---|---|
| **The position** (income, liabilities, expenditure) | **Items**, independent of any document: `income_sources` (employment, self-employment, directorship, property rental, pension, benefit, investment, maintenance, other), `commitments` (9 kinds; joint liability through `commitment_parties`, with shares), `expenditure_items` (household or individual) | An item is the underlying financial position. It exists before any document arrives (declared) and outlives any document |
| **Statements about a position** | **Observations**: typed values against an item, from a source (`declared`, `broker_entry`, `document`), with frequency, period, tax year or as-of date. Corrections supersede; nothing is overwritten | Payslip, P60 and SA302 figures for one income source sit **side by side on the same item**, each with its own period and source. They are never four unrelated income figures |
| **A shared vocabulary** | **Measures**, with meaning fixed for ever and applicability per item kind. Already there: payroll (basic, gross, net, tax, NI, YTD), **self-employment and directorship** (turnover, net profit, dividends, share of profit), **rental** (gross rent, costs), pension, benefit, investment, `total_gross_income` (a P60 or SA302 total), and commitments (monthly payment, balance, limit, rate, remaining term, final payment) | A P60, SA302 or credit report needs **no new measures** for the figures it carries |
| **Provenance** | Figure → broker's field decision → extracted field (printed label, value, page, boxes) → extraction run → document version and hash. Generic: nothing in it is payslip-specific | Any future document type gets the same traceability for free |
| **Reconciliation** | Pure, versioned rule sets. Findings are keyed to the item (`income_source_id`) and cite evidence of any kind; acknowledgements are generic | New cross-document checks (P60 against payslip year to date; SA302 against declared self-employed income) are new rule sets, not a redesign |
| **The profile (3-A)** | Built **per item**, not per document; every figure carries its origin and source | Already "the profile is the product", with documents as the evidence behind it |

## 2. What is shaped by payslips and statements

| # | Where | Today | Limits | Change, and when |
|---|---|---|---|---|
| **G1** | **Binding evidence to items** | A document review links the **whole document to one income source** (`document_reviews.income_source_id`), or a statement to one bank account (CHECK `payslip_or_statement`) | A **credit report** describes many commitments; an SA302 can cover several sources (self-employment and rental); accounts describe a business | **Bind per decision, not per document:** each field decision names the item it evidences. A migration when the first many-item document type arrives (credit reports, Phase 5). **Not now:** payslips and P60s are one-item documents |
| **G2** | **Field to measure mapping** | Hard-coded for payslips in `documents/review.py` (`FIGURE_CODES`, date and text codes); statements have their own module. Two `if` branches | A third type would add a third branch | **A per-type definition** (extractor, field-to-measure map, required fields, review rules) behind one interface, introduced **with the third document type**. Not now: two types don't justify the abstraction |
| **G3** | **Evidence that isn't a figure** | Documents link to an **applicant**, not to an item. Statement salary credits support an income source only through a computed check (`SALARY-CREDIT`). An employment contract or a P60 that only confirms employment has **nowhere to attach** | There's no single answer to "what supports this item?" | **Stage 3 (step 3-A2):** a "Supported by" summary per item, **derived** from links that exist (section 3). **Phase 5:** an explicit document-to-item link for evidence that carries no figure (contracts, letters) |
| **G4** | **Assets and deposit** | Three subject kinds only: income, commitment, expenditure. Bank accounts exist, but as statement records in the documents module, not as financial items | The vision's **assets** and **deposit** (savings, gifted deposit, equity, investments) can't be represented | **Phase 5:** an `asset` subject kind with stock measures (balance, value), and the deposit as the sources the applicant will use, linkable to bank accounts. Additive: new kinds and measures, no change to existing ones |

**Not a gap: a single "position" per item.** One adopted figure per item (for example
"annual basic £45,000, supported by 3 payslips, a P60 and 3 salary credits") is
**assessment**. It must be an explicit, versioned rule over the observations, producing
derived values with their own provenance. It must never be a `current` flag on observations
(the decision of 2026-11-18 stands). It belongs to the vision's "assessment support" layer
(Phase 5) and sits **on top of** the observation layer without changing it. Stage 3 should
leave room for it, which it does: the snapshot is versioned (`profile/1`), so `profile/2` can
add positions later.

## 3. Stage 3 addition: "Supported by" for each item (step 3-A2, approved)

Added to the profile **before 3-B freezes the snapshot**. It's derived from links that
already exist: no new tables, and the financial domain is unchanged.

### 3.1 Five kinds of support, never merged
Each item (income source, commitment, expenditure) states what supports it **by kind**.
The kinds are shown separately and never combined into one measure:

| Kind | What it is | What it can establish | From |
|---|---|---|---|
| **1. Declared by the applicant** | Values the applicant has stated | What the applicant says. **Never verification** | `declared` observations |
| **2. Documentary evidence (extracted and broker-reviewed)** | Each reviewed document that contributed figures to this item | **Only the measures it carries, for the period it covers**. For example, a payslip gives gross, basic, deductions and net pay for one pay period. Each entry says which measures and which period, and whether any were corrected | figure provenance (3-A) |
| **3. Corroborating evidence** | Evidence that confirms one fact about the item without supplying its figures. Today, **a salary credit on a confirmed statement that exactly matches a payslip's net pay** (`SALARY-CREDIT`, outcome *exact*, in an account the payslip's applicant holds) | **Only what it shows.** A salary credit confirms that **an amount equal to the net pay was received** on a date into a named account. It does **not** confirm contractual basic salary, gross pay, the employer, or that the income will continue | bank checks (2B-3) |
| **4. Reconciliation findings** | The checks about this item, by status: **discrepancies (acknowledged, with the note, or not)**, could not run (with the reason), passed | Where the evidence agrees, disagrees, or can't be compared | findings keyed to the item |
| **5. Broker approval status** | Not approved · approved (version, by whom, when) · superseded on reopening | Whether a broker has attested the profile, and which version. **Approval is a broker act, not a property of the evidence** | the case's approval (3-B); until then, "Not approved" |

### 3.2 No reliability implied by quantity
- **No counts, scores or strength labels.** The summary never says "4 supporting
  documents", "well evidenced" or similar, and nothing ranks one item above another by how
  much evidence it has.
- **Each piece of evidence states its own scope:** which measures, which period, and for
  corroboration, the fixed sentence saying what it does and doesn't confirm. The sentence
  is versioned with the profile rule.
- **Only exact salary matches corroborate.** A near match, multiple possible credits or no
  credit are **findings** (kind 4), never corroboration. An exact match into an account the
  payslip's applicant doesn't hold is a finding too (decision C4).
- **Gaps are stated, not inferred away.** Periods without documentary evidence appear
  through their findings (for example a missing payslip, `PERIOD-GAP`). The summary doesn't
  turn coverage into a judgement.

### 3.3 Example (wording indicative)

> **Employment income, Alex Example**
>
> - **Declared:** basic salary £45,000 a year.
> - **Documentary evidence (broker-reviewed):**
>   - Payslip, May 2026: gross, basic, deductions and net pay.
>   - Payslip, June 2026: gross, basic, deductions and net pay (net pay corrected).
>   - Payslip, July 2026: gross, basic, deductions and net pay.
> - **Corroborating:** net pay for May and July received into account ending 4821 (joint:
>   Alex Example and Sam Synthetic). *Confirms receipt of the net amount only; not
>   contractual salary.*
> - **Findings:** 1 discrepancy, acknowledged ("Employer confirmed a £1.50 deduction") ·
>   1 could not run (August statement missing).
> - **Approval:** not approved.

### 3.4 Where it lives
- **API:** each item in `GET …/profile` gains `support: {declared[], documentary[],
  corroborating[], findings{…}, approval}`. Approval is the case-level status, repeated per
  item for reading.
- **Screen:** a "Supported by" block on each item card.
- **Snapshot:** included in what 3-B freezes, so an approved version records the support as
  it stood.
- Planned as step **3-A2** of the Stage 3 plan, reviewed before it's built.

## 4. The approved roadmap

Your five phases, with R1 applied: **minimum production safeguards and a controlled broker
pilot come immediately after Stage 3, before any new document type.** That makes them a
phase of their own. Phases are numbered as in [ROADMAP.md](../ROADMAP.md):

| Phase | Focus | Status |
|---|---|---|
| 1 | Core financial domain and platform foundation | Done |
| 2 | Document intelligence and reconciliation (payslips, bank statements) | Done |
| 3 | Unified financial profile, evidence traceability and broker approval (Stage 3: 3-A, 3-A2, 3-B, 3-C) | In progress |
| 4 | **Minimum production safeguards and a controlled broker pilot** | Next |
| 5 | Broader evidence coverage and financial assessment support (P60, SA302, credit reports, accounts; G1–G4; positions per item; missing-evidence rules; affordability preparation) | Planned |
| 6 | Integrations, operational readiness and commercial rollout | Planned |

## 5. Decisions (approved 2026-11-20)

| # | Decision | Approved |
|---|---|---|
| **A1** | A per-item "Supported by" summary in Stage 3, before 3-B | **Yes, refined:** five kinds kept separate (declared · documentary · corroborating · findings · approval). No count or score implies reliability. Each piece of evidence states what it does and doesn't establish (section 3) |
| **A2** | Adopt the five-phase roadmap | **Yes**, with R1 applied (section 4) |
| **R1** | When the real-data safeguards and a pilot happen | **Immediately after Stage 3, before new document types** (Phase 4) |
| **A3** | G1 (per-decision item binding) and G2 (per-type definitions) | **When the third document type arrives** (Phase 5), not now |

**Standing instruction:** keep the existing financial domain intact unless a specific
requirement shows a change is necessary. Nothing in Stage 3 or Phase 4 does. G1 and G4 are
expected in Phase 5, each to be justified by its document type when it's planned.
