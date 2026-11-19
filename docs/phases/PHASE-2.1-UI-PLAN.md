# Phase 2.1 — Broker UI Redesign: Plan (revision 1)

Status: **Approved (U1 and U2 as recommended); implemented, see
[PHASE-2.1-UI-REPORT.md](PHASE-2.1-UI-REPORT.md)** · Date: 2026-11-19 · Baseline: `d850e54` · Builds on
[PHASE-2E-REPORT.md](PHASE-2E-REPORT.md)

**Goal:** make the payslip workflow look and feel like a product a broker would trust, and
make the demo flow smoothly from start to finish, **without changing the backend** or
weakening anything step 2-E established (the allowlisted forwarder, httpOnly session, the
tested coordinate conversion, and save-only-when-every-field-is-decided).

**Synthetic data only.** Nothing here changes what data may be used.

## 1. What's wrong today

Seen in the running app and the code (`frontend/app/**`, `globals.css`, about 100 lines):

| # | Problem | Where |
|---|---|---|
| P1 | No visual hierarchy: browser-default selects, radios and file input; one accent colour; everything the same weight | all screens |
| P2 | The header says only "Payslip review". No firm, no user, no sign-out except on the home page, no breadcrumbs (the case page is titled just "Case") | layout, case, review |
| P3 | Opening a link with no firm chosen shows a bare **"Invalid request"** | case, review |
| P4 | Failures show raw codes such as `extraction_failed` and `too_many_pages`, with no explanation or next step | case page |
| P5 | The upload form, income sources and documents compete side by side; the order of work (income source → upload → review) isn't visible | case page |
| P6 | The review list is long and flat: each field is a card with three radio buttons; you can't tell at a glance what's decided, corrected or rejected | review |
| P7 | Selecting a box on the PDF highlights the field, but the field list doesn't scroll to it (and the reverse isn't obvious either) | review |
| P8 | Progress ("7 of 12 fields decided") is small text; Save sits at the bottom of a long list with no summary of what it will do | review |
| P9 | The saved review is a plain table; "original → corrected" and the reasons are hard to scan | review (read-only) |
| P10 | A demo can't start from nothing: cases and applicants can only be created through the API | case list |

## 2. Decisions needed

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **U1** | Styling approach | (a) **Tailwind CSS + a few shadcn/ui components** (source copied into the repo, built on Radix primitives); (b) keep hand-written CSS, just much more of it | **(a).** Accessible menus, dialogs and tooltips (focus handling, keyboard, screen readers) are hard to get right by hand, and consistent spacing and type come for free. It reverses 2-E's "no UI kit" rule, which was about speed, not security, so it needs your OK. |
| **U2** | Create cases and applicants in the UI (fixes P10) | include / leave out | **Include.** The API endpoints already exist (`POST /cases`, `POST /cases/{id}/applicants`); it's two small forms plus **two allowlist entries**. No backend change. It lets a demo start from an empty firm. |

Without U2, everything else in this plan still stands.

## 3. What changes

### 3.1 Foundation
- **Tailwind CSS v4** with design tokens for colour, spacing, radius and type, and one
  font (Inter, self-hosted through `next/font`, so the CSP's `font-src 'self'` is unchanged).
- **shadcn/ui components, only the ones used:** Button, Badge, Card, Input, Textarea,
  Select, Table, Dialog, Tooltip, Toast, Progress, DropdownMenu. Each is a file in
  `components/ui/` that we own.
- New runtime dependencies (U1a): `tailwindcss`, `@radix-ui/*` primitives for the
  components above, `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`
  (icons). All pinned to exact versions. **No** state library, data-fetching library or
  form library: React state stays as it is.
- The pdf.js text-layer rules and the evidence-box styles are kept exactly. The alignment
  they give was measured in 2-E.

### 3.2 App shell (P2, P3)
- **Header:** product name, the "Synthetic data only" badge, a **firm switcher**
  (memberships from `/me`, as now) and a user menu with sign-out.
- **Breadcrumbs:** Cases › `SYN-DEMO-001` › `payslip.pdf`.
- **No firm chosen:** any page shows "Choose a firm to continue" with the switcher, instead
  of "Invalid request". Same for an expired session ("Your session has ended; sign in
  again").

### 3.3 Cases (P10 with U2)
- A proper table: reference, purpose, status badge, applicants, created; the whole row
  clickable; an empty state with a **New case** button.
- **New case** dialog: reference and purpose only (the required fields). Other case
  fields stay API-only.

### 3.4 Case page (P4, P5, P10 with U2)
- A case header: reference, purpose, status, and the applicants as chips, with **Add
  applicant** (first and last name only).
- **A three-step "next action" strip** that reflects the real state: 1. income source,
  2. upload a payslip, 3. review. The current step is highlighted and done steps are ticked.
- **Upload as a drop zone** (drag a PDF or click), showing the file name and size before
  upload, and the applicant to file it under.
- **Documents as a list of cards:** file name, applicant, uploaded time, a status badge with
  an icon, and the action ("Review", "View review").
  - Processing shows a spinner and keeps refreshing, as now.
  - **Failures in plain English**, with what to do next:

| Code | Shown as |
|---|---|
| `extraction_failed` | "We couldn't read figures from this PDF. Synthetic payslips must be uploaded exactly as generated; a PDF opened and re-saved in a viewer loses its test data." |
| `too_many_pages` | "This PDF has more than 10 pages. A payslip is usually one or two." |
| `encrypted_pdf` | "This PDF is password-protected. Upload an unprotected copy." |
| `unreadable_pdf` | "This file couldn't be opened as a PDF." |
| `timeout` | "Opening this PDF took too long." |
| `hash_mismatch`, `missing_original`, `retries_exhausted` | "Processing failed on our side. Upload it again or contact support." (code shown small for support) |
| `unrecognised` status | "This doesn't look like a payslip." |

  These are frontend text only; the codes and API are unchanged.

### 3.5 Review screen (P6, P7, P8)
Layout: the PDF on the left (as now, sticky), and the decision panel on the right.

- **A sticky panel header:** a progress bar ("9 of 12 decided"), counts of accepted,
  corrected and rejected, **Accept remaining**, and **Save**.
- **Compact field rows:** label, extracted value (tabular numbers), page, and a
  confidence pill shown **only when it is below a threshold** (still labelled
  "indicator"). The raw printed text and field code go in a tooltip.
  - **A three-way segmented control:** Accept · Correct · Reject, coloured green, amber and
    red; the row gets a coloured left edge for its decision.
  - Correct and Reject open inline inputs (the value, then the reason), with the same
    validation messages as now.
- **Sections are collapsible** with a count each ("Payments 3/4"), and a filter:
  All · Undecided · Corrected or rejected.
- **Two-way linking:** selecting a row scrolls the PDF to its box and pulses it; clicking a
  box selects the row and scrolls the list to it. Hovering either highlights the other.
- **Keyboard:** ↑/↓ to move between fields, `A` accept, `C` correct, `R` reject, `Enter`
  to confirm an input, `?` to show the shortcuts. Never active while typing in an input.
- **Zoom:** fit to width by default, plus − / + and the percentage.
- **Save opens a confirmation dialog** summarising exactly what will happen:
  "9 figures will be saved to *Employment income — Sam Synthetic*, monthly. 1 corrected,
  1 rejected. A review can't be changed once saved." Then Save. The request is the same
  as today (`buildReview`, `If-Match`), and 409, 412 and 422 messages stay.
- **Income source and frequency** move into that dialog (they're save-time choices). If
  there's no income source, the dialog offers "Add employment income source" (the same call
  as the case page).

### 3.6 Saved review (P9)
- Read-only rows in the same layout as the review, with a decision badge per row.
- Corrections shown as ~~£647.24~~ → **£647.00** with the reason beneath; rejections greyed
  with the reason; "saved as figure" shown with the measure.
- Linking to the PDF boxes works the same as in the review.

### 3.7 Sign-in page
- The same development-only behaviour, styled as a centred card.

## 4. What does not change
- **No backend change.** The OpenAPI snapshot is unchanged. With U2, the forwarder gains
  exactly two allowlist entries: `POST cases` and `POST cases/{id}/applicants`, checked
  by the API's own permissions (`case:create` and `applicant:write`).
- The session cookies, the forwarder, the coordinate conversion, `decisions.ts` and the
  review request body are unchanged. Their tests stay as they are.
- The CSP is unchanged. (Tailwind outputs a plain stylesheet; the font is self-hosted.)

## 5. Left out, and why (the four questions)
- **Dark mode, mobile layout:** brokers will review on desktop; not needed for the
  milestone. The layout will still hold together at 1280 px wide.
- **Editing cases or applicants, case closure, member admin:** not part of the payslip
  workflow; the API has them when needed.
- **A data-fetching or state library:** the screens are small; plain React state works.
- **Automated browser tests:** still deferred (2-E's E2). Covered by vitest for new logic
  and the manual walkthrough below.
- **A design system or Storybook:** a dozen owned components is enough.

## 6. Tests and verification
- **vitest, new:** failure-code → message mapping; decision counts and the filter;
  the save-summary text (figures, corrections, rejections); keyboard handling ignores
  keys while typing; with U2, the two new allowlist entries and that nearby paths stay
  refused.
- **Existing:** all 62 frontend tests unchanged and passing; `tsc --noEmit`, `eslint` and
  `next build` clean; the production build still has no development sign-in.
- **Manual walkthrough** (in-app browser, local stack, synthetic data), with screenshots in
  the report:
  1. Sign in; choose the firm from the header.
  2. (U2) Create a case and an applicant.
  3. Add an income source; upload by drag-and-drop; watch it process.
  4. Upload a re-saved PDF and see the plain-English failure.
  5. Review: keyboard through fields, correct one, reject one, check the two-way linking,
     save through the dialog.
  6. Saved review shows the correction and rejection clearly.
  7. Open a deep link with no firm chosen: the "choose a firm" state, not an error.
  8. Cross-firm check repeated from 2-E: another firm's case and document give not-found.
- **Evidence-box alignment** re-measured on one layout at 125 % and 200 %, to show the new
  styling didn't move the boxes (2-E's method).
- The backend suite isn't rerun (no backend change).

## 7. Deliverables
- Changed: `frontend/app/**`, `frontend/components/**`, `globals.css`, `package.json` and
  lockfile, `lib/allowlist.ts` (U2 only); new `components/ui/`, `lib/failures.ts`, tests.
- `docs/phases/PHASE-2.1-UI-REPORT.md` with screenshots and results; README updated if the
  run steps change (they shouldn't).
