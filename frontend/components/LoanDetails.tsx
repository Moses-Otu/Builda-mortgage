"use client";

// The case's loan details: what the affordability and loan-to-value figures are calculated
// for. Nothing is assumed silently: without a product rate the assessment uses the policy's
// assumed rate and says so.

import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { get } from "@/lib/api";
import { loanDetailsProblem, type LoanForm, loanPatch, POLICY_OPTIONS } from "@/lib/case-form";
import { problemMessage } from "@/lib/problems";

interface CaseLoan {
  status: string;
  version: number;
  property_use: string | null;
  property_value: { amount_minor: number } | null;
  loan_amount: { amount_minor: number } | null;
  term_months: number | null;
  product_rate_percent: string | null;
  policy_code: string | null;
}

function toForm(c: CaseLoan): LoanForm {
  return {
    propertyValue: c.property_value ? String(c.property_value.amount_minor / 100) : "",
    loanAmount: c.loan_amount ? String(c.loan_amount.amount_minor / 100) : "",
    termYears: c.term_months ? String(c.term_months / 12) : "",
    propertyUse: c.property_use ?? "residential",
    productRate: c.product_rate_percent ?? "",
    policyCode: c.policy_code ?? "uk-resi-standard/1",
  };
}

export function LoanDetails({
  caseId,
  onSaved,
  refreshKey,
}: {
  caseId: string;
  onSaved: () => void;
  /** Re-read when this changes (e.g. the case's version after reopening). */
  refreshKey?: string | number;
}) {
  const toast = useToast();
  const [loanCase, setLoanCase] = useState<CaseLoan | null>(null);
  const [form, setForm] = useState<LoanForm | null>(null);
  const [busy, setBusy] = useState(false);

  const read = useCallback(async () => {
    const c = await get<CaseLoan>(`cases/${caseId}`);
    if (!c.ok) return;
    setLoanCase(c.data);
    setForm(toForm(c.data));
  }, [caseId]);

  useEffect(() => {
    void read();
  }, [read, refreshKey]);

  if (!loanCase || !form) return null;
  const locked = loanCase.status === "verified" || loanCase.status === "closed";
  const set = (patch: Partial<LoanForm>) => setForm((f) => (f ? { ...f, ...patch } : f));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!loanCase || !form) return;
    const problem = loanDetailsProblem(form);
    if (problem) {
      toast("error", problem);
      return;
    }
    setBusy(true);
    const response = await fetch(`/api/meip/cases/${caseId}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "if-match": `"${loanCase.version}"` },
      body: JSON.stringify(loanPatch(form)),
    });
    setBusy(false);
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { type?: string; title: string; status: number } | null;
      toast("error", problemMessage(response.status, problem));
      await read();
      return;
    }
    toast("success", "Loan details saved.");
    await read();
    onSaved();
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Loan details</CardTitle>
          <CardDescription>
            {locked
              ? "The profile is approved: add new evidence to change these."
              : "What the affordability and loan-to-value figures are calculated for."}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={save} className="grid gap-3 sm:grid-cols-2">
          <Label>
            Property value (£)
            <Input inputMode="numeric" value={form.propertyValue} onChange={(e) => set({ propertyValue: e.target.value })} disabled={locked} />
          </Label>
          <Label>
            Mortgage amount (£)
            <Input inputMode="numeric" value={form.loanAmount} onChange={(e) => set({ loanAmount: e.target.value })} disabled={locked} />
          </Label>
          <Label>
            Term (years)
            <Input type="number" min={1} max={50} value={form.termYears} onChange={(e) => set({ termYears: e.target.value })} disabled={locked} />
          </Label>
          <Label>
            Property use
            <Select value={form.propertyUse} onChange={(e) => set({ propertyUse: e.target.value })} disabled={locked}>
              <option value="residential">Residential</option>
              <option value="buy_to_let">Buy to let</option>
              <option value="second_home">Second home</option>
            </Select>
          </Label>
          <Label>
            Product rate (%)
            <Input inputMode="decimal" value={form.productRate} onChange={(e) => set({ productRate: e.target.value })} disabled={locked} placeholder="Optional" />
            <FieldHint>Leave blank to use the policy&apos;s assumed rate (stated in the results).</FieldHint>
          </Label>
          <Label>
            Lending policy
            <Select value={form.policyCode} onChange={(e) => set({ policyCode: e.target.value })} disabled={locked}>
              {POLICY_OPTIONS.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.label}
                </option>
              ))}
            </Select>
          </Label>
          {!locked && (
            <Button type="submit" size="sm" variant="secondary" disabled={busy} className="sm:col-span-2">
              Save loan details
            </Button>
          )}
        </form>
      </CardContent>
    </Card>
  );
}
