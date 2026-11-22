import { describe, expect, it } from "vitest";

import { statementsDue } from "@/lib/case-steps";
import { problemMessage } from "@/lib/problems";
import type { DocumentOut } from "@/lib/types";

const A = "applicant-a";
const B = "applicant-b";

function doc(applicant_id: string, document_type: string, status: string): DocumentOut {
  return {
    id: `${applicant_id}-${document_type}-${status}-${Math.random()}`,
    case_id: "case",
    applicant_id,
    document_type,
    status,
    failure_code: null,
    page_count: 1,
    version: 1,
    created_at: "2026-11-22T12:00:00Z",
    original: { id: "o", original_filename: "x.pdf", size_bytes: 1 },
  } as DocumentOut;
}

describe("statementsDue", () => {
  it("prompts once every payslip is reviewed and no statement is on file", () => {
    expect(statementsDue([doc(A, "payslip", "reviewed"), doc(A, "payslip", "reviewed")], A)).toBe(true);
  });

  it("waits while a payslip is still processing or waiting for review", () => {
    expect(statementsDue([doc(A, "payslip", "reviewed"), doc(A, "payslip", "extracted")], A)).toBe(false);
    expect(statementsDue([doc(A, "payslip", "reviewed"), doc(A, "payslip", "received")], A)).toBe(false);
  });

  it("ignores failed and unrecognised uploads", () => {
    expect(statementsDue([doc(A, "payslip", "reviewed"), doc(A, "payslip", "failed")], A)).toBe(true);
  });

  it("stops once a statement is uploaded, unless that upload failed", () => {
    expect(statementsDue([doc(A, "payslip", "reviewed"), doc(A, "bank_statement", "received")], A)).toBe(false);
    expect(statementsDue([doc(A, "payslip", "reviewed"), doc(A, "bank_statement", "failed")], A)).toBe(true);
  });

  it("is per applicant, and needs at least one payslip", () => {
    expect(statementsDue([doc(B, "payslip", "reviewed")], A)).toBe(false);
    expect(statementsDue([], A)).toBe(false);
  });
});

describe("feature-disabled message", () => {
  const type = "urn:meip:problem:feature-disabled";

  it("names the feature the API refused", () => {
    expect(problemMessage(404, { type, title: "x", status: 404, detail: "Bank statements are not enabled in this deployment" })).toBe(
      "Bank statements are not enabled in this deployment.",
    );
  });

  it("falls back to the general message without a detail", () => {
    expect(problemMessage(404, { type, title: "x", status: 404 })).toBe("Documents are not enabled in this environment.");
  });
});
