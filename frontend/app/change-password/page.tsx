"use client";

import { KeyRound } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FieldHint, Input, Label } from "@/components/ui/input";
import { passwordProblem } from "@/lib/accounts";

export default function ChangePasswordPage() {
  const [first, setFirst] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setFirst(new URLSearchParams(window.location.search).get("first") === "1");
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = passwordProblem(next, confirm);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    const response = await fetch("/api/session/password", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ current_password: current, new_password: next }),
    });
    setBusy(false);
    if (response.status === 204) {
      setDone(true);
      return;
    }
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    setError(body?.detail ?? "The password wasn't changed");
  }

  return (
    <main className="mx-auto max-w-md px-6 py-16">
      <Card>
        <CardContent className="flex flex-col gap-5 p-6">
          <div className="grid size-10 place-items-center rounded-full bg-brand-50 text-brand-600">
            <KeyRound className="size-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold">{first ? "Choose your password" : "Change your password"}</h1>
            <p className="mt-1 text-sm text-slate-500">
              {first
                ? "You signed in with a temporary password. Choose your own before continuing."
                : "You'll be asked to sign in again afterwards."}
            </p>
          </div>
          {done ? (
            <Alert tone="info" title="Password changed">
              <Link href="/sign-in" className="text-brand-700 hover:underline">
                Sign in with your new password
              </Link>
            </Alert>
          ) : (
            <form onSubmit={submit} className="flex flex-col gap-4">
              <Label>
                {first ? "Temporary password" : "Current password"}
                <Input
                  type="password"
                  autoComplete="current-password"
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                  required
                />
              </Label>
              <Label>
                New password
                <Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />
                <FieldHint>At least 12 characters. A short sentence works well.</FieldHint>
              </Label>
              <Label>
                Confirm new password
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                />
              </Label>
              <Button type="submit" disabled={busy || !current || !next}>
                Save password
              </Button>
              {error && <Alert tone="error">{error}</Alert>}
            </form>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
