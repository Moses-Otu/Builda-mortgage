"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { get, postJson, uploadPdf } from "@/lib/api";
import { problemMessage } from "@/lib/problems";
import type { Applicant, DocumentOut, IncomeSource } from "@/lib/types";

const PROCESSING = new Set(["received", "ready"]);
const STATUS_TEXT: Record<string, string> = {
  received: "Processing",
  ready: "Extracting",
  extracted: "Ready to review",
  unrecognised: "Not recognised as a payslip",
  reviewed: "Reviewed",
  failed: "Failed",
};

export default function CasePage() {
  const { caseId } = useParams<{ caseId: string }>();
  const [applicants, setApplicants] = useState<Applicant[]>([]);
  const [sources, setSources] = useState<IncomeSource[]>([]);
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [applicantId, setApplicantId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [a, s, d] = await Promise.all([
      get<Applicant[]>(`cases/${caseId}/applicants`),
      get<IncomeSource[]>(`cases/${caseId}/financial/income-sources`),
      get<DocumentOut[]>(`cases/${caseId}/documents`),
    ]);
    if (!a.ok) {
      setMessage({ kind: "error", text: problemMessage(a.status, a.problem) });
      return;
    }
    setApplicants(a.data);
    setApplicantId((current) => current || a.data[0]?.id || "");
    if (s.ok) setSources(s.data);
    if (d.ok) setDocuments(d.data);
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Refresh while any document is still being processed.
  useEffect(() => {
    if (!documents.some((d) => PROCESSING.has(d.status))) return;
    const timer = setInterval(() => void load(), 3000);
    return () => clearInterval(timer);
  }, [documents, load]);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || !applicantId) return;
    setBusy(true);
    const result = await uploadPdf<DocumentOut>(caseId, file, applicantId);
    setBusy(false);
    if (result.ok) {
      setMessage({ kind: "ok", text: "Uploaded. Processing has started." });
      setFile(null);
      (event.target as HTMLFormElement).reset();
      await load();
    } else {
      setMessage({ kind: "error", text: problemMessage(result.status, result.problem) });
    }
  }

  async function addIncomeSource() {
    if (!applicantId) return;
    const result = await postJson<IncomeSource>(`cases/${caseId}/financial/income-sources`, {
      applicant_id: applicantId,
      kind: "employment",
    });
    if (result.ok) {
      setMessage({ kind: "ok", text: "Employment income source added." });
      await load();
    } else {
      setMessage({ kind: "error", text: problemMessage(result.status, result.problem) });
    }
  }

  const nameOf = (id: string | null) => {
    const a = applicants.find((x) => x.id === id);
    return a ? `${a.first_name} ${a.last_name}` : "—";
  };

  return (
    <main>
      <p>
        <Link href="/">← Cases</Link>
      </p>
      <h1>Case</h1>
      {message && (
        <p className={message.kind} role="status">
          {message.text}
        </p>
      )}

      <div className="row" style={{ alignItems: "stretch" }}>
        <form className="card stack" onSubmit={upload} style={{ flex: "1 1 320px" }}>
          <h2 style={{ marginTop: 0 }}>Upload a payslip</h2>
          <label>
            Applicant
            <select value={applicantId} onChange={(e) => setApplicantId(e.target.value)}>
              {applicants.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.first_name} {a.last_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            PDF (synthetic documents only)
            <input
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <button type="submit" disabled={!file || !applicantId || busy}>
            Upload
          </button>
        </form>

        <div className="card stack" style={{ flex: "1 1 320px" }}>
          <h2 style={{ marginTop: 0 }}>Income sources</h2>
          {sources.filter((s) => !s.withdrawn_at).length === 0 && (
            <p className="note">None yet. A payslip is evidence of an income source.</p>
          )}
          <ul>
            {sources
              .filter((s) => !s.withdrawn_at)
              .map((s) => (
                <li key={s.id}>
                  {s.name ?? s.kind} — {nameOf(s.applicant_id)}
                </li>
              ))}
          </ul>
          <button className="secondary" onClick={addIncomeSource} disabled={!applicantId}>
            Add employment income source for the selected applicant
          </button>
        </div>
      </div>

      <h2>Documents</h2>
      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Applicant</th>
            <th>Status</th>
            <th>Uploaded</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {documents.length === 0 && (
            <tr>
              <td colSpan={5} className="note">
                No documents yet.
              </td>
            </tr>
          )}
          {documents.map((d) => (
            <tr key={d.id}>
              <td>{d.original.original_filename ?? "document.pdf"}</td>
              <td>{nameOf(d.applicant_id)}</td>
              <td>
                <span className={`status ${d.status}`}>{STATUS_TEXT[d.status] ?? d.status}</span>
                {d.failure_code && <span className="note"> ({d.failure_code})</span>}
              </td>
              <td>{new Date(d.created_at).toLocaleString("en-GB")}</td>
              <td>
                {(d.status === "extracted" || d.status === "reviewed") && (
                  <Link href={`/cases/${caseId}/documents/${d.id}`}>
                    {d.status === "extracted" ? "Review" : "View review"}
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
