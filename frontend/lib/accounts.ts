// Phase 4 pilot accounts: client-side checks that mirror the server's rules (the server
// decides), and the team page's request bodies.

export const MIN_PASSWORD_LENGTH = 12;

export function passwordProblem(next: string, confirm: string): string | null {
  if (next.length < MIN_PASSWORD_LENGTH) return `Use at least ${MIN_PASSWORD_LENGTH} characters`;
  if (next !== confirm) return "The two new passwords don't match";
  return null;
}

export const INVITABLE_ROLES = ["adviser", "case_administrator", "read_only", "firm_admin"] as const;

/** In password mode a member is invited by email, which is also their sign-in. */
export function inviteBody(email: string, displayName: string, role: string): Record<string, string> {
  const address = email.trim().toLowerCase();
  const body: Record<string, string> = { idp_subject: address, email: address, role };
  if (displayName.trim()) body.display_name = displayName.trim();
  return body;
}
