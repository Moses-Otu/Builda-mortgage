"use client";

import {
  ArrowRight,
  Bot,
  Briefcase,
  Check,
  FileText,
  FileUp,
  ListChecks,
  Loader2,
  Plus,
  UserRound,
  X,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import { AddApplicantDialog } from "@/components/AddApplicantDialog";
import { CaseStatusControl } from "@/components/CaseStatusControl";
import { ChecksCard } from "@/components/ChecksCard";
import { DeclaredSalary } from "@/components/DeclaredSalary";
import { Breadcrumbs, Loading, PageContainer, RequireFirm } from "@/components/Page";
import { caseTitle, useCaseSummary } from "@/components/useCaseSummary";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { get, postJson, uploadPdf } from "@/lib/api";
import { caseSteps, statementsDue, type Step } from "@/lib/case-steps";
import { documentStatusView } from "@/lib/failures";
import { caseStatusLabel, formatBytes, formatDateTime, purposeLabel } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import { currentDeclaredBasic } from "@/lib/checks";
import type { Applicant, DocumentOut, IncomeSource, Observation } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function CasePageRoute() {
  const { caseId } = useParams<{ caseId: string }>();
  return <RequireFirm>{() => <CasePage caseId={caseId} />}</RequireFirm>;
}

function CasePage({ caseId }: { caseId: string }) {
  const toast = useToast();
  const summary = useCaseSummary(caseId);
  const [applicants, setApplicants] = useState<Applicant[] | null>(null);
  const [sources, setSources] = useState<IncomeSource[]>([]);
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [observations, setObservations] = useState<Observation[]>([]);
  // The case row itself: its live status and version (the ETag for status changes).
  const [caseRow, setCaseRow] = useState<{ status: string; version: number } | null>(null);
  const [applicantId, setApplicantId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [a, s, d, o, c] = await Promise.all([
      get<Applicant[]>(`cases/${caseId}/applicants`),
      get<IncomeSource[]>(`cases/${caseId}/financial/income-sources`),
      get<DocumentOut[]>(`cases/${caseId}/documents`),
      get<Observation[]>(`cases/${caseId}/financial/observations`),
      get<{ status: string; version: number }>(`cases/${caseId}`),
    ]);
    if (o.ok) setObservations(o.data);
    if (c.ok) setCaseRow({ status: c.data.status, version: c.data.version });
    if (!a.ok) {
      setError(problemMessage(a.status, a.problem));
      return;
    }
    const active = a.data.filter((x) => x.status !== "removed");
    setApplicants(active);
    setApplicantId((current) => current || active[0]?.id || "");
    if (s.ok) setSources(s.data);
    if (d.ok) setDocuments([...d.data].sort((x, y) => y.created_at.localeCompare(x.created_at)));
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refresh while any document is still being processed.
  useEffect(() => {
    if (!documents.some((d) => documentStatusView(d.status, d.failure_code).busy)) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [documents, load]);

  async function addIncomeSource() {
    if (!applicantId) return;
    const result = await postJson<IncomeSource>(`cases/${caseId}/financial/income-sources`, {
      applicant_id: applicantId,
      kind: "employment",
    });
    if (result.ok) {
      toast("success", "Employment income source added.");
      await load();
    } else {
      toast("error", problemMessage(result.status, result.problem));
    }
  }

  const title = caseTitle(summary, caseId);
  const crumbs = [{ label: "Cases", href: "/" }, { label: title }];

  if (error) {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <Alert tone="error" title="This case can't be shown">
          {error}
        </Alert>
      </PageContainer>
    );
  }
  if (!applicants) return <Loading />;

  const nameOf = (id: string | null) => {
    const a = applicants.find((x) => x.id === id);
    return a ? `${a.first_name} ${a.last_name}` : "Unknown applicant";
  };
  const activeSources = sources.filter((s) => !s.withdrawn_at);
  const status = caseRow?.status ?? summary?.status ?? "draft";
  const steps = caseSteps(sources, documents, status);
  const suggestStatements = statementsDue(documents, applicantId);
  // The checks re-run when a document changes state or a declared salary changes.
  const checksKey = [
    ...documents.map((d) => `${d.id}:${d.status}`),
    ...observations.filter((o) => o.source_kind === "declared" && !o.retired_at).map((o) => o.id),
  ].join(",");

  return (
    <PageContainer>
      <Breadcrumbs items={crumbs} />
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-3 text-2xl font-semibold tracking-tight">
            {title}
            {(caseRow || summary) && (
              <Badge tone={status === "verified" ? "success" : "info"}>{caseStatusLabel(status)}</Badge>
            )}
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-500">
            {summary && (
              <span className="inline-flex items-center gap-1.5">
                <Briefcase className="size-3.5" /> {purposeLabel(summary.purpose)}
              </span>
            )}
            {applicants.map((a) => (
              <span
                key={a.id}
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-slate-700"
              >
                <UserRound className="size-3.5 text-slate-400" /> {a.first_name} {a.last_name}
              </span>
            ))}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {caseRow && (
            <CaseStatusControl
              caseId={caseId}
              status={caseRow.status}
              version={caseRow.version}
              onChanged={() => void load()}
            />
          )}
          <Link
            href={`/cases/${caseId}/profile`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:border-brand-500 hover:text-brand-700"
          >
            <ListChecks className="size-4" /> Financial profile
          </Link>
          <Link
            href={`/cases/${caseId}/assessment`}
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-brand-600 px-3 text-sm font-medium text-white shadow-sm hover:bg-brand-700"
          >
            <Bot className="size-4" /> Assessment
          </Link>
          <AddApplicantDialog caseId={caseId} onAdded={() => void load()} />
        </div>
      </div>

      {applicants.length === 0 ? (
        <Alert tone="info" title="Add an applicant first">
          Payslips are filed under an applicant. Use “Add applicant” to add one.
        </Alert>
      ) : (
        <>
          <StepStrip steps={steps} />

          <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Documents</CardTitle>
                  <CardDescription>
                    Upload payslips and review their figures, then the bank statements for the same months.
                  </CardDescription>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {suggestStatements && (
                  <Alert tone="info" title="Payslips reviewed. Next: bank statements">
                    Upload {applicants.length > 1 ? `${nameOf(applicantId).split(" ")[0]}'s` : "the"} bank
                    statements for the same months, so each salary payment can be matched to its payslip.
                  </Alert>
                )}
                <UploadZone
                  caseId={caseId}
                  applicants={applicants}
                  applicantId={applicantId}
                  onApplicant={setApplicantId}
                  onUploaded={() => void load()}
                  suggestStatements={suggestStatements}
                />
                <DocumentList caseId={caseId} documents={documents} nameOf={nameOf} />
              </CardContent>
            </Card>

            <Card className="self-start">
              <CardHeader>
                <div>
                  <CardTitle>Income sources</CardTitle>
                  <CardDescription>Reviewed payslip figures are saved against one.</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                {activeSources.length === 0 && (
                  <p className="rounded-lg border border-dashed border-slate-300 px-3 py-4 text-center text-sm text-slate-500">
                    None yet.
                  </p>
                )}
                {activeSources.map((s) => (
                  <div key={s.id} className="flex flex-col gap-2 rounded-lg border border-slate-200 px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="grid size-8 place-items-center rounded-md bg-brand-50 text-brand-600">
                        <Briefcase className="size-4" />
                      </span>
                      <div className="text-sm">
                        <p className="font-medium">{s.name ?? "Employment income"}</p>
                        <p className="text-slate-500">{nameOf(s.applicant_id)}</p>
                      </div>
                    </div>
                    {s.kind === "employment" && (
                      <DeclaredSalary
                        caseId={caseId}
                        incomeSourceId={s.id}
                        current={currentDeclaredBasic(observations, s.id)}
                        onSaved={() => void load()}
                      />
                    )}
                  </div>
                ))}
                <Button variant="secondary" onClick={addIncomeSource} disabled={!applicantId}>
                  <Plus /> Add employment income
                  {applicants.length > 1 && ` for ${nameOf(applicantId).split(" ")[0]}`}
                </Button>
              </CardContent>
            </Card>
          </div>

          <div className="mt-6">
            <ChecksCard caseId={caseId} refreshKey={checksKey} />
          </div>
        </>
      )}
    </PageContainer>
  );
}

function StepStrip({ steps }: { steps: Step[] }) {
  return (
    <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      {steps.map((step, i) => (
        <li
          key={step.key}
          className={cn(
            "flex items-start gap-3 rounded-xl border bg-white p-4",
            step.state === "current" ? "border-brand-500 ring-3 ring-brand-500/15" : "border-slate-200",
          )}
        >
          <span
            className={cn(
              "grid size-7 shrink-0 place-items-center rounded-full text-sm font-semibold",
              step.state === "done" && "bg-accept text-white",
              step.state === "current" && "bg-brand-600 text-white",
              step.state === "upcoming" && "bg-slate-100 text-slate-500",
            )}
          >
            {step.state === "done" ? <Check className="size-4" /> : i + 1}
          </span>
          <div className="text-sm">
            <p className={cn("font-medium", step.state === "upcoming" && "text-slate-500")}>{step.title}</p>
            <p className="text-slate-500">{step.detail}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

function UploadZone(props: {
  caseId: string;
  applicants: Applicant[];
  applicantId: string;
  onApplicant: (id: string) => void;
  onUploaded: () => void;
  suggestStatements?: boolean;
}) {
  const { caseId, applicants, applicantId, onApplicant, onUploaded, suggestStatements = false } = props;
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [documentType, setDocumentType] = useState<"payslip" | "bank_statement">("payslip");

  // When the payslips are done, preselect statements. Only on that change, so the broker can
  // still switch back to payslips.
  useEffect(() => {
    if (suggestStatements) setDocumentType("bank_statement");
  }, [suggestStatements]);

  function pick(candidate: File | undefined) {
    setError(null);
    if (!candidate) return;
    if (candidate.type !== "application/pdf" && !candidate.name.toLowerCase().endsWith(".pdf")) {
      setError("Only PDF files can be uploaded.");
      return;
    }
    setFile(candidate);
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    pick(event.dataTransfer.files[0]);
  }

  function clear() {
    setFile(null);
    if (input.current) input.current.value = "";
  }

  async function upload() {
    if (!file || !applicantId) return;
    setBusy(true);
    setError(null);
    const result = await uploadPdf<DocumentOut>(caseId, file, applicantId, documentType);
    setBusy(false);
    if (result.ok) {
      toast("success", "Uploaded. Processing has started.");
      clear();
      onUploaded();
    } else {
      setError(problemMessage(result.status, result.problem));
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3">
        <Label className="w-56">
          Document type
          <Select
            value={documentType}
            onChange={(e) => setDocumentType(e.target.value as "payslip" | "bank_statement")}
          >
            <option value="payslip">Payslip</option>
            <option value="bank_statement">Bank statement</option>
          </Select>
        </Label>
        {applicants.length > 1 && (
          <Label className="w-56">
            File under
            <Select value={applicantId} onChange={(e) => onApplicant(e.target.value)}>
              {applicants.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.first_name} {a.last_name}
                </option>
              ))}
            </Select>
          </Label>
        )}
      </div>
      {file ? (
        <div className="flex items-center gap-3 rounded-xl border border-brand-200 bg-brand-50 p-4">
          <span className="grid size-10 place-items-center rounded-lg bg-white text-brand-600">
            <FileText className="size-5" />
          </span>
          <div className="min-w-0 flex-1 text-sm">
            <p className="truncate font-medium">{file.name}</p>
            <p className="text-slate-500">
              {formatBytes(file.size)}
              {applicants.length === 1 && applicants[0] && ` · for ${applicants[0].first_name} ${applicants[0].last_name}`}
            </p>
          </div>
          <Button variant="ghost" size="icon" onClick={clear} aria-label="Remove file" disabled={busy}>
            <X />
          </Button>
          <Button onClick={upload} disabled={busy || !applicantId}>
            {busy ? <Loader2 className="animate-spin" /> : <FileUp />} Upload
          </Button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn(
            "flex flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition-colors",
            dragging ? "border-brand-500 bg-brand-50" : "border-slate-300 hover:border-brand-500 hover:bg-slate-50",
          )}
        >
          <span className="grid size-10 place-items-center rounded-full bg-brand-50 text-brand-600">
            <FileUp className="size-5" />
          </span>
          <span className="text-sm font-medium">Drop a {documentType === "bank_statement" ? "bank statement" : "payslip"} PDF here, or click to choose</span>
          <span className="text-xs text-slate-500">Synthetic documents only · upload the file exactly as generated</span>
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept="application/pdf,.pdf"
        className="sr-only"
        tabIndex={-1}
        aria-label="Choose a PDF"
        onChange={(e) => pick(e.target.files?.[0])}
      />
      {error && <Alert tone="error">{error}</Alert>}
    </div>
  );
}

function DocumentList(props: {
  caseId: string;
  documents: DocumentOut[];
  nameOf: (id: string | null) => string;
}) {
  const { caseId, documents, nameOf } = props;
  if (documents.length === 0) {
    return <p className="py-2 text-center text-sm text-slate-500">No documents uploaded yet.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {documents.map((d) => {
        // Bank statements are extracted from step 2B-1; their confirmation screen is 2B-2.
        const isStatement = d.document_type === "bank_statement";
        const status = documentStatusView(d.status, d.failure_code);
        const view =
          isStatement && d.status === "extracted"
            ? { ...status, label: "Ready to confirm" }
            : isStatement && d.status === "reviewed"
              ? { ...status, label: "Confirmed" }
              : status;
        const href = `/cases/${caseId}/documents/${d.id}`;
        const canOpen = d.status === "extracted" || d.status === "reviewed";
        return (
          <li key={d.id} className="rounded-xl border border-slate-200 p-4">
            <div className="flex items-center gap-3">
              <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500">
                <FileText className="size-5" />
              </span>
              <div className="min-w-0 flex-1 text-sm">
                <p className="flex items-center gap-2 truncate font-medium">
                  {d.original.original_filename ?? (isStatement ? "statement.pdf" : "payslip.pdf")}
                  {isStatement && <Badge tone="neutral">Bank statement</Badge>}
                </p>
                <p className="text-slate-500">
                  {nameOf(d.applicant_id)} · {formatDateTime(d.created_at)}
                  {d.page_count !== null && ` · ${d.page_count} page${d.page_count === 1 ? "" : "s"}`}
                </p>
              </div>
              <Badge tone={view.tone}>
                {view.busy && <Loader2 className="animate-spin" />}
                {view.label}
              </Badge>
              {canOpen && (
                <Button asChild size="sm" variant={d.status === "extracted" ? "primary" : "secondary"}>
                  <Link href={href}>
                    {d.status === "extracted" ? (isStatement ? "Confirm" : "Review") : "View"} <ArrowRight />
                  </Link>
                </Button>
              )}
            </div>
            {view.explanation && (
              <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
                {view.explanation}
                {view.supportCode && <span className="ml-1 font-mono text-xs text-slate-400">({view.supportCode})</span>}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
