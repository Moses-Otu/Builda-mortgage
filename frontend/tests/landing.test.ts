import { existsSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import { DEMO_EMAIL, STAGES, STEPS } from "@/lib/landing";

describe("landing page", () => {
  it("names the six journey stages in order", () => {
    expect(STAGES).toEqual(["Applicant", "Payslips", "Bank statements", "Profile", "Assessment", "Decision"]);
  });

  it("only shows screenshots that ship with the app", () => {
    for (const s of STEPS) {
      expect(existsSync(path.join(__dirname, "..", "public", s.image)), s.image).toBe(true);
      expect(s.alt.length).toBeGreaterThan(10);
    }
    expect(existsSync(path.join(__dirname, "..", "public", "landing", "ceda-demo.mp4"))).toBe(true);
  });

  it("sends demo requests to the right address", () => {
    expect(DEMO_EMAIL).toBe("azeez@jadasquad.com");
  });
});
