// Phase 4: email and password sign-in. The API checks the password and returns a short-lived
// token, which is kept only in an httpOnly, Secure, SameSite=Strict cookie: browser code never
// sees it. Unavailable (404) unless MEIP_UI_AUTH=password.

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { apiBaseUrl, FIRM_COOKIE, passwordSignInEnabled, SESSION_COOKIE_OPTIONS, TOKEN_COOKIE } from "@/lib/server-config";

export async function POST(request: NextRequest): Promise<Response> {
  if (!passwordSignInEnabled()) return new NextResponse(null, { status: 404 });
  let email = "";
  let password = "";
  try {
    const body = (await request.json()) as { email?: unknown; password?: unknown };
    email = typeof body.email === "string" ? body.email.trim() : "";
    password = typeof body.password === "string" ? body.password : "";
  } catch {
    // fall through: empty values are refused below
  }
  if (!email || !password) return NextResponse.json({ detail: "Enter your email and password" }, { status: 422 });

  const response = await fetch(`${apiBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
    cache: "no-store",
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
    return NextResponse.json(
      { detail: problem?.detail ?? "Sign-in failed" },
      { status: response.status === 401 ? 401 : 502 },
    );
  }
  const signedIn = (await response.json()) as { access_token: string; expires_in: number; must_change_password: boolean };
  const jar = await cookies();
  jar.set(TOKEN_COOKIE, signedIn.access_token, { ...SESSION_COOKIE_OPTIONS, maxAge: signedIn.expires_in });
  jar.delete(FIRM_COOKIE); // a new sign-in chooses its firm again
  return NextResponse.json({ must_change_password: signedIn.must_change_password });
}
