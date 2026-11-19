"use client";

import { UserPlus } from "lucide-react";
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
import { Input, Label } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import { problemMessage } from "@/lib/problems";
import type { Applicant } from "@/lib/types";

export function AddApplicantDialog({ caseId, onAdded }: { caseId: string; onAdded: () => void }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await postJson<Applicant>(`cases/${caseId}/applicants`, {
      first_name: first.trim(),
      last_name: last.trim(),
    });
    setBusy(false);
    if (!result.ok) {
      setError(problemMessage(result.status, result.problem));
      return;
    }
    toast("success", "Applicant added.");
    setFirst("");
    setLast("");
    setOpen(false);
    onAdded();
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="secondary" size="sm">
          <UserPlus /> Add applicant
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Add applicant</DialogTitle>
            <DialogDescription>Synthetic data only: use made-up names.</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            <Label>
              First name
              <Input value={first} onChange={(e) => setFirst(e.target.value)} required maxLength={100} autoFocus />
            </Label>
            <Label>
              Last name
              <Input value={last} onChange={(e) => setLast(e.target.value)} required maxLength={100} />
            </Label>
          </div>
          {error && <Alert tone="error">{error}</Alert>}
          <DialogFooter>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !first.trim() || !last.trim()}>
              Add applicant
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
