"use client";

import { ChevronDown, CircleCheck, CircleDashed, ExternalLink, Info, MessageSquareText, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

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
import { Tooltip } from "@/components/ui/tooltip";
import { get, postJson } from "@/lib/api";
import {
  BANK_RULESET,
  checksSummary,
  evidenceHref,
  evidenceLabel,
  groupFindings,
  ofRuleset,
  outcomeLabel,
  PAYSLIP_RULESET,
} from "@/lib/checks";
import { formatDateTime } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { Checks, Finding } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Wording agreed for Stage 2-A: acknowledging is not resolving or approving. */
export const ACKNOWLEDGE_EXPLANATION =
  "This records that you've seen this discrepancy. It doesn't change any figures, resolve the discrepancy or approve the income.";

export function ChecksCard({ caseId, refreshKey }: { caseId: string; refreshKey: string }) {
  const [checks, setChecks] = useState<Checks | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [acknowledging, setAcknowledging] = useState<Finding | null>(null);

  const load = useCallback(async () => {
    const result = await get<Checks>(`cases/${caseId}/checks`);
    if (result.ok) {
      setChecks(result.data);
      setError(null);
    } else setError(problemMessage(result.status, result.problem));
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  const groups = checks ? groupFindings(checks.findings) : null;
  const payslipFindings = checks ? ofRuleset(checks.findings, PAYSLIP_RULESET) : [];
  const bankFindings = checks ? ofRuleset(checks.findings, BANK_RULESET) : [];
  const versionOf = (family: string) =>
    checks?.ruleset_versions.find((v) => v.split("/")[0] === family) ?? family;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            Checks
          </CardTitle>
          <CardDescription>{groups ? checksSummary(groups) : "Comparing the reviewed documents…"}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error && <Alert tone="error">{error}</Alert>}
        {groups && checks && checks.findings.length === 0 && (
          <p className="rounded-lg border border-dashed border-slate-300 px-3 py-4 text-center text-sm text-slate-500">
            Nothing to check yet. Review a payslip, confirm a bank statement, or declare a basic salary.
          </p>
        )}
        {payslipFindings.length > 0 && (
          <FindingSection
            title="Payslips"
            version={versionOf(PAYSLIP_RULESET)}
            explanation="Deterministic checks over the reviewed payslips and declared figures. They never decide anything about the case."
            caseId={caseId}
            findings={payslipFindings}
            onAcknowledge={setAcknowledging}
            headed={bankFindings.length > 0}
          />
        )}
        {bankFindings.length > 0 && (
          <FindingSection
            title="Bank statements"
            version={versionOf(BANK_RULESET)}
            explanation="Deterministic checks over the confirmed statements: totals, running balance, gaps between statements, and whether each payslip's net pay arrived. A near match is never treated as received."
            caseId={caseId}
            findings={bankFindings}
            onAcknowledge={setAcknowledging}
            headed
          />
        )}
      </CardContent>
      {acknowledging && (
        <AcknowledgeDialog
          caseId={caseId}
          finding={acknowledging}
          onClose={() => setAcknowledging(null)}
          onDone={() => {
            setAcknowledging(null);
            void load();
          }}
        />
      )}
    </Card>
  );
}

function FindingSection(props: {
  title: string;
  version: string;
  explanation: string;
  caseId: string;
  findings: Finding[];
  onAcknowledge: (f: Finding) => void;
  headed: boolean;
}) {
  const { title, version, explanation, caseId, findings, onAcknowledge, headed } = props;
  const groups = groupFindings(findings);
  return (
    <section className="flex flex-col gap-3" aria-label={title}>
      {headed && (
        <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-200 pb-1.5">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            {title}
            <Tooltip content={explanation}>
              <span tabIndex={0} className="font-mono text-xs font-normal text-slate-400">
                {version}
              </span>
            </Tooltip>
          </h3>
          <span className="text-xs text-slate-500">{checksSummary(groups)}</span>
        </div>
      )}
      {!headed && (
        <Tooltip content={explanation}>
          <span tabIndex={0} className="self-start font-mono text-xs text-slate-400">
            {version}
          </span>
        </Tooltip>
      )}
      {groups.open.map((f) => (
        <FindingRow key={f.key} caseId={caseId} finding={f} onAcknowledge={() => onAcknowledge(f)} />
      ))}
      {groups.acknowledged.map((f) => (
        <FindingRow key={f.key} caseId={caseId} finding={f} />
      ))}
      {groups.notRun.length > 0 && (
        <Collapsible title={`Could not run (${groups.notRun.length})`} icon={<CircleDashed className="size-4 text-slate-400" />}>
          {groups.notRun.map((f) => (
            <FindingRow key={f.key} caseId={caseId} finding={f} compact />
          ))}
        </Collapsible>
      )}
      {groups.passed.length > 0 && (
        <Collapsible title={`Passed (${groups.passed.length})`} icon={<CircleCheck className="size-4 text-accept" />}>
          {groups.passed.map((f) => (
            <FindingRow key={f.key} caseId={caseId} finding={f} compact />
          ))}
        </Collapsible>
      )}
    </section>
  );
}

function Collapsible({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-slate-200">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-50"
      >
        {icon}
        <span className="flex-1">{title}</span>
        <ChevronDown className={cn("size-4 text-slate-400 transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="flex flex-col gap-2 border-t border-slate-200 p-2">{children}</div>}
    </div>
  );
}

function FindingRow(props: { caseId: string; finding: Finding; compact?: boolean; onAcknowledge?: () => void }) {
  const { caseId, finding: f, compact, onAcknowledge } = props;
  const discrepancy = f.status === "discrepancy";
  return (
    <div
      className={cn(
        "rounded-lg border p-3 text-sm",
        discrepancy ? "border-amber-200 bg-correct-soft/60" : "border-slate-200 bg-white",
        compact && "p-2.5",
      )}
    >
      <div className="flex items-start gap-2">
        {discrepancy ? (
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-correct" />
        ) : f.status === "passed" ? (
          <CircleCheck className="mt-0.5 size-4 shrink-0 text-accept" />
        ) : (
          <Info className="mt-0.5 size-4 shrink-0 text-slate-400" />
        )}
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-center gap-2 font-medium text-slate-900">
            {f.title}
            {discrepancy && (
              <Badge tone="warning">{f.acknowledgement ? "Discrepancy · acknowledged" : "Discrepancy"}</Badge>
            )}
            {outcomeLabel(f.match_outcome) && <Badge tone="neutral">{outcomeLabel(f.match_outcome)}</Badge>}
          </p>
          <p className="mt-0.5 text-slate-600">{f.message}</p>
          {!compact && f.evidence.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {f.evidence.map((e, i) => {
                const href = evidenceHref(caseId, e);
                const chip = (
                  <span className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-700">
                    {evidenceLabel(e)}
                    {href && <ExternalLink className="size-3 text-slate-400" />}
                  </span>
                );
                return href ? (
                  <Link key={i} href={href} className="hover:[&>span]:border-brand-500 hover:[&>span]:text-brand-700">
                    {chip}
                  </Link>
                ) : (
                  <span key={i}>{chip}</span>
                );
              })}
            </div>
          )}
          {f.acknowledgement && (
            <p className="mt-2 flex items-start gap-1.5 rounded-md bg-white/70 px-2 py-1.5 text-xs text-slate-600">
              <MessageSquareText className="mt-px size-3.5 shrink-0" />
              <span>
                “{f.acknowledgement.note}” · acknowledged {formatDateTime(f.acknowledgement.acknowledged_at)}
              </span>
            </p>
          )}
        </div>
        {discrepancy && !f.acknowledgement && onAcknowledge && (
          <Button size="sm" variant="secondary" onClick={onAcknowledge}>
            Acknowledge…
          </Button>
        )}
      </div>
    </div>
  );
}

function AcknowledgeDialog(props: { caseId: string; finding: Finding; onClose: () => void; onDone: () => void }) {
  const { caseId, finding, onClose, onDone } = props;
  const toast = useToast();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await postJson<Finding>(`cases/${caseId}/checks/${finding.key}/acknowledgement`, {
      note: note.trim(),
    });
    setBusy(false);
    if (result.ok) {
      toast("success", "Acknowledged. It stays listed as a discrepancy.");
      onDone();
    } else if (result.status === 409) {
      toast("error", problemMessage(result.status, result.problem));
      onDone(); // the finding changed or was acknowledged already: show the current checks
    } else {
      setError(problemMessage(result.status, result.problem));
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Acknowledge discrepancy</DialogTitle>
            <DialogDescription>{ACKNOWLEDGE_EXPLANATION}</DialogDescription>
          </DialogHeader>
          <div className="rounded-lg bg-slate-50 p-3 text-sm">
            <p className="font-medium">{finding.title}</p>
            <p className="mt-0.5 text-slate-600">{finding.message}</p>
          </div>
          <Label>
            What you found or did
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={1000}
              required
              autoFocus
              placeholder="e.g. Employer confirmed a back-dated pay rise in June"
            />
            <FieldHint>Recorded with your name and the time. Synthetic data only: no real client details.</FieldHint>
          </Label>
          {error && <Alert tone="error">{error}</Alert>}
          <DialogFooter>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !note.trim()}>
              Acknowledge
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
