"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { type Highlight, PdfView } from "@/components/PdfView";
import { API, get, postJson } from "@/lib/api";
import {
  acceptAll,
  buildReview,
  type Draft,
  type Drafts,
  FIGURE_CODES,
  formatMoney,
  problems,
  QUALIFIED,
  savesFigure,
  valueKind,
} from "@/lib/decisions";
import { problemMessage } from "@/lib/problems";
import {
  type DecisionKind,
  type DocumentOut,
  type ExtractedField,
  type Extraction,
  type IncomeSource,
  type Measure,
  type ReviewOut,
  type Section,
  SECTIONS,
} from "@/lib/types";

const SECTION_TITLES: Record<Section, string> = {
  summary: "Summary",
  payments: "Payments",
  deductions: "Deductions",
  year_to_date: "Year to date",
};
const FREQUENCIES = ["weekly", "fortnightly", "four_weekly", "monthly", "quarterly", "annual"];

function shownValue(f: ExtractedField): string {
  const kind = valueKind(f);
  if (kind === "money") return formatMoney(f.value_money_minor);
  if (kind === "date") return f.value_date ?? "—";
  return f.value_text ?? "—";
}

export default function ReviewPage() {
  const { caseId, documentId } = useParams<{ caseId: string; documentId: string }>();
  const base = `cases/${caseId}/documents/${documentId}`;
  const [document, setDocument] = useState<DocumentOut | null>(null);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [review, setReview] = useState<ReviewOut | null>(null);
  const [sources, setSources] = useState<IncomeSource[]>([]);
  const [measures, setMeasures] = useState<Measure[]>([]);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [selected, setSelected] = useState<number | null>(null);
  const [incomeSourceId, setIncomeSourceId] = useState("");
  const [frequency, setFrequency] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const d = await get<DocumentOut>(base);
    if (!d.ok) {
      setMessage(problemMessage(d.status, d.problem));
      return;
    }
    setDocument(d.data);
    const [x, s, m] = await Promise.all([
      get<Extraction>(`${base}/extraction`),
      get<IncomeSource[]>(`cases/${caseId}/financial/income-sources`),
      get<Measure[]>("financial/measures"),
    ]);
    if (x.ok) setExtraction(x.data);
    if (s.ok) {
      const usable = s.data.filter(
        (src) =>
          !src.withdrawn_at && (!d.data.applicant_id || src.applicant_id === d.data.applicant_id),
      );
      setSources(usable);
      setIncomeSourceId((current) => current || usable[0]?.id || "");
    }
    if (m.ok) {
      setMeasures(m.data.filter((x) => x.subject_kind === "income_source" && x.value_type === "money"));
    }
    if (d.data.status === "reviewed") {
      const r = await get<ReviewOut>(`${base}/review`);
      if (r.ok) setReview(r.data);
    }
  }, [base, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

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
  const figures = fields.filter((f) => savesFigure(f, drafts[f.ordinal] ?? {})).length;

  function update(ordinal: number, change: Partial<Draft>) {
    setDrafts((current) => ({ ...current, [ordinal]: { ...current[ordinal], ...change } }));
  }

  async function save() {
    if (!document || !incomeSourceId) return;
    setSaving(true);
    setMessage(null);
    const body = buildReview(fields, drafts, incomeSourceId, frequency || undefined);
    const result = await postJson<ReviewOut>(`${base}/review`, body, document.version);
    setSaving(false);
    if (result.ok) {
      setReview(result.data);
      await load();
      return;
    }
    setMessage(problemMessage(result.status, result.problem));
    if (result.status === 409) await load(); // show the review that already exists
  }

  if (!document) return <main>{message ? <p className="error">{message}</p> : "Loading…"}</main>;

  return (
    <main>
      <p>
        <Link href={`/cases/${caseId}`}>← Case</Link>
      </p>
      <h1>{review ? "Saved review" : "Review payslip"}</h1>
      {extraction && (
        <p className="note">
          Extracted by <strong>{extraction.provider}</strong> ({extraction.schema_version}).
          {extraction.provider === "synthetic-fixture" && " Synthetic test data."} Confidence
          is an indicator from the provider and never approves a value.
        </p>
      )}
      {message && <p className="error" role="alert">{message}</p>}

      <div className="review">
        <div className="viewer">
          <PdfView
            url={`${API}/${base}/content`}
            highlights={highlights}
            selected={selected}
            onSelect={setSelected}
          />
        </div>

        <div>
          {review ? (
            <SavedReview review={review} onSelect={setSelected} selected={selected} />
          ) : !extraction ? (
            <p className="note">No extraction for this document.</p>
          ) : (
            <>
              {extraction.missing_required.length > 0 && (
                <p className="warn">
                  Not found on the payslip: {extraction.missing_required.join(", ")}.
                </p>
              )}
              <div className="row" style={{ marginBottom: "0.5rem" }}>
                <button className="secondary" onClick={() => setDrafts((d) => acceptAll(fields, d))}>
                  Accept all
                </button>
                <span className="note">
                  {fields.length - openProblems.size} of {fields.length} fields decided
                </span>
              </div>
              {SECTIONS.map((section) =>
                extraction[section].length === 0 ? null : (
                  <section key={section}>
                    <h3>{SECTION_TITLES[section]}</h3>
                    {extraction[section].map((f) => (
                      <FieldRow
                        key={f.ordinal}
                        field={f}
                        draft={drafts[f.ordinal] ?? {}}
                        problem={openProblems.get(f.ordinal) ?? null}
                        selected={selected === f.ordinal}
                        measures={measures}
                        onSelect={() => setSelected(f.ordinal)}
                        onChange={(change) => update(f.ordinal, change)}
                      />
                    ))}
                  </section>
                ),
              )}
              <div className="savebar">
                <label>
                  Income source
                  <select value={incomeSourceId} onChange={(e) => setIncomeSourceId(e.target.value)}>
                    {sources.length === 0 && <option value="">None: add one on the case page</option>}
                    {sources.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name ?? s.kind}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Frequency
                  <select value={frequency} onChange={(e) => setFrequency(e.target.value)}>
                    <option value="">As shown on the payslip</option>
                    {FREQUENCIES.map((f) => (
                      <option key={f} value={f}>
                        {f.replace("_", "-")}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  onClick={save}
                  disabled={saving || openProblems.size > 0 || !incomeSourceId}
                  title={openProblems.size > 0 ? "Every field needs a complete decision" : undefined}
                >
                  Save review ({figures} figure{figures === 1 ? "" : "s"})
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </main>
  );
}

function FieldRow(props: {
  field: ExtractedField;
  draft: Draft;
  problem: string | null;
  selected: boolean;
  measures: Measure[];
  onSelect: () => void;
  onChange: (change: Partial<Draft>) => void;
}) {
  const { field: f, draft, problem, selected, measures, onSelect, onChange } = props;
  const kind = valueKind(f);
  const effectiveMeasure = f.field_code && FIGURE_CODES.has(f.field_code) ? f.field_code : draft.measure;
  const qualifiers = measures.find((m) => m.code === effectiveMeasure)?.qualifiers ?? [];
  const choices: DecisionKind[] = ["accept", "correct", "reject"];

  return (
    <div className={`field${selected ? " selected" : ""}`} data-field={f.ordinal}>
      <div className="head" onClick={onSelect}>
        <div>
          <div className="label">{f.label_text}</div>
          <div className="meta">
            {f.field_code ?? "unrecognised line"} · printed “{f.raw_text}” · page{" "}
            {f.value_evidence.page_number}
            {f.confidence !== null && ` · confidence ${(f.confidence * 100).toFixed(0)}% (indicator)`}
          </div>
        </div>
        <div className="value">{shownValue(f)}</div>
      </div>
      <div className="choices" role="radiogroup" aria-label={`Decision for ${f.label_text}`}>
        {choices.map((choice) => (
          <label key={choice}>
            <input
              type="radio"
              name={`decision-${f.ordinal}`}
              checked={draft.decision === choice}
              onChange={() => onChange({ decision: choice })}
            />
            {choice[0]?.toUpperCase() + choice.slice(1)}
          </label>
        ))}
      </div>
      {draft.decision === "correct" && (
        <div className="row" style={{ marginTop: "0.4rem" }}>
          <input
            type={kind === "date" ? "date" : "text"}
            inputMode={kind === "money" ? "decimal" : undefined}
            placeholder={kind === "money" ? "1,234.56" : "Corrected value"}
            value={draft.corrected ?? ""}
            onChange={(e) => onChange({ corrected: e.target.value })}
            aria-label="Corrected value"
          />
        </div>
      )}
      {(draft.decision === "correct" || draft.decision === "reject") && (
        <div className="row" style={{ marginTop: "0.4rem" }}>
          <input
            style={{ flex: 1 }}
            placeholder="Reason"
            value={draft.reason ?? ""}
            maxLength={500}
            onChange={(e) => onChange({ reason: e.target.value })}
            aria-label="Reason"
          />
        </div>
      )}
      {draft.decision !== "reject" && f.field_code === null && (
        <label style={{ marginTop: "0.4rem" }}>
          Save as (optional)
          <select value={draft.measure ?? ""} onChange={(e) => onChange({ measure: e.target.value || undefined })}>
            <option value="">Don&apos;t save as a figure</option>
            {measures.map((m) => (
              <option key={m.code} value={m.code}>
                {m.code}
              </option>
            ))}
          </select>
        </label>
      )}
      {draft.decision !== "reject" && effectiveMeasure && QUALIFIED.has(effectiveMeasure) && (
        <label style={{ marginTop: "0.4rem" }}>
          Type
          <select value={draft.qualifier ?? ""} onChange={(e) => onChange({ qualifier: e.target.value || undefined })}>
            <option value="">Unspecified</option>
            {qualifiers
              .filter((q) => q !== "unspecified")
              .map((q) => (
                <option key={q} value={q}>
                  {q}
                </option>
              ))}
          </select>
        </label>
      )}
      {problem && draft.decision && <div className="problem">{problem}</div>}
    </div>
  );
}

function SavedReview(props: {
  review: ReviewOut;
  selected: number | null;
  onSelect: (ordinal: number) => void;
}) {
  const { review, selected, onSelect } = props;
  const show = (money: number | null, date: string | null, text: string | null) =>
    money !== null ? formatMoney(money) : (date ?? text ?? "—");
  return (
    <div>
      <p className="ok">
        Saved {new Date(review.created_at).toLocaleString("en-GB")} · frequency {review.frequency} ·{" "}
        {review.decisions.filter((d) => d.observation_id).length} figures saved. Later corrections
        go through the case&apos;s financial records.
      </p>
      <table>
        <thead>
          <tr>
            <th>Field</th>
            <th>Extracted</th>
            <th>Decision</th>
            <th>Saved figure</th>
          </tr>
        </thead>
        <tbody>
          {review.decisions.map((d) => (
            <tr
              key={d.ordinal}
              onClick={() => onSelect(d.ordinal)}
              style={selected === d.ordinal ? { background: "#eef4fb" } : undefined}
            >
              <td>
                {d.label_text}
                <div className="note">{d.field_code ?? "unrecognised line"}</div>
              </td>
              <td>{show(d.extracted_money_minor, d.extracted_date, d.extracted_text)}</td>
              <td>
                {d.decision}
                {d.decision === "correct" && (
                  <div>→ {show(d.corrected_money_minor, d.corrected_date, d.corrected_text)}</div>
                )}
                {d.reason && <div className="note">“{d.reason}”</div>}
              </td>
              <td>{d.observation_id ? `yes${d.measure ? ` (${d.measure})` : ""}` : "no"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
