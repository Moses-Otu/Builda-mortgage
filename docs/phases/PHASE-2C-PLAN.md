# Step 2-C — Payslip Classification and Extraction: Plan (revision 2)

Status: **Approved and implemented (see PHASE-2C-REPORT.md)** · Date: 2026-11-19 · Part of
[PHASE-2.0-PLAN.md](PHASE-2.0-PLAN.md) · Baseline: `74b5782`

**Revision 2 incorporates your decisions:**
- **Synthetic test provider only.** No Azure provisioning, and **no Azure adapter or SDK in
  this step.** The provider interface is kept so Azure Document Intelligence (general layout
  model) can be connected later.
- **No `employee_name`.** Name extraction and applicant identity matching are deferred.
- Exact classification markers, with false-positive tests.
- Pay components and deductions as **individual lines**, each with its label, amount and
  evidence.
- Three distinct outcomes.
- Field-level provenance.
- No extracted data or raw output in logs.

**Numbering:** 2-C is extraction (this plan), 2-D is review and save to the case, and 2-E
is the review screen.

## 1. Outcome

Take a synthetic UK payslip, identify it as a payslip, extract its financial lines and
summary figures, and keep the evidence a broker needs to verify each value.

Nothing is reconciled, checked arithmetically, approved or written to the financial model.

## 2. Three distinct outcomes

| Outcome | Document status | Run stored? | Fields stored? | Meaning for the broker |
|---|---|---|---|---|
| **Extracted** | `extracted` | Yes (`classification = payslip`) | Yes | Figures are ready to review. Missing required fields are listed, not hidden. |
| **Unrecognised** | `unrecognised` | Yes (`classification = unrecognised`, with the markers found) | No | The document wasn't identified as a payslip. A person needs to look at it. |
| **Provider failure** | `failed`, `failure_code = extraction_failed` | No | No | Extraction couldn't run (after the usual retries, for transient errors) |

Each outcome writes its own audit event (`document.extracted`, `document.unrecognised` or
`document.processing_failed`), and they are tested separately.

## 3. Built on what exists

- **Jobs:** a successful ingest enqueues a `document.extract` job in the same transaction,
  on the same `jobs` table, worker, claiming, fencing and retries. The job kind CHECK gains
  `document.extract`.
- **Documents:** `ready` moves on to `extracted`, `unrecognised` or `failed`. The original
  is read through the existing storage layer and never changed.
- **No new infrastructure.** Runs and fields are two Postgres tables. The raw output is a
  JSONB column.

## 4. The provider interface and the synthetic test provider

```python
class LayoutProvider(Protocol):
    name: str                                  # recorded on every run
    model_version: str
    async def analyse(self, pdf: bytes) -> Layout   # raises ProviderError on failure

@dataclass
class Layout:
    pages: list[Page]          # number, width, height, unit ("pt" here; "inch" for Azure later)
    lines: list[Line]          # page, text, box (x0, y0, x1, y1), confidence | None
    raw: dict                  # the provider's response as received, kept for traceability
```

**`SyntheticLayoutProvider`** (`name = "synthetic-fixture"`) is the only implementation in
this step:
- The **synthetic payslip generator** (a test and development tool using `reportlab`) draws
  each PDF and records where it placed every line. It embeds that layout in the PDF, in a
  marked block (`% MEIP-SYNTHETIC-LAYOUT …`).
- The provider reads that block and returns it, with `raw.source = "synthetic-fixture"`. A
  PDF without the block causes a **provider failure**, so a document without a synthetic
  layout ends up `failed` with `extraction_failed`, as the outcomes table says.
- Confidence is whatever the generator recorded: **none by default**. Tests set explicit
  values only to exercise the handling of confidence.
- **It is refused outside local and test:** startup fails in staging or production when
  `extraction_provider = synthetic`. Every run and every API response carries the provider
  name, so synthetic results are always identifiable.
- **Security note:** whoever uploads a document controls its embedded "layout". That's
  acceptable only because the provider is test-only and refused when deployed.

Connecting Azure later means a second implementation of `LayoutProvider` plus a settings
value. Nothing else changes.

## 5. Classification: exact markers (`payslip-rules/1`)

Matching is done on the document's text, **case-insensitive** and **on word boundaries**.

**Required: a net pay marker, either of**
- `\bnet\s+pay\b`
- `\btake[\s-]home\s+pay\b`

**Supporting: at least 2 of these 6 groups, each group counted once**

| Group | Patterns |
|---|---|
| Gross | `\bgross\s+pay\b`, `\btotal\s+(?:gross\s+)?pay\b`, `\btotal\s+earnings\b` |
| Tax code | `\btax\s+code\b` |
| PAYE | `\bpaye\b` |
| National Insurance | `\bnational\s+insurance\b`, `\bni\s+(?:contributions?\|ee\|employee)\b`, `\bnic\b` |
| Pay period | `\bpay\s+(?:date\|period)\b`, `\bperiod\s+end(?:ing)?\b`, `\btax\s+(?:period\|week\|month)\b` |
| Deductions | `\bdeductions\b` |

**Exclusions** (any one makes the document `unrecognised`, whatever else matches):
`\bP60\b`, `\bend\s+of\s+year\s+certificate\b`, `\bP45\b`, `\bdetails\s+of\s+employee\s+leaving\b`,
`\bSA302\b`, `\btax\s+calculation\b`, `\bstatement\s+of\s+account\b`, `\bopening\s+balance\b`.

**Result:** a payslip if it has the required marker, at least two supporting groups and no
exclusion. Otherwise it is unrecognised, and the run records which groups and exclusions
matched (marker names only, no document text).

**False-positive tests** (synthetic documents, each must be `unrecognised`):
1. A **P60** (it has tax code, PAYE and National Insurance, but no net pay, plus the
   exclusion).
2. A **P45**.
3. An **SA302 tax calculation**.
4. A **bank statement** whose transaction text includes `ACME LTD NET PAY` (it has the
   required marker but no supporting groups, plus the exclusion).
5. A **job offer letter** mentioning "gross pay" and "pension deductions" (no net pay).
6. A **pension statement**.
7. A blank page and a page of filler text.

Plus **true positives:** at least three payslip layouts, with different label wording (for
example "Take home pay", "Total earnings", "NI"), column positions and lengths. All must be
classified as payslips.

## 6. Extraction: individual lines with evidence (`payslip/1.0.0`)

Every extracted value is one row of **`extracted_fields`**, with its **own label, value and
evidence**:

| Column | Meaning |
|---|---|
| `section` | `summary`, `payment`, `deduction` or `year_to_date` |
| `ordinal` | Order on the payslip |
| `label_text` | The label exactly as printed (e.g. "Car Allowance", "Employee Pension") |
| `field_code` | The canonical code when the label is recognised (e.g. `basic_pay`, `overtime`, `bonus`, `income_tax`, `national_insurance`, `pension_contribution`, `student_loan_deduction`), otherwise **null**. **Unrecognised lines are kept, never dropped.** |
| `raw_text` | The value exactly as printed (e.g. "£1,250.00", "(12.50)") |
| `value_money_minor` + `currency`, or `value_date`, or `value_text` | The normalised value. **Null when parsing fails**, never guessed. |
| `page_number`, `x0`, `y0`, `x1`, `y1`, `page_width`, `page_height`, `unit` | Where the **value** is |
| `label_x0` … `label_y1` | Where the **label** is (same page) |
| `confidence` | The provider's indicator, nullable. **Never used to accept a value.** |

**Summary fields** (`section = summary`), at most one each:
- `pay_date` (required), `pay_period_start`, `pay_period_end`;
- `pay_frequency` (as printed);
- `tax_code`;
- `gross_pay` (required), `net_pay` (required);
- `total_deductions` (as printed; not checked against the lines).

**Payments and deductions:**
- Every row under a payments or earnings heading becomes one `payment` line, and every row
  under deductions one `deduction` line, until the section's total row.
- Each line's label is matched against a versioned label table to set `field_code`. For
  example, "Basic Salary" and "Basic Pay" map to `basic_pay`, "PAYE Tax" and "Income Tax"
  to `income_tax`, "Employee NI" to `national_insurance`.
- Bonus lines get `field_code = bonus`. The qualifier is decided at review in step 2-D,
  because the payslip doesn't say what kind of bonus it is.

**Year to date:** `ytd_gross_pay` and `ytd_income_tax` when printed.

**Not extracted:** employee name, National Insurance number, addresses, bank details,
employee or payroll numbers.

**How values are found:** deterministically from the line boxes. A value is the nearest
money or date text on the same row to the right of its label (row tolerance: half a line
height). Missing required summary fields are listed in the API response and don't fail the
extraction.

**Parsing:**
- Money: `£`, commas, and brackets or a minus sign for negatives, into integer pence (GBP).
- Dates: `dd/mm/yyyy`, `dd-mm-yyyy`, `d Mon yyyy` and `d Month yyyy`.
- Anything else keeps its raw text with a null value.

## 7. Data (migration `0014`)

| Table | Contents | Rules |
|---|---|---|
| `extraction_runs` | `document_version_id`, `provider`, `model_version`, `schema_version`, `classifier_version`, `classification`, `markers` (group names only), **`raw_output` (JSONB)**, `raw_output_sha256`, `created_at` | RLS; composite foreign key to the version (same tenant and case); **at most one run per version** (unique); runtime role SELECT and INSERT only |
| `extracted_fields` | As in section 6 | RLS; composite foreign key to the run (same tenant and case); CHECKs: page ≥ 1; both boxes inside the page; at most one value column; currency set exactly with money; `section` and `unit` from closed lists; confidence between 0 and 1; **insert-only** |

- **`documents`:** the statuses `extracted` and `unrecognised` are added, and the failure
  code `extraction_failed`. **`jobs`:** the kind `document.extract` is added.
- **Provenance guaranteed by the database:** the links from field to run to version to
  document, within one tenant and case. Every value has a page and a box inside that page.
- **Not guaranteed:** that a box really shows the value it claims. That is checked by the
  broker in review, and the report will say so.
- **Downgrade:** refuses while runs exist.
- **Reprocessing isn't built.** With one run per version, a failed run can't invalidate a
  successful one.

## 8. API (read-only, one new route)

`GET /api/v1/cases/{case_id}/documents/{document_id}/extraction` (`document:read`):
- Run metadata: `provider` (so `synthetic-fixture` is always visible), model, schema and
  classifier versions, classification, and markers.
- Summary fields, payment lines, deduction lines and year-to-date figures, each with its
  label, raw text, normalised value, value box, label box, page size and unit, and
  confidence. Also the list of missing required fields.
- 404 until a run exists. The raw provider output is **not** returned.

## 9. No extracted data or raw output in logs

- Worker and API logs carry **IDs, the outcome, error codes and counts only**.
- A provider error is logged by **exception type and a code**, never its message, which
  could quote document text. Unexpected exceptions keep the existing type-and-stack-frames
  logging, without messages.
- Audit events record the classification, schema version, and field and line counts.
  **No values, labels or raw text.**
- **Tests** capture all logs and audit changes through the whole pipeline, for a payslip
  with unusual, distinctive labels and amounts, an unrecognised document and a provider
  failure. They assert that **no label, raw text, amount or piece of the raw output**
  appears anywhere in them.

## 10. Tests (summary)

- **Golden files:** three or more payslip layouts. The exact summary fields, payment lines
  and deduction lines (labels, values and codes) are compared with the generator's ground
  truth, and every value has a page and a box inside it.
- **Classification:** the seven false-positive documents are `unrecognised`, the payslip
  layouts are recognised, and every marker group and exclusion is unit-tested.
- **Parsing:** money and dates, including bad input (null value, raw text kept).
- **Outcomes:** extracted, unrecognised and provider failure each give the right status, run
  or no run, fields or no fields, and audit event. A transient provider error is retried,
  then `failed`. Running a job again creates no second run.
- **Database:** foreign keys across tenants refused; runs and fields can't be updated or
  deleted; the CHECKs (box inside the page, one value, currency pairing).
- **Security:** the new route is in the authorization matrix and cross-tenant probes, and
  the new tables in the RLS and catalog suites; the synthetic provider is refused in staging
  and production; the logs and audit test from section 9.

## 11. Dependencies

`reportlab`, **development only**, for the synthetic payslip generator. **No new runtime
dependency.**

## 12. Not in this step

The Azure adapter (interface only), name extraction and identity matching, reprocessing,
review, correction and saving (2-D), the review screen (2-E), arithmetic or year-to-date
checks, other document types, LLMs, and new infrastructure.

## 13. Decisions

None outstanding: both of your decisions are incorporated.
