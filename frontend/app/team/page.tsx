"use client";

import { KeyRound, UserPlus } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Breadcrumbs, Loading, PageContainer, RequireFirm } from "@/components/Page";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { get, postJson } from "@/lib/api";
import { INVITABLE_ROLES, inviteBody } from "@/lib/accounts";
import { roleLabel } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { Membership } from "@/lib/types";

interface Member {
  membership_id: string;
  email: string | null;
  display_name: string | null;
  role: string;
  status: string;
  version: number;
  temporary_password?: string | null;
}

export default function TeamRoute() {
  return <RequireFirm>{(firm) => <TeamPage firm={firm} />}</RequireFirm>;
}

function TeamPage({ firm }: { firm: Membership }) {
  const toast = useToast();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<string>("adviser");
  const [busy, setBusy] = useState(false);
  // A temporary password is shown once, to the admin who created it, then forgotten.
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null);

  const load = useCallback(async () => {
    const result = await get<Member[]>("members");
    if (result.ok) setMembers(result.data);
    else setError(problemMessage(result.status, result.problem));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (firm.role !== "firm_admin") {
    return (
      <PageContainer>
        <Alert tone="info">Only a firm administrator manages the team.</Alert>
      </PageContainer>
    );
  }

  async function invite(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const result = await postJson<Member>("members", inviteBody(email, name, role));
    setBusy(false);
    if (!result.ok) {
      toast("error", problemMessage(result.status, result.problem));
      return;
    }
    if (result.data.temporary_password) setSecret({ email: email.trim().toLowerCase(), password: result.data.temporary_password });
    else toast("success", "Invited. They already have an account and sign in with their own password.");
    setEmail("");
    setName("");
    await load();
  }

  async function reset(m: Member) {
    const result = await postJson<{ temporary_password: string }>(`members/${m.membership_id}/password-reset`, {});
    if (result.ok) setSecret({ email: m.email ?? "this member", password: result.data.temporary_password });
    else toast("error", problemMessage(result.status, result.problem));
  }

  async function deactivate(m: Member) {
    const result = await fetch(`/api/meip/members/${m.membership_id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "if-match": `"${m.version}"` },
      body: JSON.stringify({ status: "deactivated" }),
    });
    if (result.ok) {
      toast("success", "Access removed.");
      await load();
    } else toast("error", "Couldn't remove access. Reload and try again.");
  }

  return (
    <PageContainer>
      <Breadcrumbs items={[{ label: "Cases", href: "/" }, { label: "Team" }]} />
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">Team</h1>
      {error && <Alert tone="error">{error}</Alert>}
      {secret && (
        <Alert tone="warning" title="Temporary password: shown once">
          <p>
            Give this to <strong>{secret.email}</strong> securely (not by the same email as the sign-in link). They must
            choose their own password when they first sign in.
          </p>
          <p className="mt-2 font-mono text-base select-all">{secret.password}</p>
          <Button className="mt-2" size="sm" variant="secondary" onClick={() => setSecret(null)}>
            I&apos;ve passed it on
          </Button>
        </Alert>
      )}
      <div className="mt-4 grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Members</CardTitle>
          </CardHeader>
          <CardContent>
            {!members ? (
              <Loading />
            ) : (
              <ul className="divide-y divide-slate-100 text-sm">
                {members.map((m) => (
                  <li key={m.membership_id} className="flex flex-wrap items-center gap-3 py-2.5">
                    <span className="font-medium">{m.display_name ?? m.email ?? "Member"}</span>
                    <span className="text-slate-500">{m.email}</span>
                    <Badge tone={m.status === "active" ? "success" : m.status === "invited" ? "info" : "neutral"}>
                      {m.status}
                    </Badge>
                    <span className="text-slate-500">{roleLabel(m.role)}</span>
                    {m.status !== "deactivated" && (
                      <span className="ml-auto flex gap-2">
                        <Button size="sm" variant="secondary" onClick={() => void reset(m)}>
                          <KeyRound /> Reset password
                        </Button>
                        <Button size="sm" variant="secondary" onClick={() => void deactivate(m)}>
                          Remove access
                        </Button>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
        <Card className="self-start">
          <CardHeader>
            <div>
              <CardTitle>Invite a colleague</CardTitle>
              <CardDescription>They sign in with their email and accept the invitation.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={invite} className="flex flex-col gap-3">
              <Label>
                Email
                <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
              </Label>
              <Label>
                Name
                <Input value={name} onChange={(e) => setName(e.target.value)} />
                <FieldHint>Shown to your firm only.</FieldHint>
              </Label>
              <Label>
                Role
                <Select value={role} onChange={(e) => setRole(e.target.value)}>
                  {INVITABLE_ROLES.map((r) => (
                    <option key={r} value={r}>
                      {roleLabel(r)}
                    </option>
                  ))}
                </Select>
              </Label>
              <Button type="submit" disabled={busy || !email}>
                <UserPlus /> Invite
              </Button>
            </form>
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
