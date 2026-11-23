import { describe, expect, it } from "vitest";

import {
  applicantProblem,
  type ApplicantForm,
  incomeSourceBody,
  loanDetailsProblem,
  type LoanForm,
  loanPatch,
} from "@/lib/case-form";
import { caseStage, type Journey, type JourneyStage, stageHref, stageState, uploadTypeFor } from "@/lib/journey";

const counts = { received: 0, processing: 0, to_review: 0, reviewed: 0, failed: 0 };

function stage(key: JourneyStage["key"], number: number, done = false): JourneyStage {
  return { key, number, title: key, done, optional: false, summary: "", requirements: [] };
}

function journey(over: Partial<Journey> = {}): Journey {
  return {
    case_id: "c",
    case_status: "collecting_evidence",
    stage: "income",
    stages: [
      stage("details", 1, true),
      stage("income", 2),
      stage("statements", 3),
      stage("profile", 4),
      stage("assessment", 5),
      stage("decision", 6),
    ],
    next_action: { label: "Next: Upload Payslips", target: "income", kind: "navigate" },
    reassessment_required: false,
    reassessment_message: null,
    profile_approved: false,
    approval_id: null,
    latest_assessment_id: null,
    assessment_running: false,
    assessment_failed: false,
    assessment_blockers: ["profile_not_approved"],
    approval_blockers: [],
    open_evidence_requests: 0,
    policy_code: "uk-resi-standard/1",
    payslips: counts,
    statements: counts,
    ...over,
  };
}

describe("journey screens", () => {
  it("links each stage to where it's worked on", () => {
    expect(stageHref("c", "details")).toBe("/cases/c?stage=details");
    expect(stageHref("c", "statements")).toBe("/cases/c?stage=statements");
    expect(stageHref("c", "profile")).toBe("/cases/c/profile");
    expect(stageHref("c", "assessment")).toBe("/cases/c/assessment");
    expect(stageHref("c", "decision")).toBe("/cases/c/decision");
  });

  it("draws done, current, upcoming and reassessment stages distinctly", () => {
    const j = journey();
    expect(stageState(j, j.stages[0]!)).toBe("done");
    expect(stageState(j, j.stages[1]!)).toBe("current");
    expect(stageState(j, j.stages[3]!)).toBe("upcoming");
    const stale = journey({ reassessment_required: true });
    expect(stageState(stale, stale.stages[4]!)).toBe("attention");
    // Moved on to approval with a statement month still missing: flagged, not "done".
    const gap = journey({ stage: "profile" });
    gap.stages[1] = { ...gap.stages[1]!, done: true };
    gap.stages[2] = { ...gap.stages[2]!, requirements: ["Bank statements showing the Oct 2026 salary."] };
    expect(stageState(gap, gap.stages[2]!)).toBe("attention");
    expect(stageState(gap, gap.stages[5]!)).toBe("upcoming");
  });

  it("shows the requested case-page stage, else where the journey is", () => {
    expect(caseStage(journey(), "statements")).toBe("statements");
    expect(caseStage(journey(), null)).toBe("income");
    expect(caseStage(journey({ stage: "assessment" }), null)).toBe("details");
    expect(caseStage(journey(), "decision")).toBe("income"); // not a case-page stage
    expect(caseStage(null, null)).toBe("details");
  });

  it("presets the upload type by stage", () => {
    expect(uploadTypeFor("income")).toBe("payslip");
    expect(uploadTypeFor("statements")).toBe("bank_statement");
  });
});

const loan: LoanForm = {
  propertyValue: "300,000",
  loanAmount: "240000",
  termYears: "25",
  propertyUse: "residential",
  productRate: "",
  policyCode: "uk-resi-standard/1",
};

describe("new case form", () => {
  it("requires the loan figures and validates them", () => {
    expect(loanDetailsProblem(loan)).toBeNull();
    expect(loanDetailsProblem({ ...loan, propertyValue: "" })).toMatch(/property value/);
    expect(loanDetailsProblem({ ...loan, loanAmount: "abc" })).toMatch(/mortgage amount/);
    expect(loanDetailsProblem({ ...loan, loanAmount: "400000" })).toMatch(/more than the property value/);
    expect(loanDetailsProblem({ ...loan, termYears: "0" })).toMatch(/term/);
    expect(loanDetailsProblem({ ...loan, termYears: "2.5" })).toMatch(/term/);
    expect(loanDetailsProblem({ ...loan, productRate: "4.25" })).toBeNull();
    expect(loanDetailsProblem({ ...loan, productRate: "25" })).toMatch(/product rate/);
    expect(loanDetailsProblem({ ...loan, productRate: "4.2.5" })).toMatch(/product rate/);
  });

  it("sends pence, months and a null rate when blank", () => {
    expect(loanPatch(loan)).toEqual({
      property_value: { amount_minor: 30_000_000 },
      loan_amount: { amount_minor: 24_000_000 },
      term_months: 300,
      property_use: "residential",
      product_rate_percent: null,
      policy_code: "uk-resi-standard/1",
    });
    expect(loanPatch({ ...loan, productRate: " 4.5 " }).product_rate_percent).toBe("4.5");
  });

  const applicant: ApplicantForm = {
    firstName: "Sam",
    lastName: "Synthetic",
    dateOfBirth: "",
    incomeKind: "employment",
    employer: "Acme Ltd",
    basis: "permanent",
    startDate: "2019-04-01",
  };

  it("needs the applicant's name", () => {
    expect(applicantProblem(applicant)).toBeNull();
    expect(applicantProblem({ ...applicant, lastName: " " })).toMatch(/name/);
    expect(applicantProblem({ ...applicant, startDate: "" })).toMatch(/started/);
    expect(applicantProblem({ ...applicant, incomeKind: "other", startDate: "" })).toBeNull();
  });

  it("builds the income source, with the basis only for employment", () => {
    expect(incomeSourceBody("a1", applicant)).toEqual({
      applicant_id: "a1",
      kind: "employment",
      is_primary: true,
      name: "Acme Ltd",
      basis: "permanent",
      start_date: "2019-04-01",
    });
    expect(incomeSourceBody("a1", { ...applicant, incomeKind: "self_employment", employer: "", startDate: "" })).toEqual({
      applicant_id: "a1",
      kind: "self_employment",
      is_primary: true,
    });
  });
});
