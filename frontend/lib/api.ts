// Browser-side calls, always through this app's forwarder (/api/meip/...). The browser never
// holds the token or talks to the API directly.

import type { Problem } from "./types";

export type Result<T> =
  | { ok: true; status: number; data: T; etag: string | null }
  | { ok: false; status: number; problem: Problem | null };

export const API = "/api/meip";

async function read<T>(response: Response): Promise<Result<T>> {
  if (response.ok) {
    const data = (response.status === 204 ? null : await response.json()) as T;
    return { ok: true, status: response.status, data, etag: response.headers.get("etag") };
  }
  const problem = (await response.json().catch(() => null)) as Problem | null;
  return { ok: false, status: response.status, problem };
}

export async function get<T>(path: string): Promise<Result<T>> {
  return read<T>(await fetch(`${API}/${path}`, { cache: "no-store" }));
}

export async function postJson<T>(
  path: string,
  body: unknown,
  ifMatch?: number,
): Promise<Result<T>> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (ifMatch !== undefined) headers["if-match"] = `"${ifMatch}"`;
  return read<T>(
    await fetch(`${API}/${path}`, { method: "POST", headers, body: JSON.stringify(body) }),
  );
}

export async function uploadPdf<T>(
  caseId: string,
  file: File,
  applicantId: string,
): Promise<Result<T>> {
  const query = new URLSearchParams({ document_type: "payslip", applicant_id: applicantId });
  return read<T>(
    await fetch(`${API}/cases/${caseId}/documents?${query.toString()}`, {
      method: "POST",
      headers: {
        "content-type": "application/pdf",
        "upload-filename": encodeURIComponent(file.name),
      },
      body: file,
    }),
  );
}
