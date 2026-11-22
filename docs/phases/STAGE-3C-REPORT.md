# Stage 3, step 3-C — Evidence Pack: Report

Date: 2026-11-20 · Status: **Implemented and verified; awaiting your review (not committed,
not pushed)** · Plan: [STAGE-3C-EVIDENCE-PACK-PLAN.md](STAGE-3C-EVIDENCE-PACK-PLAN.md) (S4
scope) · Baseline: `1c1f612` (3-B). **Phase 3 is complete with this step.**

## 1. Delivered
- **`GET /cases/{id}/profile/approvals/{approval_id}/pack`:**
  - `document:read`; behind the documents switch, like original downloads;
  - returns `evidence-pack-<reference>-v<version>.zip`;
  - **audited** as `evidence_pack.downloaded`, with the approval, version, file count and the
    pack's SHA-256.
- **Contents:**
  - `summary.pdf`: cover with the attestation; figures per item with origin and evidence;
    each item's "Supported by" in its five kinds; checks with acknowledgement notes; bank
    accounts; document inventory; "page n of m" footers;
  - `snapshot.json`: the stored text, byte for byte;
  - `attestation.txt`;
  - `originals/<n>-<file name>`: documents that contributed a figure, and corroborating
    statements;
  - `manifest.json`: every file's SHA-256 and size, the approval and the snapshot hash.
- **Built only from the approval row and the originals:** one module, `profile/pack.py`.
  Nothing is stored.
- **Screen:**
  - "Download evidence pack" on the verified banner;
  - a download icon next to each version;
  - a link when viewing a past version.
- **Allowlist:** the one `GET` route.

## 2. Tests and build
| Gate | Result |
|---|---|
| Backend full suite (PostgreSQL 17) | **1,218 passed, 0 failed**, coverage 93.15% (2026-11-22, with the Phase 4 work in the tree) |
| New: `test_evidence_pack.py` | **3 passed**; plus matrix and cross-tenant probes for the route |
| Targeted run (pack, matrix, isolation, schema and API snapshot, audit, unit) | **662 passed** |
| ruff (check and format), `mypy --strict` | clean (143 source files) |
| Frontend vitest | **179 passed** (4 new) |
| `tsc`, eslint, production build | clean; build succeeds (from a copy) |
| Walkthrough (`SYN-BANK-001`) | packs for versions 1 and 2 downloaded through the screen; contents, manifest hashes, snapshot hash and PDF text checked |

## 3. Byte-identical for the same approval: **yes**
- **Tested:** two downloads are identical, and version 1's pack is **still identical after a
  reopen, a change and a version 2 approval**, while version 2's pack differs.
- **On the demo case:** version 1 = `2df6720b…bd1de` and version 2 = `940e6641…48ecf`, each
  the same across separate downloads, direct from the API and through the browser.
- **How:**
  - fixed ZIP order and timestamps (the approval time);
  - ReportLab's invariant mode;
  - no current dates, live names or firm settings read.

## 4. Missing or mismatched originals fail the whole export: **confirmed**
- Tested on real stored files:
  - an **altered** original → 409 `original-unavailable`, naming the document;
  - a **deleted** original → 409 `original-unavailable`;
  - in both cases **no ZIP is produced and no download is recorded**;
  - restoring the file makes the pack download again.
- Each original is checked twice: against its stored row's hash (by the existing
  `open_original`), and against the hash recorded **in the snapshot**.
- A tampered snapshot → 409 `snapshot-integrity`.

## 5. Deviations and blockers
| # | What | Why |
|---|---|---|
| 1 | **Lockfile not updated.** ReportLab moved to runtime dependencies in `pyproject.toml`; **`uv.lock` is unchanged** | `uv` isn't installed and no other lock workflow exists in the project, so per your instruction I didn't install tooling or hand-edit the lock. The local environment already has ReportLab installed, so nothing fails here. **To do:** run `uv lock` where uv is available, before any deployment or CI build from the lock |
| 2 | **The cover has no firm name** | The plan listed "firm". The firm's name lives in live settings, outside the approval; reading it would break byte-identical packs if the firm renamed itself. The case reference, applicants, version, approver, date and hash are shown |
| 3 | **The approver's name comes from the snapshot's members** | It's the name as at approval, keeping the pack deterministic. If the approver isn't referenced elsewhere in the snapshot, the pack shows "firm member" and a short id |

No other changes to scope or design.

## 6. Files
| | Files |
|---|---|
| New | `backend/src/meip/profile/pack.py`, `backend/tests/integration/test_evidence_pack.py` |
| Changed (backend) | `api/routers/profile.py` (the route), `audit/service.py` (action and two classified fields), `pyproject.toml` (ReportLab runtime), `tests/integration/test_authorization_matrix.py`, `tests/isolation/test_cross_tenant_api.py`, `openapi.json` |
| Changed (frontend) | `components/profile/ApprovalPanel.tsx`, `lib/approval.ts`, `lib/allowlist.ts`, `tests/approval.test.ts` |
| Docs | this report; the 3-C and Stage 3 plan status; ROADMAP (Phase 3 done); ARCHITECTURE; README; retention brief (packs leave the platform's control) |

## 7. Proposed commit (not made: awaiting your review)

```
Stage 3-C: evidence pack built only from the approval (closes Phase 3)
```
