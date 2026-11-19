"use client";

import { CircleCheck } from "lucide-react";

import { shownValue } from "@/components/review/FieldRow";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { formatMoney } from "@/lib/decisions";
import { formatDateTime, frequencyLabel, measureLabel } from "@/lib/labels";
import type { DecisionKind, FieldDecisionOut, ReviewOut } from "@/lib/types";
import { cn } from "@/lib/utils";

const SECTION_TITLES: Record<string, string> = {
  summary: "Summary",
  payments: "Payments",
  deductions: "Deductions",
  year_to_date: "Year to date",
};
const DECISION: Record<DecisionKind, { label: string; tone: BadgeTone; edge: string }> = {
  accept: { label: "Accepted", tone: "success", edge: "border-l-accept" },
  correct: { label: "Corrected", tone: "warning", edge: "border-l-correct" },
  reject: { label: "Rejected", tone: "danger", edge: "border-l-reject" },
};

const extracted = (d: FieldDecisionOut) =>
  shownValue({
    field_code: d.field_code,
    value_money_minor: d.extracted_money_minor,
    value_date: d.extracted_date,
    value_text: d.extracted_text,
  });

const corrected = (d: FieldDecisionOut) =>
  d.corrected_money_minor !== null
    ? formatMoney(d.corrected_money_minor)
    : d.corrected_date
      ? shownValue({ field_code: "pay_date", value_money_minor: null, value_date: d.corrected_date, value_text: null })
      : (d.corrected_text ?? "—");

export function SavedReview(props: {
  review: ReviewOut;
  sourceName: string;
  selected: number | null;
  hovered: number | null;
  onSelect: (ordinal: number) => void;
  onHover: (ordinal: number | null) => void;
}) {
  const { review, sourceName, selected, hovered, onSelect, onHover } = props;
  const saved = review.decisions.filter((d) => d.observation_id).length;
  const sections = [...new Set(review.decisions.map((d) => d.section))];

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-start gap-3 rounded-lg border border-green-200 bg-accept-soft p-3 text-sm">
        <CircleCheck className="mt-0.5 size-4 shrink-0 text-accept" />
        <div className="text-green-950">
          <p className="font-medium">
            Reviewed {formatDateTime(review.created_at)} · {saved} figure{saved === 1 ? "" : "s"} saved
          </p>
          <p className="opacity-80">
            Saved to {sourceName}, {frequencyLabel(review.frequency).toLowerCase()}. Later corrections go through the
            case&apos;s financial records.
          </p>
        </div>
      </div>

      {sections.map((section) => (
        <section key={section} className="flex flex-col gap-2">
          <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{SECTION_TITLES[section] ?? section}</h3>
          {review.decisions
            .filter((d) => d.section === section)
            .map((d) => {
              const look = DECISION[d.decision];
              return (
                <div
                  key={d.ordinal}
                  data-field={d.ordinal}
                  onClick={() => onSelect(d.ordinal)}
                  onMouseEnter={() => onHover(d.ordinal)}
                  onMouseLeave={() => onHover(null)}
                  className={cn(
                    "scroll-my-4 cursor-pointer rounded-lg border border-l-4 bg-white px-3 py-2.5",
                    look.edge,
                    selected === d.ordinal ? "border-brand-500 ring-3 ring-brand-500/15" : hovered === d.ordinal ? "border-brand-200" : "border-slate-200",
                  )}
                >
                  <div className="flex items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{d.label_text}</p>
                      {d.observation_id && (
                        <p className="text-xs text-slate-500">
                          Saved as {measureLabel(d.measure ?? d.field_code ?? "")}
                          {d.qualifier && ` (${d.qualifier.replace(/_/g, " ")})`}
                        </p>
                      )}
                    </div>
                    <div className="text-right text-sm tabular-nums">
                      {d.decision === "correct" ? (
                        <>
                          <span className="mr-2 text-slate-400 line-through">{extracted(d)}</span>
                          <span className="font-semibold text-slate-900">{corrected(d)}</span>
                        </>
                      ) : (
                        <span className={cn("font-semibold", d.decision === "reject" && "text-slate-400 line-through")}>
                          {extracted(d)}
                        </span>
                      )}
                    </div>
                    <Badge tone={look.tone} className="w-20 justify-center">
                      {look.label}
                    </Badge>
                  </div>
                  {d.reason && <p className="mt-1.5 text-xs text-slate-600">“{d.reason}”</p>}
                </div>
              );
            })}
        </section>
      ))}
    </div>
  );
}
