"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { get } from "@/lib/api";
import { problemMessage } from "@/lib/problems";
import type { CaseSummary, Me, Page } from "@/lib/types";

export default function Home() {
  const [me, setMe] = useState<Me | null>(null);
  const [signedOut, setSignedOut] = useState(false);
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const loadCases = useCallback(async () => {
    const result = await get<Page<CaseSummary>>("cases?limit=100");
    if (result.ok) {
      setCases(result.data.items);
      setMessage(null);
    } else if (result.status === 400 || result.status === 422) {
      setCases(null); // no firm chosen yet
    } else {
      setMessage(problemMessage(result.status, result.problem));
    }
  }, []);

  useEffect(() => {
    void (async () => {
      const result = await get<Me>("me");
      if (!result.ok) {
        setSignedOut(true);
        return;
      }
      setMe(result.data);
      await loadCases();
    })();
  }, [loadCases]);

  async function chooseFirm(tenantId: string) {
    const response = await fetch("/api/session/firm", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tenant_id: tenantId }),
    });
    if (response.status === 204) await loadCases();
    else setMessage("Could not choose that firm.");
  }

  async function signOut() {
    await fetch("/api/session", { method: "DELETE" });
    window.location.href = "/";
  }

  if (signedOut) {
    return (
      <main className="narrow">
        <h1>Sign in</h1>
        <p className="note">
          Sign-in is not configured for this environment. In local development, enable it with{" "}
          <code>MEIP_UI_DEV_SIGN_IN=true</code> and use the{" "}
          <Link href="/dev-sign-in">development sign-in</Link>.
        </p>
      </main>
    );
  }
  if (!me) return <main>Loading…</main>;

  return (
    <main>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Cases</h1>
        <button className="secondary" onClick={signOut}>
          Sign out
        </button>
      </div>
      <div className="card stack">
        <label>
          Firm
          <select defaultValue="" onChange={(e) => e.target.value && chooseFirm(e.target.value)}>
            <option value="">Choose a firm…</option>
            {me.memberships.map((m) => (
              <option key={m.tenant_id} value={m.tenant_id}>
                {m.tenant_name} ({m.role})
              </option>
            ))}
          </select>
        </label>
        <p className="note">
          Choosing a firm only selects where you work. Access is checked by the server on every
          request.
        </p>
      </div>
      {message && <p className="error" role="alert">{message}</p>}
      {cases && (
        <table style={{ marginTop: "1rem" }}>
          <thead>
            <tr>
              <th>Reference</th>
              <th>Purpose</th>
              <th>Status</th>
              <th>Applicants</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {cases.length === 0 && (
              <tr>
                <td colSpan={5} className="note">
                  No cases yet.
                </td>
              </tr>
            )}
            {cases.map((c) => (
              <tr key={c.id}>
                <td>
                  <Link href={`/cases/${c.id}`}>{c.external_reference ?? c.id.slice(0, 8)}</Link>
                </td>
                <td>{c.purpose}</td>
                <td>{c.status}</td>
                <td>{c.applicant_count}</td>
                <td>{new Date(c.created_at).toLocaleDateString("en-GB")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </main>
  );
}
