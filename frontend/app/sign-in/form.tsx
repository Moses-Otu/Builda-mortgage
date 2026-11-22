"use client";

import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";

export function SignInForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const response = await fetch("/api/session/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    setBusy(false);
    if (response.ok) {
      const body = (await response.json()) as { must_change_password: boolean };
      setPassword("");
      window.location.href = body.must_change_password ? "/change-password?first=1" : "/";
      return;
    }
    const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
    setError(problem?.detail ?? "Sign-in failed");
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <Label>
        Email
        <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      </Label>
      <Label>
        Password
        <Input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </Label>
      <Button type="submit" disabled={busy || !email || !password}>
        Sign in
      </Button>
      {error && <Alert tone="error">{error}</Alert>}
    </form>
  );
}
