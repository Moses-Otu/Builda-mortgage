// API problem responses (RFC 9457) -> what the broker is told.

import type { Problem } from "./types";

const PREFIX = "urn:meip:problem:";

export function problemMessage(status: number, problem: Problem | null): string {
  const type = problem?.type?.startsWith(PREFIX) ? problem.type.slice(PREFIX.length) : null;
  switch (type) {
    case "already-reviewed":
      return "This payslip has already been reviewed. Showing the saved review.";
    case "precondition-failed":
      return "Someone changed this document since you opened it. Reload and review again.";
    case "duplicate-document":
      return "This exact file is already on the case.";
    case "payload-too-large":
      return "The file is too large.";
    case "unsupported-media-type":
      return "Only PDF files can be uploaded.";
    case "feature-disabled":
      // The API says which feature: documents in general, or bank statements only.
      return problem?.detail ? `${problem.detail.replace(/\.$/, "")}.` : "Documents are not enabled in this environment.";
    case "forbidden":
      return "Your role does not allow this.";
    case "unauthorized":
      return "Your session has expired. Sign in again.";
    default:
      break;
  }
  if (status === 404) return "Not found, or not available to you.";
  if (problem?.detail) return problem.detail;
  if (status >= 500) return "Something went wrong on the server. Try again shortly.";
  return `Request failed (${status}).`;
}
