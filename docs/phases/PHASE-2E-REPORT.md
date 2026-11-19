# Step 2-E — Minimal Review Screen: Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** · Plan:
[PHASE-2E-PLAN.md](PHASE-2E-PLAN.md) (revision 2, approved with the OpenAPI fix) ·
Baseline: `bb0e6a2`

**Synthetic data only.** Every document in the walkthrough was a synthetic payslip from the
generator, uploaded to two synthetic firms in a local demo database.

## 1. Verification results

| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17.4) | **909 passed**, 1 deselected, 0 failed; coverage **94.95 %** |
| Backend ruff and `mypy --strict` | clean (127 files formatted; 109 source files type-checked) |
| Backend isolation suite, run on its own | **133 passed** |
| Frontend vitest | **62 passed** |
| Frontend `tsc --noEmit` and `eslint .` | clean |
| Frontend production build | succeeds; development sign-in absent in production (section 6) |
| Manual walkthrough | all steps passed (section 4); evidence-box alignment within 1.34 px on all 3 layouts at 125 % and 200 % (section 5) |

## 2. What was built

- **`frontend/`:** Next.js 15.5.29 (App Router) and React 19.3.1, with strict TypeScript.
  - Runtime dependencies: `next`, `react`, `react-dom` and `pdfjs-dist` 5.7.284.
  - Development only: `typescript`, `eslint` with the Next.js config, and `vitest`.
  - **No** UI kit, CSS framework, state library or component system: one stylesheet and
    React state.
- **The forwarder,** `app/api/meip/[...path]`: the browser talks only to the Next.js app,
  which forwards an **explicit allowlist** of 14 method-and-path patterns (the endpoints in
  the plan) to the API. It adds the bearer token and firm from cookies, and passes problem
  responses through unchanged. Anything else is refused.
- **Session:**
  - **Development-only sign-in:** it exists only when `MEIP_UI_DEV_SIGN_IN=true` and not in
    a production build. The token is checked against `/me`, then stored in an **httpOnly,
    Secure, SameSite=Strict** cookie.
  - The **firm is chosen from `/me`** (only firms where the user is a member are offered or
    accepted).
  - Sign-out clears both cookies.
- **Screens:**
  1. **Cases:** choose the firm, then the case.
  2. **Case:** documents and their status (the page refreshes itself while any are
     processing); upload a PDF for an applicant; income sources, with "add employment
     income source" (an explicit action).
  3. **Review:**
     - **Left:** the original rendered with pdf.js, with **value and label evidence boxes**
       drawn over it, plus pdf.js's invisible, selectable text layer, and a zoom control.
     - **Right:** fields by section, with confidence labelled "indicator", and the accept,
       correct or reject controls (a typed value and a reason). There's a measure picker for
       uncoded lines and a qualifier for bonus and allowance.
     - "Accept all" fills explicit decisions. **Save is disabled until every field has a
       complete decision**, then sends `If-Match`.
     - 409, 412 and 422 problems are shown.
     - A reviewed document is shown read-only, with the original beside any correction.
- **Backend (approved fix):** the review POST now documents 404, 409, 412 and 428 in OpenAPI.
- **Demo:** `backend/scripts/make_synthetic_payslip.py` writes a labelled synthetic payslip.

## 3. Defects found by the walkthrough, and fixed

The manual walkthrough found two real backend defects and two frontend defects, which the
automated tests had not caught.

| # | Defect | Cause | Fix and regression test |
|---|---|---|---|
| 1 | **The standalone worker (`meip-worker`) failed every job** with `NoReferencedTableError` | It imported only the document models. Foreign keys to cases, applicants and users couldn't be resolved. Tests always run with the whole app imported, and the only subprocess test was "refuses to start", which never processed a job. **This dates from step 2-B.** | The worker's entry point loads the full model registry (`meip.models`). The test runs fresh interpreters importing only the worker module: without the registry the error reproduces, and with it every foreign key resolves. |
| 2 | **One escaped error stopped the worker process** | The first failure's retry bookkeeping raised the same error outside any handler, and the loop ended | The worker loop logs such an error by type and carries on (the job's lock expires and it is claimed again, counting attempts). Tested with a loop step that raises. |
| 3 | **pdf.js text layer misaligned** (spans wider than the text) | My cut-down CSS left out pdf.js's sizing and scaling rules | pdf.js's own essential text-layer rules are used. (The evidence boxes didn't depend on this; the text layer is the alignment reference.) |
| 4 | **Pages sometimes never rendered** (no boxes, no error) | Effect restarts (zoom, React's double-run in development) called pdf.js `render()` on a canvas still rendering; pdf.js refuses that, and the error was swallowed | Each run cancels its own render and text-layer tasks on cleanup, the next run waits for the previous one to stop, and a real failure is shown. After the fix: rapid zoom changes followed by a complete render. |

**Demo-data note:** because of defect 1, one job was left holding a 10-minute lock with all 3
attempts used. As an operator action on the **demo** database, I returned it to the queue.
In normal operation it would have ended as `retries_exhausted`, as designed.

## 4. Manual end-to-end walkthrough (in-app browser, local stack)

**Stack:**
- PostgreSQL 17.4 with a fresh `meip_demo` database, migrated to head;
- the API (uvicorn);
- the worker (`python -m meip.documents.processing`);
- the frontend (`npm run dev`).

**Data:** two synthetic firms, "Synthetic Demo Brokers" and "Other Firm (isolation
check)", and one synthetic case (`SYN-DEMO-001`) with one synthetic applicant.

| Step | Result |
|---|---|
| 1. Signed out | Home shows "sign-in not configured", with the link to the development sign-in; `/me` returns 401 |
| 2. Development sign-in | The token is accepted; **`document.cookie` is empty and local and session storage are empty** (httpOnly). The response sets `meip_session` with `Path=/; Max-Age=28800; Secure; HttpOnly; SameSite=strict`. Security headers present: Content-Security-Policy, `nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`. |
| 3. Choose the firm from `/me` | Only "Synthetic Demo Brokers (firm_admin)" is offered; the case list appears |
| 4. Income source | "Add employment income source" creates one for the applicant |
| 5. Upload | Three synthetic payslips (classic, earnings, two pages) uploaded **through the form's own file input and Upload button**. The files were served from a temporary `public/_demo/` folder because the browser's file picker can't be automated. **The folder has been removed** (`public/` now holds only the generated, git-ignored pdf.js worker). |
| 6. Processing | The status refreshes from "Processing" to "Ready to review" (after the defect 1 fix) |
| 7. Evidence boxes | Section 5 |
| 8. Decide and save (two-page payslip) | Save disabled at 0 of 12; "Accept all" enables it ("9 figures"). Choosing "correct" for net pay disables it again ("Enter the corrected value") until the value **647.00** and a reason are given. After saving, the read-only review shows the original **£647.24 → £647.00** with the reason; dates, frequency and total deductions are reviewed but not saved as figures. |
| 9. Figures and duplicates | Document `reviewed`. The financial API holds exactly **9 `document` figures**, each linked to its decision: net pay 64,700 pence (the correction), weekly frequency on flow figures, year to date as of 2026-03-06. Resubmitting gives **409 `already-reviewed`** with the existing review's ID. |
| Other firm | Signed in as the other firm's admin. Choosing the demo firm: **403**. Every route on the demo firm's case, document, extraction, original and review: **404**, and a review POST: 404. **A forged firm cookie** (the other user's token with the demo firm's ID): **403 "You do not have access to this firm"** on every route. The firm ID is never treated as proof of access. |
| Forwarder | Unlisted routes (`members`, `events`, `tenants`, an encoded `..%2F` path): 404. `DELETE`: 405. No session: 401. |

## 5. Evidence-box alignment

**Method:** for every extracted field, each drawn box (value and label) was compared, in the
browser, with the pdf.js text-layer span holding the same text. pdf.js's text layer is
positioned by pdf.js from the PDF's own geometry, independently of our conversion. Page
sizes are A4 (595 × 842 pt) in both the extraction and pdf.js, and the code checks this
before drawing anything.

| Layout | Zoom | Boxes checked | Max left-edge Δ | Max right-edge Δ | Max vertical-centre Δ |
|---|---|---|---|---|---|
| Classic (1 page) | 125 % | 34 / 34 | 0.04 px | 0.72 px | 0.84 px |
| Classic (1 page) | 200 % | 34 / 34 | 0.06 px | 1.14 px | 1.34 px |
| Earnings (1 page) | 125 % | 24 / 24 | 0.04 px | 0.72 px | 0.84 px |
| Earnings (1 page) | 200 % | 24 / 24 | 0.06 px | 1.15 px | 1.34 px |
| Two pages | 125 % | 24 / 24 (12 on page 1, 12 on page 2) | 0.04 px | 0.27 px | 0.83 px |
| Two pages | 200 % | 24 / 24 (12 on page 1, 12 on page 2) | 0.06 px | 0.41 px | 1.34 px |

- On the two-page payslip, every box was drawn on the page its evidence names.
- The differences scale with zoom (about 0.67 pt at most). That is the gap between the
  generator's font metrics and pdf.js's line box, well inside one character.
- Screenshots at 125 % and 200 % show each grey label box and blue value box over its text,
  and no box on text that wasn't extracted (the "Payments" heading, the units and rate
  columns).
- The conversion itself is also unit-tested: points and inches, four scales, the page edge,
  page 2, non-uniform scaling, the rounding tolerance, and refusal of mismatched page sizes,
  rotated pages, unknown units and malformed boxes.

## 6. Tests

| Suite | Result |
|---|---|
| Frontend vitest | **62 passed**: coordinates (13), decisions (20: money parsing, completeness, corrections, figures, the exact request body, money formatting), forwarder allowlist, sign-in gate, cookie attributes, problem messages (29) |
| Frontend `tsc --noEmit` and `eslint .` | clean |
| Frontend production build | **Succeeds** (`next build`, run after stopping the dev server): 8 routes. Then `next start` was run once with `MEIP_UI_DEV_SIGN_IN=true` still set: **`/dev-sign-in` and `POST /api/session` return 404**, and the production CSP has no `unsafe-eval`. |
| Backend (PostgreSQL 17.4) | **909 passed** (907 before 2-E, plus the 2 worker regression tests), 1 deselected; coverage **94.95 %**; ruff and `mypy --strict` clean (109 files); isolation suite on its own **133 passed** |

## 7. Known limitations

1. **Development sign-in only.** Real Entra sign-in is Stage 3. In production the screen
   can't sign anyone in, which is fail-closed.
2. **The CSP allows inline scripts,** which Next.js needs without per-request nonces. A
   nonce-based CSP is a hardening item.
3. **ESLint 9** is what Next.js 15's lint config targets, and npm reports it as out of
   support. Moving to Next.js 16 and ESLint 10 is a later upgrade.
4. **Rendering pauses while the browser tab is hidden** (pdf.js schedules drawing with
   animation frames). This is normal browser behaviour; it resumes when the tab is shown.
5. **No automated browser tests** (E2: deferred to Stage 3).
6. **The frontend isn't containerised;** it runs with `npm run dev` or `npm start` (Stage 4).
7. **No access logging** of original downloads yet (R17, before real documents).
8. **The screen relies on the API for permissions.** It hides nothing role-specific beyond
   what the API refuses; for example, a read-only user sees the decision controls and gets
   a 403 on save.

## 8. Files

| | Files |
|---|---|
| New | `frontend/`: `package.json` and lockfile, `next.config.ts` (security headers), `tsconfig.json`, `eslint.config.mjs`, `vitest.config.ts`, `.gitignore`, `scripts/copy-pdf-worker.mjs`, `app/` (layout, styles, home, case, review, dev sign-in, session routes, forwarder), `components/PdfView.tsx`, `lib/` (types, coordinates, decisions, problems, allowlist, server config, API client), `tests/` (3 files). Also `backend/scripts/make_synthetic_payslip.py`, `.claude/launch.json` (local preview), this report. |
| Changed (backend) | `api/routers/documents.py` (OpenAPI responses), `documents/processing.py` (registry import, resilient loop), `tests/integration/test_processing.py` (2 regression tests), `tests/unit/test_module_boundaries.py` (registry import allowed), `openapi.json` |
| Changed (docs) | README (running the frontend), ARCHITECTURE (frontend section) |

## 9. Proposed commit (not made: awaiting your approval)

```
Phase 2.0 step 2-E: minimal review screen; fix the standalone worker
```
