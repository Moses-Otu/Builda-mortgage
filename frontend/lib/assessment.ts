// The agentic assessment (docs/phases/AGENTIC-ASSESSMENT-PLAN.md): API shapes and the pure
// helpers the workspace uses to present them. Money is pence; every figure comes from the API.

import type { BadgeTone } from "@/components/ui/badge";

export type Outcome =
  | "potentially_eligible"
  | "eligible_subject_to_verification"
  | "not_currently_eligible"
  | "insufficient_information";

export type RunStatus = "queued" | "running" | "completed" | "failed";
export type Verification = "verified" | "extracted" | "declared" | "missing";
export type CriterionStatus = "met" | "not_met" | "unknown" | "not_assessed";

export interface Policy {
  code: string;
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface IncomeLine {
  income_source_id: string;
  applicant_label: string;
  kind: string;
  name: string | null;
  basis: string | null;
  verification: Verification;
  recurring: boolean;
  periods_evidenced: string[];
  corroborated_periods: string[];
  gross_monthly_minor: number | null;
  net_monthly_minor: number | null;
  variable_monthly_minor: number;
  qualifying_gross_monthly_minor: number;
  qualifying_net_monthly_minor: number | null;
  counted: boolean;
  declared_gross_monthly_minor: number | null;
  months_in_employment: number | null;
  notes: string[];
}

export interface CommitmentLine {
  commitment_id: string;
  kind: string;
  creditor: string | null;
  status: string;
  monthly_minor: number | null;
  basis: string;
  counted: boolean;
  outstanding_balance_minor: number | null;
  notes: string[];
}

export interface Limit {
  code: "affordability" | "income_multiple" | "loan_to_value";
  max_loan_minor: number;
  explanation: string;
}

export interface Affordability {
  policy_code: string;
  gross_monthly_income_minor: number;
  net_monthly_income_minor: number;
  commitments_monthly_minor: number;
  living_costs_monthly_minor: number;
  living_costs_basis: string;
  property_value_minor: number | null;
  loan_amount_minor: number | null;
  deposit_minor: number | null;
  min_deposit_minor: number | null;
  rate_percent: string;
  stressed_rate_percent: string;
  term_months: number;
  disposable_before_mortgage_minor: number;
  max_affordable_monthly_repayment_minor: number;
  limits: Limit[];
  max_mortgage_minor: number | null;
  binding_constraint: string | null;
  monthly_repayment_minor: number | null;
  stressed_monthly_repayment_minor: number | null;
  total_repayable_minor: number | null;
  disposable_after_mortgage_minor: number | null;
  dti_percent: string | null;
  ltv_percent: string | null;
  lti: string | null;
  requested_within_limits: boolean | null;
  shortfall_minor: number | null;
  risks: string[];
  assumptions: string[];
}

export interface ScenarioResult {
  label: string;
  hypothetical: true;
  changed: string[];
  loan_amount_minor: number | null;
  deposit_minor: number | null;
  term_months: number;
  rate_percent: string;
  monthly_repayment_minor: number | null;
  stressed_monthly_repayment_minor: number | null;
  total_repayable_minor: number | null;
  max_mortgage_minor: number | null;
  binding_constraint: string | null;
  ltv_percent: string | null;
  lti: string | null;
  dti_percent: string | null;
  within_limits: boolean | null;
  shortfall_minor: number | null;
  disposable_after_mortgage_minor: number | null;
}

export interface Criterion {
  code: string;
  title: string;
  status: CriterionStatus;
  detail: string;
  applicant_label: string | null;
  evidence: string[];
}

export interface Eligibility {
  policy_code: string;
  outcome: Outcome;
  outcome_label: string;
  criteria: Criterion[];
  reasons: string[];
  unmet: string[];
  outstanding: string[];
  indicative_range_minor: [number, number] | null;
}

export interface Gap {
  code: string;
  category: "document" | "information" | "action";
  title: string;
  reason: string;
  applicant_label: string | null;
  document_type: string | null;
  months: string[];
  supported_on_platform: boolean;
  affects_outcome: boolean;
}

export interface Discrepancy {
  key: string;
  rule_code: string;
  title: string;
  message: string;
  applicant_label: string | null;
  amounts: Record<string, number>;
  acknowledged: boolean;
  acknowledgement_note: string | null;
  analysis: string[];
}

export interface AssessmentResult {
  notice: string;
  policy: Policy;
  case: Record<string, unknown>;
  applicants: Array<{ applicant_id: string; label: string; party_role: string; is_primary: boolean; age_at_term_end: number | null }>;
  income: IncomeLine[];
  commitments: CommitmentLine[];
  affordability: Affordability | null;
  eligibility: Eligibility;
  scenarios: ScenarioResult[];
  evidence_gaps: Gap[];
  discrepancies: Discrepancy[];
  findings: Array<{ title: string; detail: string; severity: "info" | "warning" | "critical"; refs: string[] }>;
  escalations: Array<{ reason: string; refs: string[] }>;
  evidence_request_ids: string[];
  reassessment: {
    previous_assessment_id: string;
    previous_version: number;
    previous_outcome: string | null;
    changes: Array<{ kind: string; detail: string }>;
    affected: string[];
  } | null;
  summary: string;
  recommendations: string[];
  limitations: string[];
  profile_fingerprint: string;
  profile_approved: boolean;
}

export interface Step {
  seq: number;
  kind: "tool" | "note" | "error";
  tool: string | null;
  rationale: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  status: "ok" | "error" | "cached";
  error_code: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface AssessmentSummary {
  id: string;
  case_id: string;
  version: number;
  status: RunStatus;
  outcome: Outcome | null;
  policy_code: string;
  planner: string;
  model: string | null;
  trigger: string;
  previous_assessment_id: string | null;
  started_by: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  step_count: number;
  stale: boolean;
  final_outcome: Outcome | null;
  decision: string | null;
}

export interface Decision {
  id: string;
  decision: "accept" | "override" | "request_information" | "escalate";
  override_outcome: Outcome | null;
  reason: string | null;
  decided_by: string | null;
  created_at: string;
}

export interface Assessment extends AssessmentSummary {
  summary: string | null;
  result: AssessmentResult | null;
  result_sha256: string | null;
  steps: Step[];
  decisions: Decision[];
}

export interface EvidenceRequest {
  id: string;
  assessment_id: string | null;
  gap_code: string;
  category: string;
  title: string;
  reason: string;
  document_type: string | null;
  months: string[] | null;
  supported_on_platform: boolean;
  requested_by: "agent" | "broker";
  status: "open" | "fulfilled" | "withdrawn";
  created_at: string;
  closed_at: string | null;
}

export interface ScenarioInput {
  label: string;
  loan_amount_minor?: number;
  deposit_minor?: number;
  term_months?: number;
  rate_percent?: string;
  include_unverified_income?: boolean;
}

// --- presentation ----------------------------------------------------------------------------

export const OUTCOMES: Record<Outcome, { label: string; tone: BadgeTone; explanation: string }> = {
  potentially_eligible: {
    label: "Potentially eligible",
    tone: "success",
    explanation: "Every assessed criterion is met with the evidence on file.",
  },
  eligible_subject_to_verification: {
    label: "Eligible subject to verification",
    tone: "info",
    explanation: "Nothing fails, but some criteria can't be confirmed yet.",
  },
  not_currently_eligible: {
    label: "Not currently eligible",
    tone: "danger",
    explanation: "At least one criterion isn't met on the current figures.",
  },
  insufficient_information: {
    label: "Insufficient information",
    tone: "warning",
    explanation: "There isn't enough information to assess yet.",
  },
};

export const VERIFICATION: Record<Verification, { label: string; tone: BadgeTone; explanation: string }> = {
  verified: { label: "Verified", tone: "success", explanation: "Reviewed documents, and the salary seen arriving in the bank." },
  extracted: { label: "Extracted", tone: "info", explanation: "Read from reviewed documents, not yet independently confirmed." },
  declared: { label: "Declared", tone: "warning", explanation: "Stated by the applicant or broker; no document supports it." },
  missing: { label: "Missing", tone: "danger", explanation: "No figures recorded." },
};

export const CRITERION: Record<CriterionStatus, { label: string; tone: BadgeTone }> = {
  met: { label: "Met", tone: "success" },
  not_met: { label: "Not met", tone: "danger" },
  unknown: { label: "To confirm", tone: "warning" },
  not_assessed: { label: "Not assessed", tone: "neutral" },
};

export const TOOL_LABELS: Record<string, string> = {
  get_assessment_context: "Established the case context",
  get_applicant_profile: "Read the applicant profile",
  get_financial_evidence: "Inspected the evidence",
  identify_evidence_gaps: "Identified evidence gaps",
  reconcile_financial_evidence: "Cross-checked the documents",
  validate_income: "Validated income",
  calculate_monthly_commitments: "Totalled commitments",
  calculate_disposable_income: "Calculated disposable income",
  calculate_affordability: "Calculated affordability",
  evaluate_eligibility: "Evaluated pre-qualification",
  simulate_mortgage_scenarios: "Compared scenarios",
  generate_evidence_request: "Requested evidence",
  record_finding: "Recorded a finding",
  escalate_to_broker: "Escalated to you",
  update_assessment_status: "Updated the stage",
  generate_assessment_report: "Wrote the assessment",
};

export const LIMIT_LABELS: Record<string, string> = {
  affordability: "Affordability",
  income_multiple: "Income multiple (LTI)",
  loan_to_value: "Loan to value (LTV)",
};

export function gbp(minor: number | null | undefined, pence = false): string {
  if (minor === null || minor === undefined) return "—";
  const value = minor / 100;
  return value.toLocaleString("en-GB", {
    style: "currency",
    currency: "GBP",
    minimumFractionDigits: pence ? 2 : 0,
    maximumFractionDigits: pence ? 2 : 0,
  });
}

/** "£150,000" -> 15000000 pence; empty or invalid -> null. Whole pounds only. */
export function poundsToMinor(text: string): number | null {
  const cleaned = text.replace(/[£,\s]/g, "");
  if (!/^\d{1,9}$/.test(cleaned)) return null;
  return Number(cleaned) * 100;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09" -> "Sep 2026" (fixed names: some locales' data say "Sept"). */
export function monthLabel(month: string): string {
  const [y, m] = month.split("-").map(Number);
  if (!y || !m || m < 1 || m > 12) return month;
  return `${MONTHS[m - 1]} ${y}`;
}

export function stepTitle(step: Pick<Step, "kind" | "tool" | "status" | "error_code">): string {
  if (step.kind === "note" || step.kind === "error") {
    if (step.error_code === "planner_fallback") return "Switched to the rules planner";
    if (step.error_code === "step_limit") return "Reached the step limit";
    return "Note";
  }
  const label = TOOL_LABELS[step.tool ?? ""] ?? `Called ${step.tool}`;
  if (step.status === "error") return `${label}: failed`;
  if (step.status === "cached") return `${label} (reused)`;
  return label;
}

/** A one-line, factual digest of a tool's output for the activity timeline. */
export function stepDigest(step: Step): string | null {
  const o = step.output ?? {};
  if (step.status === "error") return typeof o.error === "string" ? o.error : step.error_code;
  switch (step.tool) {
    case "reconcile_financial_evidence": {
      const d = Array.isArray(o.discrepancies) ? o.discrepancies.length : 0;
      return d
        ? `${d} discrepanc${d === 1 ? "y" : "ies"} found`
        : `No discrepancies (${String(o.passed ?? 0)} checks passed)`;
    }
    case "identify_evidence_gaps": {
      const g = Array.isArray(o.gaps) ? o.gaps.length : 0;
      return g ? `${g} gap${g === 1 ? "" : "s"} found` : "No gaps";
    }
    case "validate_income":
      return `Qualifying income ${gbp(o.qualifying_gross_monthly_minor as number)} a month (gross)`;
    case "calculate_affordability":
      return o.requested_within_limits === false
        ? `Requested loan exceeds the limits by ${gbp(o.shortfall_minor as number)}`
        : `Maximum ${gbp(o.max_mortgage_minor as number)}; requested loan within limits`;
    case "evaluate_eligibility":
      return typeof o.outcome_label === "string" ? o.outcome_label : null;
    case "generate_evidence_request": {
      const c = Array.isArray(o.created) ? o.created.length : 0;
      const a = Array.isArray(o.already_open) ? o.already_open.length : 0;
      return `${c} new request${c === 1 ? "" : "s"}${a ? `, ${a} already open` : ""}`;
    }
    case "simulate_mortgage_scenarios":
      return `${Array.isArray(o.scenarios) ? o.scenarios.length : 0} scenarios compared`;
    case "update_assessment_status":
      return typeof o.stage === "string" ? o.stage.replace(/_/g, " ") : null;
    default:
      return null;
  }
}

/** What the agent is doing or will do next, for a run in progress. */
export function nextAction(a: Pick<AssessmentSummary, "status">, steps: Step[]): string {
  if (a.status === "queued") return "Starting…";
  const last = [...steps].reverse().find((s) => s.kind === "tool");
  if (!last) return "Establishing the case context…";
  if (last.tool === "generate_assessment_report") return "Finishing…";
  return `Working: ${TOOL_LABELS[last.tool ?? ""] ?? last.tool} done, choosing the next step…`;
}

export function verificationSplit(income: IncomeLine[]): { verified: number; unverified: number } {
  const counted = income.filter((i) => i.counted);
  const verified = counted.filter((i) => i.verification === "verified").reduce((s, i) => s + i.qualifying_gross_monthly_minor, 0);
  const all = counted.reduce((s, i) => s + i.qualifying_gross_monthly_minor, 0);
  return { verified, unverified: all - verified };
}

export function decisionLabel(d: Decision["decision"]): string {
  return {
    accept: "Accepted",
    override: "Overridden",
    request_information: "Information requested",
    escalate: "Escalated",
  }[d];
}

export function isRunning(a: Pick<AssessmentSummary, "status"> | null | undefined): boolean {
  return a?.status === "queued" || a?.status === "running";
}
