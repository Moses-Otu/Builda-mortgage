# Stage 2-A — Payslip Reconciliation: Plan (revision 2)

Status: **Revision 2 agreed; assumptions A1–A8 accepted as proposed (2026-11-19).
Implemented: see [STAGE-2A-RECONCILIATION-REPORT.md](STAGE-2A-RECONCILIATION-REPORT.md).** · Date: 2026-11-19 · Roadmap: [Stage 2](../ROADMAP.md) · Baseline:
Phase 2.1 (to be committed first)

**Revision 2** applies your seven refinements:
- missing payslips judged on **pay-period coverage** (section 2.2);
- **basic salary only** for declared versus evidenced (2.6);
- year-to-date checks that **say when they can't be interpreted** (2.4);
- the 2 % threshold as a **versioned product assumption** (3);
- **fingerprints** over rule version, identities, values and periods (4.2);
- **acknowledgement is not resolution or approval** (5);
- **computed on request, nothing stored but acknowledgements** (4.1).

The six checks and the scope boundaries are unchanged. Open assumptions are in section 8.

**Goal:** the platform checks reviewed payslip figures against each other and against what
the applicant declared, and shows each discrepancy with **both sources cited**, down to the
highlighted box on each payslip. The broker can acknowledge a discrepancy with a note. The
platform never decides anything: no pass/fail for the case, no figure chosen for the broker.

**Synthetic data only.**

## 1. What a broker will see

On the case page, a **Checks** card:

> **Checks** · payslip-checks/1 · 2 discrepancies · 8 passed · 2 could not run
>
> ⚠ **Year to date doesn't add up — June 2026 payslip**
> Gross pay to date went from £8,750.00 (May) to £13,500.00 (June), an increase of £4,750.00;
> June's gross pay is £4,375.00. Difference £375.00 (allowance £1.00).
> [May · Gross pay to date] [June · Gross pay to date] [June · Gross pay] · **Acknowledge…**
>
> ⚠ **Pay period not covered — 1 to 31 August 2026**
> July's payslip covers up to 31 Jul; the next, September's, starts 1 Sep. No payslip covers
> August.
>
> ○ **Could not run (2)**
> - Declared versus evidenced basic salary: no payslip shows a basic pay line (only total
>   earnings, which can include overtime, bonus and allowances).
> - Year to date (tax), October: the payslip has a "Tax refund" line, so the change in tax
>   to date can't be compared reliably. Check it by eye.
>
> ✓ **Passed (8)** (collapsed)

Each evidence chip opens that payslip's review screen with the field selected and its box
highlighted. An acknowledged discrepancy **stays a discrepancy**, shown as
"Discrepancy · acknowledged" with the note (section 5).

## 2. The checks (rule set `payslip-checks/1`)

**Inputs:**
- the **current** reviewed data per **income source**: document figures saved by reviews
  (`source_kind = 'document'`, not retired);
- the reviewed descriptive fields (pay date, period start and end, total deductions), as
  accepted or corrected;
- the current **declared** figures.

Rejected fields are never used. Money is integer pence. Every check returns one of:
- **passed**;
- **discrepancy**: values and difference, and both sources cited;
- **could not run**: always with a plain reason.

### 2.1 `PAY-ARITH`: the payslip adds up
- **Rule:** gross pay − total deductions = net pay, to within the arithmetic allowance (1p).
- **Could not run:** if any of the three is missing or rejected.

### 2.2 `PERIOD-GAP`: every pay period is covered (refinement 1)
Judged on **the periods the payslips cover**, not on the days between pay dates. Pay dates
move for weekends, bank holidays and early Christmas pay; periods don't.

1. **Both period dates reviewed** on two consecutive payslips: the uncovered span is from
   the day after the earlier period's end to the day before the later period's start.
   - **Discrepancy** if that span is at least **one whole pay period**, less the alignment
     allowance (3 days). Example: monthly, 1 Aug to 31 Aug uncovered.
   - **Could not run** ("periods don't line up: 5 days, 26 to 30 June, aren't covered by
     either payslip") if the span is shorter than a period but longer than the allowance.
     That's a shift in pay periods, not a missing payslip.
2. **Only period ends reviewed:** coverage is inferred from consecutive period ends.
   - Monthly: the number of month steps between the two period ends, with the allowance for
     month-end dates (28 Feb to 31 Mar is one step). One step is covered; two or more means
     a missing period. Four-weekly, fortnightly and weekly use 28, 14 and 7 days.
   - When the gap isn't close to a whole number of periods (more than the allowance away),
     the check **could not run** ("irregular period ends").
3. **Only pay dates:** **could not run** ("the payslips don't show their pay periods").
   This check is not judged on pay dates (assumption A1).
4. **Mixed frequencies** within one income source: **could not run**.

### 2.3 `PERIOD-DUP`: no period is covered twice
- **Discrepancy** if:
  - two payslips have the same period end;
  - two payslips have overlapping periods (both dates reviewed);
  - two payslips have the same pay date and no period dates.
- Cites both payslips.

### 2.4 `YTD-GROSS` and `YTD-TAX`: year to date adds up (refinement 3)
**Rule:** for consecutive payslips in the **same tax year** (6 April to 5 April), the change in
the year-to-date figure equals this period's figure, within the **£1** allowance:
- `YTD-GROSS`: `ytd_gross_pay` against `gross_pay`;
- `YTD-TAX`: `ytd_income_tax` against `income_tax`.

The checks **run only where the comparison means something**, and otherwise say why:

| Situation | Outcome |
|---|---|
| Pair straddles 6 April (different tax years) | Not compared as a pair. The later payslip is checked as **first of its tax year** if its period starts within the first pay period after 6 April: its year to date should equal its period figure. Otherwise **could not run** ("no earlier payslip from this tax year"). |
| A period between the two is uncovered (`PERIOD-GAP`), or they overlap (`PERIOD-DUP`) | **Could not run** ("a period is missing between these payslips"). This avoids reporting the same gap twice. |
| Year to date went **down** | **Could not run** ("year to date fell from £x to £y: a new tax year, a new employer or an employer correction; check by eye"). |
| The year-to-date label says **taxable** pay (e.g. "Taxable pay to date") for `YTD-GROSS` | **Could not run** ("taxable pay excludes pension and salary-sacrifice deductions, so it can't be compared with gross pay"). The extractor accepts this label today. |
| The figures don't add up **and** either payslip has an **adjustment line**: a label containing *arrears*, *back pay*, *backdated*, *adjustment*, *correction*, *refund*, *rebate*, *previous employment* or *P45* | **Could not run** ("may be explained by '<line>' on the <month> payslip; check by eye"). If the figures add up anyway, **passed**. |
| A year-to-date or period figure is missing or rejected | **Could not run**. |
| Otherwise | **Passed** or **discrepancy**. |

Backdated pay itself doesn't break the rule, because it's in that period's gross and in the
year to date. The adjustment-line rule covers the cases where employers book it separately.

### 2.5 `DECLARED-BASIC`: declared versus evidenced basic salary (refinement 2)
This replaces `DECLARED-PAY`. There are still six checks.

- **Compares only basic with basic:**
  - **Declared:** the applicant's current declared **basic salary**, a `basic_pay`
    observation with `source_kind = 'declared'`, entered as annual (section 6).
  - **Evidenced:** `basic_pay` figures from the payslips. That includes uncoded lines the
    broker saved as basic pay during review. **Never total gross pay**, which can contain
    overtime, bonus, commission and allowances.
- **Evidenced annual basic:** the **latest** payslip's basic pay, annualised:
  - monthly ×12, four-weekly ×13, fortnightly ×26, weekly ×52;
  - the latest because a declared salary is a current salary (assumption A3);
  - the message also shows the range across all payslips ("basic pay was £3,750.00 on all
    4 payslips", or "varied from £3,500.00 to £3,750.00").
- **Discrepancy** if the declared and evidenced annual figures differ by more than the
  **threshold (2 % of the declared figure, section 3)**.
- **Could not run:**
  - no declared basic salary;
  - no payslip shows a basic pay line ("only total earnings, which can include overtime,
    bonus and allowances");
  - the latest payslip's basic pay looks part-period, for example where an unpaid-leave or
    proration line is present. Then: "the latest payslip's basic pay may not be a full
    period's".

## 3. Rule configuration: versioned, an initial product assumption (refinement 4)

The thresholds live in **one versioned configuration in code**, next to the rules:

```
RULESET = payslip-checks/1
  arithmetic_allowance_pence      = 1
  ytd_allowance_pence             = 100
  period_alignment_allowance_days = 3
  declared_basic_threshold_pct    = 2.0   # initial product assumption, not a regulatory standard
  adjustment_markers              = (arrears, back pay, backdated, adjustment, correction,
                                     refund, rebate, previous employment, P45)
```

- Changing any value means a new rule-set version (`payslip-checks/2`). The old version's
  values stay in the code history.
- Every finding returns the version and the parameters it used. The UI shows the threshold
  in the message ("more than 2 % of the declared figure").
- The plan, the code comment and the UI tooltip all describe the 2 % as **an initial
  product assumption for highlighting differences, not a regulatory or lender standard**.
- There are no per-firm settings. They can be added if pilots ask (written down, not built).

## 4. Architecture (refinement 7)

### 4.1 Computed on request; only acknowledgements stored
- `GET /cases/{case_id}/checks` loads the case's current reviewed data and runs the rule set
  **in the API request**, then returns the findings.
- There is **no background worker, no job, no results or history table, and no trigger**.
- The result can be reproduced from the immutable reviewed inputs plus the rule-set version.
- Stage 3's approval snapshot will freeze the findings shown at approval. That is written
  down, not built.

Code, in a new `meip/reconciliation/` module (module 10 in ARCHITECTURE):

| File | Contents |
|---|---|
| `rules.py` | the configuration (section 3) and the six checks as **pure functions** over small frozen data classes; no database, no FastAPI |
| `service.py` | loads one case's inputs (read-only, through the documents and financial models), runs the rules, attaches acknowledgements, records an acknowledgement |
| `models.py` | `CheckAcknowledgement` |
| `api/routers/checks.py` | the two routes |

That is all: no plug-in registry, no rule engine, no generic framework.

### 4.2 Finding fingerprints (refinement 5)
Each finding's `key` is the SHA-256 of a canonical JSON document containing:
- the **rule-set version** and **rule code**, and the **parameter values** used;
- for every cited input, its **identity and content**:
  - observations: `observation_id`, `measure`, `qualifier`, value (pence), `frequency`,
    `period_start`, `period_end`, `as_of_date`;
  - reviewed fields: `field_review_id`, field code, decision, effective value;
  - the document version and pay date the input came from;
- for declared figures, the declared `observation_id` and value.

So a corrected figure (a superseding observation has a new ID), a changed period, a new
declared salary, or a new rule-set version produces a **new key**, and therefore a new,
unacknowledged finding. Keys are 64 lowercase hex characters. Unit tests pin that:
- the same inputs give the same key;
- changing any of them gives a different key.

## 5. Acknowledging is not resolving or approving (refinement 6)

| | What it is | In 2-A? |
|---|---|---|
| **Acknowledge** | The broker has **seen** the discrepancy and recorded a note. A record about the finding, nothing more. | Yes |
| **Resolve** | The underlying inputs change (a figure is corrected, a missing payslip is added), so the discrepancy is no longer produced. | Happens naturally: the old finding disappears and a new one appears if anything is still wrong. There is no "mark as resolved" button. |
| **Approve an income figure** | The broker decides which figure the profile uses. | **No.** That's Stage 3's profile approval. |

Guarantees:
- An acknowledgement **does not alter the financial profile**. It writes nothing to
  observations, income sources, reviews or documents, and changes no status or
  verification. A test compares every financial and document row before and after.
- An acknowledged finding is **still reported as a discrepancy**: `status: "discrepancy"`
  with an `acknowledgement` attached. It is never hidden, never counted as passed, and never
  turned into a different status.
- Counts show it separately ("2 discrepancies, 1 acknowledged"). Stage 3's approval must
  show acknowledged discrepancies to the approver.
- **Wording:** the button is "Acknowledge", the dialog says *"This records that you've seen
  this discrepancy. It doesn't change any figures, resolve the discrepancy or approve the
  income."*, and the note field is "What you found or did".

### 5.1 The one table: `check_acknowledgements`
| Column | |
|---|---|
| `id`, `tenant_id`, `case_id` | UUIDv7; composite FK to the case within the tenant |
| `finding_key` | `CHECK (finding_key ~ '^[0-9a-f]{64}$')`; `UNIQUE (tenant_id, case_id, finding_key)` |
| `rule_code`, `ruleset_version` | e.g. `YTD-GROSS`, `payslip-checks/1` |
| `note` | required, 1 to 1,000 characters |
| `acknowledged_by_user_id`, `created_at` | who and when |

- **Insert-only:** a trigger blocks UPDATE, DELETE and TRUNCATE.
- RLS on `tenant_id`.
- Grants `SELECT, INSERT` to `meip_app`.
- Classified in `schema_policy.py`.
- Migration `0016_check_acknowledgements`.
- **Audit:** one `check.acknowledged` event in the same transaction. The rule code, rule-set
  version and key go in the permanent row; **the note goes in the erasable details**.
- **Accepted only for a key the service has just computed as a current discrepancy:**
  - a key that is passed, could not run, or is no longer produced gives 409
    `finding-not-current`;
  - a second acknowledgement of the same key gives 409 `already-acknowledged`.

## 6. API and frontend

| Endpoint | Permission | |
|---|---|---|
| `GET /cases/{case_id}/checks` | `case:read` | `{ruleset_version, parameters, findings[]}` |
| `POST /cases/{case_id}/checks/{finding_key}/acknowledgement` | `financial:write` | `{note}` → 201 · 404 unknown case · 409 `finding-not-current` / `already-acknowledged` · 422 empty or long note |

Each finding has:
- `key`, `rule_code`, `status` (`passed`, `discrepancy`, `not_run`);
- `title`, `message`, and `reason` when `not_run`;
- `applicant_id`, `income_source_id`;
- `amounts` (`expected`, `actual`, `difference`, `allowance`, in pence, where relevant);
- `periods`;
- `evidence[]`: `source_kind`, `observation_id` or `field_review_id`, `document_id`,
  `field_ordinal`, `label`, `value`;
- `acknowledgement` (`note`, `by`, `at`) or null.

Both routes go into the OpenAPI snapshot and the authorization matrix.

**Frontend:**
- **Checks card** on the case page:
  - discrepancies first (acknowledged ones marked, note shown);
  - then "could not run" with reasons;
  - then passed (collapsed);
  - refreshes after a review is saved.
- **Evidence chips** link to `/cases/{id}/documents/{doc}?field={ordinal}`; the review page
  selects that field on load.
- **Acknowledge dialog:** the wording in section 5 and a required note.
- **Declared basic salary** on each employment income source:
  - labelled **"Declared basic salary (annual, before overtime, bonus and allowances)"**;
  - sent to the existing `POST …/financial/observations` endpoint: `measure: basic_pay`,
    `frequency: annual`, `source_kind: declared`;
  - changing it supersedes the previous value, through the existing API.
- **Allowlist additions (3):**
  - `GET cases/{id}/checks`;
  - `POST cases/{id}/checks/{64-hex}/acknowledgement`;
  - `POST cases/{id}/financial/observations`.

**Synthetic series:** `payslip_series(start, months, basic, …)` in `tests/synthetic_docs.py`
produces consecutive monthly payslips with **both period dates** and consistent year-to-date
totals across a tax year. It has options for:
- a missing month;
- a year-to-date break;
- an arithmetic error;
- a duplicate period;
- an adjustment line;
- a "taxable pay to date" label;
- an only-period-end layout;
- no basic line.

`scripts/make_synthetic_payslip.py series …` writes the files for the demo.

## 7. Tests and verification
- **Rules, unit (no database).** Every outcome of every check, including:
  - pay dates moved for bank holidays (no gap reported);
  - month-end period ends (28 Feb to 31 Mar);
  - a 5-day misalignment (could not run, not a discrepancy);
  - only-pay-date payslips (could not run);
  - the 6 April boundary, first payslip of a tax year, a pair straddling tax years;
  - year to date falling;
  - "taxable pay to date";
  - adjustment lines, both when the figures still add up and when they don't;
  - exactly 1p and £1 at the boundary;
  - weekly and four-weekly;
  - basic present on some payslips only, no basic line, varying basic, a part-period line;
  - exactly 2 % at the threshold;
  - rejected and superseded inputs ignored.
- **Fingerprints:** stable for the same inputs; different when the rule-set version, a
  parameter, an observation ID, a value or a period changes.
- **Integration:**
  - the endpoint over each synthetic series variant;
  - acknowledgement returns 201, then 409 for a duplicate, 409 for a stale key, and 422 for
    an empty note;
  - **acknowledging changes no financial, review or document row** (the before-and-after
    comparison);
  - one audit event, with the note in the erasable details;
  - a finding is replaced when one of its figures is superseded.
- **Isolation:**
  - the catalog guard covers the new table (RLS, grants, insert-only trigger);
  - an RLS test for the table;
  - another firm gets 404 on both routes.
- **Authorization:** `read_only` can read checks but can't acknowledge.
- **Frontend (vitest):** grouping and counts (acknowledged still counted as a discrepancy),
  the dialog wording, the evidence link, the allowlist entries and nearby paths refused, the
  declared-salary request body.
- **Gates:** the full backend suite, ruff, `mypy --strict`, and the isolation suite on its own.
- **Manual walkthrough (synthetic):**
  - **A 5-month series with a missing month, a year-to-date break and an adjustment line, and
    a declared basic 10 % above the payslips.** Expected:
    - 3 discrepancies;
    - 1 could not run, with the adjustment explanation;
    - each chip opens the right payslip at the right box.
  - **Acknowledge one.** It stays a discrepancy, and no figure changes.
  - **Supersede a figure through the API.** The finding and its acknowledgement are replaced.
  - **A clean series.** Everything passes.

## 8. Assumptions (all accepted as proposed, 2026-11-19)

| # | Assumption | Accepted | Why it matters |
|---|---|---|---|
| **A1** | Payslips showing only a pay date (no period dates) | `PERIOD-GAP` and `PERIOD-DUP` **could not run** for them; no fallback to pay-date spacing | The safest reading of refinement 1; the cost is fewer checks on sparse layouts |
| **A2** | Period alignment allowance | **3 days**; a missing period is reported only when **a whole period** is uncovered | Smaller shifts are reported as "could not run", never as missing payslips |
| **A3** | Which payslip represents evidenced basic salary | The **latest** payslip's basic, annualised; the range across payslips shown in the message | The alternative, the average, understates a recent pay rise; the latest can be distorted by a part-period, which the part-period rule catches |
| **A4** | Adjustment markers | The keyword list in section 3, matched case-insensitively on line labels | It's heuristic. A missing keyword can produce a discrepancy that an adjustment explains; an over-broad keyword can suppress a real one. Easy to extend in a new rule-set version. |
| **A5** | "Taxable pay to date" against gross pay | **Could not run** | The alternative, comparing with gross minus pension, assumes how the employer runs pension and salary sacrifice |
| **A6** | Tax year | Fixed at **6 April to 5 April** | UK only, consistent with the product |
| **A7** | Declared salary entry | Annual basic salary only; no declared gross, overtime or bonus in 2-A | Matches refinement 2; other declared measures come with later checks |
| **A8** | Tax period number ("Tax Period 11") | **Not used.** The extractor doesn't capture it today. | It would make coverage and year-to-date checks more robust. Capturing it is an extraction schema change (`payslip/1.1.0`), proposed as a follow-up rather than added here. |

## 9. Left out (unchanged scope boundaries)
- **Lender-style criteria** (minimum number of payslips, recency): lender policy, edging
  into advice.
- **Bank statements and other documents:** Stage 2-B, planned separately.
- **Stored runs, history, worker jobs and per-firm tolerances:** not needed (section 4.1).
  Stage 3's snapshot covers "what was shown at approval".
- **Choosing or approving figures:** Stage 3.
- **Averaging variable pay** (overtime, bonus, commission): an affordability concern.
- **Notifications.**

## 10. Deliverables
- **Backend:**
  - `meip/reconciliation/` (rules, service, model);
  - `api/routers/checks.py`;
  - migration `0016`;
  - schema policy;
  - the synthetic series;
  - tests;
  - the OpenAPI snapshot.
- **Frontend:**
  - the Checks card;
  - the acknowledge dialog;
  - the declared basic salary form;
  - `?field=` on the review page;
  - 3 allowlist entries;
  - tests.
- **Docs:**
  - `STAGE-2A-RECONCILIATION-REPORT.md`;
  - ARCHITECTURE (reconciliation);
  - ROADMAP (Stage 1 done, Stage 2 in progress);
  - README conventions (checks and acknowledgements).
