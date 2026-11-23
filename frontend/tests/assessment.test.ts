import { describe, expect, it } from "vitest";

import {
  gbp,
  monthLabel,
  nextAction,
  OUTCOMES,
  poundsToMinor,
  type Step,
  stepDigest,
  stepTitle,
  TOOL_LABELS,
  verificationSplit,
  type IncomeLine,
} from "@/lib/assessment";
import { isAllowed } from "@/lib/allowlist";

const split = (path: string) => path.split("/");
const C = "01a2032c-1268-7dbe-9c07-65737d21a01c";
const A = "01a2032c-1268-7dbe-9c07-65737d21a01d";

function step(over: Partial<Step>): Step {
  return {
    seq: 1,
    kind: "tool",
    tool: "validate_income",
    rationale: "why",
    input: {},
    output: {},
    status: "ok",
    error_code: null,
    duration_ms: 1,
    created_at: "2026-11-23T10:00:00Z",
    ...over,
  };
}

describe("money and months", () => {
  it("formats pence as pounds, whole or with pence", () => {
    expect(gbp(15_000_000)).toBe("£150,000");
    expect(gbp(111_166, true)).toBe("£1,111.66");
    expect(gbp(null)).toBe("—");
  });

  it("parses whole pounds typed by the broker", () => {
    expect(poundsToMinor("£140,000")).toBe(14_000_000);
    expect(poundsToMinor("140000")).toBe(14_000_000);
    expect(poundsToMinor("12.50")).toBeNull();
    expect(poundsToMinor("")).toBeNull();
  });

  it("labels months", () => {
    expect(monthLabel("2026-10")).toBe("Oct 2026");
    expect(monthLabel("2026-09")).toBe("Sep 2026");
    expect(monthLabel("bad")).toBe("bad");
  });
});

describe("the activity timeline", () => {
  it("names every tool in plain English", () => {
    expect(Object.keys(TOOL_LABELS)).toHaveLength(16);
    expect(stepTitle(step({ tool: "escalate_to_broker" }))).toBe("Escalated to you");
    expect(stepTitle(step({ status: "cached" }))).toBe("Validated income (reused)");
    expect(stepTitle(step({ status: "error" }))).toBe("Validated income: failed");
    expect(stepTitle(step({ kind: "error", tool: null, error_code: "planner_fallback" }))).toBe("Switched to the rules planner");
  });

  it("digests outputs factually, from the API's figures only", () => {
    expect(stepDigest(step({ output: { qualifying_gross_monthly_minor: 375_000 } }))).toBe("Qualifying income £3,750 a month (gross)");
    expect(stepDigest(step({ tool: "calculate_affordability", output: { requested_within_limits: false, shortfall_minor: 5_524_100 } }))).toBe(
      "Requested loan exceeds the limits by £55,241",
    );
    expect(stepDigest(step({ tool: "reconcile_financial_evidence", output: { discrepancies: [{}], passed: 3 } }))).toBe("1 discrepancy found");
    expect(stepDigest(step({ tool: "reconcile_financial_evidence", output: { discrepancies: [{}, {}] } }))).toBe("2 discrepancies found");
    expect(stepDigest(step({ status: "error", output: { error: "Not a tool." } }))).toBe("Not a tool.");
  });

  it("says what the agent is doing next", () => {
    expect(nextAction({ status: "queued" }, [])).toBe("Starting…");
    expect(nextAction({ status: "running" }, [step({ tool: "validate_income" })])).toContain("Validated income done");
  });
});

describe("outcomes and verification", () => {
  it("has a label and an explanation for each outcome", () => {
    expect(Object.keys(OUTCOMES)).toEqual([
      "potentially_eligible",
      "eligible_subject_to_verification",
      "not_currently_eligible",
      "insufficient_information",
    ]);
  });

  it("splits counted income into verified and unverified", () => {
    const base = { counted: true, qualifying_gross_monthly_minor: 100 } as IncomeLine;
    expect(
      verificationSplit([
        { ...base, verification: "verified" },
        { ...base, verification: "declared" },
        { ...base, verification: "extracted", counted: false },
      ]),
    ).toEqual({ verified: 100, unverified: 100 });
  });
});

describe("forwarder allowlist for the assessment", () => {
  it.each([
    ["GET", "assessment/policies"],
    ["POST", `cases/${C}/assessments`],
    ["GET", `cases/${C}/assessments`],
    ["GET", `cases/${C}/assessments/${A}`],
    ["POST", `cases/${C}/assessments/${A}/decisions`],
    ["GET", `cases/${C}/evidence-requests`],
    ["POST", `cases/${C}/evidence-requests/${A}/withdrawal`],
    ["POST", `cases/${C}/affordability/scenarios`],
    ["PATCH", `cases/${C}`],
  ])("allows %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(true));

  it.each([
    ["DELETE", `cases/${C}/assessments/${A}`],
    ["POST", `cases/${C}/assessments/${A}`],
    ["GET", `cases/${C}/assessments/not-a-uuid`],
    ["POST", `cases/${C}/assessments/${A}/decisions/x`],
  ])("refuses %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(false));
});
