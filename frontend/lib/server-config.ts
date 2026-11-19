// Server-side settings and session rules. Imported only by route handlers and server
// components; nothing here reaches the browser bundle.

export const TOKEN_COOKIE = "meip_session";
export const FIRM_COOKIE = "meip_firm";

/** The FastAPI service the forwarder talks to. */
export function apiBaseUrl(env: NodeJS.ProcessEnv = process.env): string {
  return (env.MEIP_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

/** Fail-closed: the development sign-in exists only when explicitly enabled, and never in a
 * production build, whatever the flag says. */
export function devSignInEnabled(env: NodeJS.ProcessEnv = process.env): boolean {
  return env.MEIP_UI_DEV_SIGN_IN === "true" && env.NODE_ENV !== "production";
}

/** Attributes for both session cookies. Browsers treat http://localhost as secure, so
 * `secure` holds in local development too. */
export const SESSION_COOKIE_OPTIONS = {
  httpOnly: true,
  secure: true,
  sameSite: "strict",
  path: "/",
  maxAge: 60 * 60 * 8,
} as const;
