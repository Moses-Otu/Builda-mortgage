// The only API calls the forwarder will make on the browser's behalf: exactly the
// endpoints this screen uses (docs/phases/PHASE-2E-PLAN.md, section 1). Anything else -
// other routes, traversal, encoded separators, absolute URLs - is refused.

const ID = "{id}";
const KEY = "{key}";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
/** A reconciliation finding's key: a SHA-256 in lowercase hex. */
const FINDING_KEY = /^[0-9a-f]{64}$/;

const ROUTES: Array<[method: string, pattern: string]> = [
  ["GET", "me"],
  ["GET", "cases"],
  ["POST", "cases"], // new case (Phase 2.1, U2); the API requires case:create
  ["GET", `cases/${ID}/applicants`],
  ["POST", `cases/${ID}/applicants`], // add applicant (Phase 2.1, U2); applicant:write
  ["GET", `cases/${ID}/financial/income-sources`],
  ["POST", `cases/${ID}/financial/income-sources`],
  ["GET", `cases/${ID}/financial/observations`],
  // Stage 2-A: the declared basic salary (financial:write; the API checks the measure).
  ["POST", `cases/${ID}/financial/observations`],
  // Stage 2-A: reconciliation checks, and acknowledging a discrepancy (financial:write).
  ["GET", `cases/${ID}/checks`],
  ["GET", `cases/${ID}/profile`], // the financial profile (Stage 3-A); document:read
  // Stage 2-B: bank accounts confirmed from statements, and adding a holder.
  ["GET", `cases/${ID}/bank-accounts`],
  ["POST", `cases/${ID}/bank-accounts/${ID}/holders`],
  ["POST", `cases/${ID}/checks/${KEY}/acknowledgement`],
  ["GET", "financial/measures"],
  ["GET", `cases/${ID}/documents`],
  ["POST", `cases/${ID}/documents`],
  ["GET", `cases/${ID}/documents/${ID}`],
  ["GET", `cases/${ID}/documents/${ID}/content`],
  ["GET", `cases/${ID}/documents/${ID}/extraction`],
  ["GET", `cases/${ID}/documents/${ID}/review`],
  ["POST", `cases/${ID}/documents/${ID}/review`],
];

/** Whether ``method`` on these path segments (relative to /api/v1) may be forwarded. */
export function isAllowed(method: string, segments: readonly string[]): boolean {
  return ROUTES.some(([allowedMethod, pattern]) => {
    if (allowedMethod !== method.toUpperCase()) return false;
    const parts = pattern.split("/");
    return (
      parts.length === segments.length &&
      parts.every((part, i) => {
        const segment = segments[i] ?? "";
        if (part === ID) return UUID.test(segment);
        if (part === KEY) return FINDING_KEY.test(segment);
        return part === segment;
      })
    );
  });
}

/** Request headers passed through to the API (everything else is dropped). */
export const FORWARDED_REQUEST_HEADERS = ["content-type", "if-match", "upload-filename", "accept"];
/** Response headers passed back to the browser. */
export const FORWARDED_RESPONSE_HEADERS = [
  "content-type",
  "etag",
  "cache-control",
  "content-disposition",
  "repr-digest",
  "x-content-type-options",
];
