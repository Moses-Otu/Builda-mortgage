"use client";

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import { PURPOSES } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { CaseSummary } from "@/lib/types";

export function NewCaseDialog() {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState("");
  const [purpose, setPurpose] = useState("purchase");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body: Record<string, string> = { purpose };
    if (reference.trim()) body.external_reference = reference.trim();
    const result = await postJson<CaseSummary>("cases", body);
    setBusy(false);
    if (!result.ok) {
      setError(problemMessage(result.status, result.problem));
      return;
    }
    toast("success", "Case created.");
    setOpen(false);
    router.push(`/cases/${result.data.id}`);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New case
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>New case</DialogTitle>
            <DialogDescription>Other case details can be added later.</DialogDescription>
          </DialogHeader>
          <Label>
            Your reference
            <Input
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              maxLength={100}
              placeholder="e.g. SYN-DEMO-002"
              autoFocus
            />
            <FieldHint>Optional: your CRM or file reference. Don&apos;t use a client&apos;s name.</FieldHint>
          </Label>
          <Label>
            Purpose
            <Select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
              {Object.entries(PURPOSES).map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </Select>
          </Label>
          {error && <Alert tone="error">{error}</Alert>}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              Create case
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
