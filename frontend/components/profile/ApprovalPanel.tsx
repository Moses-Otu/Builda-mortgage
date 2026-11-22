"use client";

import { BadgeCheck, CircleAlert, Download, History, Info, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useState } from "react";

import { useSession } from "@/components/session";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FieldHint, Label, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import { approvalBanner, approveBody, blockerLink, canApprove, packHref } from "@/lib/approval";
import { formatDateTime } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { Approval, ApprovalDetail, Profile } from "@/lib/types";

/** Readiness, approving, the verified banner and the version history (Stage 3-B). */
export function ApprovalPanel(props: {
  caseId: string;
  profile: Profile;
  approvals: Approval[];
  viewing: ApprovalDetail | null;
  onChanged: () => void;
}) {
  const { caseId, profile, approvals, viewing, onChanged } = props;
  const { session } = useSession();
  const role = session.status === "ready" ? session.firm?.role : null;
  const [approving, setApproving] = useState(false);
  const banner = approvalBanner(profile.approval);
  const r = profile.readiness;

  if (viewing) {
    return (
      <Alert tone="info" title={`Version ${viewing.version}${viewing.current ? " (current)" : " (superseded)"}`}>
        <p>
          Approved by {viewing.approved_by?.name ?? "unknown"} on {formatDateTime(viewing.approved_at)}
          {viewing.superseded_at ? `; superseded on ${formatDateTime(viewing.superseded_at)}` : ""}. This is the
          profile exactly as approved, read-only.
        </p>
        <p className="mt-2 text-xs text-slate-600">
          <span className="font-medium">Attested ({viewing.attestation_version}):</span> {viewing.attestation_text}
        </p>
        <div className="mt-2 flex flex-wrap gap-4 text-sm">
          <a href={packHref(caseId, viewing.id)} download className="inline-flex items-center gap-1.5 text-brand-700 hover:underline">
            <Download className="size-4" /> Download evidence pack (version {viewing.version})
          </a>
          <Link href={`/cases/${caseId}/profile`} className="text-brand-700 hover:underline">
            Back to the current profile
          </Link>
        </div>
      </Alert>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {banner ? (
        <div role="status" className="rounded-lg border border-green-200 bg-accept-soft px-4 py-3 text-sm text-slate-700">
          <p className="flex items-center gap-2 font-semibold text-accept">
            <BadgeCheck className="size-4" /> {banner}
          </p>
          <p className="mt-1">
            The case is verified and read-only. Reopen it from the case page to change anything; this version stays in the
            history.
          </p>
          {profile.approval.approval_id && (
            <a
              href={packHref(caseId, profile.approval.approval_id)}
              download
              className="mt-2 inline-flex items-center gap-1.5 font-medium text-brand-700 hover:underline"
            >
              <Download className="size-4" /> Download evidence pack
            </a>
          )}
        </div>
      ) : (
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-slate-400" /> Approval
              </CardTitle>
              <CardDescription>
                {r.ready ? "Nothing outstanding. The profile can be approved." : "Before the profile can be approved:"}
              </CardDescription>
            </div>
            {r.ready && canApprove(role) && <Button onClick={() => setApproving(true)}>Approve profile…</Button>}
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-sm">
            {r.blockers.map((b) => {
              const link = blockerLink(caseId, b.code, b.ids);
              return (
                <p key={b.code} className="flex items-start gap-2">
                  <CircleAlert className="mt-0.5 size-4 shrink-0 text-correct" />
                  <span>
                    {b.message}
                    {link && (
                      <Link href={link.href} className="ml-2 text-brand-700 hover:underline">
                        {link.label}
                      </Link>
                    )}
                  </span>
                </p>
              );
            })}
            {r.notices.map((n) => (
              <p key={n.message} className="flex items-start gap-2 text-slate-600">
                <Info className="mt-0.5 size-4 shrink-0 text-slate-400" /> {n.message}
              </p>
            ))}
            {r.ready && !canApprove(role) && (
              <p className="text-slate-500">An adviser or firm admin approves the profile.</p>
            )}
          </CardContent>
        </Card>
      )}
      {approvals.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
          <History className="size-4 text-slate-400" /> Versions:
          {approvals.map((a) => (
            <span key={a.id} className="inline-flex items-center gap-1">
              <Link href={`/cases/${caseId}/profile?version=${a.version}`} className="hover:text-brand-700">
                <Badge tone={a.current ? "success" : "neutral"}>
                  v{a.version} {a.current ? "current" : "superseded"}
                </Badge>
              </Link>
              <a
                href={packHref(caseId, a.id)}
                download
                aria-label={`Download evidence pack, version ${a.version}`}
                className="text-slate-400 hover:text-brand-700"
              >
                <Download className="size-3.5" />
              </a>
            </span>
          ))}
        </div>
      )}
      {approving && (
        <ApproveDialog
          caseId={caseId}
          profile={profile}
          onClose={() => setApproving(false)}
          onDone={() => {
            setApproving(false);
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function ApproveDialog(props: { caseId: string; profile: Profile; onClose: () => void; onDone: () => void }) {
  const { caseId, profile, onClose, onDone } = props;
  const toast = useToast();
  const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const r = profile.readiness;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    // The fingerprint the screen loaded with: the server refuses if anything changed since.
    const result = await postJson(`cases/${caseId}/profile/approvals`, approveBody(profile.fingerprint, r, note));
    setBusy(false);
    if (result.ok) {
      toast("success", "Profile approved. The case is verified.");
      onDone();
    } else if (result.status === 409) {
      toast("error", problemMessage(result.status, result.problem));
      onDone(); // the profile changed or is blocked: show the current one
    } else {
      setError(problemMessage(result.status, result.problem));
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <BadgeCheck className="size-5 text-accept" /> Approve the profile
            </DialogTitle>
            <DialogDescription>
              This records the profile exactly as shown, as a new version that can&apos;t be changed. The case becomes
              verified.
            </DialogDescription>
          </DialogHeader>
          {r.notices.length > 0 && (
            <ul className="flex flex-col gap-1 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
              {r.notices.map((n) => (
                <li key={n.message}>{n.message}</li>
              ))}
            </ul>
          )}
          <label className="flex items-start gap-3 rounded-lg border border-slate-200 p-3 text-sm">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="mt-1"
              required
            />
            <span>{r.attestation.text}</span>
          </label>
          <Label>
            Note (optional)
            <Textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
            <FieldHint>
              {r.attestation.version} · profile {profile.fingerprint.slice(0, 12)} · synthetic data only
            </FieldHint>
          </Label>
          {error && <Alert tone="error">{error}</Alert>}
          <DialogFooter>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !confirmed}>
              Approve
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
