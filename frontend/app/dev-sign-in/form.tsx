"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label, Textarea } from "@/components/ui/input";

export function DevSignInForm() {
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
      // A full load, so the header and session pick up the new sign-in.
      window.location.href = "/";
      return;
    }
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    setError(body?.detail ?? `Sign-in failed (${response.status})`);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Label>
        Token
        <Textarea
          value={token}
          onChange={(e) => setToken(e.target.value)}
          rows={4}
          autoComplete="off"
          spellCheck={false}
          required
          className="font-mono text-xs"
        />
      </Label>
      <Button type="submit" disabled={busy || !token.trim()}>
        Sign in
      </Button>
      {error && <Alert tone="error">{error}</Alert>}
    </form>
  );
}
