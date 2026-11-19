# Step 2-C — Payslip Classification and Extraction: Report

Date: 2026-11-19 · Status: **Implemented; final review checks closed; committed** · Plan:
[PHASE-2C-PLAN.md](PHASE-2C-PLAN.md) (revision 2, approved) · Baseline: `74b5782`

**Synthetic data only.** Every document is drawn by the test generator and carries a visible
`SYNTHETIC TEST DOCUMENT` line. The only provider is the test-only synthetic provider, and
every run and API response names it (`synthetic-fixture`).

## 1. Verification results

| Gate | Result |
|---|---|
| Full test suite (PostgreSQL 17.4) | **864 passed**, 1 deselected (the opt-in Azurite test), 0 failed (final run, after the review checks) |
| Coverage | **94.83 %** (the gate requires 85 %) |
| Ruff | format: 123 files already formatted; lint: all checks passed |
| mypy | `--strict`: no issues in 107 source files |
| Isolation suite, run on its own | **125 passed** (118 after 2-B) |
| Migrations (`test_schema.py`) | 14 passed: round trip, drift, OpenAPI snapshot (regenerated), and the new 0014 downgrade refusal |
| Extraction unit tests | 73 passed |
| Extraction integration tests | 18 passed (13, plus 5 added in the final review) |

**A consequence worth knowing:** in staging and production, document processing **can't be
switched on at all** until a real provider is connected, because the synthetic provider is
refused there. The settings checks now run in order, so each problem (approval, storage,
HTTPS, emulator) is reported first and the synthetic-provider refusal comes last. Three 2-A
settings tests were updated to expect it.

## 2. Your testing condition: fixtures are checked against the actual PDF

The synthetic provider reads a layout embedded in the PDF, so a fixture could in principle
pass while the PDF itself is wrong. The tests rule that out by reading the **visible text
back out of each PDF with PDFium**, independently of the embedded data:

| Check | What it proves | Covered |
|---|---|---|
| Every embedded line is read back at its box | The text PDFium finds inside each recorded box equals the line's text exactly | All 11 synthetic documents (3 payslips and 8 look-alikes) |
| Each page shows nothing that isn't in the layout | Each page's visible characters, as a multiset, equal the layout's characters for that page, so no unrecorded text is drawn | All 11 documents, every page |
| **Every extracted value and label is visible at its evidence box** | For each field, the text at the value box equals `raw_text` and the text at the label box equals `label_text` | All 3 payslip layouts, every field (unit tests) |
| **The same, end to end** | The original is downloaded through the API and each field's evidence, as returned by `GET …/extraction`, is checked against it | All 3 payslip layouts (integration tests) |
| **The check isn't vacuous** | It **fails** for a layout with a wrong value, a box moved off its text, or a visible line missing | 3 tampering tests |

### What this validation does and does not show

| Shown by the synthetic tests | **Not** shown (unknown until real documents are allowed) |
|---|---|
| The pipeline's plumbing is correct: classification, extraction, storage, API and audit | **Extraction accuracy on real payslips:** real layouts, fonts, scans, photos and payroll software vary far beyond three synthetic layouts |
| The stored evidence (page and boxes) points at the text it claims, **for these synthetic PDFs** | How a real provider (Azure Document Intelligence) segments lines, its coordinates and its confidence values |
| The classifier separates the 3 synthetic payslips from 8 synthetic look-alikes | False-positive and false-negative rates on real documents |
| The fixtures are faithful to their PDFs (they cannot pass while the PDF is wrong) | That the synthetic payslips are representative of real ones |

**The test counts and figures in this report measure correctness against synthetic ground
truth, not real-world extraction accuracy.** Measuring accuracy needs D10 approval, a real
provider and a labelled sample of real (or realistic) payslips.

## 3. What was built

- **Pipeline:** a successful ingest queues `document.extract` in the same transaction. The
  same worker, claiming, fencing and retries apply.
- **Three distinct outcomes:**

  | Outcome | Document | Run | Fields | Audit |
  |---|---|---|---|---|
  | Extracted | `extracted` | yes (`payslip`) | yes | `document.extracted` |
  | Unrecognised | `unrecognised` | yes (`unrecognised`, with markers) | none | `document.unrecognised` |
  | Provider failure | `failed` / `extraction_failed` | none | none | `document.processing_failed` |

  Transient errors (such as storage) are retried 3 times, then end in `extraction_failed`.
- **Provider:** the `LayoutProvider` interface, with one implementation,
  `SyntheticLayoutProvider`.
  - It validates the embedded layout strictly: it must be marked synthetic, use known units
    and the top-left origin, keep every box on its page, and have confidence between 0 and 1.
    Anything else is a provider error.
  - It is refused in staging and production by settings validation.
  - **No Azure adapter or SDK.**
- **Classification (`payslip-rules/1`):** exactly the markers in the plan: a required net
  pay marker, at least two of the six supporting groups, and four exclusion groups. Only
  marker names are stored.
- **Extraction (`payslip/1.0.0`):**
  - **Summary:** pay date, period start and end, frequency, tax code, gross pay, total
    deductions and net pay.
  - **Individual lines:** each payment and deduction line has its own label, raw value,
    normalised amount, canonical code (or null for unrecognised labels such as "Unpaid
    Leave Adjustment", which are kept), value box and label box.
  - **Year to date:** gross pay and tax.
  - **Parsing:** `£`, commas, brackets or a minus sign for negatives; four UK date formats.
    Values that can't be parsed keep their raw text with no value.
  - **Missing required fields** (pay date, gross and net) are listed in the response, not
    hidden.
  - **Not extracted:** employee name, NI number, addresses, bank details.
- **API:** `GET /api/v1/cases/{case_id}/documents/{document_id}/extraction` (`document:read`)
  returns the provider, versions, classification, markers, the four sections and the
  missing required fields. It is 404 until a run exists. The raw provider output is not
  exposed.

## 4. Migration `0014_extraction`

| | |
|---|---|
| `extraction_runs` | Provider, model, schema and classifier versions; classification; marker names; **raw output (JSONB)** and its SHA-256. One per version (unique). Composite foreign key to the version within one tenant and case. RLS. Runtime role SELECT and INSERT only. |
| `extracted_fields` | One row per value, as in section 3. Composite foreign key to the run. CHECKs: section, field code and unit from closed lists; at most one value column; GBP exactly when there's an amount; **value and label boxes inside the page**; confidence between 0 and 1. RLS. Insert-only. |
| Other | `documents` statuses `extracted` and `unrecognised`, failure code `extraction_failed`; `jobs` kind `document.extract` |
| Downgrade | Refuses while runs, extract jobs or extraction outcomes exist (tested) |

**Provenance:** the database guarantees the links from field to run to version to document
within one firm, and that boxes are inside their page. **It cannot verify that a box really
shows its value**; the tests do that for the synthetic data, and the broker does in review.

## 5. No document content in logs or audit

The worker logs job IDs, kinds, outcomes and exception types. Audit events record status,
classification, schema version, marker names and counts.

A test runs all three outcomes using a payslip with distinctive labels and amounts
("Zebracorn Allowance £7,777.77", "Quokka Levy", tax code "ZQ987X" and so on). It asserts
that none of those strings, nor the embedded-layout marker, appears in any captured log or in
the worker's audit changes or details.

## 6. Tests

| Area | Tests |
|---|---|
| Fixtures against the PDF | 11 documents, plus 3 tampering cases (section 2) |
| Golden files | 3 layouts (classic table with units and rate columns; "Earnings" wording, written dates and a negative adjustment; two pages, weekly). Exact summary, line-by-line payments and deductions, year to date, normalised values, ordinals. |
| Classification | 8 look-alikes `unrecognised` (P60, P45, SA302, bank statement containing "NET PAY", offer letter, pension statement, blank, filler); 8 marker-combination cases, including word boundaries ("netpay", "planet pay"); every supporting group and exclusion |
| Parsing | 12 money cases, 7 date cases, and an unparseable value kept as text |
| Provider | No marker, bad base64, bad JSON, not synthetic, box off the page: each a provider error. Refused in staging and production. |
| Pipeline (integration, 13) | Extracted (3 layouts, end-to-end evidence); amounts in pence; unrecognised (3 look-alikes); provider failure (no run, 404); transient errors then `extraction_failed`; one run per version even if the job is delivered twice; 404 before extraction; no content in logs or audit; runs and fields immutable; box CHECK; another firm's run refused by the foreign key |
| Existing suites | Authorization matrix and cross-tenant probes include the new route; schema policy (two insert-only tables); worker import bound now includes the extraction module; 0014 downgrade guard; OpenAPI snapshot. One 2-B test now runs only the ingest job and asserts the extract job was queued. |

## 7. Final review checks

| Check | Result | Evidence |
|---|---|---|
| **Retries can't leave duplicate or conflicting results** | Pass | **Database:** `UNIQUE (document_version_id)` on runs, so a second run is refused (new test). **Service:** extraction locks the document row and acts only on a `ready` document. Two concurrent attempts on the same version give `extracted` and `skipped` with exactly one run (new test). A duplicate delivery of the job creates no second run. The fenced finish means a stale worker commits nothing. Fields belong to exactly one run, and there is no "active" flag that could conflict. |
| **A failed extraction keeps the original; unrecognised documents keep their evidence** | Pass | After `extraction_failed`, the original downloads byte-identical with an unchanged SHA-256, and versions are immutable for every role (2-A trigger) (new test). Unrecognised documents keep their run, classification, classifier version, marker names (for example `exclusion:p60`) and the raw provider output with its hash (new test). |
| **Tenant isolation and permissions cover the new tables and route** | Pass, with one gap closed | Both tables are in the schema policy (standard RLS policy; runtime role SELECT and INSERT only, checked in the catalog). The route is in the authorization matrix (`document:read`, every role) and the cross-tenant probes. **Gap found:** the generic RLS tests saw these tables empty, so they passed trivially. **Closed** by a new test with real extraction rows: another firm sees 0 runs and 0 fields, and no tenant setting sees nothing; another firm gets 404 from the API. Fields can't reference another firm's run (foreign key test). |
| **Migration upgrade, downgrade and drift** | Pass | `test_schema.py`, 14 passed: upgrade to head, downgrade to base, upgrade again; ORM-to-migration drift; OpenAPI snapshot; and the 0014 downgrade refusing while extraction results exist, leaving the revision and data unchanged. 0014 only adds tables and widens CHECK lists, so existing rows stay valid. |
| **The report separates synthetic validation from real accuracy** | Done | The table above section 3 |
| *Found during the final run* | Fixed | One 2-B test failed once: its helper looked up "the" job for a document version, but each version now has two jobs (ingest and extract), and the lookup returned whichever row came first. It was a **test defect introduced in 2-C**, not a product defect. The helper now asks for the job kind explicitly, and the test passed three times in a row, then in the full run. |

## 8. Known limitations

1. **Only synthetic data has been extracted.** Extraction quality on real payslips, scans
   and the variety of real layouts is unknown until D10 allows real documents and a real
   provider is connected.
2. **No real provider.** Azure Document Intelligence is an interface only. Its coordinate
   unit (inches) and confidence are accounted for in the schema but untested.
3. **The label tables and row matching are deliberately simple:** same-row matching,
   rightmost amount, one set of headings. Real layouts (stacked labels, multi-column
   payslips) will need more patterns; unknown lines are kept rather than dropped.
4. **The synthetic provider trusts the uploaded file's embedded layout.** That is why it is
   refused when deployed.
5. **No reprocessing.** One run per version. The approved run lifecycle comes with
   reprocessing.
6. Carried forward: Docker verification, the PDF memory limit, the dedicated worker role,
   real Azure, and D10 (ROADMAP Stage 4).

## 9. Files

| | Files |
|---|---|
| New | `migrations/versions/0014_extraction.py`; `src/meip/documents/extraction.py`; `tests/synthetic_docs.py` (the generator and documents); `tests/unit/test_extraction_unit.py`; `tests/integration/test_extraction.py`; this report |
| Changed | `documents/models.py` (runs, fields, statuses), `documents/processing.py` (extract job, dispatch by kind, per-kind exhaustion), `documents/service.py` and `schemas.py` (extraction response), `api/routers/documents.py` (route), `core/config.py` (`extraction_provider`, refused when deployed), `audit/service.py`, `models.py`; tests: `schema_policy.py`, the authorization matrix, cross-tenant, module boundaries, `test_processing.py`, `test_schema.py`; `pyproject.toml` and `uv.lock` (`reportlab`, dev only); `openapi.json`; README; ARCHITECTURE; the retention brief (categories 2 and 3); ROADMAP and the Phase 2.0 plan (step numbering) |

## 10. Proposed commit (not made: awaiting your approval)

```
Phase 2.0 step 2-C: payslip classification and extraction (synthetic provider)
```
