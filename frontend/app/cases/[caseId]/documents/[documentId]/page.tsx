"use client";

import { Check, ChevronDown, Keyboard, Pencil, X } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Breadcrumbs, Loading, PageContainer, RequireFirm } from "@/components/Page";
import { type Highlight, PdfView } from "@/components/PdfView";
import { FieldRow } from "@/components/review/FieldRow";
import { SavedReview } from "@/components/review/SavedReview";
import { SaveReviewDialog } from "@/components/review/SaveReviewDialog";
import { ShortcutsDialog } from "@/components/review/ShortcutsDialog";
import { caseTitle, useCaseSummary } from "@/components/useCaseSummary";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { API, get, postJson } from "@/lib/api";
import { buildReview, type Draft, type Drafts, problems, savesFigure } from "@/lib/decisions";
import { measureLabel } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import {
  acceptRemaining,
  decisionCounts,
  type Filter,
  isTypingTarget,
  matchesFilter,
  payslipFrequency,
  shortcutFor,
  step,
} from "@/lib/review-ui";
import {
  type Applicant,
  type DecisionKind,
  type DocumentOut,
  type Extraction,
  type IncomeSource,
  type Measure,
  type ReviewOut,
  type Section,
  SECTIONS,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const SECTION_TITLES: Record<Section, string> = {
  summary: "Summary",
  payments: "Payments",
  deductions: "Deductions",
  year_to_date: "Year to date",
};
const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All" },
  { key: "undecided", label: "Undecided" },
  { key: "changed", label: "Corrected or rejected" },
];

export default function ReviewRoute() {
  const { caseId, documentId } = useParams<{ caseId: string; documentId: string }>();
  return <RequireFirm>{() => <ReviewPage caseId={caseId} documentId={documentId} />}</RequireFirm>;
}

function ReviewPage({ caseId, documentId }: { caseId: string; documentId: string }) {
  const toast = useToast();
  const summary = useCaseSummary(caseId);
  const base = `cases/${caseId}/documents/${documentId}`;
  const [document, setDocument] = useState<DocumentOut | null>(null);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [review, setReview] = useState<ReviewOut | null>(null);
  const [allSources, setAllSources] = useState<IncomeSource[]>([]);
  const [applicants, setApplicants] = useState<Applicant[]>([]);
  const [measures, setMeasures] = useState<Measure[]>([]);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [collapsed, setCollapsed] = useState<Set<Section>>(new Set());
  const [incomeSourceId, setIncomeSourceId] = useState("");
  const [frequency, setFrequency] = useState("");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);

  const load = useCallback(async () => {
    const d = await get<DocumentOut>(base);
    if (!d.ok) {
      setLoadError(problemMessage(d.status, d.problem));
      return;
    }
    setDocument(d.data);
    const [x, s, a, m] = await Promise.all([
      get<Extraction>(`${base}/extraction`),
      get<IncomeSource[]>(`cases/${caseId}/financial/income-sources`),
      get<Applicant[]>(`cases/${caseId}/applicants`),
      get<Measure[]>("financial/measures"),
    ]);
    if (x.ok) setExtraction(x.data);
    if (s.ok) setAllSources(s.data);
    if (a.ok) setApplicants(a.data);
    if (m.ok) setMeasures(m.data.filter((y) => y.subject_kind === "income_source" && y.value_type === "money"));
    if (d.data.status === "reviewed") {
      const r = await get<ReviewOut>(`${base}/review`);
      if (r.ok) setReview(r.data);
    }
  }, [base, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const sources = useMemo(
    () =>
      allSources.filter(
        (src) => !src.withdrawn_at && (!document?.applicant_id || src.applicant_id === document.applicant_id),
      ),
    [allSources, document],
  );
  useEffect(() => {
    setIncomeSourceId((current) => (sources.some((s) => s.id === current) ? current : (sources[0]?.id ?? "")));
  }, [sources]);

  const applicantName = useMemo(() => {
    const a = applicants.find((x) => x.id === document?.applicant_id);
    return a ? `${a.first_name} ${a.last_name}` : null;
  }, [applicants, document]);
  const sourceName = useCallback(
    (s: IncomeSource) => `${s.name ?? "Employment income"}${applicantName ? ` — ${applicantName}` : ""}`,
    [applicantName],
  );

  const fields = useMemo(
    () => (extraction ? SECTIONS.flatMap((section) => extraction[section]) : []),
    [extraction],
  );
  const highlights: Highlight[] = useMemo(
    () =>
      fields.flatMap((f) => {
        const geometry = { page_width: f.page_width, page_height: f.page_height, unit: f.unit };
        return [
          { ordinal: f.ordinal, kind: "label" as const, evidence: f.label_evidence, geometry },
          { ordinal: f.ordinal, kind: "value" as const, evidence: f.value_evidence, geometry },
        ];
      }),
    [fields],
  );
  const openProblems = useMemo(() => problems(fields, drafts), [fields, drafts]);
  const counts = useMemo(() => decisionCounts(fields, drafts, openProblems), [fields, drafts, openProblems]);
  const figures = fields.filter((f) => savesFigure(f, drafts[f.ordinal] ?? {})).length;
  const visible = useMemo(
    () =>
      fields.filter(
        (f) =>
          matchesFilter(f, drafts, openProblems, filter) &&
          !collapsed.has(SECTIONS.find((s) => extraction?.[s].includes(f)) ?? "summary"),
      ),
    [fields, drafts, openProblems, filter, collapsed, extraction],
  );
  const decisionsByOrdinal = useMemo(() => {
    const out: Record<number, DecisionKind | undefined> = {};
    if (review) for (const d of review.decisions) out[d.ordinal] = d.decision;
    else for (const f of fields) out[f.ordinal] = drafts[f.ordinal]?.decision;
    return out;
  }, [review, fields, drafts]);

  const update = useCallback((ordinal: number, change: Partial<Draft>) => {
    setDrafts((current) => ({ ...current, [ordinal]: { ...current[ordinal], ...change } }));
  }, []);

  /** Select a field and bring its row into view (the PDF scrolls to the box by itself). */
  const select = useCallback((ordinal: number | null) => {
    setSelected(ordinal);
    if (ordinal === null) return;
    requestAnimationFrame(() =>
      window.document.querySelector(`[data-field="${ordinal}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }),
    );
  }, []);

  const order = useMemo(() => visible.map((f) => f.ordinal), [visible]);
  const moveNext = useCallback(() => select(step(order, selected, 1)), [order, selected, select]);

  // A link from a check's evidence (?field=<ordinal>) opens with that field selected and its
  // box highlighted, once the fields (or the saved review) have loaded.
  const linkedField = useRef(false);
  useEffect(() => {
    if (linkedField.current || (fields.length === 0 && !review)) return;
    linkedField.current = true;
    const field = Number(new URLSearchParams(window.location.search).get("field"));
    if (Number.isInteger(field) && field > 0) setTimeout(() => select(field), 300);
  }, [fields, review, select]);

  // Keyboard shortcuts (not while typing, and not while a dialog is open).
  useEffect(() => {
    if (review || !extraction) return;
    const onKey = (event: KeyboardEvent) => {
      if (isTypingTarget(event.target as HTMLElement | null)) return;
      if (window.document.querySelector('[role="dialog"]')) return;
      const action = shortcutFor(event);
      if (!action) return;
      event.preventDefault();
      if (action === "help") return setHelpOpen(true);
      if (action === "next") return select(step(order, selected, 1));
      if (action === "previous") return select(step(order, selected, -1));
      const target = selected ?? order[0] ?? null;
      if (target === null) return;
      update(target, { decision: action });
      if (action === "accept") {
        select(step(order, target, 1));
      } else {
        select(target);
        requestAnimationFrame(() =>
          window.document.querySelector<HTMLInputElement>(`[data-field="${target}"] [data-first-input]`)?.focus(),
        );
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [review, extraction, order, selected, select, update]);

  async function addIncomeSource() {
    if (!document?.applicant_id) return;
    const result = await postJson<IncomeSource>(`cases/${caseId}/financial/income-sources`, {
      applicant_id: document.applicant_id,
      kind: "employment",
    });
    if (result.ok) {
      toast("success", "Employment income source added.");
      await load();
    } else {
      setSaveError(problemMessage(result.status, result.problem));
    }
  }

  async function save() {
    if (!document || !incomeSourceId) return;
    setSaving(true);
    setSaveError(null);
    const body = buildReview(fields, drafts, incomeSourceId, frequency || undefined);
    const result = await postJson<ReviewOut>(`${base}/review`, body, document.version);
    setSaving(false);
    if (result.ok) {
      setSaveOpen(false);
      setReview(result.data);
      toast("success", `Review saved. ${figures} figure${figures === 1 ? "" : "s"} added to the case.`);
      await load();
      return;
    }
    if (result.status === 409) {
      setSaveOpen(false);
      toast("error", problemMessage(result.status, result.problem));
      await load(); // show the review that already exists
      return;
    }
    setSaveError(problemMessage(result.status, result.problem));
  }

  const crumbs = [
    { label: "Cases", href: "/" },
    { label: caseTitle(summary, caseId), href: `/cases/${caseId}` },
    { label: document?.original.original_filename ?? "Payslip" },
  ];

  if (loadError) {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <Alert tone="error" title="This payslip can't be shown">
          {loadError}
        </Alert>
      </PageContainer>
    );
  }
  if (!document) return <Loading />;

  const reviewedSource = review ? allSources.find((s) => s.id === review.income_source_id) : undefined;

  return (
    <main className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-[1600px] flex-col px-6 pt-4 pb-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Breadcrumbs items={crumbs} />
          <h1 className="flex items-center gap-3 text-xl font-semibold tracking-tight">
            {review ? "Saved review" : "Review payslip"}
            {applicantName && <span className="text-base font-normal text-slate-500">{applicantName}</span>}
          </h1>
        </div>
        {extraction && (
          <p className="text-xs text-slate-500">
            Extracted by <span className="font-medium text-slate-700">{extraction.provider}</span> ·{" "}
            {extraction.schema_version}
            {extraction.provider === "synthetic-fixture" && " · synthetic test data"} · confidence is an indicator
            only
          </p>
        )}
      </div>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <div className="min-h-0 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <PdfView
            url={`${API}/${base}/content`}
            highlights={highlights}
            selected={selected}
            hovered={hovered}
            decisions={decisionsByOrdinal}
            onSelect={select}
            onHover={setHovered}
          />
        </div>

        <div className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
          {review ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <SavedReview
                review={review}
                sourceName={reviewedSource ? sourceName(reviewedSource) : "the income source"}
                selected={selected}
                hovered={hovered}
                onSelect={select}
                onHover={setHovered}
              />
            </div>
          ) : !extraction ? (
            <div className="p-4">
              <Alert tone="info">No extracted figures for this document yet.</Alert>
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-3 border-b border-slate-200 bg-white p-4">
                <div className="flex items-center gap-3">
                  <Progress value={counts.complete} max={counts.total} label="Fields decided" className="flex-1" />
                  <span className="text-sm font-medium tabular-nums text-slate-700">
                    {counts.complete} of {counts.total} decided
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="success">
                    <Check /> {counts.accepted}
                  </Badge>
                  <Badge tone="warning">
                    <Pencil /> {counts.corrected}
                  </Badge>
                  <Badge tone="danger">
                    <X /> {counts.rejected}
                  </Badge>
                  <div className="ml-auto flex items-center gap-1.5">
                    <Button
                      size="sm"
                      variant="secondary"
                      disabled={counts.accepted + counts.corrected + counts.rejected === counts.total}
                      onClick={() => setDrafts((d) => acceptRemaining(fields, d))}
                    >
                      <Check /> Accept remaining
                    </Button>
                    <Tooltip content="Keyboard shortcuts (?)">
                      <Button size="icon" variant="ghost" onClick={() => setHelpOpen(true)} aria-label="Keyboard shortcuts">
                        <Keyboard />
                      </Button>
                    </Tooltip>
                  </div>
                </div>
                <div className="flex gap-1 rounded-lg bg-slate-100 p-1 text-sm" role="tablist" aria-label="Show fields">
                  {FILTERS.map((f) => (
                    <button
                      key={f.key}
                      role="tab"
                      aria-selected={filter === f.key}
                      onClick={() => setFilter(f.key)}
                      className={cn(
                        "flex-1 rounded-md px-2 py-1 transition-colors",
                        filter === f.key ? "bg-white font-medium text-slate-900 shadow-xs" : "text-slate-600 hover:text-slate-900",
                      )}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-auto p-4">
                {extraction.missing_required.length > 0 && (
                  <Alert tone="warning" title="Not found on the payslip" className="mb-4">
                    {extraction.missing_required.map(measureLabel).join(", ")}
                  </Alert>
                )}
                <div className="flex flex-col gap-5">
                  {SECTIONS.map((section) => {
                    const all = extraction[section];
                    if (all.length === 0) return null;
                    const shown = all.filter((f) => matchesFilter(f, drafts, openProblems, filter));
                    const done = all.filter((f) => !openProblems.has(f.ordinal)).length;
                    const isCollapsed = collapsed.has(section);
                    if (shown.length === 0 && filter !== "all") return null;
                    return (
                      <section key={section} className="flex flex-col gap-2">
                        <button
                          type="button"
                          aria-expanded={!isCollapsed}
                          onClick={() =>
                            setCollapsed((c) => {
                              const next = new Set(c);
                              if (next.has(section)) next.delete(section);
                              else next.add(section);
                              return next;
                            })
                          }
                          className="flex items-center gap-2 text-left text-xs font-semibold tracking-wide text-slate-500 uppercase hover:text-slate-800"
                        >
                          <ChevronDown className={cn("size-4 transition-transform", isCollapsed && "-rotate-90")} />
                          {SECTION_TITLES[section]}
                          <span
                            className={cn(
                              "rounded-full px-1.5 py-px font-medium tabular-nums normal-case",
                              done === all.length ? "bg-accept-soft text-accept" : "bg-slate-200 text-slate-600",
                            )}
                          >
                            {done}/{all.length}
                          </span>
                        </button>
                        {!isCollapsed &&
                          shown.map((f) => (
                            <FieldRow
                              key={f.ordinal}
                              field={f}
                              draft={drafts[f.ordinal] ?? {}}
                              problem={openProblems.get(f.ordinal) ?? null}
                              selected={selected === f.ordinal}
                              hovered={hovered === f.ordinal}
                              measures={measures}
                              onSelect={() => select(f.ordinal)}
                              onHover={(on) => setHovered(on ? f.ordinal : null)}
                              onChange={(change) => update(f.ordinal, change)}
                              onNext={moveNext}
                            />
                          ))}
                      </section>
                    );
                  })}
                  {visible.length === 0 && filter !== "all" && (
                    <p className="py-8 text-center text-sm text-slate-500">
                      {filter === "undecided" ? "Every field has a complete decision." : "Nothing corrected or rejected."}
                    </p>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-3 border-t border-slate-200 bg-white p-4">
                <p className="flex-1 text-sm text-slate-600">
                  {openProblems.size > 0
                    ? `${openProblems.size} field${openProblems.size === 1 ? "" : "s"} still need${openProblems.size === 1 ? "s" : ""} a complete decision.`
                    : `Ready to save ${figures} figure${figures === 1 ? "" : "s"}.`}
                </p>
                <Button
                  size="lg"
                  disabled={openProblems.size > 0}
                  onClick={() => {
                    setSaveError(null);
                    setSaveOpen(true);
                  }}
                >
                  Review and save…
                </Button>
              </div>

              <SaveReviewDialog
                open={saveOpen}
                onOpenChange={setSaveOpen}
                counts={counts}
                figures={figures}
                sources={sources}
                sourceName={sourceName}
                incomeSourceId={incomeSourceId}
                onIncomeSource={setIncomeSourceId}
                frequency={frequency}
                onFrequency={setFrequency}
                payslipFrequency={payslipFrequency(fields, drafts)}
                onAddSource={() => void addIncomeSource()}
                saving={saving}
                error={saveError}
                onSave={() => void save()}
              />
            </>
          )}
        </div>
      </div>
      <ShortcutsDialog open={helpOpen} onOpenChange={setHelpOpen} />
    </main>
  );
}
