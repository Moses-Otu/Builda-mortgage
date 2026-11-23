// The guided mortgage journey (GET /cases/{id}/journey). The server derives the stages, what's
// outstanding and the single next action from the case's real state; this module only maps
// them to screens.

export type StageKey = "details" | "income" | "statements" | "profile" | "assessment" | "decision";

export interface EvidenceCounts {
  received: number;
  processing: number;
  to_review: number;
  reviewed: number;
  failed: number;
}

export interface JourneyStage {
  key: StageKey;
  number: number;
  title: string;
  done: boolean;
  optional: boolean;
  summary: string;
  requirements: string[];
}

export interface JourneyAction {
  label: string;
  target: StageKey;
  kind: "navigate" | "start_assessment";
}

export interface Journey {
  case_id: string;
  case_status: string;
  stage: StageKey | "complete";
  stages: JourneyStage[];
  next_action: JourneyAction;
  reassessment_required: boolean;
  reassessment_message: string | null;
  profile_approved: boolean;
  approval_id: string | null;
  latest_assessment_id: string | null;
  assessment_running: boolean;
  assessment_failed: boolean;
  assessment_blockers: string[];
  approval_blockers: string[];
  open_evidence_requests: number;
  policy_code: string;
  payslips: EvidenceCounts;
  statements: EvidenceCounts;
}

/** The case-page stages (details and evidence) and the stages with their own page. */
export const CASE_PAGE_STAGES: StageKey[] = ["details", "income", "statements"];

export function stageHref(caseId: string, stage: StageKey): string {
  switch (stage) {
    case "profile":
      return `/cases/${caseId}/profile`;
    case "assessment":
      return `/cases/${caseId}/assessment`;
    case "decision":
      return `/cases/${caseId}/decision`;
    default:
      return `/cases/${caseId}?stage=${stage}`;
  }
}

export type StageState = "done" | "current" | "upcoming" | "attention";

/** How a stage is drawn: done, the current one, needing attention (e.g. reassessment), or not
 * reached yet. */
export function stageState(journey: Journey, stage: JourneyStage): StageState {
  if (stage.key === "assessment" && journey.reassessment_required) return "attention";
  if (journey.stage === stage.key) return "current";
  if (stage.done) return "done";
  // Passed with something still listed (e.g. a month the statements don't show).
  const current = journey.stages.find((s) => s.key === journey.stage);
  const passed = journey.stage === "complete" || (current !== undefined && stage.number < current.number);
  if (passed && !stage.optional && stage.requirements.length > 0) return "attention";
  return "upcoming";
}

/** The stage a case-page visit should show: the one asked for, else the journey's current
 * case-page stage, else the first. */
export function caseStage(journey: Journey | null, requested: string | null): StageKey {
  if (requested && (CASE_PAGE_STAGES as string[]).includes(requested)) return requested as StageKey;
  if (journey && (CASE_PAGE_STAGES as string[]).includes(journey.stage)) return journey.stage as StageKey;
  return "details";
}

/** The upload type a case-page stage collects. */
export function uploadTypeFor(stage: StageKey): "payslip" | "bank_statement" {
  return stage === "statements" ? "bank_statement" : "payslip";
}
