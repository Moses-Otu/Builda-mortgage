// Human wording for API codes. Display only: the codes themselves are never changed.

const ROLES: Record<string, string> = {
  firm_admin: "Firm admin",
  adviser: "Adviser",
  case_administrator: "Case administrator",
  read_only: "Read only",
};

export const PURPOSES: Record<string, string> = {
  purchase: "Purchase",
  remortgage: "Remortgage",
  further_advance: "Further advance",
  product_transfer: "Product transfer",
  porting: "Porting",
};

const CASE_STATUSES: Record<string, string> = {
  draft: "Draft",
  collecting_evidence: "Collecting evidence",
  in_review: "In review",
  verified: "Verified",
  closed: "Closed",
};

export const FREQUENCIES: Record<string, string> = {
  weekly: "Weekly",
  fortnightly: "Fortnightly",
  four_weekly: "Four-weekly",
  monthly: "Monthly",
  quarterly: "Quarterly",
  annual: "Annual",
};

const MEASURES: Record<string, string> = {
  gross_pay: "Gross pay",
  net_pay: "Net pay",
  basic_pay: "Basic pay",
  overtime: "Overtime",
  commission: "Commission",
  bonus: "Bonus",
  allowance: "Allowance",
  income_tax: "Income tax",
  national_insurance: "National Insurance",
  pension_contribution: "Pension contribution",
  student_loan_deduction: "Student loan",
  ytd_gross_pay: "Gross pay to date",
  ytd_income_tax: "Income tax to date",
  pay_date: "Pay date",
  pay_period_start: "Period start",
  pay_period_end: "Period end",
  pay_frequency: "Pay frequency",
  tax_code: "Tax code",
  total_deductions: "Total deductions",
  account_ending: "Account ending",
  statement_period_start: "Period start",
  statement_period_end: "Period end",
  opening_balance: "Opening balance",
  closing_balance: "Closing balance",
  total_paid_in: "Total paid in",
  total_paid_out: "Total paid out",
};

const humanise = (code: string) => {
  const text = code.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export const roleLabel = (code: string) => ROLES[code] ?? humanise(code);
export const purposeLabel = (code: string) => PURPOSES[code] ?? humanise(code);
export const caseStatusLabel = (code: string) => CASE_STATUSES[code] ?? humanise(code);
export const frequencyLabel = (code: string) => FREQUENCIES[code] ?? humanise(code);
export const measureLabel = (code: string) => MEASURES[code] ?? humanise(code);

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { dateStyle: "medium" });
}
