// Phase 4: set a new password (also the required step after a temporary password). On
// success the session is ended, so the person signs in again with the new password.

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { apiBaseUrl, FIRM_COOKIE, passwordSignInEnabled, TOKEN_COOKIE } from "@/lib/server-config";

export async function POST(request: NextRequest): Promise<Response> {
  if (!passwordSignInEnabled()) return new NextResponse(null, { status: 404 });
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Sign in first" }, { status: 401 });
  let body: { current_password?: unknown; new_password?: unknown } = {};
  try {
    body = (await request.json()) as typeof body;
  } catch {
    body = {};
  }
  const response = await fetch(`${apiBaseUrl()}/api/v1/auth/password`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({ current_password: body.current_password, new_password: body.new_password }),
    cache: "no-store",
  });
  if (response.status === 204) {
    jar.delete(TOKEN_COOKIE);
    jar.delete(FIRM_COOKIE);
    return new NextResponse(null, { status: 204 });
  }
  const problem = (await response.json().catch(() => null)) as { detail?: string; errors?: { msg?: string }[] } | null;
  const detail = problem?.detail ?? problem?.errors?.[0]?.msg ?? "The password wasn't changed";
  return NextResponse.json({ detail }, { status: response.status === 401 ? 401 : 422 });
}
