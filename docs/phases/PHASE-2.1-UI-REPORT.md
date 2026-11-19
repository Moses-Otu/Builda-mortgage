# Phase 2.1 — Broker UI Redesign: Report

Date: 2026-11-19 · Status: **Implemented and verified; awaiting review (not committed)** · Plan:
[PHASE-2.1-UI-PLAN.md](PHASE-2.1-UI-PLAN.md) (approved with U1 and U2) · Baseline: `d850e54`

**Synthetic data only.** Every document in the walkthrough was a synthetic payslip from the
generator, in a local demo database.

## 1. Verification results

| Gate | Result |
|---|---|
| Frontend vitest | **99 passed** (62 before, 37 new); none of the existing tests changed except the allowlist cases for U2 |
| `tsc --noEmit` and `eslint .` | clean |
| Production build (`next build`) | succeeds; `next start` with `MEIP_UI_DEV_SIGN_IN=true` still gives **404** for `/dev-sign-in` and `POST /api/session`; CSP `script-src 'self' 'unsafe-inline'` (no `unsafe-eval`, as before) |
| Evidence-box alignment | 24 / 24 boxes matched to pdf.js's text layer at **115 %** (fit width) and **215 %**: max Δ 0.04 / 0.06 px left, 0.65 / 1.23 px right, 0.77 / 1.44 px vertical centre (2-E: 1.34 px at 200 %) |
| Manual walkthrough | all steps passed (section 4); two defects found and fixed (section 3) |
| Backend | **unchanged** (no backend file touched, OpenAPI snapshot unchanged), so the suite wasn't rerun; 909 passed earlier today on the same commit |

## 2. What was built

### Foundation (U1)
- **Tailwind CSS 4.3.3** (build-time only) with tokens for brand, accept, correct and
  reject colours; **Inter** self-hosted through `next/font` (served from `/_next/static/media`,
  so CSP `font-src 'self'` is unchanged).
- **Owned components** in `components/ui/`: button, badge, card, input/select/textarea,
  dialog, tooltip, dropdown menu, progress, toast, alert. Built on Radix primitives for the
  dialog, tooltip and menu (focus handling, keyboard, screen readers); the select is a styled
  native `<select>`.
- New runtime dependencies, pinned exactly: `@radix-ui/react-dialog` 1.3.1,
  `react-tooltip` 1.4.1, `react-dropdown-menu` 2.2.1, `react-slot` 1.5.0,
  `class-variance-authority` 0.7.1, `clsx` 2.1.1, `tailwind-merge` 3.7.0,
  `lucide-react` 1.66.0. Dev: `tailwindcss` and `@tailwindcss/postcss` 4.3.3.
  No state, data-fetching or form library.

### Screens
- **App shell:** header with the product mark, the "Synthetic data only" badge (tooltip
  explains D10), a **firm switcher** and an account menu with sign-out. Breadcrumbs on every
  page. Switching firm reloads the page's data.
- **No firm / signed out (P3):** every page shows "Choose a firm to continue" (with the
  firms from `/me`) or "Sign in to continue", instead of "Invalid request".
- **Cases:** table with status badges and clickable rows; empty state; **New case** dialog
  (reference and purpose) (U2).
- **Case page:** header with purpose, status and applicant chips; **Add applicant** dialog
  (U2); the **three-step strip** (income source → upload → review) worked out from the real
  state; **drag-and-drop upload**; document cards with status badges, spinner while
  processing, and **plain-English failures** (`lib/failures.ts`, the table from the plan).
- **Review screen:** full-height workspace, the PDF and the decision panel each scrolling on
  their own.
  - Panel header: progress bar, accepted / corrected / rejected counts, **Accept remaining**
    (only undecided fields; corrections and rejections are kept), shortcuts button, and the
    filter **All · Undecided · Corrected or rejected**.
  - Compact rows: label, details tooltip (code, printed text, page), value, low-confidence
    pill only below 80 % (still "indicator"), a green / amber / red **Accept · Correct ·
    Reject** control and a matching coloured edge. Corrected and rejected values are struck
    through.
  - Sections collapse, with a done/total count each.
  - **Two-way linking:** selecting a row scrolls to its box and pulses it; clicking a box
    selects and scrolls to its row; hover highlights the other side. Value boxes take the
    decision's colour.
  - **Keyboard:** ↑/↓ (or J/K), A (accept and move on), C (correct: focus the value),
    R (reject: focus the reason), Enter (value → reason → next field), Esc, ? for help.
    Never while typing, never while a dialog is open.
  - **PDF toolbar:** zoom −/+, percentage, **Fit width** (the default).
  - **Save dialog:** income source and frequency, accepted / corrected / rejected counts,
    the exact summary ("7 figures will be saved to Employment income — Alex Example,
    monthly. 1 corrected, 1 rejected.") and the "can't be changed once saved" note. If
    there's no income source, the dialog offers to add one. 409, 412 and 422 messages show
    in the dialog; 409 closes it and shows the saved review.
- **Saved review:** the same row layout, decision badges, ~~extracted~~ → **corrected**,
  reasons in quotes, "Saved as …" for figures; linking to boxes works the same way.
- **Development sign-in:** same behaviour, styled card.

## 3. Defects found by the walkthrough, and fixed

| # | Defect | Fix |
|---|---|---|
| 1 | After typing a reason, **Enter moved the selection but kept the cursor in the box**, so the next shortcut keys were typed into the reason ("…statementaa") | Enter and Esc now leave the box; Enter in a corrected value goes on to its reason. Re-run of the whole keyboard flow: values intact, focus on the page, selection on the next field. |
| 2 | **The "earnings" synthetic layout prints no pay frequency.** Saving with "As shown on the payslip" got the server's 422 ("The pay frequency is unknown"). The old screen had the same gap. | `payslipFrequency()` mirrors the server's recognised words; the dialog shows "As shown on the payslip (Monthly)" when known, otherwise asks "Choose…" with a hint and keeps Save disabled. The server stays the authority. 6 tests. |

## 4. Manual walkthrough (in-app browser, local stack)

Stack: Postgres 17 (Docker, port 5433), API (uvicorn), worker (`meip-worker`), frontend
(`npm run dev`). Firm "Synthetic Demo Brokers" (alice); a second firm "Other Firm
(isolation check)" (bob) created for step 8.

| Step | Result |
|---|---|
| 1. Signed in, no firm chosen | "Choose a firm to continue" with the one membership; header switcher says "Choose a firm" |
| 2. New case (U2) | `SYN-DEMO-002`, Purchase: toast, then the case page |
| 3. Add applicant (U2) | "Alex Example": chip in the header; the step strip appears with step 1 current |
| 4. Add employment income | step 1 ticked, step 2 current |
| 5. Upload by drag and drop | A drop event with the generated "earnings" payslip (served from a temporary `public/_demo/`, **since removed**: the file picker can't be automated) shows name, size and applicant; Upload → "Processing" with spinner → "Ready to review"; step 3 current |
| 6. Upload the re-saved PDF | "Failed" with: *We couldn't read figures from this PDF … a PDF opened and re-saved in a viewer loses its test data.* |
| 7. Review by keyboard | ↓, A×5, C → 2,959.00 → Enter → reason → Enter, A×2, R → reason → Enter, A×3: **12 of 12 decided**, counts 10 / 1 / 1; filters showed exactly the 2 changed and then 3 undecided fields; R pressed with the dialog open changed nothing |
| 8. Save | Dialog asked for a frequency (defect 2); Monthly → Save → "Saved review" with **£2,949.35 → £2,959.00** and its reason, the rejected line struck through with its reason, and "7 figures saved" |
| 9. Figures | Financial API: **7 `document` figures**, all monthly, period end 2026-02-28; `net_pay` **295,900** pence (the correction); no figure for the rejected line |
| 10. Duplicate | A second valid save: **409 `already-reviewed`** with the existing review ID |
| 11. Other firm, through the forwarder | bob in his own firm: **404** on alice's case applicants, document, original, review, and on adding an applicant; bob with a forged firm cookie: **403** on all; setting the firm cookie to a non-member firm: **403**; `GET /api/session/firm` without a session: **401** |

## 5. Differences from the plan

- **`GET /api/session/firm`** (a Next.js route, not the API) tells the page which firm the
  httpOnly cookie holds, so the header can show it. It returns only the ID the browser
  itself chose, needs a session (401 otherwise), and is still not proof of access.
- **The case reference for headers** comes from the case list (`GET cases?limit=100`, already
  allowed) rather than a new allowlisted `GET cases/{id}`, keeping the allowlist change to
  the two approved entries. Beyond 100 cases the header falls back to "Case 01a1f3b8"; a
  `GET cases/{id}` entry is the fix when that matters.
- **The frequency prompt** (defect 2) wasn't in the plan; it fixes an existing gap.
- The new-case dialog says not to use a client's name in the reference (data minimisation).

## 6. Known limitations

1. Development sign-in only (unchanged; Entra is Stage 3).
2. CSP still allows inline scripts (unchanged from 2-E).
3. No automated browser tests (unchanged; E2 deferred). The new logic is unit-tested and the
   UI was walked through by hand.
4. Desktop only: the layout holds at 1280 px; there's no mobile layout (out of scope).
5. The screen still relies on the API for permissions: a read-only user sees the controls
   and gets a 403 on save.
6. `npm audit` reports advisories in the dependency tree (it did before this step as well);
   not triaged here.

## 7. Files

| | Files |
|---|---|
| New | `postcss.config.mjs`; `components/ui/*` (10); `components/AppHeader.tsx`, `Page.tsx`, `Providers.tsx`, `session.tsx`, `NewCaseDialog.tsx`, `AddApplicantDialog.tsx`, `useCaseSummary.ts`; `components/review/FieldRow.tsx`, `SaveReviewDialog.tsx`, `SavedReview.tsx`, `ShortcutsDialog.tsx`; `lib/utils.ts`, `labels.ts`, `failures.ts`, `case-steps.ts`, `review-ui.ts`; `tests/ui.test.ts`; this report |
| Changed | `app/layout.tsx`, `globals.css`, `page.tsx`, `cases/[caseId]/page.tsx`, the review page, `dev-sign-in/*`, `api/session/firm/route.ts` (GET), `components/PdfView.tsx` (fit width, hover, decision colours, toolbar), `lib/allowlist.ts` (2 entries), `tests/server.test.ts`, `package.json` and lockfile |
| Unchanged | everything in `backend/`; `lib/decisions.ts`, `coordinates.ts`, `api.ts`, `problems.ts`, `server-config.ts`, the forwarder route |

`backend/synthetic-payslip-classic.pdf` (generated for the earlier demo) is untracked and
should not be committed.

## 8. Proposed commit (not made: awaiting your approval)

```
Phase 2.1: broker UI redesign (Tailwind, review workflow, create cases and applicants)
```
