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
  income_source_id: Uuid;
  frequency?: string;
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
  income_source_id: Uuid;
  frequency: string;
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

export interface Problem {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  existing_review_id?: string;
  existing_document_id?: string;
}
