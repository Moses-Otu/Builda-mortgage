// The parts of the API contract this screen uses (see backend/openapi.json).

export type Uuid = string;

/** An active membership (pending invitations are listed separately by /me). */
export interface Membership {
  membership_id: Uuid;
  tenant_id: Uuid;
  tenant_name: string;
  role: string;
}

export interface Me {
  user_id: Uuid | null;
  memberships: Membership[];
  invitations?: Membership[];
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface CaseSummary {
  id: Uuid;
  external_reference: string | null;
  applicant_count: number;
  purpose: string;
  status: string;
  created_at: string;
}

export interface Applicant {
  id: Uuid;
  first_name: string;
  last_name: string;
  status: string;
}

export interface IncomeSource {
  id: Uuid;
  applicant_id: Uuid;
  kind: string;
  name: string | null;
  basis?: string | null;
  start_date?: string | null;
  version?: number;
  withdrawn_at: string | null;
}

export type DocumentStatus =
  | "received"
  | "ready"
  | "extracted"
  | "unrecognised"
  | "reviewed"
  | "failed";

export interface DocumentOut {
  id: Uuid;
  case_id: Uuid;
  applicant_id: Uuid | null;
  document_type: string;
  status: DocumentStatus;
  failure_code: string | null;
  page_count: number | null;
  version: number;
  created_at: string;
  original: { id: Uuid; original_filename: string | null; size_bytes: number };
}

export interface Evidence {
  page_number: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface ExtractedField {
  ordinal: number;
  field_code: string | null;
  label_text: string;
  raw_text: string;
  value_money_minor: number | null;
  currency: string | null;
  value_date: string | null;
  value_text: string | null;
  value_evidence: Evidence;
  label_evidence: Evidence;
  page_width: number;
  page_height: number;
  unit: string;
  confidence: number | null;
}

export interface Extraction {
  provider: string;
  model_version: string;
  schema_version: string;
  classifier_version: string;
  classification: string;
  markers: string[];
  summary: ExtractedField[];
  payments: ExtractedField[];
  deductions: ExtractedField[];
  year_to_date: ExtractedField[];
  missing_required: string[];
  // Bank statements (Stage 2-B)
  transactions?: Transaction[];
  integrity?: Integrity | null;
  account_match?: AccountMatch | null;
}

export interface Cell {
  text: string;
  page: number;
  box: [number, number, number, number];
}

export interface Transaction {
  row_number: number;
  kind: "transaction" | "brought_forward" | "carried_forward" | "unparsed";
  posted_on: string | null;
  description: string;
  description_truncated: boolean;
  paid_in_minor: number | null;
  paid_out_minor: number | null;
  balance_minor: number | null;
  cells: {
    date: Cell | null;
    date_inherited: boolean;
    description: Cell[];
    paid_out: Cell | null;
    paid_in: Cell | null;
    balance: Cell | null;
    other: Cell[];
  };
  row_evidence: Evidence;
  page_width: number;
  page_height: number;
  unit: string;
}

export type CheckOutcome = "passed" | "discrepancy" | "not_run";

/** Mathematical validation of a statement: separate from the broker's confirmation. */
export interface Integrity {
  totals: CheckOutcome;
  totals_message: string;
  running: CheckOutcome;
  running_message: string;
  break_row: number | null;
}

export interface AccountMatch {
  status: "existing" | "new" | "unverified";
  account_id: Uuid | null;
  same_ending_account_ids: Uuid[];
}

export interface BankAccount {
  id: Uuid;
  account_ending: string;
  label: string | null;
  fingerprinted: boolean;
  joint: boolean;
  holders: { applicant_id: Uuid; added_at: string }[];
  statement_document_ids: Uuid[];
  created_at: string;
}

export type Section = "summary" | "payments" | "deductions" | "year_to_date";
export const SECTIONS: Section[] = ["summary", "payments", "deductions", "year_to_date"];

export type DecisionKind = "accept" | "correct" | "reject";

export interface FieldDecisionIn {
  ordinal: number;
  decision: DecisionKind;
  corrected_money_minor?: number;
  corrected_date?: string;
  corrected_text?: string;
  reason?: string;
  measure?: string;
  qualifier?: string;
}

export interface ReviewIn {
  income_source_id?: Uuid;
  frequency?: string;
  bank_account_id?: Uuid;
  new_account?: { holder_ids: Uuid[]; label?: string | null };
  decisions: FieldDecisionIn[];
}

export interface FieldDecisionOut {
  ordinal: number;
  section: string;
  field_code: string | null;
  label_text: string;
  raw_text: string;
  extracted_money_minor: number | null;
  extracted_date: string | null;
  extracted_text: string | null;
  decision: DecisionKind;
  corrected_money_minor: number | null;
  corrected_date: string | null;
  corrected_text: string | null;
  reason: string | null;
  measure: string | null;
  qualifier: string | null;
  observation_id: Uuid | null;
}

export interface ReviewOut {
  id: Uuid;
  document_id: Uuid;
  income_source_id: Uuid | null;
  frequency: string | null;
  bank_account?: BankAccount | null;
  integrity?: Integrity | null;
  created_at: string;
  decisions: FieldDecisionOut[];
}

export interface Measure {
  code: string;
  subject_kind: string;
  value_type: string;
  temporal_kind: string;
  qualifier_required: boolean;
  qualifiers: string[];
}

// --- reconciliation checks (Stage 2-A payslips, Stage 2-B bank statements) ---

export type FindingStatus = "passed" | "discrepancy" | "not_run";

export interface CheckEvidence {
  /** document: a payslip field · declared: a declared figure · statement: a confirmed statement
   * value · transaction: one printed statement row (description verbatim in value_text). */
  source_kind: "document" | "declared" | "statement" | "transaction";
  observation_id: Uuid | null;
  field_review_id: Uuid | null;
  document_id: Uuid | null;
  document_label: string | null;
  field_ordinal: number | null;
  row_number?: number | null;
  label: string;
  measure: string | null;
  value_minor: number | null;
  value_date: string | null;
  value_text: string | null;
}

export interface Acknowledgement {
  note: string;
  acknowledged_by_user_id: Uuid | null;
  acknowledged_at: string;
}

export type MatchOutcome = "exact" | "multiple" | "near" | "none";

/** For a salary credit: whose payslip, which account the money arrived in, and the row. */
export interface Relationship {
  payslip_applicant_id: Uuid;
  account_id: Uuid;
  account_ending: string;
  account_label: string | null;
  joint: boolean;
  holder_ids: Uuid[];
  applicant_holds_account: boolean;
  filing_applicant_id: Uuid | null;
  document_id: Uuid;
  row_number: number;
}

export interface Finding {
  key: string;
  ruleset_version: string;
  rule_code: string;
  status: FindingStatus;
  title: string;
  message: string;
  income_source_id: Uuid | null;
  applicant_id: Uuid | null;
  bank_account_id?: Uuid | null;
  document_id?: Uuid | null;
  /** SALARY-CREDIT only. A near match is never treated as received. */
  match_outcome?: MatchOutcome | null;
  relationship?: Relationship | null;
  amounts: Record<string, number>;
  evidence: CheckEvidence[];
  acknowledgement: Acknowledgement | null;
}

export interface Checks {
  ruleset_versions: string[];
  /** Each rule set's thresholds, by version. */
  parameters: Record<string, Record<string, unknown>>;
  findings: Finding[];
}

export interface Observation {
  id: Uuid;
  income_source_id: Uuid | null;
  measure: string;
  source_kind: string;
  frequency: string | null;
  value: { type: string; amount_minor?: number };
  retired_at: string | null;
  recorded_at: string;
}

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  existing_review_id?: string;
  existing_document_id?: string;
}

// --- financial profile (Stage 3-A) ---

export type Origin = "extracted" | "corrected" | "declared" | "broker_entry";

export interface MemberRef {
  user_id: Uuid | null;
  name: string;
}

export interface FigureValue {
  type: string;
  amount_minor?: number;
  currency?: string;
  percent?: string | number;
  months?: number;
  count?: number;
  date?: string;
}

export interface FigureEvidence {
  document_id: Uuid;
  document_version_id: Uuid;
  document_type: string;
  file_name: string | null;
  sha256: string;
  field: ExtractedField;
  decision: "accept" | "correct";
  correction_reason: string | null;
  run_id: Uuid;
  provider: string;
  schema_version: string;
  reviewed_by: MemberRef | null;
  reviewed_at: string;
}

export interface FigureHistory {
  observation_id: Uuid;
  value: FigureValue;
  source_kind: string;
  recorded_by: MemberRef | null;
  recorded_at: string;
  retired_reason: string | null;
}

export interface Figure {
  id: Uuid;
  item_kind: "income_source" | "commitment" | "expenditure";
  item_id: Uuid;
  measure: string;
  qualifier: string | null;
  value: FigureValue;
  frequency: string | null;
  period_start: string | null;
  period_end: string | null;
  tax_year: string | null;
  as_of_date: string | null;
  source_kind: string;
  origin: Origin;
  recorded_by: MemberRef | null;
  recorded_at: string;
  evidence: FigureEvidence | null;
  history: FigureHistory[];
  cited_by: string[];
}

export interface ProfileApplicant {
  id: Uuid;
  display_order: number;
  party_role: string;
  is_primary: boolean;
  status: string;
  first_name: string;
  middle_names: string | null;
  last_name: string;
}

export interface ProfileItem {
  id: Uuid;
  kind?: string;
  name?: string | null;
  applicant_id?: Uuid;
  category?: string;
  description?: string | null;
  creditor?: string | null;
}

// "Supported by" (step 3-A2): five kinds per item, as lists; never counted or scored.

export interface DocumentarySupport {
  document_id: Uuid;
  document_type: string;
  file_name: string | null;
  period_start: string | null;
  period_end: string | null;
  measures: string[];
  corrected_measures: string[];
  figures_saved: boolean;
  reviewed_by: MemberRef | null;
  reviewed_at: string;
}

export interface Corroboration {
  kind: "salary_credit";
  finding_key: string;
  payslip_document_id: Uuid | null;
  credit_date: string | null;
  amount_minor: number | null;
  account_id: Uuid;
  account_ending: string;
  account_label: string | null;
  joint: boolean;
  holder_ids: Uuid[];
  statement_document_id: Uuid;
  row_number: number;
  scope: string;
}

export interface FindingRef {
  key: string;
  ruleset_version: string;
  rule_code: string;
  title: string;
}

export interface ItemSupport {
  item_kind: "income_source" | "commitment" | "expenditure";
  item_id: Uuid;
  declared: Uuid[];
  documentary: DocumentarySupport[];
  corroborating: Corroboration[];
  findings: {
    discrepancies: (FindingRef & { acknowledgement: Acknowledgement | null })[];
    not_run: (FindingRef & { reason: string })[];
    passed: FindingRef[];
  };
}

export interface ApprovalStatus {
  status: "not_approved" | "approved";
  approval_id?: Uuid | null;
  version?: number | null;
  approved_by?: MemberRef | null;
  approved_at?: string | null;
  superseded?: { version: number; superseded_at: string | null }[];
}

// --- profile approval (Stage 3-B) ---

export type BlockerCode =
  | "case_not_open"
  | "documents_outstanding"
  | "discrepancies_unacknowledged"
  | "nothing_recorded";

export interface Readiness {
  ready: boolean;
  blockers: { code: BlockerCode; message: string; ids: string[] }[];
  notices: { message: string }[];
  attestation: { version: string; text: string };
}

export interface Approval {
  id: Uuid;
  case_id: Uuid;
  version: number;
  profile_rules: string;
  snapshot_sha256: string;
  attestation_version: string;
  note: string | null;
  approved_by: MemberRef | null;
  approved_at: string;
  current: boolean;
  superseded_at: string | null;
}

export interface ApprovalDetail extends Approval {
  attestation_text: string;
  snapshot: ProfileSnapshot;
}

/** What an approval freezes: the profile without its derived state fields. */
export type ProfileSnapshot = Omit<Profile, "readiness" | "approval" | "fingerprint">;

export interface Profile {
  rules: string;
  case: { id: Uuid; external_reference: string | null; purpose: string; status: string };
  applicants: ProfileApplicant[];
  income_sources: ProfileItem[];
  commitments: ProfileItem[];
  expenditure: ProfileItem[];
  figures: Figure[];
  bank_accounts: BankAccount[];
  checks: Checks;
  documents: DocumentOut[];
  support: ItemSupport[];
  members: MemberRef[];
  readiness: Readiness;
  approval: ApprovalStatus;
  fingerprint: string;
}
