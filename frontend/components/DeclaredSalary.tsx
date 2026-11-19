"use client";

import { Pencil } from "lucide-react";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { FieldHint, Input, Label } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import { declaredBasicBody } from "@/lib/checks";
import { formatMoney } from "@/lib/decisions";
import { problemMessage } from "@/lib/problems";
import type { Observation } from "@/lib/types";

/** The applicant's declared annual basic salary on one employment income source. A change
 * supersedes the previous declared value; it never edits it. */
export function DeclaredSalary(props: {
  caseId: string;
  incomeSourceId: string;
  current: Observation | null;
  onSaved: () => void;
}) {
  const { caseId, incomeSourceId, current, onSaved } = props;
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [pounds, setPounds] = useState("");
  const [busy, setBusy] = useState(false);
  const body = declaredBasicBody(incomeSourceId, pounds, current?.id ?? null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!body) return;
    setBusy(true);
    const result = await postJson(`cases/${caseId}/financial/observations`, body);
    setBusy(false);
    if (result.ok) {
      toast("success", "Declared basic salary saved.");
      setEditing(false);
      setPounds("");
      onSaved();
    } else {
      toast("error", problemMessage(result.status, result.problem));
    }
  }

  if (!editing) {
    return (
      <div className="flex items-center justify-between gap-2 rounded-md bg-slate-50 px-2.5 py-1.5 text-xs">
        <span className="text-slate-600">
          Declared basic salary:{" "}
          <span className="font-medium text-slate-900 tabular-nums">
            {current?.value.amount_minor !== undefined && current ? `${formatMoney(current.value.amount_minor)} a year` : "not given"}
          </span>
        </span>
        <Button size="sm" variant="ghost" className="h-7 px-2" onClick={() => setEditing(true)}>
          <Pencil /> {current ? "Change" : "Add"}
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 rounded-md bg-slate-50 p-2.5">
      <Label className="text-xs">
        Declared basic salary (annual, before overtime, bonus and allowances)
        <Input
          inputMode="decimal"
          placeholder="45,000.00"
          value={pounds}
          onChange={(e) => setPounds(e.target.value)}
          autoFocus
          aria-invalid={pounds.length > 0 && !body}
          className="tabular-nums"
        />
        <FieldHint>What the applicant says. Compared with basic pay on the payslips.</FieldHint>
      </Label>
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="secondary" onClick={() => setEditing(false)}>
          Cancel
        </Button>
        <Button size="sm" type="submit" disabled={busy || !body}>
          Save
        </Button>
      </div>
    </form>
  );
}
