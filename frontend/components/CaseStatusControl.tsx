"use client";

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
} from "@/components/ui/dialog";
import { FieldHint, Label, Textarea } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import { REOPEN_EXPLANATION, type StatusAction, statusActions } from "@/lib/approval";
import { problemMessage } from "@/lib/problems";

/** The case's next status steps (decision V4). Verifying is not here: only approving the
 * profile verifies a case. Closing stays API-only. */
export function CaseStatusControl(props: {
  caseId: string;
  status: string;
  version: number;
  onChanged: () => void;
}) {
  const { caseId, status, version, onChanged } = props;
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [reopening, setReopening] = useState<StatusAction | null>(null);

  async function move(action: StatusAction, note?: string): Promise<boolean> {
    setBusy(true);
    const result = await postJson(`cases/${caseId}/transitions`, { to_status: action.to, ...(note ? { note } : {}) }, version);
    setBusy(false);
    if (result.ok) {
      toast("success", action.reopen ? "Reopened. The approval is kept in the version history." : "Case status updated.");
      onChanged();
      return true;
    }
    toast("error", problemMessage(result.status, result.problem));
    if (result.status === 409 || result.status === 412) onChanged();
    return false;
  }

  const actions = statusActions(status);
  if (actions.length === 0) return null;
  return (
    <>
      {actions.map((a) => (
        <Button
          key={a.to + a.label}
          variant={a.to === "in_review" && !a.reopen ? "primary" : "secondary"}
          disabled={busy}
          onClick={() => (a.reopen ? setReopening(a) : void move(a))}
        >
          {a.label}
        </Button>
      ))}
      {reopening && (
        <ReopenDialog
          onClose={() => setReopening(null)}
          onSubmit={async (note) => {
            if (await move(reopening, note)) setReopening(null);
          }}
        />
      )}
    </>
  );
}

function ReopenDialog({ onClose, onSubmit }: { onClose: () => void; onSubmit: (note: string) => Promise<void> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!note.trim()) {
      setError("Say why the case is being reopened.");
      return;
    }
    setBusy(true);
    await onSubmit(note.trim());
    setBusy(false);
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <DialogHeader>
            <DialogTitle>Reopen the case</DialogTitle>
            <DialogDescription>{REOPEN_EXPLANATION}</DialogDescription>
          </DialogHeader>
          <Label>
            Why are you reopening it?
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={500}
              required
              autoFocus
              placeholder="e.g. The client sent a later payslip"
            />
            <FieldHint>Recorded with your name and the time. Synthetic data only.</FieldHint>
          </Label>
          {error && <Alert tone="error">{error}</Alert>}
          <DialogFooter>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !note.trim()}>
              Reopen
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
