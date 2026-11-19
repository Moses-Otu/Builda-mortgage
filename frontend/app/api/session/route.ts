// Development-only sign-in: accept a dev token, check it against the API's /me, and keep it
// in an httpOnly, Secure, SameSite=Strict cookie. Unavailable (404) unless explicitly enabled
// and never in a production build. Signing out (DELETE) always works.

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import {
  apiBaseUrl,
  devSignInEnabled,
  FIRM_COOKIE,
  SESSION_COOKIE_OPTIONS,
  TOKEN_COOKIE,
} from "@/lib/server-config";

export async function POST(request: NextRequest): Promise<Response> {
  if (!devSignInEnabled()) return new NextResponse(null, { status: 404 });
  let token = "";
  try {
    const body = (await request.json()) as { token?: unknown };
    token = typeof body.token === "string" ? body.token.trim() : "";
  } catch {
    token = "";
  }
  if (!token) return NextResponse.json({ detail: "Paste a token" }, { status: 422 });

  const me = await fetch(`${apiBaseUrl()}/api/v1/me`, {
    headers: { authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!me.ok) return NextResponse.json({ detail: "The API did not accept this token" }, { status: 401 });

  const jar = await cookies();
  jar.set(TOKEN_COOKIE, token, SESSION_COOKIE_OPTIONS);
  jar.delete(FIRM_COOKIE); // a new identity chooses its firm again
  return new NextResponse(null, { status: 204 });
}

export async function DELETE(): Promise<Response> {
  const jar = await cookies();
  jar.delete(TOKEN_COOKIE);
  jar.delete(FIRM_COOKIE);
  return new NextResponse(null, { status: 204 });
}
