"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export function DevSignInForm() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/session", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token }),
    });
    setBusy(false);
    if (response.status === 204) {
      setToken("");
      router.push("/");
      return;
    }
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    setError(body?.detail ?? `Sign-in failed (${response.status})`);
  }

  return (
    <form onSubmit={submit} className="stack">
      <label>
        Token
        <textarea
          value={token}
          onChange={(e) => setToken(e.target.value)}
          rows={4}
          autoComplete="off"
          spellCheck={false}
          required
        />
      </label>
      <button type="submit" disabled={busy || !token.trim()}>
        Sign in
      </button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}
