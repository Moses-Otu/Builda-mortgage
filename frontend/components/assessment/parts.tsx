"use client";

// The assessment's building blocks, shared by the assessment workspace and the broker
// decision page. Every figure comes from the API; nothing is calculated in the browser.

import {
  Bot,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleX,
  FileSearch,
  Gavel,
  ListChecks,
  Loader2,
  Scale,
  SlidersHorizontal,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { type FormEvent, type ReactNode, useMemo, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, Select, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import {
  type Assessment,
  type AssessmentResult,
  type AssessmentSummary,
  CRITERION,
  type Decision,
  decisionLabel,
  type EvidenceRequest,
  gbp,
  LIMIT_LABELS,
  monthLabel,
  nextAction,
  type Outcome,
  OUTCOMES,
  poundsToMinor,
  type ScenarioInput,
  type ScenarioResult,
  type Step,
  stepDigest,
  stepTitle,
  VERIFICATION,
  verificationSplit,
} from "@/lib/assessment";
import { formatDateTime } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import { cn } from "@/lib/utils";

export function EmptyState() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
        <span className="grid size-12 place-items-center rounded-full bg-brand-50 text-brand-600">
          <Sparkles className="size-6" />
        </span>
        <h2 className="text-lg font-semibold">No assessment yet</h2>
        <p className="max-w-xl text-sm text-slate-500">
          Run the assessment once the applicant&apos;s documents are reviewed, or at any point: the agent works with
          whatever is on file and tells you exactly what&apos;s missing. Set the loan amount and property value on the case
          for affordability and loan to value.
        </p>
      </CardContent>
    </Card>
  );
}

export function Running({ assessment }: { assessment: Assessment }) {
  return (
    <Alert tone="info" title={`Assessment version ${assessment.version} in progress`} className="mb-4">
      <span className="inline-flex items-center gap-2">
        <Loader2 className="size-4 animate-spin" /> {nextAction(assessment, assessment.steps)} ({assessment.step_count}{" "}
        steps so far)
      </span>
    </Alert>
  );
}

export function Stat({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-medium tracking-wide text-slate-500 uppercase">{label}</p>
      <div className="mt-1.5 text-lg font-semibold">{children}</div>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

export function rangeHint(range: [number, number] | null): string {
  if (!range) return "Needs counted income";
  if (range[0] === range[1]) return "All counted income is verified";
  if (range[0] === 0) return "None of the counted income is verified yet";
  return "From verified income only, to all counted income";
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  const o = OUTCOMES[outcome];
  return <Badge tone={o.tone}>{o.label}</Badge>;
}

export function Overview({ assessment, result, requests }: { assessment: Assessment; result: AssessmentResult; requests: EvidenceRequest[] }) {
  const a = result.affordability;
  const e = result.eligibility;
  const split = verificationSplit(result.income);
  const open = requests.filter((r) => r.status === "open").length;
  const overridden = assessment.final_outcome && assessment.final_outcome !== assessment.outcome;
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat label="Pre-qualification" hint={OUTCOMES[assessment.final_outcome ?? e.outcome].explanation}>
        <div className="flex flex-wrap items-center gap-2">
          <OutcomeBadge outcome={assessment.final_outcome ?? e.outcome} />
          {overridden && <Badge tone="neutral">Broker override</Badge>}
        </div>
      </Stat>
      <Stat label="Indicative borrowing" hint={rangeHint(e.indicative_range_minor)}>
        {!e.indicative_range_minor
          ? "—"
          : e.indicative_range_minor[0] === e.indicative_range_minor[1]
            ? gbp(e.indicative_range_minor[1])
            : e.indicative_range_minor[0] === 0
              ? `Up to ${gbp(e.indicative_range_minor[1])}`
              : `${gbp(e.indicative_range_minor[0])} – ${gbp(e.indicative_range_minor[1])}`}
      </Stat>
      <Stat
        label="Requested loan"
        hint={
          a?.requested_within_limits === false
            ? `Over the limit by ${gbp(a.shortfall_minor)}`
            : a?.requested_within_limits
              ? `Within every limit (max ${gbp(a.max_mortgage_minor)})`
              : "Set the loan and property value on the case"
        }
      >
        <span className={cn(a?.requested_within_limits === false && "text-reject")}>{gbp(a?.loan_amount_minor)}</span>
      </Stat>
      <Stat label="Evidence" hint={`${open} open request${open === 1 ? "" : "s"} · ${result.discrepancies.filter((d) => !d.acknowledged).length} unreviewed discrepancies`}>
        <span className="text-base">
          {gbp(split.verified)} verified · {gbp(split.unverified)} unverified
          <span className="block text-xs font-normal text-slate-500">qualifying gross income a month</span>
        </span>
      </Stat>
    </div>
  );
}

export function AgentSummary({ assessment, result }: { assessment: Assessment; result: AssessmentResult }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-4 text-brand-600" /> What the agent concluded
          </CardTitle>
          <CardDescription>
            Version {assessment.version} · {result.policy.name} · planner {assessment.planner}
            {assessment.model ? ` (${assessment.model})` : ""} · {assessment.finished_at && formatDateTime(assessment.finished_at)}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        <p className="leading-relaxed whitespace-pre-line">{result.summary}</p>
        {result.escalations.length > 0 && (
          <Alert tone="warning" title="Needs your attention">
            <ul className="list-disc pl-4">
              {result.escalations.map((x, i) => (
                <li key={i}>{x.reason}</li>
              ))}
            </ul>
          </Alert>
        )}
        {result.findings.length > 0 && (
          <div>
            <h3 className="mb-2 font-medium">Findings</h3>
            <ul className="flex flex-col gap-2">
              {result.findings.map((f, i) => (
                <li key={i} className="rounded-lg border border-slate-200 p-3">
                  <p className="flex items-center gap-2 font-medium">
                    <Badge tone={f.severity === "critical" ? "danger" : f.severity === "warning" ? "warning" : "neutral"}>
                      {f.severity}
                    </Badge>
                    {f.title}
                  </p>
                  <p className="mt-1 text-slate-600">{f.detail}</p>
                </li>
              ))}
            </ul>
          </div>
        )}
        {result.recommendations.length > 0 && (
          <div>
            <h3 className="mb-2 font-medium">Recommendations</h3>
            <ul className="list-disc space-y-1 pl-5 text-slate-700">
              {result.recommendations.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}
        {result.reassessment && (
          <div className="rounded-lg border border-brand-200 bg-brand-50 p-3">
            <h3 className="font-medium">Since version {result.reassessment.previous_version}</h3>
            {result.reassessment.changes.length === 0 ? (
              <p className="text-slate-600">No changes in the evidence; re-run on request.</p>
            ) : (
              <ul className="mt-1 list-disc pl-5 text-slate-700">
                {result.reassessment.changes.map((c, i) => (
                  <li key={i}>{c.detail}</li>
                ))}
              </ul>
            )}
            {result.reassessment.affected.length > 0 && (
              <p className="mt-2 text-xs text-slate-600">Affects: {result.reassessment.affected.join("; ")}.</p>
            )}
          </div>
        )}
        {result.limitations.length > 0 && (
          <p className="text-xs text-slate-500">Limitations: {result.limitations.join(" ")}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-slate-100 py-1.5 last:border-0">
      <dt className="text-slate-600">{label}</dt>
      <dd className={cn("font-medium tabular-nums", warn && "text-reject")}>{value}</dd>
    </div>
  );
}

export function FinancialAssessment({ result }: { result: AssessmentResult }) {
  const a = result.affordability;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Scale className="size-4 text-brand-600" /> Affordability
          </CardTitle>
          <CardDescription>Deterministic calculations under {result.policy.code}. Monthly figures.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left">
            <thead className="text-xs text-slate-500">
              <tr>
                <th className="py-1.5 font-medium">Income</th>
                <th className="font-medium">Evidence</th>
                <th className="text-right font-medium">Gross</th>
                <th className="text-right font-medium">Net</th>
                <th className="text-right font-medium">Counted</th>
              </tr>
            </thead>
            <tbody>
              {result.income.map((i) => (
                <tr key={i.income_source_id} className="border-t border-slate-100 align-top">
                  <td className="py-2">
                    <p className="font-medium">
                      {i.applicant_label} · {i.kind.replace(/_/g, " ")}
                    </p>
                    {i.periods_evidenced.length > 0 && (
                      <p className="text-xs text-slate-500">Payslips: {i.periods_evidenced.map(monthLabel).join(", ")}</p>
                    )}
                    {i.notes.map((n, k) => (
                      <p key={k} className="text-xs text-slate-500">
                        {n}
                      </p>
                    ))}
                  </td>
                  <td className="py-2">
                    <Badge tone={VERIFICATION[i.verification].tone} title={VERIFICATION[i.verification].explanation}>
                      {VERIFICATION[i.verification].label}
                    </Badge>
                  </td>
                  <td className="py-2 text-right tabular-nums">{gbp(i.gross_monthly_minor)}</td>
                  <td className="py-2 text-right tabular-nums">{gbp(i.net_monthly_minor)}</td>
                  <td className="py-2 text-right font-medium tabular-nums">
                    {i.counted ? gbp(i.qualifying_gross_monthly_minor) : <span className="text-slate-400">Not counted</span>}
                  </td>
                </tr>
              ))}
              {result.income.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-3 text-slate-500">
                    No income recorded.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {a ? (
          <div className="grid gap-6 lg:grid-cols-2">
            <dl>
              <Metric label="Qualifying gross income" value={gbp(a.gross_monthly_income_minor)} />
              <Metric label="Qualifying net income" value={gbp(a.net_monthly_income_minor)} />
              <Metric label="Existing commitments" value={gbp(a.commitments_monthly_minor)} />
              <Metric
                label={`Living costs (${a.living_costs_basis === "declared" ? "declared" : "policy default"})`}
                value={gbp(a.living_costs_monthly_minor)}
              />
              <Metric label="Disposable before mortgage" value={gbp(a.disposable_before_mortgage_minor)} warn={a.disposable_before_mortgage_minor <= 0} />
              <Metric label="Max affordable repayment" value={gbp(a.max_affordable_monthly_repayment_minor)} />
            </dl>
            <dl>
              <Metric label={`Repayment at ${a.rate_percent} %`} value={gbp(a.monthly_repayment_minor, true)} />
              <Metric label={`Stressed at ${a.stressed_rate_percent} %`} value={gbp(a.stressed_monthly_repayment_minor, true)} />
              <Metric label={`Total over ${a.term_months / 12} years`} value={gbp(a.total_repayable_minor)} />
              <Metric label="Left after stressed repayment" value={gbp(a.disposable_after_mortgage_minor)} warn={(a.disposable_after_mortgage_minor ?? 0) < 0} />
              <Metric label="Debt to income" value={a.dti_percent ? `${a.dti_percent} %` : "—"} />
              <Metric label="Loan to value" value={a.ltv_percent ? `${a.ltv_percent} %` : "—"} />
              <Metric label="Loan to income" value={a.lti ? `${a.lti}×` : "—"} />
            </dl>
          </div>
        ) : (
          <Alert tone="info">Affordability needs counted income: see the evidence gaps.</Alert>
        )}

        {a && (
          <div>
            <h3 className="mb-2 font-medium">Borrowing limits</h3>
            <ul className="flex flex-col gap-1.5">
              {a.limits.map((l) => (
                <li
                  key={l.code}
                  className={cn(
                    "flex items-center justify-between gap-3 rounded-lg border px-3 py-2",
                    l.code === a.binding_constraint ? "border-brand-300 bg-brand-50" : "border-slate-200",
                  )}
                >
                  <span>
                    {LIMIT_LABELS[l.code]}
                    {l.code === a.binding_constraint && <Badge tone="info" className="ml-2">Binding</Badge>}
                    <span className="block text-xs text-slate-500">{l.explanation}</span>
                  </span>
                  <span className="font-medium tabular-nums">{gbp(l.max_loan_minor)}</span>
                </li>
              ))}
            </ul>
            {a.risks.length > 0 && (
              <ul className="mt-3 flex flex-col gap-1 text-amber-800">
                {a.risks.map((r, i) => (
                  <li key={i} className="flex gap-2">
                    <TriangleAlert className="mt-0.5 size-4 shrink-0" /> {r}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {result.commitments.length > 0 && (
          <div>
            <h3 className="mb-2 font-medium">Commitments</h3>
            <ul className="flex flex-col gap-1">
              {result.commitments.map((c) => (
                <li key={c.commitment_id} className="flex justify-between gap-3">
                  <span>
                    {c.kind.replace(/_/g, " ")}
                    {c.creditor ? ` (${c.creditor})` : ""} <span className="text-xs text-slate-500">{c.basis}</span>
                    {c.notes.map((n, k) => (
                      <span key={k} className="block text-xs text-slate-500">
                        {n}
                      </span>
                    ))}
                  </span>
                  <span className={cn("tabular-nums", !c.counted && "text-slate-400")}>
                    {gbp(c.monthly_minor)} {!c.counted && "(not counted)"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {a && (
          <details className="text-xs text-slate-500">
            <summary className="cursor-pointer">Assumptions</summary>
            <ul className="mt-1 list-disc pl-5">
              {a.assumptions.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

export function ScenarioTable({ rows }: { rows: ScenarioResult[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1.5 font-medium">Scenario (hypothetical)</th>
            <th className="text-right font-medium">Loan</th>
            <th className="text-right font-medium">Term</th>
            <th className="text-right font-medium">Repayment</th>
            <th className="text-right font-medium">Max</th>
            <th className="text-right font-medium">LTV</th>
            <th className="font-medium">Fits?</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s, i) => (
            <tr key={i} className="border-t border-slate-100">
              <td className="py-2">
                {s.label}
                {s.changed.length > 0 && <span className="block text-xs text-slate-500">Changes: {s.changed.join(", ")}</span>}
              </td>
              <td className="text-right tabular-nums">{gbp(s.loan_amount_minor)}</td>
              <td className="text-right tabular-nums">{s.term_months / 12}y</td>
              <td className="text-right tabular-nums">{gbp(s.monthly_repayment_minor, true)}</td>
              <td className="text-right tabular-nums">{gbp(s.max_mortgage_minor)}</td>
              <td className="text-right tabular-nums">{s.ltv_percent ? `${s.ltv_percent} %` : "—"}</td>
              <td>
                {s.within_limits === null ? "—" : s.within_limits ? <Badge tone="success">Yes</Badge> : <Badge tone="danger">No</Badge>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Scenarios({ caseId, policy, initial }: { caseId: string; policy: string; initial: ScenarioResult[] }) {
  const toast = useToast();
  const [rows, setRows] = useState<ScenarioResult[]>(initial);
  const [loan, setLoan] = useState("");
  const [deposit, setDeposit] = useState("");
  const [term, setTerm] = useState("");
  const [rate, setRate] = useState("");
  const [unverified, setUnverified] = useState(false);
  const [busy, setBusy] = useState(false);

  async function run(event: FormEvent) {
    event.preventDefault();
    const s: ScenarioInput = { label: "Your scenario" };
    const l = poundsToMinor(loan);
    const d = poundsToMinor(deposit);
    if (l !== null) s.loan_amount_minor = l;
    if (d !== null) s.deposit_minor = d;
    if (term) s.term_months = Number(term) * 12;
    if (rate) s.rate_percent = rate;
    if (unverified) s.include_unverified_income = true;
    setBusy(true);
    const out = await postJson<{ scenarios: ScenarioResult[] }>(`cases/${caseId}/affordability/scenarios`, {
      policy_code: policy,
      scenarios: [s],
    });
    setBusy(false);
    if (!out.ok) {
      toast("error", problemMessage(out.status, out.problem));
      return;
    }
    setRows((r) => [...out.data.scenarios, ...r.filter((x) => x.label !== "Your scenario")]);
  }

  return (
    <div>
      <p className="mb-2 text-xs text-slate-500">
        Each changes only the assumptions shown. Scenarios never change the assessment above.
      </p>
      {rows.length > 0 && <ScenarioTable rows={rows} />}
      <form onSubmit={run} className="mt-3 grid gap-2 sm:grid-cols-5">
        <Label>
          Loan (£)
          <Input inputMode="numeric" value={loan} onChange={(e) => setLoan(e.target.value)} placeholder="e.g. 140,000" />
        </Label>
        <Label>
          Deposit (£)
          <Input inputMode="numeric" value={deposit} onChange={(e) => setDeposit(e.target.value)} />
        </Label>
        <Label>
          Term (years)
          <Input type="number" min={1} max={40} value={term} onChange={(e) => setTerm(e.target.value)} />
        </Label>
        <Label>
          Rate (%)
          <Input inputMode="decimal" value={rate} onChange={(e) => setRate(e.target.value)} />
        </Label>
        <div className="flex flex-col justify-end gap-1">
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={unverified} onChange={(e) => setUnverified(e.target.checked)} /> Count unverified
            income as if verified
          </label>
          <Button type="submit" size="sm" variant="secondary" disabled={busy}>
            {busy && <Loader2 className="animate-spin" />} Compare
          </Button>
        </div>
      </form>
    </div>
  );
}

export function EvidenceIntelligence({ result, requests, caseId }: { result: AssessmentResult; requests: EvidenceRequest[]; caseId: string }) {
  const open = requests.filter((r) => r.status === "open");
  const closed = requests.filter((r) => r.status !== "open").slice(0, 6);
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <FileSearch className="size-4 text-brand-600" /> Evidence intelligence
          </CardTitle>
          <CardDescription>
            Discrepancies, what&apos;s missing and why, and what&apos;s been requested.{" "}
            <Link href={`/cases/${caseId}/profile`} className="text-brand-700 hover:underline">
              Evidence behind each figure
            </Link>
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 text-sm">
        {result.discrepancies.length > 0 && (
          <div>
            <h3 className="mb-2 font-medium">Discrepancies</h3>
            <ul className="flex flex-col gap-2">
              {result.discrepancies.map((d) => (
                <li key={d.key} className="rounded-lg border border-slate-200 p-3">
                  <p className="flex flex-wrap items-center gap-2 font-medium">
                    {d.title}
                    {d.acknowledged ? <Badge tone="neutral">Acknowledged</Badge> : <Badge tone="warning">Needs review</Badge>}
                  </p>
                  <p className="mt-1 text-slate-600">{d.message}</p>
                  {d.analysis.length > 0 && (
                    <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
                      {d.analysis.map((x, i) => (
                        <li key={i}>{x}</li>
                      ))}
                    </ul>
                  )}
                  {d.acknowledgement_note && <p className="mt-1 text-xs text-slate-500">Your note: {d.acknowledgement_note}</p>}
                </li>
              ))}
            </ul>
            <Link href={`/cases/${caseId}`} className="mt-2 inline-block text-xs text-brand-700 hover:underline">
              Review and acknowledge on the case page
            </Link>
          </div>
        )}
        <div>
          <h3 className="mb-2 font-medium">Evidence gaps</h3>
          {result.evidence_gaps.length === 0 ? (
            <p className="text-slate-500">None: the evidence the policy needs is on file.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {result.evidence_gaps.map((g) => (
                <li key={g.code} className="rounded-lg border border-slate-200 p-3">
                  <p className="flex flex-wrap items-center gap-2 font-medium">
                    {g.title}
                    {g.applicant_label && <Badge tone="neutral">{g.applicant_label}</Badge>}
                    <Badge tone={g.category === "action" ? "warning" : "info"}>{g.category}</Badge>
                    {!g.supported_on_platform && <Badge tone="neutral">Read by the broker</Badge>}
                    {!g.affects_outcome && <Badge tone="neutral">Doesn&apos;t affect the outcome</Badge>}
                  </p>
                  <p className="mt-1 text-slate-600">{g.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <h3 className="mb-2 font-medium">Evidence requests</h3>
          {open.length === 0 && closed.length === 0 && <p className="text-slate-500">None.</p>}
          <ul className="flex flex-col gap-1.5">
            {[...open, ...closed].map((r) => (
              <li key={r.id} className="flex items-start justify-between gap-3">
                <span>
                  {r.title}
                  <span className="block text-xs text-slate-500">
                    {r.requested_by === "agent" ? "Requested by the agent" : "Requested by you"} ·{" "}
                    {formatDateTime(r.created_at)}
                  </span>
                </span>
                <Badge tone={r.status === "open" ? "warning" : r.status === "fulfilled" ? "success" : "neutral"}>
                  {r.status}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}

export function Criteria({ result }: { result: AssessmentResult }) {
  const order = { not_met: 0, unknown: 1, not_assessed: 3, met: 2 } as const;
  const sorted = useMemo(() => [...result.eligibility.criteria].sort((a, b) => order[a.status] - order[b.status]), [result]); // eslint-disable-line react-hooks/exhaustive-deps
  const icon = { met: CircleCheck, not_met: CircleX, unknown: CircleAlert, not_assessed: CircleDashed };
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <ListChecks className="size-4 text-brand-600" /> Pre-qualification criteria
          </CardTitle>
          <CardDescription>{result.policy.name}</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-2 text-sm">
          {sorted.map((c, i) => {
            const Icon = icon[c.status];
            return (
              <li key={`${c.code}-${i}`} className="flex gap-2">
                <Icon
                  className={cn(
                    "mt-0.5 size-4 shrink-0",
                    c.status === "met" && "text-accept",
                    c.status === "not_met" && "text-reject",
                    c.status === "unknown" && "text-correct",
                    c.status === "not_assessed" && "text-slate-400",
                  )}
                />
                <span>
                  <span className="font-medium">
                    {c.title}
                    {c.applicant_label ? ` (${c.applicant_label})` : ""}
                  </span>{" "}
                  <Badge tone={CRITERION[c.status].tone}>{CRITERION[c.status].label}</Badge>
                  <span className="block text-slate-600">{c.detail}</span>
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}

export function Activity({ steps, running }: { steps: Step[]; running: boolean }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Bot className="size-4 text-brand-600" /> Agent activity
          </CardTitle>
          <CardDescription>Each step: the tool it chose, why, and what it found.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ol className="relative flex flex-col gap-3 border-l border-slate-200 pl-4 text-sm">
          {steps.map((s) => (
            <li key={s.seq} className="relative">
              <span
                className={cn(
                  "absolute top-1.5 -left-[21px] size-2.5 rounded-full",
                  s.status === "error" ? "bg-reject" : s.tool === "escalate_to_broker" ? "bg-correct" : "bg-brand-500",
                )}
              />
              <p className="font-medium">{stepTitle(s)}</p>
              {s.rationale && <p className="text-slate-600">{s.rationale}</p>}
              {stepDigest(s) && <p className="text-xs text-slate-500">{stepDigest(s)}</p>}
              {(s.input && Object.keys(s.input).length > 0) || s.output ? (
                <details className="mt-0.5 text-xs text-slate-500">
                  <summary className="cursor-pointer">Details</summary>
                  <pre className="mt-1 max-h-64 overflow-auto rounded bg-slate-50 p-2 whitespace-pre-wrap">
                    {JSON.stringify({ input: s.input, output: s.output }, null, 2)}
                  </pre>
                </details>
              ) : null}
            </li>
          ))}
          {running && (
            <li className="flex items-center gap-2 text-slate-500">
              <Loader2 className="size-4 animate-spin" /> Choosing the next step…
            </li>
          )}
        </ol>
      </CardContent>
    </Card>
  );
}

export function BrokerDecision({
  caseId,
  assessment,
  result,
  onDecided,
}: {
  caseId: string;
  assessment: Assessment;
  result: AssessmentResult;
  onDecided: () => void;
}) {
  const toast = useToast();
  const [kind, setKind] = useState<Decision["decision"]>("accept");
  const [outcome, setOutcome] = useState<Outcome>("potentially_eligible");
  const [reason, setReason] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    const body: Record<string, unknown> = { decision: kind, reason: reason || null };
    if (kind === "override") body.override_outcome = outcome;
    if (kind === "request_information") body.request_title = title;
    const out = await postJson<Decision>(`cases/${caseId}/assessments/${assessment.id}/decisions`, body);
    setBusy(false);
    if (!out.ok) {
      toast("error", problemMessage(out.status, out.problem));
      return;
    }
    toast("success", `${decisionLabel(kind)}.`);
    setReason("");
    setTitle("");
    onDecided();
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Gavel className="size-4 text-brand-600" /> Your decision
          </CardTitle>
          <CardDescription>
            The agent&apos;s outcome: <OutcomeBadge outcome={result.eligibility.outcome} />. The final decision is yours and is
            recorded with your name.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {!result.profile_approved && (
          <Alert tone="info">
            The financial profile isn&apos;t approved yet.{" "}
            <Link href={`/cases/${caseId}/profile`} className="text-brand-700 hover:underline">
              Review and approve it
            </Link>
            .
          </Alert>
        )}
        <form onSubmit={submit} className="flex flex-col gap-3">
          <Label>
            Decision
            <Select value={kind} onChange={(e) => setKind(e.target.value as Decision["decision"])}>
              <option value="accept">Accept the recommendation</option>
              <option value="override">Override the outcome</option>
              <option value="request_information">Request information</option>
              <option value="escalate">Escalate</option>
            </Select>
          </Label>
          {kind === "override" && (
            <Label>
              Outcome
              <Select value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome)}>
                {(Object.keys(OUTCOMES) as Outcome[])
                  .filter((o) => o !== result.eligibility.outcome)
                  .map((o) => (
                    <option key={o} value={o}>
                      {OUTCOMES[o].label}
                    </option>
                  ))}
              </Select>
            </Label>
          )}
          {kind === "request_information" && (
            <Label>
              What&apos;s needed
              <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} required />
            </Label>
          )}
          <Label>
            Reason {kind === "accept" ? "(optional)" : ""}
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={1000}
              rows={3}
              required={kind !== "accept"}
            />
          </Label>
          <Button type="submit" disabled={busy}>
            {busy && <Loader2 className="animate-spin" />} Record decision
          </Button>
        </form>
        {assessment.decisions.length > 0 && (
          <ul className="flex flex-col gap-2 border-t border-slate-100 pt-3">
            {[...assessment.decisions].reverse().map((d) => (
              <li key={d.id}>
                <p className="font-medium">
                  {decisionLabel(d.decision)}
                  {d.override_outcome && <> to {OUTCOMES[d.override_outcome].label}</>}
                </p>
                <p className="text-xs text-slate-500">
                  {d.decided_by ?? "A firm member"} · {formatDateTime(d.created_at)}
                </p>
                {d.reason && <p className="text-slate-600">{d.reason}</p>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

export function History({ list, currentId, onSelect }: { list: AssessmentSummary[]; currentId: string; onSelect: (id: string) => void }) {
  if (list.length < 2) return null;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Versions</CardTitle>
          <CardDescription>Every assessment is kept; none is overwritten.</CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-col gap-1 text-sm">
          {list.map((a) => (
            <li key={a.id}>
              <button
                type="button"
                onClick={() => onSelect(a.id)}
                className={cn(
                  "flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-50",
                  a.id === currentId && "bg-brand-50",
                )}
              >
                <span>
                  Version {a.version}
                  <span className="block text-xs text-slate-500">
                    {a.trigger} · {formatDateTime(a.created_at)}
                  </span>
                </span>
                {a.final_outcome ? <OutcomeBadge outcome={a.final_outcome} /> : <Badge tone="neutral">{a.status}</Badge>}
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}


/** Scenario analysis: hypothetical changes to the loan, deposit, term or rate. */
export function ScenarioAnalysis({ caseId, policy, initial }: { caseId: string; policy: string; initial: ScenarioResult[] }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <SlidersHorizontal className="size-4 text-brand-600" /> Scenario analysis
          </CardTitle>
          <CardDescription>What-ifs under the same policy. They never change the assessment.</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="text-sm">
        <Scenarios caseId={caseId} policy={policy} initial={initial} />
      </CardContent>
    </Card>
  );
}

/** A numbered part of the assessment. */
export function Section({ number, title, children }: { number: number; title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-4">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <span className="grid size-6 place-items-center rounded-full bg-brand-600 text-xs text-white">{number}</span>
        {title}
      </h2>
      {children}
    </section>
  );
}
