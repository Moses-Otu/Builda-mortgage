# Step 2-D — Review, Correct and Save to the Case: Report

Date: 2026-11-19 · Status: **Implemented, awaiting review** · Plan:
[PHASE-2D-PLAN.md](PHASE-2D-PLAN.md) (revision 2, approved) · Baseline: `1a9119d`

**Synthetic data only.** Every reviewed payslip is a synthetic document from the 2-C generator.

## 1. Verification results

| Gate | Result |
|---|---|
| Full test suite (PostgreSQL 17.4) | **907 passed**, 1 deselected (the opt-in Azurite test), 0 failed, in 7 min 24 s |
| Coverage | **94.84 %** (the gate requires 85 %) |
| Ruff | format: 126 files already formatted; lint: all checks passed |
| mypy | `--strict`: no issues in 109 source files |
| Isolation suite, run on its own | **133 passed** (125 after 2-C) |
| Migrations (`test_schema.py`) | 15 passed: round trip, drift, OpenAPI snapshot (regenerated), and the new 0015 downgrade refusal |
| Review tests (`test_review.py`) | 25 passed |

## 2. What was built

- **`POST /api/v1/cases/{case_id}/documents/{document_id}/review`** (`financial:write`,
  `If-Match` on the document):
  - The broker submits one decision for **every** extracted field (D1):
    - `accept`;
    - `correct`, with a value of the field's type and a reason;
    - `reject`, with a reason.
  - The review names an **existing** income source on the case, and optionally a frequency.
  - 201 returns every decision, with the extracted and corrected values and the figure
    saved, if any.
- **`GET …/review`** (`document:read`) returns the saved review.
- **Only financial fields become figures** (gross and net pay, pay lines, deductions and year
  to date) as `source_kind = 'document'` observations on the income source.
  - Pay date, period, frequency, tax code and total deductions are reviewed **evidence
    only**.
  - The accepted or corrected pay date, period and frequency set the figures' period.
    Flow figures use the frequency; year-to-date figures are as of the pay date.
- **Unrecognised lines** are saved only if the broker assigns an existing, applicable
  measure (D2).
- **Bonus and allowance** take the broker's qualifier, `unspecified` by default.
- **Reuse:**
  - Figures are saved by one new financial-service function, `record_document_observations`.
    It shares the existing measure checks with `record_observation`, now factored into
    `_checked_definition`: measure, deprecation, value type, qualifier,
    `ensure_applicable`, a withdrawn item and the applicant's status.
  - Permissions use the existing `financial:write` and `document:read`.
  - Audit uses the existing `audit.record`, with one `document.reviewed` event.
  - Later corrections use the existing financial endpoints.
- **Never:** creating, changing or matching income sources automatically; reconciliation;
  affordability.

## 3. Separate records and provenance

| Record | Holds | Changes after creation |
|---|---|---|
| `extracted_fields` (2-C) | raw text, normalised value, evidence boxes | never (insert-only) |
| `document_reviews` | income source, frequency, reviewer, time; one per run | never |
| `field_reviews` | decision, **corrected value beside the original**, reason, measure and qualifier; one per field | never |
| `financial_observations` (`document`) | the approved figure, linked by `field_review_id` | only retirement, as for every observation |

**The database enforces the links:**
- a `document` observation **must** have a `field_review_id`, and only a `document`
  observation may have one (CHECK);
- one observation per decision (unique);
- every link is a composite foreign key within one tenant and case.

A test follows a saved net pay figure back through its decision, field (raw text
`£2,451.96`), run (`synthetic-fixture`) and document version to the original's SHA-256.

## 4. Atomicity, concurrency and duplicates

- **Validation before any write**, covering:
  - the case, document status and version;
  - one decision per field, with no unknown or duplicate fields;
  - corrections and reasons;
  - the measures, qualifiers and applicability;
  - non-negative amounts for money measures;
  - the pay date and frequency;
  - the income source (on the case, not withdrawn, right applicant).
- **One transaction:** any failure, including the audit write, leaves no review, decisions
  or figures, and the document stays `extracted` (tested with an invalid last decision and
  with a forced audit failure).
- **Concurrency:**
  - The case row lock serialises submissions.
  - "Already reviewed" is checked **before** `If-Match`, so a concurrent or retried
    submission gets **409 `already-reviewed` with the existing review's ID**, not a
    misleading 412.
  - `UNIQUE (run_id)` refuses a second review row underneath (tested with raw SQL).
  - Two simultaneous submissions give exactly one 201 and one 409, and one set of figures.

## 5. Migration `0015_document_reviews`

- New tables `document_reviews` and `field_reviews`: RLS, insert-only for the runtime role,
  composite foreign keys.
  - CHECKs: a decision from a closed list; exactly one corrected value for a correction and
    none otherwise; a reason for a correction or rejection (**NULL-safe**: a plain
    `length(trim(reason)) > 0` would let NULL through, because a CHECK passes on NULL);
    a rejected field carries no measure.
- `financial_observations.field_review_id`, with the lineage CHECK and uniqueness;
  `source_kind` gains `document`, which stays outside the current-value index.
- `extracted_fields` gains `UNIQUE (tenant_id, case_id, id)` as a foreign-key target.
- `documents` status gains `reviewed`.
- **Downgrade:** refuses while reviews or document figures exist (tested).

## 6. Tests

| Area | Tests (`test_review.py`, 25) |
|---|---|
| Happy path | All 3 layouts: only financial fields saved; values equal the extracted values; frequency from the payslip or sent (the "earnings" layout prints none); year to date as of the pay date; document `reviewed` |
| Provenance | Saved figure traced back to the original's SHA-256 |
| Corrections | The corrected value is saved; the field keeps its original; the decision holds both plus the reason; a rejection saves nothing; a corrected pay date moves the as-of date |
| Assigned measures | A negative line under a money measure is refused; an inapplicable measure (`net_profit`) is refused; a valid assignment is saved |
| Qualifiers | The broker's qualifier is saved for bonus |
| Atomicity | An invalid last decision, and a forced audit failure: nothing saved |
| Concurrency and duplicates | 2 simultaneous submissions give 201 and 409 with the review ID and one set of figures; a retry gives 409; stale `If-Match` gives 412; a raw duplicate review row is refused |
| Validation | Missing, unknown or duplicate decisions; a correction without a reason or of the wrong type; a measure on a descriptive field; no pay date left; a qualifier on a non-qualified measure; an unknown frequency; an income source on another case, of another applicant, withdrawn or unknown; an unrecognised document |
| Database | A document figure without lineage, or a declared one with it, is refused; reviews and decisions can't be updated or deleted |
| Isolation | Another firm gets 404, and sees 0 reviews and 0 decisions with real rows present |
| Audit and logs | One `document.reviewed` event; corrections and reasons only in **erasable details**; nothing in logs |
| Existing suites | Authorization matrix (save: case workers; view: everyone), cross-tenant probes, audit-route coverage (with a preparation hook, because preparing a reviewable document writes its own events), schema policy, module boundaries (`documents` may use `financial`; never the reverse), 0015 downgrade guard, OpenAPI |

## 7. Known limitations

1. **A document is reviewed once.** Re-review waits for reprocessing; later corrections use
   the financial endpoints (retire, then a broker entry).
2. **Lines aren't summed.** Two overtime lines give two overtime figures; combining figures
   is reconciliation (Stage 2).
3. **The frequency mapping is a small fixed list** ("Monthly", "Weekly", "Four-weekly" and so
   on). Anything else must be sent with the review.
4. **No name or identity checks;** the income source must be chosen by the broker.
5. **Synthetic documents only,** as in 2-C. Real-world behaviour is unknown until D10 and a
   real provider.
6. Carried forward: Docker verification, the PDF memory limit, the dedicated worker role,
   real Azure, D10, and download access logging (R17).

## 8. Files

| | Files |
|---|---|
| New | `migrations/versions/0015_document_reviews.py`; `src/meip/documents/review.py`; `tests/integration/test_review.py`; the 2-D plan and this report |
| Changed | `documents/models.py` (reviews, decisions, `reviewed`), `documents/schemas.py` (review input and output), `api/routers/documents.py` (two routes), `financial/service.py` (`_checked_definition` shared with `record_observation`; `record_document_observations`), `financial/models.py` (`field_review_id`, lineage CHECK), `financial/measures.py` (`SourceKind.DOCUMENT`), `financial/schemas.py` (`field_review_id` on observations), `audit/service.py`, `models.py`; tests: audit, the authorization matrix, cross-tenant, schema policy, module boundaries, `test_schema.py`; `openapi.json`; README; ARCHITECTURE; the retention brief (categories 4 and 6a) |

## 9. Proposed commit (not made: awaiting your approval)

```
Phase 2.0 step 2-D: review, correct and save payslip figures to the case
```
