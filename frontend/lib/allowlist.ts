// The only API calls the forwarder will make on the browser's behalf: exactly the
// endpoints this screen uses (docs/phases/PHASE-2E-PLAN.md, section 1). Anything else -
// other routes, traversal, encoded separators, absolute URLs - is refused.

const ID = "{id}";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const ROUTES: Array<[method: string, pattern: string]> = [
  ["GET", "me"],
  ["GET", "cases"],
  ["GET", `cases/${ID}/applicants`],
  ["GET", `cases/${ID}/financial/income-sources`],
  ["POST", `cases/${ID}/financial/income-sources`],
  ["GET", `cases/${ID}/financial/observations`],
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
        return part === ID ? UUID.test(segment) : part === segment;
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
