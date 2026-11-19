"use client";

import { Check, Info, Pencil, X } from "lucide-react";
import { type KeyboardEvent, useRef } from "react";

import { Badge } from "@/components/ui/badge";
import { Input, Label, Select } from "@/components/ui/input";
import { Tooltip } from "@/components/ui/tooltip";
import { type Draft, FIGURE_CODES, formatMoney, QUALIFIED, valueKind } from "@/lib/decisions";
import { measureLabel } from "@/lib/labels";
import { showConfidence } from "@/lib/review-ui";
import type { DecisionKind, ExtractedField, Measure } from "@/lib/types";
import { cn } from "@/lib/utils";

export function shownValue(f: Pick<ExtractedField, "field_code" | "value_money_minor" | "value_date" | "value_text">): string {
  const kind = valueKind(f);
  if (kind === "money") return formatMoney(f.value_money_minor);
  if (kind === "date") return f.value_date ? new Date(`${f.value_date}T00:00:00Z`).toLocaleDateString("en-GB", { timeZone: "UTC" }) : "—";
  return f.value_text ?? "—";
}

const CHOICES: Array<{ kind: DecisionKind; label: string; icon: typeof Check; active: string; key: string }> = [
  { kind: "accept", label: "Accept", icon: Check, active: "bg-accept text-white border-accept", key: "A" },
  { kind: "correct", label: "Correct", icon: Pencil, active: "bg-correct text-white border-correct", key: "C" },
  { kind: "reject", label: "Reject", icon: X, active: "bg-reject text-white border-reject", key: "R" },
];

const EDGE: Record<DecisionKind, string> = {
  accept: "border-l-accept",
  correct: "border-l-correct",
  reject: "border-l-reject",
};

export function FieldRow(props: {
  field: ExtractedField;
  draft: Draft;
  problem: string | null;
  selected: boolean;
  hovered: boolean;
  measures: Measure[];
  onSelect: () => void;
  onHover: (on: boolean) => void;
  onChange: (change: Partial<Draft>) => void;
  onNext: () => void;
}) {
  const { field: f, draft, problem, selected, hovered, measures, onSelect, onHover, onChange, onNext } = props;
  const kind = valueKind(f);
  const effectiveMeasure = f.field_code && FIGURE_CODES.has(f.field_code) ? f.field_code : draft.measure;
  const qualifiers = measures.find((m) => m.code === effectiveMeasure)?.qualifiers ?? [];
  const reasonInput = useRef<HTMLInputElement>(null);
  const nextOnEnter = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === "Escape") {
      event.preventDefault();
      // Leave the box, so the next key press is a shortcut again rather than more text.
      event.currentTarget.blur();
      if (event.key === "Enter") onNext();
    }
  };

  return (
    <div
      data-field={f.ordinal}
      onClick={onSelect}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
      className={cn(
        "scroll-my-4 rounded-lg border border-l-4 bg-white px-3 py-2.5 transition-shadow",
        draft.decision ? EDGE[draft.decision] : "border-l-slate-200",
        selected ? "border-brand-500 ring-3 ring-brand-500/15" : hovered ? "border-brand-200" : "border-slate-200",
      )}
    >
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-slate-900">{f.label_text}</span>
            <Tooltip
              content={
                <span>
                  {f.field_code ? measureLabel(f.field_code) : "Unrecognised line"} · printed “{f.raw_text}” · page{" "}
                  {f.value_evidence.page_number}
                </span>
              }
            >
              <button type="button" className="text-slate-400 hover:text-slate-600" aria-label="Field details" onClick={(e) => e.stopPropagation()}>
                <Info className="size-3.5" />
              </button>
            </Tooltip>
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-500">
            {f.field_code === null && <Badge tone="neutral">Unrecognised line</Badge>}
            {showConfidence(f.confidence) && f.confidence !== null && (
              <Badge tone="warning">Low confidence {(f.confidence * 100).toFixed(0)}% · indicator</Badge>
            )}
            {f.value_evidence.page_number > 1 && <span>Page {f.value_evidence.page_number}</span>}
          </div>
        </div>
        <div
          className={cn(
            "text-right text-sm font-semibold tabular-nums",
            draft.decision === "reject" && "text-slate-400 line-through",
            draft.decision === "correct" && "text-slate-400 line-through",
          )}
        >
          {shownValue(f)}
        </div>
        <div className="flex overflow-hidden rounded-md border border-slate-300" role="group" aria-label={`Decision for ${f.label_text}`}>
          {CHOICES.map((choice, i) => {
            const Icon = choice.icon;
            const active = draft.decision === choice.kind;
            return (
              <Tooltip key={choice.kind} content={`${choice.label} (${choice.key})`}>
                <button
                  type="button"
                  aria-pressed={active}
                  aria-label={choice.label}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelect();
                    onChange({ decision: choice.kind });
                  }}
                  className={cn(
                    "grid h-8 w-9 place-items-center text-slate-500 transition-colors",
                    i > 0 && "border-l border-slate-300",
                    active ? choice.active : "bg-white hover:bg-slate-50 hover:text-slate-800",
                  )}
                >
                  <Icon className="size-4" />
                </button>
              </Tooltip>
            );
          })}
        </div>
      </div>

      {(draft.decision === "correct" || draft.decision === "reject") && (
        <div className="mt-2.5 grid gap-2 sm:grid-cols-[10rem_minmax(0,1fr)]" onClick={(e) => e.stopPropagation()}>
          {draft.decision === "correct" ? (
            <Input
              type={kind === "date" ? "date" : "text"}
              inputMode={kind === "money" ? "decimal" : undefined}
              placeholder={kind === "money" ? "1,234.56" : "Corrected value"}
              value={draft.corrected ?? ""}
              onChange={(e) => onChange({ corrected: e.target.value })}
              onKeyDown={(event) => {
                // Enter in the value goes on to its reason; Escape leaves the box.
                if (event.key === "Enter") {
                  event.preventDefault();
                  reasonInput.current?.focus();
                } else nextOnEnter(event);
              }}
              aria-label="Corrected value"
              aria-invalid={Boolean(problem) && !draft.corrected}
              className="tabular-nums"
              data-first-input
            />
          ) : (
            <span className="hidden sm:block" />
          )}
          <Input
            placeholder={draft.decision === "correct" ? "Reason for the correction" : "Reason for rejecting"}
            value={draft.reason ?? ""}
            maxLength={500}
            onChange={(e) => onChange({ reason: e.target.value })}
            ref={reasonInput}
            onKeyDown={nextOnEnter}
            aria-label="Reason"
            {...(draft.decision === "reject" ? { "data-first-input": true } : {})}
          />
        </div>
      )}

      {draft.decision !== "reject" && draft.decision && (f.field_code === null || (effectiveMeasure && QUALIFIED.has(effectiveMeasure))) && (
        <div className="mt-2.5 flex flex-wrap gap-3" onClick={(e) => e.stopPropagation()}>
          {f.field_code === null && (
            <Label className="min-w-48 flex-1 text-xs">
              Save as
              <Select value={draft.measure ?? ""} onChange={(e) => onChange({ measure: e.target.value || undefined })}>
                <option value="">Don&apos;t save as a figure</option>
                {measures.map((m) => (
                  <option key={m.code} value={m.code}>
                    {measureLabel(m.code)}
                  </option>
                ))}
              </Select>
            </Label>
          )}
          {effectiveMeasure && QUALIFIED.has(effectiveMeasure) && (
            <Label className="min-w-40 text-xs">
              Type
              <Select value={draft.qualifier ?? ""} onChange={(e) => onChange({ qualifier: e.target.value || undefined })}>
                <option value="">Unspecified</option>
                {qualifiers
                  .filter((q) => q !== "unspecified")
                  .map((q) => (
                    <option key={q} value={q}>
                      {q.replace(/_/g, " ")}
                    </option>
                  ))}
              </Select>
            </Label>
          )}
        </div>
      )}

      {problem && draft.decision && <p className="mt-2 text-xs font-medium text-correct">{problem}</p>}
    </div>
  );
}
