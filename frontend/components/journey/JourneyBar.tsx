"use client";

// The persistent guided journey: six stages, where the case is, what's outstanding, and the
// one next action. Shown on every stage's page. Everything comes from GET …/journey.

import { ArrowRight, Check, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { get, postJson } from "@/lib/api";
import {
  type Journey,
  type StageKey,
  stageHref,
  stageState,
} from "@/lib/journey";
import { problemMessage } from "@/lib/problems";
import { cn } from "@/lib/utils";

/** Load the journey, and reload it on demand (after an upload, a review, an approval…). */
export function useJourney(caseId: string): {
  journey: Journey | null;
  reload: () => Promise<void>;
} {
  const [journey, setJourney] = useState<Journey | null>(null);
  const reload = useCallback(async () => {
    const result = await get<Journey>(`cases/${caseId}/journey`);
    if (result.ok) setJourney(result.data);
  }, [caseId]);
  useEffect(() => {
    void reload();
  }, [reload]);
  return { journey, reload };
}

export function JourneyBar({
  caseId,
  journey,
  showing,
  onStageSelect,
  nextHref,
}: {
  caseId: string;
  journey: Journey | null;
  /** The stage this page shows (highlighted with a ring). */
  showing?: StageKey;
  /** Case-page stages switch in place instead of navigating. */
  onStageSelect?: (stage: StageKey) => void;
  /** Where the next action goes when the page knows better (e.g. the first document to review). */
  nextHref?: string | null;
}) {
  if (!journey)
    return (
      <div
        className="mb-6 h-24 animate-pulse rounded-xl bg-slate-100"
        aria-hidden
      />
    );
  return (
    <nav aria-label="Mortgage journey" className="mb-6 flex flex-col gap-3">
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
        {journey.stages.map((stage) => {
          const state = stageState(journey, stage);
          const inPlace =
            onStageSelect &&
            ["details", "income", "statements"].includes(stage.key);
          const body = (
            <>
              <span
                className={cn(
                  "grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold",
                  state === "done" && "bg-accept text-white",
                  state === "current" && "bg-brand-600 text-white",
                  state === "attention" && "bg-correct text-white",
                  state === "upcoming" && "bg-slate-100 text-slate-500",
                )}
              >
                {state === "done" ? (
                  <Check className="size-3.5" />
                ) : (
                  stage.number
                )}
              </span>
              <span className="min-w-0 text-left">
                <span
                  className={cn(
                    "block text-sm font-medium",
                    state === "upcoming" && "text-slate-500",
                  )}
                >
                  {stage.title}
                  {stage.optional && (
                    <span className="font-normal text-slate-400">
                      {" "}
                      (optional)
                    </span>
                  )}
                </span>
                <span className="block truncate text-xs text-slate-500">
                  {stage.summary}
                </span>
              </span>
            </>
          );
          const className = cn(
            "flex h-full w-full items-start gap-2.5 rounded-xl border bg-white p-3 transition-colors hover:border-brand-400",
            state === "current" && "border-brand-500",
            state === "attention" && "border-amber-300",
            state !== "current" && state !== "attention" && "border-slate-200",
            showing === stage.key && "ring-3 ring-brand-500/20",
          );
          return (
            <li
              key={stage.key}
              aria-current={journey.stage === stage.key ? "step" : undefined}
            >
              {inPlace ? (
                <button
                  type="button"
                  className={className}
                  onClick={() => onStageSelect(stage.key)}
                >
                  {body}
                </button>
              ) : (
                <Link href={stageHref(caseId, stage.key)} className={className}>
                  {body}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
      {journey.reassessment_message && (
        <Alert tone="warning" title={journey.reassessment_message}>
          The earlier assessment is kept in the history.{" "}
          {journey.profile_approved
            ? "Run the reassessment to bring it up to date."
            : "Review and approve the updated profile; the reassessment then starts by itself."}
        </Alert>
      )}
      <NextAction
        caseId={caseId}
        journey={journey}
        href={nextHref}
        showing={showing}
      />
    </nav>
  );
}

/** The hint when the page shown is where the next step happens. */
const HERE: Record<StageKey, string> = {
  details: "Complete the applicant and loan details below.",
  income: "Upload and review the payslips below.",
  statements: "Upload and confirm the bank statements below.",
  profile: "Check the profile below, then approve it.",
  assessment: "The assessment is below.",
  decision: "Review the assessment below and record your decision.",
};

/** The single primary action. */
export function NextAction({
  caseId,
  journey,
  href,
  showing,
}: {
  caseId: string;
  journey: Journey;
  href?: string | null;
  showing?: StageKey;
}) {
  const router = useRouter();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const action = journey.next_action;
  const stage = journey.stages.find((s) => s.key === action.target);
  // Already on the stage the action points to: the page's own action is the primary one.
  const here = action.kind === "navigate" && !href && showing === action.target;
  const hint =
    journey.stage === "complete"
      ? "The journey is complete. Every assessment and decision is kept in the history."
      : (stage?.requirements[0] ??
        (here ? HERE[action.target] : undefined));

  async function go() {
    if (action.kind === "start_assessment") {
      setBusy(true);
      const out = await postJson<{ assessment: { id: string } }>(
        `cases/${caseId}/assessments`,
        { force: true },
      );
      setBusy(false);
      if (!out.ok) {
        toast("error", problemMessage(out.status, out.problem));
        return;
      }
    }
    router.push(href ?? stageHref(caseId, action.target));
  }

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3">
      <p className="text-sm text-slate-600">
        {hint ?? "You're up to date on this stage."}
      </p>
      {!here && (
        <Button onClick={() => void go()} disabled={busy}>
          {busy ? (
            <Loader2 className="animate-spin" />
          ) : action.kind === "start_assessment" &&
            journey.reassessment_required ? (
            <RefreshCw />
          ) : null}
          {action.label}
          {action.kind === "navigate" && <ArrowRight />}
        </Button>
      )}
    </div>
  );
}
