"use client";

import { Lock, Plus } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FieldHint, Label, Select } from "@/components/ui/input";
import { FREQUENCIES } from "@/lib/labels";
import type { Counts } from "@/lib/review-ui";
import { saveSummary } from "@/lib/review-ui";
import type { IncomeSource } from "@/lib/types";

export function SaveReviewDialog(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  counts: Counts;
  figures: number;
  sources: IncomeSource[];
  sourceName: (s: IncomeSource) => string;
  incomeSourceId: string;
  onIncomeSource: (id: string) => void;
  frequency: string;
  onFrequency: (f: string) => void;
  /** The frequency the reviewed payslip gives, or null when it gives none. */
  payslipFrequency: string | null;
  onAddSource: () => void;
  saving: boolean;
  error: string | null;
  onSave: () => void;
}) {
  const p = props;
  const source = p.sources.find((s) => s.id === p.incomeSourceId);
  const effectiveFrequency = p.frequency || p.payslipFrequency;
  const needsFrequency = !effectiveFrequency;
  const summary = saveSummary({
    figures: p.figures,
    corrected: p.counts.corrected,
    rejected: p.counts.rejected,
    source: source ? p.sourceName(source) : "the income source",
    frequency: effectiveFrequency ? (FREQUENCIES[effectiveFrequency] ?? effectiveFrequency) : null,
  });

  return (
    <Dialog open={p.open} onOpenChange={p.onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save this review</DialogTitle>
          <DialogDescription>Check where the figures go, then save.</DialogDescription>
        </DialogHeader>

        {p.sources.length === 0 ? (
          <Alert tone="warning" title="No income source for this applicant">
            <p>The figures need an income source to be saved against.</p>
            <Button size="sm" variant="secondary" className="mt-2" onClick={p.onAddSource}>
              <Plus /> Add employment income
            </Button>
          </Alert>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Label>
              Income source
              <Select value={p.incomeSourceId} onChange={(e) => p.onIncomeSource(e.target.value)}>
                {p.sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    {p.sourceName(s)}
                  </option>
                ))}
              </Select>
            </Label>
            <Label>
              Frequency
              <Select
                value={p.frequency}
                onChange={(e) => p.onFrequency(e.target.value)}
                aria-invalid={needsFrequency}
              >
                <option value="">
                  {p.payslipFrequency
                    ? `As shown on the payslip (${FREQUENCIES[p.payslipFrequency] ?? p.payslipFrequency})`
                    : "Choose…"}
                </option>
                {Object.entries(FREQUENCIES).map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </Select>
              {needsFrequency && (
                <FieldHint className="text-correct">This payslip doesn&apos;t show how often it&apos;s paid: choose one.</FieldHint>
              )}
            </Label>
          </div>
        )}

        <div className="grid grid-cols-3 gap-2 text-center">
          <Stat value={p.counts.accepted} label="Accepted" className="text-accept" />
          <Stat value={p.counts.corrected} label="Corrected" className="text-correct" />
          <Stat value={p.counts.rejected} label="Rejected" className="text-reject" />
        </div>

        <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">{summary}</p>
        <p className="flex items-center gap-2 text-xs text-slate-500">
          <Lock className="size-3.5" /> A review can&apos;t be changed once saved. Later corrections go through
          the case&apos;s financial records.
        </p>

        {p.error && <Alert tone="error">{p.error}</Alert>}

        <DialogFooter>
          <Button variant="secondary" onClick={() => p.onOpenChange(false)}>
            Keep reviewing
          </Button>
          <Button onClick={p.onSave} disabled={p.saving || !p.incomeSourceId || needsFrequency}>
            {p.saving ? "Saving…" : "Save review"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Stat({ value, label, className }: { value: number; label: string; className: string }) {
  return (
    <div className="rounded-lg border border-slate-200 py-2">
      <p className={`text-xl font-semibold tabular-nums ${className}`}>{value}</p>
      <p className="text-xs text-slate-500">{label}</p>
    </div>
  );
}
