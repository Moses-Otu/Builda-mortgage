import { describe, expect, it } from "vitest";

import { isAllowed } from "@/lib/allowlist";
import {
  approvalBanner,
  approvalSummary,
  approveBody,
  blockerLink,
  canApprove,
  REOPEN_EXPLANATION,
  statusActions,
} from "@/lib/approval";
import { caseSteps } from "@/lib/case-steps";
import { approvalText } from "@/lib/profile";
import type { DocumentOut, IncomeSource, Readiness } from "@/lib/types";

const CASE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";
const APPROVAL = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c";

const readiness: Readiness = {
  ready: true,
  blockers: [],
  notices: [],
  attestation: { version: "attestation/1", text: "I have reviewed the evidence…" },
};

describe("case status actions (decision V4)", () => {
  it("offers the forward steps, going back, and reopening; never verifying or closing", () => {
    expect(statusActions("draft").map((a) => a.label)).toEqual(["Start collecting evidence"]);
    expect(statusActions("collecting_evidence")).toEqual([{ to: "in_review", label: "Ready for approval", reopen: false }]);
    expect(statusActions("in_review").map((a) => a.to)).toEqual(["collecting_evidence"]);
    expect(statusActions("verified")).toEqual([{ to: "in_review", label: "Reopen…", reopen: true }]);
    expect(statusActions("closed")).toEqual([]);
    for (const s of ["draft", "collecting_evidence", "in_review", "verified"]) {
      expect(statusActions(s).map((a) => a.to)).not.toContain("verified");
    }
    expect(REOPEN_EXPLANATION).toContain("supersedes the current approval");
  });

  it("lets only advisers and firm admins see the approve button", () => {
    expect(canApprove("firm_admin")).toBe(true);
    expect(canApprove("adviser")).toBe(true);
    expect(canApprove("case_administrator")).toBe(false);
    expect(canApprove("read_only")).toBe(false);
    expect(canApprove(null)).toBe(false);
  });
});

describe("approving", () => {
  it("sends the loaded fingerprint and the attestation version only, never its wording", () => {
    const body = approveBody("f".repeat(64), readiness, "  Reviewed with the client ");
    expect(body).toEqual({
      fingerprint: "f".repeat(64),
      attestation_version: "attestation/1",
      confirmed: true,
      note: "Reviewed with the client",
    });
    expect(JSON.stringify(body)).not.toContain("I have reviewed");
    expect(approveBody("f".repeat(64), readiness, "   ")).not.toHaveProperty("note");
  });

  it("points each blocker to where it can be resolved", () => {
    expect(blockerLink(CASE, "documents_outstanding", [APPROVAL])?.href).toBe(`/cases/${CASE}/documents/${APPROVAL}`);
    expect(blockerLink(CASE, "discrepancies_unacknowledged", ["k"])?.href).toBe(`/cases/${CASE}`);
    expect(blockerLink(CASE, "nothing_recorded", [])).toBeNull();
  });
});

describe("approval wording", () => {
  const approved = {
    status: "approved" as const,
    approval_id: APPROVAL,
    version: 2,
    approved_by: { user_id: null, name: "Alex Broker" },
    approved_at: "2026-11-20T10:00:00Z",
    superseded: [{ version: 1, superseded_at: "2026-11-20T09:00:00Z" }],
  };

  it("says who approved which version and when", () => {
    expect(approvalBanner(approved)).toBe("Approved, version 2, by Alex Broker on 20 Nov 2026");
    expect(approvalBanner({ status: "not_approved" })).toBeNull();
  });

  it("states supersession plainly in the fifth kind of support", () => {
    expect(approvalSummary({ status: "not_approved" })).toBe("Not approved");
    expect(approvalSummary({ status: "not_approved", superseded: [{ version: 1, superseded_at: null }] })).toBe(
      "Not approved (version 1 superseded on reopening)",
    );
    expect(approvalText(approved)).toBe("Approved, version 2, by Alex Broker on 20 Nov 2026");
  });
});

describe("the fourth step", () => {
  const source: IncomeSource = { id: "s", applicant_id: "a", kind: "employment", name: null, withdrawn_at: null };
  const reviewed = { id: "d", status: "reviewed", document_type: "payslip" } as unknown as DocumentOut;

  it("is done only when the case is verified", () => {
    expect(caseSteps([source], [reviewed], "in_review")[3]).toMatchObject({ title: "Approve the profile", state: "current" });
    expect(caseSteps([source], [reviewed], "verified")[3]?.state).toBe("done");
  });
});

describe("forwarder allowlist for approvals", () => {
  const split = (path: string) => path.split("/");
  it.each([
    ["GET", `cases/${CASE}/profile/approvals`],
    ["POST", `cases/${CASE}/profile/approvals`],
    ["GET", `cases/${CASE}/profile/approvals/${APPROVAL}`],
    ["POST", `cases/${CASE}/transitions`],
    ["GET", `cases/${CASE}`],
  ])("allows %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(true));

  it.each([
    ["DELETE", `cases/${CASE}/profile/approvals/${APPROVAL}`],
    ["PATCH", `cases/${CASE}/profile/approvals/${APPROVAL}`],
    ["POST", `cases/${CASE}/profile/approvals/${APPROVAL}`],
    ["GET", `cases/${CASE}/profile/approvals/not-a-uuid`],
  ])("refuses %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(false));
});
