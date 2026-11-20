// Stage 3-B: the screens must keep rendering a stored profile/1 snapshot (the backend keeps
// the same fixture: backend/tests/fixtures/profile_snapshot_v1.json). Approvals are never
// rewritten, so if a profile change breaks this, add support for the new version instead.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { corroborationLine, documentaryLine, figuresFor, periodTable, undatedFigures } from "@/lib/profile";
import type { ProfileSnapshot } from "@/lib/types";

const snapshot = JSON.parse(
  readFileSync(resolve(__dirname, "../../backend/tests/fixtures/profile_snapshot_v1.json"), "utf-8"),
) as ProfileSnapshot;

describe("a stored profile/1 snapshot", () => {
  it("still renders its figures, support and members", () => {
    expect(snapshot.rules).toBe("profile/1");
    const [source] = snapshot.income_sources;
    const figures = figuresFor(snapshot.figures, source!.id);
    const table = periodTable(figures);
    expect(table.rows.map((r) => r.label)).toEqual(["May 2026", "June 2026", "July 2026", "August 2026"]);
    expect(undatedFigures(figures).length).toBeGreaterThan(0);
    const support = snapshot.support.find((s) => s.item_id === source!.id)!;
    expect(documentaryLine(support.documentary[0]!)).toMatch(/^Payslip, May 2026: gross pay, basic pay/);
    const names = new Map(snapshot.applicants.map((a) => [a.id, `${a.first_name} ${a.last_name}`]));
    const line = corroborationLine(support.corroborating[0]!, "May 2026", (id) => names.get(id) ?? "?");
    expect(line).toContain("joint: Alex Example and Sam Synthetic");
    expect(snapshot.members.length).toBeGreaterThan(0);
  });
});
