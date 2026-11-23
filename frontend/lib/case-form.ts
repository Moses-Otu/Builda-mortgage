// Case creation and loan details: validation and request bodies. Money is whole pounds typed by
// the broker, sent as pence. No defaults are invented: blank optional fields are sent as null.

import { poundsToMinor } from "./assessment";

export const POLICY_OPTIONS = [
  { code: "uk-resi-standard/1", label: "UK residential, standard (indicative)" },
  { code: "uk-resi-cautious/1", label: "UK residential, cautious (indicative)" },
] as const;

export interface LoanForm {
  propertyValue: string;
  loanAmount: string;
  termYears: string;
  propertyUse: string;
  productRate: string;
  policyCode: string;
}

export interface ApplicantForm {
  firstName: string;
  lastName: string;
  dateOfBirth: string;
  incomeKind: "employment" | "self_employment" | "other";
  employer: string;
  basis: string;
  startDate: string;
}

const RATE = /^\d{1,2}(\.\d{1,3})?$/;

/** The first problem with the loan details, or null. All three main figures are required. */
export function loanDetailsProblem(f: LoanForm): string | null {
  const value = poundsToMinor(f.propertyValue);
  const loan = poundsToMinor(f.loanAmount);
  if (value === null || value <= 0) return "Enter the property value in whole pounds.";
  if (loan === null || loan <= 0) return "Enter the mortgage amount in whole pounds.";
  if (loan > value) return "The mortgage amount can't be more than the property value.";
  const years = Number(f.termYears);
  if (!f.termYears || !Number.isInteger(years) || years < 1 || years > 50) return "Enter a term between 1 and 50 years.";
  if (f.productRate.trim() && (!RATE.test(f.productRate.trim()) || Number(f.productRate) > 20)) {
    return "Enter the product rate as a percentage, e.g. 4.25, or leave it blank.";
  }
  return null;
}

export function applicantProblem(a: ApplicantForm): string | null {
  if (!a.firstName.trim() || !a.lastName.trim()) return "Enter the applicant's first and last name.";
  if (a.dateOfBirth && Number.isNaN(Date.parse(a.dateOfBirth))) return "Enter a valid date of birth.";
  // The lending policy's time-in-employment check needs it.
  if (a.incomeKind === "employment" && !a.startDate) return "Enter when the applicant started this job.";
  return null;
}

/** The loan fields for POST /cases or PATCH /cases/{id}. */
export function loanPatch(f: LoanForm): Record<string, unknown> {
  return {
    property_value: { amount_minor: poundsToMinor(f.propertyValue) },
    loan_amount: { amount_minor: poundsToMinor(f.loanAmount) },
    term_months: Number(f.termYears) * 12,
    property_use: f.propertyUse,
    product_rate_percent: f.productRate.trim() ? f.productRate.trim() : null,
    policy_code: f.policyCode,
  };
}

/** The income source to create for the applicant. */
export function incomeSourceBody(applicantId: string, a: ApplicantForm): Record<string, unknown> {
  const body: Record<string, unknown> = { applicant_id: applicantId, kind: a.incomeKind, is_primary: true };
  if (a.employer.trim()) body.name = a.employer.trim();
  if (a.incomeKind === "employment" && a.basis) body.basis = a.basis;
  if (a.startDate) body.start_date = a.startDate;
  return body;
}
