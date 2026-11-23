"use client";

// New case: captures what the journey's first stage needs in one go — the applicant, their
// income type and the loan — so the broker lands on the case ready to upload payslips.
// Nothing is defaulted silently: a blank product rate means the policy's assumed rate, and
// the assessment says so.

import { Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useState } from "react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { postJson } from "@/lib/api";
import {
  type ApplicantForm,
  applicantProblem,
  incomeSourceBody,
  loanDetailsProblem,
  type LoanForm,
  loanPatch,
  POLICY_OPTIONS,
} from "@/lib/case-form";
import { PURPOSES } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { Applicant, CaseSummary } from "@/lib/types";

const EMPTY_APPLICANT: ApplicantForm = {
  firstName: "",
  lastName: "",
  dateOfBirth: "",
  incomeKind: "employment",
  employer: "",
  basis: "permanent",
  startDate: "",
};

const EMPTY_LOAN: LoanForm = {
  propertyValue: "",
  loanAmount: "",
  termYears: "25",
  propertyUse: "residential",
  productRate: "",
  policyCode: "uk-resi-standard/1",
};

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="grid gap-3 sm:grid-cols-2">
      <legend className="mb-2 text-sm font-semibold text-slate-900">{title}</legend>
      {children}
    </fieldset>
  );
}

export function NewCaseDialog() {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [reference, setReference] = useState("");
  const [purpose, setPurpose] = useState("purchase");
  const [applicant, setApplicant] = useState<ApplicantForm>(EMPTY_APPLICANT);
  const [loan, setLoan] = useState<LoanForm>(EMPTY_LOAN);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const setA = (patch: Partial<ApplicantForm>) => setApplicant((a) => ({ ...a, ...patch }));
  const setL = (patch: Partial<LoanForm>) => setLoan((l) => ({ ...l, ...patch }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = applicantProblem(applicant) ?? loanDetailsProblem(loan);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = { purpose, ...loanPatch(loan) };
    if (reference.trim()) body.external_reference = reference.trim();
    const created = await postJson<CaseSummary>("cases", body);
    if (!created.ok) {
      setBusy(false);
      setError(problemMessage(created.status, created.problem));
      return;
    }
    const caseId = created.data.id;
    // The case exists from here: a later failure still opens it, where the step can be redone.
    const person: Record<string, string> = {
      first_name: applicant.firstName.trim(),
      last_name: applicant.lastName.trim(),
    };
    if (applicant.dateOfBirth) person.date_of_birth = applicant.dateOfBirth;
    const added = await postJson<Applicant>(`cases/${caseId}/applicants`, person);
    let followUp: string | null = null;
    if (!added.ok) {
      followUp = `The case was created, but the applicant wasn't added: ${problemMessage(added.status, added.problem)}`;
    } else {
      const source = await postJson(`cases/${caseId}/financial/income-sources`, incomeSourceBody(added.data.id, applicant));
      if (!source.ok) {
        followUp = `The case was created, but the income source wasn't added: ${problemMessage(source.status, source.problem)}`;
      }
    }
    setBusy(false);
    if (followUp) toast("error", followUp);
    else toast("success", "Case created. Next: upload the payslips.");
    setOpen(false);
    setApplicant(EMPTY_APPLICANT);
    setLoan(EMPTY_LOAN);
    setReference("");
    router.push(`/cases/${caseId}${followUp ? "?stage=details" : ""}`);
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button>
          <Plus /> New case
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={submit} className="flex flex-col gap-5" noValidate>
          <DialogHeader>
            <DialogTitle>New case</DialogTitle>
            <DialogDescription>
              The applicant and the loan they want. Joint applicants can be added on the case.
            </DialogDescription>
          </DialogHeader>

          <Section title="Case">
            <Label>
              Your reference
              <Input
                value={reference}
                onChange={(e) => setReference(e.target.value)}
                maxLength={100}
                placeholder="e.g. SYN-DEMO-002"
                autoFocus
              />
              <FieldHint>Optional. Don&apos;t use a client&apos;s name.</FieldHint>
            </Label>
            <Label>
              Purpose
              <Select value={purpose} onChange={(e) => setPurpose(e.target.value)}>
                {Object.entries(PURPOSES).map(([code, label]) => (
                  <option key={code} value={code}>
                    {label}
                  </option>
                ))}
              </Select>
            </Label>
          </Section>

          <Section title="Applicant">
            <Label>
              First name
              <Input value={applicant.firstName} onChange={(e) => setA({ firstName: e.target.value })} required />
            </Label>
            <Label>
              Last name
              <Input value={applicant.lastName} onChange={(e) => setA({ lastName: e.target.value })} required />
            </Label>
            <Label>
              Date of birth
              <Input type="date" value={applicant.dateOfBirth} onChange={(e) => setA({ dateOfBirth: e.target.value })} />
              <FieldHint>Optional. Used for the age-at-end-of-term check.</FieldHint>
            </Label>
            <Label>
              Income type
              <Select
                value={applicant.incomeKind}
                onChange={(e) => setA({ incomeKind: e.target.value as ApplicantForm["incomeKind"] })}
              >
                <option value="employment">Employed</option>
                <option value="self_employment">Self-employed</option>
                <option value="other">Other</option>
              </Select>
            </Label>
            <Label>
              {applicant.incomeKind === "self_employment" ? "Business name" : "Employer"}
              <Input value={applicant.employer} onChange={(e) => setA({ employer: e.target.value })} placeholder="Optional" />
            </Label>
            {applicant.incomeKind === "employment" && (
              <Label>
                Employment basis
                <Select value={applicant.basis} onChange={(e) => setA({ basis: e.target.value })}>
                  <option value="permanent">Permanent</option>
                  <option value="fixed_term">Fixed term</option>
                  <option value="contract">Contract</option>
                  <option value="zero_hours">Zero hours</option>
                  <option value="agency">Agency</option>
                  <option value="other">Other</option>
                </Select>
              </Label>
            )}
            <Label>
              Started
              <Input type="date" value={applicant.startDate} onChange={(e) => setA({ startDate: e.target.value })} />
              <FieldHint>
                {applicant.incomeKind === "employment" ? "Needed for the time-in-employment check." : "Optional."}
              </FieldHint>
            </Label>
          </Section>

          <Section title="Loan">
            <Label>
              Property value (£)
              <Input inputMode="numeric" value={loan.propertyValue} onChange={(e) => setL({ propertyValue: e.target.value })} required />
            </Label>
            <Label>
              Mortgage amount (£)
              <Input inputMode="numeric" value={loan.loanAmount} onChange={(e) => setL({ loanAmount: e.target.value })} required />
            </Label>
            <Label>
              Term (years)
              <Input type="number" min={1} max={50} value={loan.termYears} onChange={(e) => setL({ termYears: e.target.value })} required />
            </Label>
            <Label>
              Property use
              <Select value={loan.propertyUse} onChange={(e) => setL({ propertyUse: e.target.value })}>
                <option value="residential">Residential</option>
                <option value="buy_to_let">Buy to let</option>
                <option value="second_home">Second home</option>
              </Select>
            </Label>
            <Label>
              Product rate (%)
              <Input inputMode="decimal" value={loan.productRate} onChange={(e) => setL({ productRate: e.target.value })} placeholder="Optional" />
              <FieldHint>Blank uses the policy&apos;s assumed rate.</FieldHint>
            </Label>
            <Label>
              Lending policy
              <Select value={loan.policyCode} onChange={(e) => setL({ policyCode: e.target.value })}>
                {POLICY_OPTIONS.map((p) => (
                  <option key={p.code} value={p.code}>
                    {p.label}
                  </option>
                ))}
              </Select>
            </Label>
          </Section>

          {error && <Alert tone="error">{error}</Alert>}
          <DialogFooter>
            <Button variant="secondary" type="button" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              Create case
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
