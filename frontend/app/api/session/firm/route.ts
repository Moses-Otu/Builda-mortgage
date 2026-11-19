// Choose the firm a session acts within. Accepted only for a firm the API's /me lists as an
// active membership. This is a convenience, not proof of access: the API checks membership
// on every request and row-level security isolates the data.

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import { apiBaseUrl, FIRM_COOKIE, SESSION_COOKIE_OPTIONS, TOKEN_COOKIE } from "@/lib/server-config";
import type { Me } from "@/lib/types";

/** Which firm the session currently acts within (the cookie is httpOnly, so the page asks).
 * Only the ID the browser itself chose; still not proof of access. */
export async function GET(): Promise<Response> {
  const jar = await cookies();
  if (!jar.get(TOKEN_COOKIE)?.value) return NextResponse.json({ detail: "Sign in first" }, { status: 401 });
  return NextResponse.json({ tenant_id: jar.get(FIRM_COOKIE)?.value ?? null });
}

export async function POST(request: NextRequest): Promise<Response> {
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "Sign in first" }, { status: 401 });
  let tenantId = "";
  try {
    const body = (await request.json()) as { tenant_id?: unknown };
    tenantId = typeof body.tenant_id === "string" ? body.tenant_id : "";
  } catch {
    tenantId = "";
  }
  const response = await fetch(`${apiBaseUrl()}/api/v1/me`, {
    headers: { authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) return NextResponse.json({ detail: "Sign in again" }, { status: 401 });
  const me = (await response.json()) as Me;
  if (!me.memberships.some((m) => m.tenant_id === tenantId)) {
    return NextResponse.json({ detail: "You are not a member of that firm" }, { status: 403 });
  }
  jar.set(FIRM_COOKIE, tenantId, SESSION_COOKIE_OPTIONS);
  return new NextResponse(null, { status: 204 });
}
