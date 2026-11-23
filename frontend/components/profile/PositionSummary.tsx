"use client";

// The consolidated financial position the assessment will use (GET …/financial-position),
// shown on the profile review before approval. Every line says how far it's evidenced:
// verified, extracted, declared, inferred (a policy estimate) or unverified (stated on the
// case, no evidence). Computed by the same rules as the assessment; read-only.

import { BadgeCheck } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useEffect, useState } from "react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { get } from "@/lib/api";
import { type CommitmentLine, gbp, type IncomeLine, monthLabel, VERIFICATION } from "@/lib/assessment";
import { measureLabel } from "@/lib/labels";

export interface Position {
  policy_code: string;
  income: IncomeLine[];
  commitments: CommitmentLine[];
  gross_monthly_minor: number;
  net_monthly_minor: number;
  verified_gross_monthly_minor: number;
  commitments_monthly_minor: number;
  living_costs_monthly_minor: number;
  living_costs_basis: string;
  property_value_minor: number | null;
  loan_amount_minor: number | null;
  deposit_minor: number | null;
  product_rate_percent: string | null;
  term_months: number | null;
  notes: string[];
}

const EXTRA: Record<"inferred" | "unverified", { label: string; tone: BadgeTone; explanation: string }> = {
  inferred: { label: "Inferred", tone: "neutral", explanation: "Estimated by the lending policy, not evidenced." },
  unverified: { label: "Unverified", tone: "warning", explanation: "Stated on the case; no evidence yet." },
};

const COMMITMENT_STATUS: Record<string, "extracted" | "declared" | "inferred" | "missing"> = {
  document: "extracted",
  declared: "declared",
  assumed: "inferred",
  missing: "missing",
};

function Status({ kind }: { kind: keyof typeof VERIFICATION | keyof typeof EXTRA }) {
  const v = kind in VERIFICATION ? VERIFICATION[kind as keyof typeof VERIFICATION] : EXTRA[kind as keyof typeof EXTRA];
  return (
    <Tooltip content={v.explanation}>
      <span tabIndex={0}>
        <Badge tone={v.tone}>{v.label}</Badge>
      </span>
    </Tooltip>
  );
}

function Row({ label, value, status, detail }: { label: string; value: string; status?: ReactNode; detail?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-slate-100 py-2 last:border-0">
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium">{label}</p>
        {detail && <p className="text-xs text-slate-500">{detail}</p>}
      </div>
      {status}
      <span className="w-28 text-right text-sm font-semibold tabular-nums">{value}</span>
    </div>
  );
}

export function PositionSummary({ caseId, refreshKey }: { caseId: string; refreshKey?: string }) {
  const [position, setPosition] = useState<Position | null>(null);

  useEffect(() => {
    void get<Position>(`cases/${caseId}/financial-position`).then((r) => {
      if (r.ok) setPosition(r.data);
    });
  }, [caseId, refreshKey]);

  if (!position) return null;
  const p = position;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <BadgeCheck className="size-4 text-slate-400" /> Consolidated position
          </CardTitle>
          <CardDescription>
            What the assessment will use, by the same rules. Monthly figures; nothing here is a lending decision.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Income</h3>
          {p.income.length === 0 && <p className="text-sm text-slate-500">No income sources.</p>}
          {p.income.map((line) => (
            <Row
              key={line.income_source_id}
              label={`${line.name ?? measureLabel(line.kind)} · ${line.applicant_label}`}
              detail={[
                line.periods_evidenced.length
                  ? `Evidenced: ${line.periods_evidenced.map(monthLabel).join(", ")}`
                  : "No reviewed payslips",
                line.corroborated_periods.length
                  ? `paid into the bank: ${line.corroborated_periods.map(monthLabel).join(", ")}`
                  : null,
                line.counted ? null : "not counted",
              ]
                .filter(Boolean)
                .join(" · ")}
              status={<Status kind={line.verification} />}
              value={gbp(line.qualifying_gross_monthly_minor)}
            />
          ))}
          <Row label="Qualifying gross income" value={gbp(p.gross_monthly_minor)} detail={`of which verified ${gbp(p.verified_gross_monthly_minor)}`} />
          <Row label="Qualifying net income" value={gbp(p.net_monthly_minor)} detail="Evidenced only" />
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold tracking-wide text-slate-500 uppercase">Outgoings and loan</h3>
          {p.commitments.map((c) => (
            <Row
              key={c.commitment_id}
              label={c.creditor ?? measureLabel(c.kind)}
              detail={c.counted ? undefined : "Not counted"}
              status={<Status kind={COMMITMENT_STATUS[c.basis] ?? "unverified"} />}
              value={gbp(c.monthly_minor)}
            />
          ))}
          <Row label="Credit commitments" value={gbp(p.commitments_monthly_minor)} />
          <Row label="Living costs" detail={p.living_costs_basis === "declared" ? "Declared" : "Lending policy default"} status={<Status kind={p.living_costs_basis === "declared" ? "declared" : "inferred"} />} value={gbp(p.living_costs_monthly_minor)} />
          <Row label="Property value" status={<Status kind="unverified" />} value={gbp(p.property_value_minor)} />
          <Row label="Mortgage amount" status={<Status kind="declared" />} value={gbp(p.loan_amount_minor)} />
          <Row label="Deposit" detail="Property value less the mortgage" status={<Status kind="unverified" />} value={gbp(p.deposit_minor)} />
          <Row
            label="Term and rate"
            value={p.term_months ? `${p.term_months / 12} yrs` : "—"}
            detail={p.product_rate_percent ? `Product rate ${p.product_rate_percent}%` : "No product rate: the policy's assumed rate will be used"}
          />
          {(p.property_value_minor === null || p.loan_amount_minor === null || p.term_months === null) && (
            <p className="mt-2 text-sm text-correct">
              The loan details are incomplete.{" "}
              <Link href={`/cases/${caseId}?stage=details`} className="font-medium text-brand-700 hover:underline">
                Add them
              </Link>{" "}
              so the assessment can run.
            </p>
          )}
        </div>
        {p.notes.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-slate-600 lg:col-span-2">
            {p.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
