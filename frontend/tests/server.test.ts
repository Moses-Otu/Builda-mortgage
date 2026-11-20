import { describe, expect, it } from "vitest";

import { isAllowed } from "@/lib/allowlist";
import { problemMessage } from "@/lib/problems";
import { devSignInEnabled, SESSION_COOKIE_OPTIONS } from "@/lib/server-config";

const CASE = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";
const DOC = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c";
const split = (path: string) => path.split("/");

describe("forwarder allowlist", () => {
  it.each([
    ["GET", "me"],
    ["GET", "cases"],
    ["GET", `cases/${CASE}/applicants`],
    ["POST", `cases/${CASE}/financial/income-sources`],
    ["POST", `cases/${CASE}/documents`],
    ["GET", `cases/${CASE}/documents/${DOC}/content`],
    ["GET", `cases/${CASE}/documents/${DOC}/extraction`],
    ["POST", `cases/${CASE}/documents/${DOC}/review`],
    ["get", `cases/${CASE}/documents/${DOC}/review`],
    ["POST", "cases"], // Phase 2.1 (U2): new case
    ["POST", `cases/${CASE}/applicants`], // Phase 2.1 (U2): add applicant
    ["POST", `cases/${CASE}/transitions`], // Stage 3-B: case status control (decision V4)
  ])("allows %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(true));

  it.each([
    ["DELETE", `cases/${CASE}/documents/${DOC}`], // method not allowed
    ["POST", "members"],
    // Phase 2.1 allows creating cases and applicants, and nothing next to them:
    ["PATCH", `cases/${CASE}`],
    ["POST", `cases/${CASE}`],
    ["POST", `cases/${CASE}/applicants/${DOC}`],
    ["POST", `cases/${CASE}/applicants/${DOC}/removal`],
    ["DELETE", `cases/${CASE}/applicants`],
    ["POST", `cases/not-a-uuid/applicants`],
    ["GET", `cases/${CASE}/events`],
    ["POST", `cases/${CASE}/financial/income-sources/${DOC}/withdrawal`],
    ["GET", `cases/../me`],
    ["GET", `cases/${CASE}/../../me`],
    ["GET", `cases/${CASE}%2F..%2Fme/documents`],
    ["GET", "https://evil.example/api"],
    ["GET", `cases/${CASE.toUpperCase()}/documents`], // only canonical lowercase IDs
    ["GET", `cases/not-a-uuid/documents`],
    ["GET", `cases/${CASE}/documents/${DOC}/content/extra`],
    ["GET", ""],
  ])("refuses %s %s", (method, path) => expect(isAllowed(method, split(path))).toBe(false));
});

describe("development sign-in gate", () => {
  it("is off unless explicitly enabled", () => {
    expect(devSignInEnabled({ NODE_ENV: "development" })).toBe(false);
    expect(devSignInEnabled({ NODE_ENV: "development", MEIP_UI_DEV_SIGN_IN: "1" })).toBe(false);
    expect(devSignInEnabled({ NODE_ENV: "development", MEIP_UI_DEV_SIGN_IN: "true" })).toBe(true);
  });

  it("is off in production even when enabled", () => {
    expect(devSignInEnabled({ NODE_ENV: "production", MEIP_UI_DEV_SIGN_IN: "true" })).toBe(false);
  });

  it("uses httpOnly, Secure, SameSite=Strict cookies", () => {
    expect(SESSION_COOKIE_OPTIONS).toMatchObject({
      httpOnly: true,
      secure: true,
      sameSite: "strict",
      path: "/",
    });
  });
});

describe("problem messages", () => {
  it("explains the review conflicts", () => {
    expect(problemMessage(409, { type: "urn:meip:problem:already-reviewed" })).toMatch(
      /already been reviewed/,
    );
    expect(problemMessage(412, { type: "urn:meip:problem:precondition-failed" })).toMatch(/Reload/);
  });

  it("shows the server's detail for validation errors", () => {
    expect(problemMessage(422, { detail: "field 3: a correction needs a reason" })).toBe(
      "field 3: a correction needs a reason",
    );
  });

  it("does not reveal whether something exists in another firm", () => {
    expect(problemMessage(404, { detail: "Case not found" })).toBe("Not found, or not available to you.");
  });

  it("falls back safely", () => {
    expect(problemMessage(500, null)).toMatch(/server/);
    expect(problemMessage(418, null)).toBe("Request failed (418).");
  });
});
