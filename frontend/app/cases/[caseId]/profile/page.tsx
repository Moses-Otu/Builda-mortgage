"use client";

import { Briefcase, ExternalLink, FileText, History, Landmark, ListChecks, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ChecksCard } from "@/components/ChecksCard";
import { JourneyBar, useJourney } from "@/components/journey/JourneyBar";
import { Breadcrumbs, Loading, PageContainer, RequireFirm } from "@/components/Page";
import { PositionSummary } from "@/components/profile/PositionSummary";
import { ApprovalPanel } from "@/components/profile/ApprovalPanel";
import { caseTitle, useCaseSummary } from "@/components/useCaseSummary";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tooltip } from "@/components/ui/tooltip";
import { get } from "@/lib/api";
import { checksSummary, groupFindings } from "@/lib/checks";
import { documentStatusView } from "@/lib/failures";
import { formatDate, formatDateTime, measureLabel, roleLabel } from "@/lib/labels";
import {
  approvalText,
  corroborationLine,
  documentaryLine,
  evidenceHref,
  figureScope,
  figuresFor,
  formatValue,
  NONE_RECORDED,
  ORIGINS,
  originCounts,
  originSentence,
  periodTable,
  periodText,
  SUPPORT_KINDS,
  undatedFigures,
} from "@/lib/profile";
import { problemMessage } from "@/lib/problems";
import { accountTitle, holdersText } from "@/lib/statements";
import type { Approval, ApprovalDetail, Figure, ItemSupport, Origin, Profile, ProfileItem } from "@/lib/types";
import { cn } from "@/lib/utils";

export default function ProfileRoute() {
  const { caseId } = useParams<{ caseId: string }>();
  return <RequireFirm>{() => <ProfilePage caseId={caseId} />}</RequireFirm>;
}

const ORIGIN_TONE: Record<Origin, string> = {
  extracted: "bg-slate-100 text-slate-700",
  corrected: "bg-correct-soft text-correct",
  declared: "bg-brand-50 text-brand-700",
  broker_entry: "bg-violet-50 text-violet-700",
};

function OriginMark({ origin }: { origin: Origin }) {
  const o = ORIGINS[origin];
  return (
    <Tooltip content={`${o.label}: ${o.description}`}>
      <span
        tabIndex={0}
        aria-label={o.label}
        className={cn("inline-grid size-4 place-items-center rounded text-[10px] font-semibold", ORIGIN_TONE[origin])}
      >
        {o.mark}
      </span>
    </Tooltip>
  );
}

function ProfilePage({ caseId }: { caseId: string }) {
  const summary = useCaseSummary(caseId);
  const router = useRouter();
  const { journey, reload: reloadJourney } = useJourney(caseId);
  const [live, setLive] = useState<Profile | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [viewing, setViewing] = useState<ApprovalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [result, list] = await Promise.all([
      get<Profile>(`cases/${caseId}/profile`),
      get<Approval[]>(`cases/${caseId}/profile/approvals`),
    ]);
    if (!result.ok) {
      setError(problemMessage(result.status, result.problem));
      return;
    }
    setLive(result.data);
    setError(null);
    void reloadJourney();
    const all = list.ok ? list.data : [];
    setApprovals(all);
    // ?version=n opens that approval's stored snapshot, read-only.
    const wanted = Number(new URLSearchParams(window.location.search).get("version"));
    const match = Number.isInteger(wanted) && wanted > 0 ? all.find((a) => a.version === wanted) : undefined;
    if (match) {
      const detail = await get<ApprovalDetail>(`cases/${caseId}/profile/approvals/${match.id}`);
      if (detail.ok) setViewing(detail.data);
      else setError(problemMessage(detail.status, detail.problem));
    } else setViewing(null);
  }, [caseId, reloadJourney]);

  // A stored version renders exactly as approved, with its own approval as the fifth kind.
  const profile: Profile | null = useMemo(() => {
    if (!viewing || !live) return live;
    return {
      ...viewing.snapshot,
      readiness: live.readiness,
      approval: {
        status: "approved",
        approval_id: viewing.id,
        version: viewing.version,
        approved_by: viewing.approved_by,
        approved_at: viewing.approved_at,
        superseded: [],
      },
      fingerprint: viewing.snapshot_sha256,
    };
  }, [viewing, live]);

  useEffect(() => {
    void load();
  }, [load]);

  const title = caseTitle(summary, caseId);
  const crumbs = [
    { label: "Cases", href: "/" },
    { label: title, href: `/cases/${caseId}` },
    { label: "Review and approve profile" },
  ];
  const figure = profile?.figures.find((f) => f.id === selected) ?? null;
  const nameOf = useCallback(
    (id: string) => {
      const a = profile?.applicants.find((x) => x.id === id);
      return a ? `${a.first_name} ${a.last_name}` : "an applicant";
    },
    [profile],
  );
  const counts = useMemo(() => (profile ? originCounts(profile.figures) : null), [profile]);

  if (error) {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <Alert tone="error">{error}</Alert>
      </PageContainer>
    );
  }
  if (!profile || !counts) return <Loading />;

  const checks = groupFindings(profile.checks.findings);
  return (
    <PageContainer wide>
      <Breadcrumbs items={crumbs} />
      <JourneyBar caseId={caseId} journey={journey} showing="profile" />
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Financial profile</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-600">
            {viewing
              ? `The profile exactly as approved in version ${viewing.version}. Read-only; select a figure to see its source.`
              : "Every current figure on the case, with where it came from. Nothing is chosen, added up, averaged or annualised. Select a figure to see its source."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
          {(Object.keys(ORIGINS) as Origin[]).map((o) => (
            <span key={o} className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2 py-0.5">
              <OriginMark origin={o} /> {ORIGINS[o].label} · {counts[o]}
            </span>
          ))}
          <Tooltip content="The profile's fingerprint (SHA-256). Approving will record exactly this version.">
            <span tabIndex={0} className="font-mono text-slate-400">
              {profile.rules} · {profile.fingerprint.slice(0, 12)}
            </span>
          </Tooltip>
        </div>
      </div>

      {!viewing && (
        <Alert tone="info" title="Approving the profile is not approving the mortgage">
          Approval confirms that the evidence and figures below are complete and correct. The mortgage assessment then
          starts by itself, and the lending recommendation and final decision come after it.
        </Alert>
      )}
      {live && (
        <div className="my-6">
          <ApprovalPanel
            caseId={caseId}
            profile={live}
            approvals={approvals}
            viewing={viewing}
            onChanged={() => void load()}
            onApproved={() => router.push(`/cases/${caseId}/assessment`)}
          />
        </div>
      )}
      {!viewing && (
        <div className="mb-6 flex flex-col gap-6">
          {(live?.checks.findings ?? []).some((f) => f.status === "discrepancy") && (
            <ChecksCard caseId={caseId} refreshKey={live?.fingerprint ?? ""} />
          )}
          <PositionSummary caseId={caseId} refreshKey={live?.fingerprint} />
        </div>
      )}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex min-w-0 flex-col gap-6">
          {profile.applicants
            .filter((a) => a.status !== "removed")
            .map((a) => {
              const sources = profile.income_sources.filter((s) => s.applicant_id === a.id);
              return (
                <Card key={a.id}>
                  <CardHeader>
                    <div>
                      <CardTitle className="flex items-center gap-2">
                        <UserRound className="size-4 text-slate-400" /> {a.first_name} {a.last_name}
                      </CardTitle>
                      <CardDescription>{roleLabel(a.party_role)}</CardDescription>
                    </div>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-5">
                    {sources.length === 0 && <p className="text-sm text-slate-500">No income sources.</p>}
                    {sources.map((s) => (
                      <div key={s.id} className="flex flex-col gap-3">
                        <ItemFigures
                          title={s.name ?? "Employment income"}
                          icon={<Briefcase className="size-4" />}
                          figures={figuresFor(profile.figures, s.id)}
                          selected={selected}
                          onSelect={setSelected}
                        />
                        <SupportedBy caseId={caseId} profile={profile} itemId={s.id} nameOf={nameOf} onSelect={setSelected} />
                      </div>
                    ))}
                  </CardContent>
                </Card>
              );
            })}

          {(profile.commitments.length > 0 || profile.expenditure.length > 0) && (
            <Card>
              <CardHeader>
                <CardTitle>Commitments and expenditure</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-5">
                {[...profile.commitments, ...profile.expenditure].map((item: ProfileItem) => (
                  <div key={item.id} className="flex flex-col gap-3">
                    <ItemFigures
                      title={item.creditor ?? item.description ?? measureLabel(item.kind ?? item.category ?? "item")}
                      icon={<FileText className="size-4" />}
                      figures={figuresFor(profile.figures, item.id)}
                      selected={selected}
                      onSelect={setSelected}
                    />
                    <SupportedBy caseId={caseId} profile={profile} itemId={item.id} nameOf={nameOf} onSelect={setSelected} />
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          {profile.bank_accounts.length > 0 && (
            <Card>
              <CardHeader>
                <div>
                  <CardTitle>Bank accounts</CardTitle>
                  <CardDescription>Statements confirmed to each account. Transactions are evidence only, never figures.</CardDescription>
                </div>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                {profile.bank_accounts.map((acc) => (
                  <div key={acc.id} className="flex items-center gap-3 rounded-lg border border-slate-200 px-3 py-2 text-sm">
                    <Landmark className="size-4 text-slate-400" />
                    <span className="font-medium">{accountTitle(acc)}</span>
                    <span className="text-slate-500">{holdersText(acc, nameOf)}</span>
                    <span className="ml-auto text-slate-500">
                      {acc.statement_document_ids.length} statement{acc.statement_document_ids.length === 1 ? "" : "s"}
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <ListChecks className="size-4 text-slate-400" /> Checks
                </CardTitle>
                <CardDescription>
                  {checksSummary(checks)} ·{" "}
                  <Link href={`/cases/${caseId}`} className="text-brand-700 hover:underline">
                    see them on the case
                  </Link>
                </CardDescription>
              </div>
            </CardHeader>
          </Card>

          <Card>
            <CardHeader>
              <div>
                <CardTitle>Documents</CardTitle>
                <CardDescription>Every document on the case, including any not relied on.</CardDescription>
              </div>
            </CardHeader>
            <CardContent>
              <ul className="divide-y divide-slate-100 text-sm">
                {profile.documents.map((d) => {
                  const view = documentStatusView(d.status, d.failure_code);
                  return (
                    <li key={d.id} className="flex flex-wrap items-center gap-3 py-2">
                      <FileText className="size-4 text-slate-400" />
                      <Link href={`/cases/${caseId}/documents/${d.id}`} className="font-medium hover:text-brand-700">
                        {d.original.original_filename ?? "Document"}
                      </Link>
                      <span className="text-slate-500">{d.document_type === "bank_statement" ? "Bank statement" : "Payslip"}</span>
                      {d.applicant_id && <span className="text-slate-500">{nameOf(d.applicant_id)}</span>}
                      <Badge tone={view.tone} className="ml-auto">
                        {view.label}
                      </Badge>
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
        </div>

        <aside className="lg:sticky lg:top-20 lg:self-start">
          <ProvenancePanel caseId={caseId} figure={figure} onClose={() => setSelected(null)} />
        </aside>
      </div>
    </PageContainer>
  );
}

function ItemFigures(props: {
  title: string;
  icon: React.ReactNode;
  figures: Figure[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { title, icon, figures, selected, onSelect } = props;
  const table = periodTable(figures);
  const undated = undatedFigures(figures);
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-800">
        <span className="text-brand-600">{icon}</span> {title}
      </h3>
      {figures.length === 0 && <p className="text-sm text-slate-500">No current figures.</p>}
      {table.rows.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-3 py-2 font-medium">Period</th>
                {table.columns.map((c) => (
                  <th key={c.key} className="px-3 py-2 text-right font-medium whitespace-nowrap">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {table.rows.map((row) => (
                <tr key={row.key}>
                  <td className="px-3 py-1.5 whitespace-nowrap">
                    <Tooltip content={row.range}>
                      <span tabIndex={0}>{row.label}</span>
                    </Tooltip>
                  </td>
                  {table.columns.map((c) => (
                    <td key={c.key} className="px-1.5 py-1 text-right">
                      {(row.cells[c.key] ?? []).map((f) => (
                        <FigureButton key={f.id} figure={f} selected={selected === f.id} onSelect={onSelect} />
                      ))}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {undated.length > 0 && (
        <ul className="flex flex-col gap-1">
          {undated.map((f) => (
            <li key={f.id} className="flex items-center gap-2 text-sm">
              <span className="text-slate-600">{measureLabel(f.measure)}</span>
              <span className="text-xs text-slate-400">{figureScope(f)}</span>
              <span className="ml-auto">
                <FigureButton figure={f} selected={selected === f.id} onSelect={onSelect} />
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function FigureButton({ figure, selected, onSelect }: { figure: Figure; selected: boolean; onSelect: (id: string) => void }) {
  return (
    <button
      type="button"
      data-figure={figure.id}
      onClick={() => onSelect(figure.id)}
      aria-pressed={selected}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 tabular-nums hover:bg-brand-50",
        selected && "bg-brand-50 ring-2 ring-brand-500/40",
      )}
    >
      {formatValue(figure.value)}
      <OriginMark origin={figure.origin} />
    </button>
  );
}

function ProvenancePanel({ caseId, figure: f, onClose }: { caseId: string; figure: Figure | null; onClose: () => void }) {
  if (!f) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-slate-500">
          Select a figure to see where it came from, who reviewed it, and what it replaced.
        </CardContent>
      </Card>
    );
  }
  const e = f.evidence;
  const href = evidenceHref(caseId, f);
  return (
    <Card aria-label="Figure source">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            {measureLabel(f.measure)}
            <OriginMark origin={f.origin} />
          </CardTitle>
          <CardDescription>
            {formatValue(f.value)}
            {f.period_end ? ` · period ending ${formatDate(f.period_end)}` : figureScope(f) ? ` · ${figureScope(f)}` : ""}
          </CardDescription>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" className="text-slate-400 hover:text-slate-700">
          <X className="size-4" />
        </button>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p>{originSentence(f)}</p>
        {e && (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-slate-600">
            {f.origin === "corrected" && (
              <>
                <dt className="text-slate-400">As read</dt>
                <dd>{e.field.raw_text}</dd>
                <dt className="text-slate-400">Reason</dt>
                <dd>{e.correction_reason}</dd>
              </>
            )}
            <dt className="text-slate-400">Reviewed</dt>
            <dd>
              {e.reviewed_by?.name ?? "Unknown"} · {formatDateTime(e.reviewed_at)}
            </dd>
            <dt className="text-slate-400">Extraction</dt>
            <dd>
              {e.provider} · {e.schema_version}
            </dd>
            <dt className="text-slate-400">Original</dt>
            <dd className="truncate font-mono text-xs" title={e.sha256}>
              SHA-256 {e.sha256.slice(0, 16)}…
            </dd>
          </dl>
        )}
        {!e && (
          <p className="text-slate-600">
            Recorded by {f.recorded_by?.name ?? "unknown"} · {formatDateTime(f.recorded_at)}
          </p>
        )}
        {href && (
          <Link
            href={href}
            className="inline-flex items-center gap-1.5 self-start rounded-md border border-slate-300 px-2.5 py-1 font-medium text-brand-700 hover:border-brand-500"
          >
            Open evidence <ExternalLink className="size-3.5" />
          </Link>
        )}
        {f.history.length > 0 && (
          <div>
            <p className="mb-1 flex items-center gap-1.5 font-medium text-slate-700">
              <History className="size-3.5" /> Replaced
            </p>
            <ul className="flex flex-col gap-1 text-slate-600">
              {f.history.map((h) => (
                <li key={h.observation_id}>
                  {formatValue(h.value)} · {h.recorded_by?.name ?? "unknown"} · {formatDateTime(h.recorded_at)}
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="text-slate-500">
          {f.cited_by.length === 0
            ? "No check cites this figure."
            : `Cited by ${f.cited_by.length} check${f.cited_by.length === 1 ? "" : "s"}.`}
        </p>
      </CardContent>
    </Card>
  );
}

function SupportSection(props: { kind: keyof typeof SUPPORT_KINDS; children: React.ReactNode; empty: boolean }) {
  const { kind, children, empty } = props;
  const k = SUPPORT_KINDS[kind];
  return (
    <div>
      <p className="font-medium text-slate-700">
        {k.title}
        <span className="ml-2 font-normal text-slate-400">{k.note}</span>
      </p>
      {empty ? <p className="text-slate-500">{NONE_RECORDED}</p> : children}
    </div>
  );
}

/** What supports one item, in five separate kinds. Lists only: nothing here counts or scores
 * evidence, because the quantity of evidence is not a measure of reliability. */
function SupportedBy(props: {
  caseId: string;
  profile: Profile;
  itemId: string;
  nameOf: (id: string) => string;
  onSelect: (id: string) => void;
}) {
  const { caseId, profile, itemId, nameOf, onSelect } = props;
  const s: ItemSupport | undefined = profile.support.find((x) => x.item_id === itemId);
  if (!s) return null;
  const declared = profile.figures.filter((f) => s.declared.includes(f.id));
  const months = new Map(s.documentary.map((d) => [d.document_id, periodText(d.period_start, d.period_end)]));
  const { discrepancies, not_run: notRun, passed } = s.findings;
  return (
    <section aria-label="Supported by" className="rounded-lg border border-slate-200 bg-slate-50/60 p-3 text-sm">
      <p className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">Supported by</p>
      <div className="flex flex-col gap-3">
        <SupportSection kind="declared" empty={declared.length === 0}>
          <ul className="flex flex-col gap-0.5">
            {declared.map((f) => (
              <li key={f.id}>
                <button type="button" onClick={() => onSelect(f.id)} className="hover:text-brand-700">
                  {measureLabel(f.measure)} {formatValue(f.value)}
                  {figureScope(f) ? `, ${figureScope(f)}` : ""}
                </button>
              </li>
            ))}
          </ul>
        </SupportSection>
        <SupportSection kind="documentary" empty={s.documentary.length === 0}>
          <ul className="flex flex-col gap-0.5">
            {s.documentary.map((d) => (
              <li key={d.document_id}>
                <Link href={`/cases/${caseId}/documents/${d.document_id}`} className="hover:text-brand-700">
                  {documentaryLine(d)}
                </Link>
                <span className="text-slate-400">
                  {" "}
                  · reviewed by {d.reviewed_by?.name ?? "unknown"}, {formatDate(d.reviewed_at.slice(0, 10))}
                </span>
              </li>
            ))}
          </ul>
        </SupportSection>
        <SupportSection kind="corroborating" empty={s.corroborating.length === 0}>
          <ul className="flex flex-col gap-1.5">
            {s.corroborating.map((c) => (
              <li key={c.finding_key}>
                <Link
                  href={`/cases/${caseId}/documents/${c.statement_document_id}?row=${c.row_number}`}
                  className="hover:text-brand-700"
                >
                  {corroborationLine(c, c.payslip_document_id ? (months.get(c.payslip_document_id) ?? null) : null, nameOf)}
                </Link>
                <p className="text-xs text-slate-500 italic">{c.scope}</p>
              </li>
            ))}
          </ul>
        </SupportSection>
        <SupportSection kind="findings" empty={discrepancies.length + notRun.length + passed.length === 0}>
          <ul className="flex flex-col gap-0.5">
            {discrepancies.map((f) => (
              <li key={f.key}>
                <Badge tone="warning">{f.acknowledgement ? "Discrepancy · acknowledged" : "Discrepancy"}</Badge> {f.title}
                {f.acknowledgement && <span className="text-slate-500"> · “{f.acknowledgement.note}”</span>}
              </li>
            ))}
            {notRun.map((f) => (
              <li key={f.key} className="text-slate-600">
                Could not run: {f.title} <span className="text-slate-400">({f.reason.replace(/^could not run:\s*/i, "")})</span>
              </li>
            ))}
            {passed.length > 0 && <li className="text-slate-600">Passed: {passed.map((f) => f.title).join(" · ")}</li>}
          </ul>
          <Link href={`/cases/${caseId}`} className="text-xs text-brand-700 hover:underline">
            Open the checks
          </Link>
        </SupportSection>
        <SupportSection kind="approval" empty={false}>
          <p className="text-slate-600">{approvalText(profile.approval)}</p>
        </SupportSection>
      </div>
    </section>
  );
}
