# Step 2-E — Minimal Review Screen: Plan (revision 2)

Status: **Awaiting final approval** · Date: 2026-11-19 · Part of
[PHASE-2.0-PLAN.md](PHASE-2.0-PLAN.md) · Baseline: `bb0e6a2`

**Decisions:**
- **E1:** development-only sign-in, with the token held only in an httpOnly, Secure,
  SameSite cookie, and the route unavailable in production.
- **E2:** vitest plus a documented manual end-to-end walkthrough, with **visual checks of
  the evidence boxes**.

Revision 2 adds your four clarifications.

## 1. The step 2-D dependency: verified, not assumed

Step 2-D is **implemented and committed** (`bb0e6a2`, "Phase 2.0 step 2-D: review, correct
and save payslip figures to the case"), on top of 2-A to 2-C:
- the code is `backend/src/meip/documents/review.py`;
- 25 integration tests in `backend/tests/integration/test_review.py` passed at that commit,
  within a full suite of 907.

The frontend is built **only against these committed endpoints**, checked against the
committed OpenAPI snapshot (`backend/openapi.json`). **No mock API and no workaround.**

### Confirmed API dependencies

All routes are under `/api/v1`. All except `/me` require `X-Tenant-ID`.

| The screen needs | Endpoint | Permission | Contract used |
|---|---|---|---|
| Who am I, and which firms | `GET /me` | signed in | memberships, used to **choose the firm** |
| List cases | `GET /cases` | `case:read` | paged list |
| Applicants on a case | `GET /cases/{case_id}/applicants` | `case:read` | to choose the applicant |
| Income sources | `GET /cases/{case_id}/financial/income-sources` | `case:read` | existing sources |
| Add an income source (explicit broker action) | `POST /cases/{case_id}/financial/income-sources` | `financial:write` | `{applicant_id, kind: "employment"}` |
| Upload a payslip | `POST /cases/{case_id}/documents?document_type=payslip&applicant_id=…` | `document:write` | raw PDF body, `Content-Type: application/pdf`, optional `Upload-Filename`; 201, 409 duplicate, 413, 415, 422 |
| Documents and status | `GET /cases/{case_id}/documents`, `GET …/documents/{document_id}` | `document:read` | `status`, `failure_code`, `page_count`, `version` (the ETag for review) |
| The original PDF | `GET …/documents/{document_id}/content` | `document:read` | the PDF bytes, `no-store` |
| Extracted fields | `GET …/documents/{document_id}/extraction` | `document:read` | `ExtractionOut`: provider, versions, classification, `summary`, `payments`, `deductions`, `year_to_date`, `missing_required`. Each field has `ordinal`, `field_code`, `label_text`, `raw_text`, `value_*`, `value_evidence` and `label_evidence` (page, x0, y0, x1, y1), `page_width`, `page_height`, `unit` and `confidence`. 404 until extracted. |
| Measures for uncoded lines | `GET /financial/measures` | `case:read` | codes, value types, qualifiers. **The server stays the authority on applicability**, and an inapplicable choice gets 422. |
| Save the review | `POST …/documents/{document_id}/review` | `financial:write` | `If-Match`; `ReviewIn`: `income_source_id`, optional `frequency`, and `decisions[]` (`ordinal`, `decision`, optional `corrected_money_minor`, `corrected_date` or `corrected_text`, `reason`, `measure`, `qualifier`). Returns 201 `ReviewOut`. |
| View a saved review | `GET …/documents/{document_id}/review` | `document:read` | `ReviewOut`: decisions with extracted and corrected values, and `observation_id`. 404 until reviewed. |

**A documentation gap, not a functional one:** the OpenAPI snapshot lists only 201 and 422
for the review POST. The service also returns 404, 409 (`already-reviewed`, with
`existing_review_id`), 412 and 428 as problem responses, and the 2-D tests cover them. The
screen handles them all. Adding them to the OpenAPI document is a one-line backend change
I can make in this step if you'd like; it isn't needed for the screen.

## 2. Sign-in and tenancy (E1)

- **Development-only sign-in page:** paste a dev token from `meip-admin dev-token`. The
  server side stores it in a cookie that is **httpOnly, Secure, `SameSite=Strict` and
  `Path=/`**, and never in JavaScript-readable storage.
  - Browsers treat `http://localhost` as secure, so `Secure` works in local development.
- The route **exists only when** `MEIP_UI_DEV_SIGN_IN=true` **and** `NODE_ENV !== "production"`.
  In any other case it returns 404, and the pages say sign-in isn't configured.
  - A production build ignores the flag (tested).
- **The firm is chosen from `GET /me`**, the firms where the user actually holds a
  membership, rather than typed in. The chosen firm ID goes in a second httpOnly cookie and
  is forwarded as `X-Tenant-ID`.
- **The firm ID is never treated as proof of access.** It only says which firm the request
  acts within. The API checks the membership on every request (403 otherwise), and RLS
  isolates the data. The screen holds no permission logic of its own beyond hiding controls.

## 3. How the browser reaches the API

- The browser calls only the Next.js app.
- A single server-side route handler, `app/api/meip/[...path]/route.ts`, forwards each
  request to the FastAPI service, adding `Authorization` and `X-Tenant-ID` from the
  cookies.
- It **only forwards the method and path patterns in the table above** (an explicit
  allowlist). Anything else, including `..`, encoded slashes and absolute URLs, is refused
  with 404.
- It passes the API's status and problem body through unchanged, and passes `If-Match`,
  `Content-Type` and `Upload-Filename`.
- It never logs bodies, tokens or file names.
- The API needs no CORS.

## 4. Screens (three, focused on the workflow)

1. **Cases:** list, and choose one.
2. **Case:**
   - documents, with status and failure reason;
   - **upload** (choose the applicant);
   - income sources, with "add employment income source".
   - The document list is fetched again every few seconds while a document is processing.
3. **Review:**
   - **Left:** the original rendered with pdf.js. Each value and label has its evidence box
     drawn over the page; selecting a field emphasises and scrolls to its box.
   - **Right:** fields by section, with confidence labelled "indicator only", and the
     accept, correct or reject controls (a typed input and a reason). There's a measure
     picker for uncoded lines and a qualifier for bonus and allowance.
   - **"Accept all"** fills explicit decisions. **Save** is disabled until every field is
     decided, then sends `If-Match`.
   - 409 shows the existing review; 412 asks the broker to reload; 422 shows the server's
     message.
   - **Reviewed documents** are read-only.

**No** UI framework, CSS framework, state library, generic component system or new
infrastructure:
- plain React state;
- CSS modules;
- a handful of components specific to this workflow (PDF view with boxes, field row,
  decision controls);
- runs with `npm run dev`;
- not containerised (that's Stage 4).

**Dependencies:**
- runtime: `next`, `react`, `react-dom` and `pdfjs-dist`;
- development only: `typescript`, `eslint` with the Next.js config, and `vitest`.

## 5. Evidence boxes: coordinate conversion and visual checks

**The conversion** (one small function, unit-tested):
- The API gives boxes with the **origin at the top left**, in `unit` (`pt`, or `inch`
  converted ×72), on a page of `page_width` × `page_height`.
- pdf.js renders the page at a `scale` into a viewport of `width × height` CSS pixels.
- **Each box is placed** at `left = x0 × width / page_width_pt` and
  `top = y0 × height / page_height_pt`, with its size scaled the same way.
- **Before drawing,** it checks that pdf.js's reported page size (`page.view`, in points)
  matches the API's `page_width` and `page_height`. If they differ, the box isn't drawn and
  a visible warning appears instead, so a misplaced highlight is never shown silently.

**Unit tests (vitest):**
- points and inches;
- several scales;
- page 2 of a two-page document;
- a box at the page edge;
- a page-size mismatch (refused);
- a rotated page (refused, with a warning; no synthetic payslip is rotated).

**Visual verification** (the manual walkthrough):
- For **all three synthetic layouts**, including the **two-page** one, at **two zoom
  levels**:
  - a screenshot of the review screen;
  - a zoomed screenshot of several boxes, showing each over its value and label;
  - in the browser, the drawn overlay's rectangle compared with the matching text span in
    pdf.js's text layer for every field, with the largest difference recorded (expected to
    be within a pixel or two).
- The results and screenshots go in the report.

## 6. Tests and verification (E2)

- **vitest:**
  - coordinate conversion (as above);
  - building decisions ("accept all", every field decided, corrections typed and with a
    reason);
  - building the review request;
  - mapping problem responses to messages;
  - the forwarder's allowlist (allowed paths; refused traversal, absolute URLs and
    unknown paths);
  - the sign-in gate (off when not enabled, and off in production even when enabled);
  - the cookie attributes (httpOnly, Secure, `SameSite=Strict`).
- **`tsc --noEmit`** and **`next lint`**: clean.
- **The manual end-to-end walkthrough,** documented step by step in the report:
  1. dev sign-in;
  2. choose the firm from `/me`;
  3. a case and an applicant;
  4. add an income source;
  5. upload a synthetic payslip;
  6. the status moves from processing to extracted;
  7. the evidence-box checks from section 5;
  8. "accept all", then correct one value with a reason, then save;
  9. the document is reviewed, with the figures visible in the financial API, and a second
     save shows 409.

  The same walkthrough is repeated with a second user signed in to **another firm**: a
  pasted case or document URL gives a not-found page.
- **The demo file:** `backend/scripts/make_synthetic_payslip.py` writes a labelled synthetic
  payslip with the existing generator.
- **The backend's full gate is rerun** to show it's unaffected. Its code is unchanged,
  unless you ask for the OpenAPI documentation fix in section 1.

## 7. Out of scope

Real sign-in (Stage 3), containerising the frontend (Stage 4), reconciliation, calculations,
automatic income-source handling, admin screens, other document types, and a browser test
framework.

## Decisions

None outstanding. **Optional:** the OpenAPI documentation fix for the review POST's error
responses (section 1).
