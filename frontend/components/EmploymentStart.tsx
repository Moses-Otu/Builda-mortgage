"use client";

// The employment start date on an income source: the lending policy's time-in-employment
// check needs it, so the journey's details stage asks for it when it's missing.

import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { formatDate } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { IncomeSource } from "@/lib/types";

export function EmploymentStart(props: { caseId: string; source: IncomeSource; locked: boolean; onSaved: () => void }) {
  const { caseId, source, locked, onSaved } = props;
  const toast = useToast();
  const [value, setValue] = useState(source.start_date ?? "");
  const [editing, setEditing] = useState(!source.start_date && !locked);
  const [busy, setBusy] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!value) return;
    setBusy(true);
    const response = await fetch(`/api/meip/cases/${caseId}/financial/income-sources/${source.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json", "if-match": `"${source.version ?? 1}"` },
      body: JSON.stringify({ start_date: value }),
    });
    setBusy(false);
    if (!response.ok) {
      const problem = (await response.json().catch(() => null)) as { type?: string; title: string; status: number } | null;
      toast("error", problemMessage(response.status, problem));
      onSaved();
      return;
    }
    toast("success", "Start date saved.");
    setEditing(false);
    onSaved();
  }

  if (!editing || locked) {
    if (!source.start_date) return <p className="text-xs text-slate-500">Started: not given</p>;
    return (
      <p className="flex items-center justify-between text-xs text-slate-600">
        <span>
          Started: <span className="font-medium">{formatDate(source.start_date ?? "")}</span>
        </span>
        {!locked && (
          <button type="button" className="text-brand-700 hover:underline" onClick={() => setEditing(true)}>
            Change
          </button>
        )}
      </p>
    );
  }
  return (
    <form onSubmit={save} className="flex items-center gap-2 text-xs">
      <span className="shrink-0 text-slate-600">Started</span>
      <Input type="date" value={value} onChange={(e) => setValue(e.target.value)} className="h-8" aria-label="Employment start date" />
      <Button type="submit" size="sm" variant="secondary" disabled={busy || !value}>
        Save
      </Button>
    </form>
  );
}
