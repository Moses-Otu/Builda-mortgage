"use client";

import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const SHORTCUTS: Array<[string, string]> = [
  ["↓ or J", "Next field"],
  ["↑ or K", "Previous field"],
  ["A", "Accept, then move to the next field"],
  ["C", "Correct (type the value, then the reason)"],
  ["R", "Reject (type the reason)"],
  ["Enter", "In the corrected value: go to the reason. In the reason: done, next field"],
  ["Esc", "In a value or reason box: done, stay on this field"],
  ["?", "Show these shortcuts"],
];

export function ShortcutsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>They don&apos;t fire while you&apos;re typing in a box.</DialogDescription>
        </DialogHeader>
        <dl className="divide-y divide-slate-100 text-sm">
          {SHORTCUTS.map(([keys, action]) => (
            <div key={keys} className="flex items-center justify-between gap-4 py-2">
              <dt className="text-slate-600">{action}</dt>
              <dd>
                <kbd className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 font-mono text-xs">{keys}</kbd>
              </dd>
            </div>
          ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
