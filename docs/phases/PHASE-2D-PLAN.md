# Step 2-D — Review, Correct and Save to the Case: Plan (revision 2)

Status: **Approved and implemented (see PHASE-2D-REPORT.md)** · Date: 2026-11-19 · Part of
[PHASE-2.0-PLAN.md](PHASE-2.0-PLAN.md) · Baseline: `1a9119d`

**Decisions (approved):**
- **D1:** every extracted field gets an explicit accept, correct or reject. The screen may
  offer "accept all", which still sends explicit decisions.
- **D2:** a broker may optionally assign an existing, applicable measure to an uncoded
  payment or deduction line.
- **D3:** `financial:write` saves a review, and `document:read` views one.

Revision 2 adds your six clarifications.

## Outcome

A broker reviews a payslip's extracted fields, corrects any that are wrong, and saves the
approved financial figures to an **existing** income source on the case. Every saved figure
stays traceable to the decision, field, page and box it came from.

## 1. Only real financial figures become observations

| Field | Saved as a financial figure? | Its role |
|---|---|---|
| `gross_pay`, `net_pay`, `basic_pay`, `overtime`, `commission`, `bonus`, `allowance`, `income_tax`, `national_insurance`, `pension_contribution`, `student_loan_deduction`, `ytd_gross_pay`, `ytd_income_tax` | **Yes**, when accepted or corrected (each maps to an existing measure) | `bonus` and `allowance` take the broker's qualifier, `unspecified` by default |
| An uncoded payment or deduction line | **Only if the broker assigns** an existing measure that applies (D2) | Otherwise reviewed evidence only |
| `pay_date`, `pay_period_start`, `pay_period_end`, `pay_frequency`, `tax_code`, `total_deductions` | **Never** | **Reviewable evidence.** They are decided like any other field (accept, correct or reject). The pay date, period dates and frequency, as accepted or corrected, set the **period and frequency** of the saved figures. The tax code and total deductions are kept as reviewed evidence only. |

## 2. Separate records with complete provenance

Nothing overwrites anything. Each layer is its own record:

| Record | Holds | Linked to |
|---|---|---|
| `extracted_fields` (2-C, unchanged, immutable) | raw text, normalised value, page, value and label boxes, confidence | run, then version, then document |
| `document_reviews` (new, insert-only) | income source, frequency used, reviewer, time | run (one review per run) |
| `field_reviews` (new, insert-only) | decision; **corrected value beside the original**; reason; measure and qualifier chosen | review and field (one decision per field) |
| `financial_observations` (existing) with `source_kind = 'document'` | the saved figure (the correction if there was one, otherwise the extracted value) | **its field review**, and through it the field, run, version and document |

- **Database guarantees:**
  - Every link is a composite foreign key within one tenant and case.
  - A `document` observation **must** link to a field review, and only a `document`
    observation may (CHECK).
  - There is at most one observation per field review (unique).
- **Following any saved figure backwards** gives: decision (who, when, why) → field (raw
  text, original value) → page and box → run (provider, versions) → original document
  (SHA-256).

## 3. Atomic review

The review is **all or nothing**.
1. **Everything is validated before anything is written.** Validation covers:
   - the case is editable;
   - the document is `extracted` and not yet reviewed;
   - the income source exists on the case, isn't withdrawn, and belongs to the document's
     applicant if one is named;
   - there is exactly one decision for every field, and none for unknown fields;
   - corrections are valid for the field's type and have a reason;
   - rejections have a reason;
   - assigned measures exist and apply, and qualifiers are valid;
   - a pay date is available, and the frequency is known (or given in the request).
2. Only then does **one transaction** write the review, the decisions and the observations,
   set the document to `reviewed`, and write the audit event.
3. **Any failure rolls back the whole transaction:** a validation error, a database
   constraint, or the audit write. No partial figures are saved, and the document is not
   marked reviewed.

## 4. Concurrent and duplicate submissions

| Protection | Effect |
|---|---|
| **Case row lock** (as every case write does today) | Two submissions for the same case are serialised; the second sees the first's result |
| **Status check under the lock** (`extracted` only) | The second submission gets 409 `already-reviewed`, with the existing review's ID |
| **`UNIQUE (run_id)` on `document_reviews`** | The database refuses a second review even from a writer that bypasses the service |
| **`If-Match` on the document** | A broker working from stale data gets 412 |
| **Unique field review per field; unique observation per field review** | Figures can't be duplicated within a review |

A retried request after a successful save gets 409 with the existing review's ID, never a
second set of figures.

## 5. Income sources

The broker chooses an **existing** income source, creating one first through the existing
endpoint if needed. The review never creates, changes or matches income sources
automatically.

## 6. Reuse

- **Figures:** one new function in the financial service, `record_document_observations`.
  It uses **the same checks** as `record_observation`: measure deprecation, value type,
  qualifier, `ensure_applicable`, a withdrawn item, and the applicant's status. It doesn't
  duplicate them, and the static guard test continues to require `ensure_applicable`.
  The `documents` module may call the financial service; the financial module still may not
  import `documents`.
- **Permissions:** the existing `financial:write` and `document:read` route dependencies.
- **Audit:** the existing `audit.record`. One `document.reviewed` event per request (the
  existing "one event per mutating route" rule). It records counts, decision codes, the
  income source ID and the observation IDs; **corrected values and reasons are erasable
  details**.
- **Corrections after saving:** the existing financial endpoints (retire as
  `entered_in_error`, then record a broker entry). No new mechanism.

## 7. API

- `POST /api/v1/cases/{case_id}/documents/{document_id}/review` (`financial:write`,
  `If-Match` on the document). The body has the income source ID, an optional frequency,
  and a decision per field, identified by its ordinal: `accept`, `correct` (value and
  reason), or `reject` (reason), plus an optional measure and qualifier.
  - 201 returns the review and its observations.
  - 409 when already reviewed or the document isn't `extracted`; 412 for a stale ETag; 422
    for a validation error.
- `GET /api/v1/cases/{case_id}/documents/{document_id}/review` (`document:read`): each
  decision with the original and corrected values, and the linked observation IDs.
- Observation responses gain `field_review_id`.

## 8. Migration `0015`

- New tables `document_reviews` and `field_reviews` (RLS, insert-only, composite foreign
  keys, the uniqueness rules above, and CHECKs on decision consistency).
- `financial_observations.field_review_id` with the CHECK and uniqueness above;
  `source_kind` gains `document`, which stays outside the current-value index.
- `documents` status gains `reviewed`.
- The downgrade refuses while reviews exist.

## Tests

- **Happy path, for each of the 3 synthetic layouts:** accept all.
  - Observations are created **only** for financial fields.
  - The descriptive fields are recorded as decisions with no observation.
  - The figures equal the extracted values, with the payslip's period and frequency.
  - Each figure's provenance chain resolves back to the original's SHA-256.
- **Corrections and rejections:**
  - A corrected figure holds the new value, while the field keeps the original and the
    decision holds both plus the reason.
  - A corrected pay date moves the figures' period.
  - Rejected fields create nothing.
  - An uncoded line saved under an assigned measure; an inapplicable assigned measure
    refused.
- **Atomicity:** the first decisions valid and the **last one invalid** leaves nothing saved
  and the document `extracted`. A forced audit failure also leaves nothing saved.
- **Concurrency and duplicates:**
  - **Two simultaneous submissions** give one 201 and one 409 with the existing review ID.
  - A sequential retry gives 409.
  - A raw duplicate review row is refused by the unique constraint.
  - A stale `If-Match` gives 412.
- **Validation:** a missing decision, an unknown field, a correction without a value or
  reason, a withdrawn income source or one on another case or applicant, no pay date, an
  unknown frequency, a deprecated measure or qualifier, and a document not `extracted`.
- **Database:** a `document` observation without a field review is refused; a declared
  observation with one is refused; reviews and decisions can't be updated or deleted; a
  cross-tenant link is refused.
- **Security:**
  - The new routes are in the authorization matrix (read-only users can read but not
    save) and the cross-tenant probes.
  - The isolation checks include real review rows; the schema policy covers the new tables.
  - Corrected values and reasons appear only in erasable audit details, never in logs.
- **Gates:** migrations and drift, ruff, mypy, the full suite.

## Out of scope

Reconciliation, affordability calculations and automatic income-source creation, plus: the
screen (2-E), re-review or reprocessing, summing lines, name or identity checks, and other
document types.

## Decisions

None outstanding: D1 to D3 are approved, and the clarifications are incorporated.
