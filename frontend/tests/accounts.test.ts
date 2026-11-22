import { describe, expect, it } from "vitest";

import { inviteBody, passwordProblem } from "@/lib/accounts";
import { isAllowed } from "@/lib/allowlist";
import { passwordSignInEnabled } from "@/lib/server-config";

const ID = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b";

describe("pilot accounts", () => {
  it("checks a new password like the server does", () => {
    expect(passwordProblem("short", "short")).toBe("Use at least 12 characters");
    expect(passwordProblem("a long passphrase", "a long passphrasf")).toBe("The two new passwords don't match");
    expect(passwordProblem("a long passphrase", "a long passphrase")).toBeNull();
  });

  it("invites by email, which is also the sign-in", () => {
    expect(inviteBody("  Sam@Firm.CO.UK ", " Sam Smith ", "adviser")).toEqual({
      idp_subject: "sam@firm.co.uk",
      email: "sam@firm.co.uk",
      role: "adviser",
      display_name: "Sam Smith",
    });
    expect(inviteBody("a@b.test", "", "read_only")).not.toHaveProperty("display_name");
  });

  it("enables password sign-in only when configured", () => {
    expect(passwordSignInEnabled({ MEIP_UI_AUTH: "password" } as unknown as NodeJS.ProcessEnv)).toBe(true);
    expect(passwordSignInEnabled({} as NodeJS.ProcessEnv)).toBe(false);
  });

  it("forwards the team and invitation routes, and nothing next to them", () => {
    const split = (path: string) => path.split("/");
    for (const [method, path] of [
      ["GET", "members"],
      ["POST", "members"],
      ["PATCH", `members/${ID}`],
      ["POST", `members/${ID}/password-reset`],
      ["POST", `me/invitations/${ID}/accept`],
    ] as const) {
      expect(isAllowed(method, split(path))).toBe(true);
    }
    for (const [method, path] of [
      ["DELETE", `members/${ID}`],
      ["POST", `members/${ID}`],
      ["POST", "auth/login"],
      ["POST", "auth/password"],
      ["POST", `members/not-a-uuid/password-reset`],
    ] as const) {
      expect(isAllowed(method, split(path))).toBe(false);
    }
  });
});
