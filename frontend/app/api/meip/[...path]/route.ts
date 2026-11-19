// Forwards the browser's calls to the FastAPI service, adding the bearer token and the
// chosen firm from httpOnly cookies. Only allowlisted calls pass; bodies, tokens and file
// names are never logged. The API remains the authority on permissions and tenancy: the
// firm cookie only names which firm a request acts within.

import { cookies } from "next/headers";
import { type NextRequest, NextResponse } from "next/server";

import {
  FORWARDED_REQUEST_HEADERS,
  FORWARDED_RESPONSE_HEADERS,
  isAllowed,
} from "@/lib/allowlist";
import { apiBaseUrl, FIRM_COOKIE, TOKEN_COOKIE } from "@/lib/server-config";

type Context = { params: Promise<{ path: string[] }> };

function problem(status: number, type: string, title: string): NextResponse {
  return NextResponse.json(
    { type: `urn:meip:problem:${type}`, title, status },
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

async function forward(request: NextRequest, context: Context): Promise<Response> {
  const { path } = await context.params;
  if (!isAllowed(request.method, path)) {
    return problem(404, "not-found", "Not found");
  }
  const jar = await cookies();
  const token = jar.get(TOKEN_COOKIE)?.value;
  if (!token) return problem(401, "unauthorized", "Sign in first");

  const headers = new Headers({ authorization: `Bearer ${token}` });
  const firm = jar.get(FIRM_COOKIE)?.value;
  if (firm) headers.set("x-tenant-id", firm);
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }

  const search = request.nextUrl.search;
  const target = `${apiBaseUrl()}/api/v1/${path.join("/")}${search}`;
  const hasBody = request.method === "POST";
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    // Stream uploads instead of buffering them (Node's fetch needs this for a stream body).
    ...(hasBody ? { duplex: "half" } : {}),
    redirect: "error",
    cache: "no-store",
  } as RequestInit);

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export const GET = forward;
export const POST = forward;
export const dynamic = "force-dynamic";
