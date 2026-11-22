# Stage 3, step 3-C — Evidence Pack: Plan

Status: **Approved and implemented (2026-11-20): see [STAGE-3C-REPORT.md](STAGE-3C-REPORT.md).** · Date: 2026-11-20 ·
Parent: [Stage 3 plan](STAGE-3-BROKER-EXPERIENCE-PLAN.md), section 5 (decision S4 approved) ·
Baseline: `1c1f612` (3-B)

**Goal:** one straightforward evidence pack per approval: the approved profile, its
supporting findings, provenance and the original documents. **No export framework**: one
route, one generator module, no stored files. **No new decisions:** this fills in section 5
of the approved Stage 3 plan. **This is the last step of Phase 3.**

## 1. The route
`GET /cases/{id}/profile/approvals/{approval_id}/pack`:
- **Permission:** `document:read`, since the pack contains originals.
- **Returns** `evidence-pack-<case reference>-v<version>.zip` as a download.
- **Any approval**, current or superseded, can be downloaded. The pack names its own version
  and date.
- **Audited** as `evidence_pack.downloaded`: approval id, version, number of files and the
  pack's SHA-256. A pack exports every relied-on original at once.

## 2. Contents
| File | What |
|---|---|
| `summary.pdf` | The readable pack (section 3) |
| `snapshot.json` | The approval's stored snapshot text, byte for byte (its SHA-256 is the approved fingerprint) |
| `attestation.txt` | The exact wording the broker accepted, and its version |
| `originals/<n>-<file name>` | Each original **relied on**: a document that contributed a figure, or a statement that corroborates. Each is checked against the SHA-256 recorded in the snapshot |
| `manifest.json` | Every file's SHA-256 and size; the approval id, version and date; the snapshot hash; `profile/1`; the attestation version; the generator version (`pack/1`) |

## 3. `summary.pdf`
Plain, text-only, built with ReportLab:
1. **Cover:** firm, case reference, applicants, approval version, approver and date, the
   attestation text, the snapshot hash, "Synthetic data", and "Not a lending decision".
2. **Per item:**
   - its figures, each with its origin mark (E, C, D or B) and evidence: document, page and
     printed label; for a correction, the value as read and the reason;
   - its **"Supported by"** block, in the same five kinds as the screen, with the same
     no-count wording.
3. **Checks:** every finding by rule set; discrepancies with their acknowledgement notes;
   what couldn't run, and why.
4. **Bank accounts:** ending, sole or joint, holders, and the statements confirmed to each.
5. **Document inventory:** every document, including those not relied on and why.
6. **Footer on every page:** case reference, version, page n of m.

## 4. Built only from the approval, and reproducible
- **The generator reads two things only:** the approval row (snapshot text, attestation,
  version, approver) and the original files. It never touches the live profile.
- **Every original is checked** against its SHA-256 in the snapshot. A missing or altered
  original refuses the whole pack (409 `original-unavailable`, naming the document), never
  producing a partial one.
- **The snapshot's own hash is checked first**, as when an approval is read (3-B).
- **Deterministic:**
  - ZIP entries in a fixed order, with fixed timestamps (the approval time);
  - ReportLab's invariant mode;
  - no current dates anywhere.

  The same approval gives the same pack, byte for byte, today or after the case changes.
- **Nothing is stored.** The pack is generated on request.

## 5. Screen
- **"Download evidence pack"** on the verified banner, and next to each version in the
  versions list.
- The allowlist gains this one `GET` route. The forwarder already passes downloads through
  with their `content-disposition`.

## 6. Dependency
**ReportLab moves from development to runtime dependencies**, as S4 anticipated; it already
draws the synthetic test documents.
- `uv.lock` must change with it. `uv` isn't installed on this machine, so I'll install it
  into the project's virtual environment (`pip install uv`, used only to update the lock),
  and report it.
- If you'd rather not install anything, the alternative is to leave the lock for you to
  regenerate. Say which you prefer.

## 7. Tests
- The ZIP holds exactly the expected files.
- Manifest hashes match; originals are byte-identical; `snapshot.json` hashes to the
  approval's `snapshot_sha256`.
- **The pack is byte-identical when downloaded twice, including after a reopen, a change and
  a new approval.**
- **A version 1 pack still downloads, unchanged, after version 2.**
- Only relied-on originals are included: an unrecognised document isn't, but it is listed in
  the inventory.
- A missing or altered original gives 409 and no pack. A tampered snapshot gives 409.
- The PDF opens (pypdfium2) and contains the case reference, the attestation, a corrected
  figure's reason, a discrepancy's acknowledgement note and the "Supported by" kinds.
- The download is audited.
- Permissions (everyone with `document:read`), cross-tenant 404, the route in the matrix.
- Frontend: the download link per version, and the allowlist.
- The usual gates: full suites, ruff, `mypy --strict`, vitest, `tsc`, eslint, production
  build.

**Walkthrough (`SYN-BANK-001`):** download the version 2 and version 1 packs; open the PDF;
check the manifest and an original's hash.

## 8. Left out
| Left out | Why |
|---|---|
| Images of the evidence region in the PDF | Page and label references, plus the originals in the ZIP, identify the location |
| Stored packs, share links, other formats (Word, CSV), lender templates | Not needed for the pilot; generating on request always matches the approval |
| Styling beyond plain, readable text | Pilot feedback first |

## 9. After 3-C
Phase 3 closes. Per your direction (2026-11-20), the next work is a **Phase 4 plan limited to
what a controlled pilot needs**: handling real borrower data safely, a real extraction
provider, deployment, and onboarding a few firms. Phases 5 and 6 wait for real broker usage.
