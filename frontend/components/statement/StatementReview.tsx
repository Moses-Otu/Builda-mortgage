"use client";

// Bank statement confirmation (Stage 2-B, step 2B-2). The broker confirms that the extracted
// summary matches the document, which account it belongs to, and who holds that account.
// The balance checks (validation) are shown beside it and are never changed by confirming.

import { CircleAlert, CircleCheck, Info, Landmark, Lock, Plus, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Breadcrumbs, Loading, PageContainer } from "@/components/Page";
import { type Highlight, PdfView } from "@/components/PdfView";
import { FieldRow } from "@/components/review/FieldRow";
import { SavedReview } from "@/components/review/SavedReview";
import { caseTitle, useCaseSummary } from "@/components/useCaseSummary";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { FieldHint, Input, Label, Select } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { API, get, postJson } from "@/lib/api";
import { type Draft, type Drafts, formatMoney, problems } from "@/lib/decisions";
import { formatDate } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import { acceptRemaining } from "@/lib/review-ui";
import {
  type AccountChoice,
  accountTitle,
  buildStatementReview,
  cellEvidence,
  choiceProblem,
  choosableAccounts,
  defaultChoice,
  holdersText,
  integritySummary,
  linkedOrdinal,
  ROW_ORDINAL_BASE,
} from "@/lib/statements";
import type {
  Applicant,
  BankAccount,
  CheckOutcome,
  DecisionKind,
  DocumentOut,
  Extraction,
  Integrity,
  ReviewOut,
  Transaction,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export function StatementReview({ caseId, documentId }: { caseId: string; documentId: string }) {
  const toast = useToast();
  const summary = useCaseSummary(caseId);
  const base = `cases/${caseId}/documents/${documentId}`;
  const [document, setDocument] = useState<DocumentOut | null>(null);
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [review, setReview] = useState<ReviewOut | null>(null);
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [applicants, setApplicants] = useState<Applicant[]>([]);
  const [drafts, setDrafts] = useState<Drafts>({});
  const [choice, setChoice] = useState<AccountChoice | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [hovered, setHovered] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const load = useCallback(async () => {
    const d = await get<DocumentOut>(base);
    if (!d.ok) {
      setLoadError(problemMessage(d.status, d.problem));
      return;
    }
    setDocument(d.data);
    const [x, accs, a] = await Promise.all([
      get<Extraction>(`${base}/extraction`),
      get<BankAccount[]>(`cases/${caseId}/bank-accounts`),
      get<Applicant[]>(`cases/${caseId}/applicants`),
    ]);
    if (x.ok) {
      setExtraction(x.data);
      const match = x.data.account_match;
      if (match) setChoice((current) => current ?? defaultChoice(match, d.data.applicant_id));
    }
    if (accs.ok) setAccounts(accs.data);
    if (a.ok) setApplicants(a.data.filter((p) => p.status !== "removed"));
    if (d.data.status === "reviewed") {
      const r = await get<ReviewOut>(`${base}/review`);
      if (r.ok) setReview(r.data);
    }
  }, [base, caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  // One printed "Statement period" label yields two fields: say which is which (display only;
  // the printed label is kept as extracted).
  const fields = useMemo(
    () =>
      (extraction?.summary ?? []).map((f) =>
        f.field_code === "statement_period_start" || f.field_code === "statement_period_end"
          ? { ...f, label_text: `${f.label_text} (${f.field_code.endsWith("start") ? "start" : "end"})` }
          : f,
      ),
    [extraction],
  );
  const rows = useMemo(() => extraction?.transactions ?? [], [extraction]);
  const nameOf = useCallback(
    (id: string) => {
      const a = applicants.find((x) => x.id === id);
      return a ? `${a.first_name} ${a.last_name}` : "an applicant";
    },
    [applicants],
  );
  const ending = fields.find((f) => f.field_code === "account_ending")?.value_text ?? null;
  const integrity = review?.integrity ?? extraction?.integrity ?? null;

  const highlights: Highlight[] = useMemo(() => {
    const out: Highlight[] = fields.flatMap((f) => {
      const geometry = { page_width: f.page_width, page_height: f.page_height, unit: f.unit };
      return [
        { ordinal: f.ordinal, kind: "label" as const, evidence: f.label_evidence, geometry },
        { ordinal: f.ordinal, kind: "value" as const, evidence: f.value_evidence, geometry },
      ];
    });
    for (const r of rows) {
      const geometry = { page_width: r.page_width, page_height: r.page_height, unit: r.unit };
      const ordinal = ROW_ORDINAL_BASE + r.row_number;
      out.push({ ordinal, kind: "label", evidence: r.row_evidence, geometry });
      for (const cell of [r.cells.paid_out, r.cells.paid_in, r.cells.balance]) {
        if (cell) out.push({ ordinal, kind: "value", evidence: cellEvidence(cell), geometry });
      }
    }
    return out;
  }, [fields, rows]);

  const openProblems = useMemo(() => problems(fields, drafts), [fields, drafts]);
  const accountProblem = choice ? choiceProblem(choice) : "Choose the account";
  const decisionsByOrdinal = useMemo(() => {
    const out: Record<number, DecisionKind | undefined> = {};
    if (review) for (const d of review.decisions) out[d.ordinal] = d.decision;
    else for (const f of fields) out[f.ordinal] = drafts[f.ordinal]?.decision;
    return out;
  }, [review, fields, drafts]);

  const select = useCallback((ordinal: number | null) => {
    setSelected(ordinal);
    if (ordinal === null) return;
    const target = ordinal >= ROW_ORDINAL_BASE ? `[data-row="${ordinal}"]` : `[data-field="${ordinal}"]`;
    requestAnimationFrame(() =>
      window.document.querySelector(target)?.scrollIntoView({ block: "nearest", behavior: "smooth" }),
    );
  }, []);

  // A link from a check's evidence (?row=<n> or ?field=<ordinal>) opens with that row or
  // field selected and its box highlighted, once the extraction has loaded.
  const linked = useRef(false);
  useEffect(() => {
    if (linked.current || !extraction) return;
    linked.current = true;
    const target = linkedOrdinal(window.location.search);
    if (target !== null) setTimeout(() => select(target), 300);
  }, [extraction, select]);

  async function confirm() {
    if (!document || !choice) return;
    setSaving(true);
    setSaveError(null);
    const result = await postJson<ReviewOut>(
      `${base}/review`,
      buildStatementReview(fields, drafts, choice),
      document.version,
    );
    setSaving(false);
    if (result.ok) {
      setConfirmOpen(false);
      setReview(result.data);
      toast("success", "Statement confirmed. No figures were saved.");
      await load();
      return;
    }
    if (result.status === 409 && result.problem?.type?.endsWith("already-reviewed")) {
      setConfirmOpen(false);
      toast("error", problemMessage(result.status, result.problem));
      await load();
      return;
    }
    setSaveError(problemMessage(result.status, result.problem));
  }

  const crumbs = [
    { label: "Cases", href: "/" },
    { label: caseTitle(summary, caseId), href: `/cases/${caseId}` },
    { label: document?.original.original_filename ?? "Bank statement" },
  ];
  if (loadError) {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <Alert tone="error" title="This statement can't be shown">
          {loadError}
        </Alert>
      </PageContainer>
    );
  }
  if (!document || !extraction) return <Loading />;

  const confirmedAccount = review?.bank_account ?? null;

  return (
    <main className="mx-auto flex h-[calc(100vh-3.5rem)] w-full max-w-[1600px] flex-col px-6 pt-4 pb-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <Breadcrumbs items={crumbs} />
          <h1 className="flex items-center gap-3 text-xl font-semibold tracking-tight">
            {review ? "Confirmed bank statement" : "Confirm bank statement"}
            <Badge tone="synthetic">Synthetic</Badge>
          </h1>
        </div>
        <p className="text-xs text-slate-500">
          Extracted by <span className="font-medium text-slate-700">{extraction.provider}</span> ·{" "}
          {extraction.schema_version} · {rows.length} rows · no figures are saved from a statement
        </p>
      </div>

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
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
          <div className="min-h-0 flex-1 overflow-auto p-4">
            <div className="flex flex-col gap-5">
              <IntegrityPanel integrity={integrity} confirmed={Boolean(review)} />

              {review ? (
                <>
                  {confirmedAccount && (
                    <AccountCard
                      account={confirmedAccount}
                      caseId={caseId}
                      applicants={applicants}
                      nameOf={nameOf}
                      onChanged={() => void load()}
                    />
                  )}
                  <div className="-mx-4">
                    <SavedReview
                      review={review}
                      sourceName={
                        confirmedAccount
                          ? `${accountTitle(confirmedAccount)} (${holdersText(confirmedAccount, nameOf)})`
                          : "an account"
                      }
                      selected={selected}
                      hovered={hovered}
                      onSelect={select}
                      onHover={setHovered}
                    />
                  </div>
                </>
              ) : (
                <>
                  <section className="flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
                        Statement summary · {fields.length - openProblems.size} of {fields.length} decided
                      </h3>
                      <Button size="sm" variant="secondary" onClick={() => setDrafts((d) => acceptRemaining(fields, d))}>
                        Accept remaining
                      </Button>
                    </div>
                    {extraction.missing_required.length > 0 && (
                      <Alert tone="warning" title="Not found on the statement">
                        {extraction.missing_required.join(", ").replaceAll("_", " ")}
                      </Alert>
                    )}
                    {fields.map((f) => (
                      <FieldRow
                        key={f.ordinal}
                        field={f}
                        draft={drafts[f.ordinal] ?? {}}
                        problem={openProblems.get(f.ordinal) ?? null}
                        selected={selected === f.ordinal}
                        hovered={hovered === f.ordinal}
                        measures={[]}
                        onSelect={() => select(f.ordinal)}
                        onHover={(on) => setHovered(on ? f.ordinal : null)}
                        onChange={(change: Partial<Draft>) =>
                          setDrafts((current) => ({ ...current, [f.ordinal]: { ...current[f.ordinal], ...change } }))
                        }
                        onNext={() => undefined}
                      />
                    ))}
                  </section>

                  {extraction.account_match && choice && (
                    <AccountPicker
                      match={extraction.account_match}
                      accounts={accounts}
                      ending={ending}
                      applicants={applicants}
                      nameOf={nameOf}
                      choice={choice}
                      onChoice={setChoice}
                    />
                  )}
                </>
              )}

              <TransactionsTable
                rows={rows}
                breakRow={integrity?.break_row ?? null}
                selected={selected}
                hovered={hovered}
                onSelect={select}
                onHover={setHovered}
              />
            </div>
          </div>

          {!review && (
            <div className="flex items-center gap-3 border-t border-slate-200 bg-white p-4">
              <p className="flex-1 text-sm text-slate-600">
                {openProblems.size > 0
                  ? `${openProblems.size} summary field${openProblems.size === 1 ? "" : "s"} still need a decision.`
                  : (accountProblem ?? "Ready to confirm.")}
              </p>
              <Button
                size="lg"
                disabled={openProblems.size > 0 || accountProblem !== null}
                onClick={() => {
                  setSaveError(null);
                  setConfirmOpen(true);
                }}
              >
                Confirm statement…
              </Button>
            </div>
          )}
        </div>
      </div>

      {choice && (
        <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Confirm this statement</DialogTitle>
              <DialogDescription>
                Confirming records that the extracted information matches the document, which account it is,
                and who holds it.
              </DialogDescription>
            </DialogHeader>
            <ConfirmSummary integrity={extraction.integrity ?? null} />
            <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700">
              {choice.kind === "existing"
                ? (() => {
                    const account = accounts.find((a) => a.id === choice.accountId);
                    return account
                      ? `To ${accountTitle(account)} (${holdersText(account, nameOf)}).`
                      : "To the chosen account.";
                  })()
                : `To a new account ending ${ending ?? "?"}, held by ${choice.holderIds.map(nameOf).join(" and ")}.`}
            </p>
            <p className="flex items-center gap-2 text-xs text-slate-500">
              <Lock className="size-3.5" /> A confirmation can&apos;t be changed. It saves no figures.
            </p>
            {saveError && <Alert tone="error">{saveError}</Alert>}
            <DialogFooter>
              <Button variant="secondary" onClick={() => setConfirmOpen(false)}>
                Keep checking
              </Button>
              <Button onClick={() => void confirm()} disabled={saving}>
                {saving ? "Confirming…" : "Confirm statement"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </main>
  );
}

const OUTCOME: Record<CheckOutcome, { icon: typeof CircleCheck; className: string; label: string }> = {
  passed: { icon: CircleCheck, className: "text-accept", label: "Adds up" },
  discrepancy: { icon: TriangleAlert, className: "text-correct", label: "Doesn't add up" },
  not_run: { icon: Info, className: "text-slate-400", label: "Not available" },
};

function IntegrityPanel({ integrity, confirmed }: { integrity: Integrity | null; confirmed: boolean }) {
  if (!integrity) return null;
  const lines: Array<[string, CheckOutcome, string]> = [
    ["Totals", integrity.totals, integrity.totals_message],
    ["Running balance", integrity.running, integrity.running_message],
  ];
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-3">
      <h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500 uppercase">
        Balance checks {confirmed ? "(on the confirmed values)" : "(on the extracted values)"}
      </h3>
      <ul className="flex flex-col gap-2 text-sm">
        {lines.map(([name, outcome, message]) => {
          const look = OUTCOME[outcome];
          const Icon = look.icon;
          return (
            <li key={name} className="flex items-start gap-2">
              <Icon className={cn("mt-0.5 size-4 shrink-0", look.className)} />
              <span>
                <span className="font-medium">
                  {name}: {look.label}.
                </span>{" "}
                <span className="text-slate-600">{message.charAt(0).toUpperCase() + message.slice(1)}.</span>
              </span>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-xs text-slate-500">
        These checks are separate from confirming: confirming never clears a discrepancy.
      </p>
    </section>
  );
}

function ConfirmSummary({ integrity }: { integrity: Integrity | null }) {
  const s = integritySummary(integrity);
  if (s.tone === "warn") {
    const detail = (integrity?.running === "discrepancy" ? integrity.running_message : integrity?.totals_message) ?? "";
    return (
      <Alert tone="warning" title={s.text}>
        {detail.charAt(0).toUpperCase() + detail.slice(1)}. Confirming records that the extracted information
        matches the document; the discrepancy stays visible.
      </Alert>
    );
  }
  return <Alert tone="info">{s.text}</Alert>;
}

function AccountPicker(props: {
  match: NonNullable<Extraction["account_match"]>;
  accounts: BankAccount[];
  ending: string | null;
  applicants: Applicant[];
  nameOf: (id: string) => string;
  choice: AccountChoice;
  onChoice: (c: AccountChoice) => void;
}) {
  const { match, accounts, ending, applicants, nameOf, choice, onChoice } = props;
  const choosable = choosableAccounts(accounts, match, ending);
  const lookalikes = accounts.filter((a) => match.same_ending_account_ids.includes(a.id) && a.fingerprinted);
  const newChoice = choice.kind === "new" ? choice : { kind: "new" as const, holderIds: [], label: "" };

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">Account</h3>
      {match.status === "existing" && (
        <Alert tone="info" title="Same account as an earlier statement">
          The full account number on this statement matches this account.
        </Alert>
      )}
      {match.status === "unverified" && (
        <Alert tone="warning" title="The account can't be verified from this statement">
          It doesn&apos;t print the full account number. Choose the account it belongs to; continuity will rely on
          your choice.
        </Alert>
      )}
      {match.status === "new" && lookalikes.length > 0 && (
        <Alert tone="warning" title="A different account with the same last 4 digits">
          {lookalikes.map((a) => accountTitle(a)).join(", ")} also end{lookalikes.length === 1 ? "s" : ""} {ending},
          but the sort code or account number differs: this statement is for a different account.
        </Alert>
      )}
      {choosable.map((a) => (
        <label
          key={a.id}
          className={cn(
            "flex cursor-pointer items-start gap-3 rounded-lg border bg-white p-3 text-sm",
            choice.kind === "existing" && choice.accountId === a.id ? "border-brand-500 ring-3 ring-brand-500/15" : "border-slate-200",
          )}
        >
          <input
            type="radio"
            name="account"
            className="mt-1"
            checked={choice.kind === "existing" && choice.accountId === a.id}
            onChange={() => onChoice({ kind: "existing", accountId: a.id })}
          />
          <span>
            <span className="font-medium">{accountTitle(a)}</span>
            <span className="block text-slate-500">
              {holdersText(a, nameOf)} · {a.statement_document_ids.length} statement
              {a.statement_document_ids.length === 1 ? "" : "s"}
              {!a.fingerprinted && " · not verified by account number"}
            </span>
          </span>
        </label>
      ))}
      {match.status !== "existing" && (
        <div
          className={cn(
            "flex flex-col gap-3 rounded-lg border bg-white p-3 text-sm",
            choice.kind === "new" ? "border-brand-500 ring-3 ring-brand-500/15" : "border-slate-200",
          )}
        >
          <label className="flex cursor-pointer items-center gap-3">
            <input type="radio" name="account" checked={choice.kind === "new"} onChange={() => onChoice(newChoice)} />
            <span className="flex items-center gap-2 font-medium">
              <Plus className="size-4" /> A new account ending {ending ?? "?"}
            </span>
          </label>
          {choice.kind === "new" && (
            <>
              <fieldset className="flex flex-col gap-1.5">
                <legend className="mb-1 text-xs font-medium text-slate-700">Who holds this account?</legend>
                {applicants.map((a) => (
                  <label key={a.id} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={choice.holderIds.includes(a.id)}
                      onChange={(e) =>
                        onChoice({
                          ...choice,
                          holderIds: e.target.checked
                            ? [...choice.holderIds, a.id]
                            : choice.holderIds.filter((x) => x !== a.id),
                        })
                      }
                    />
                    {a.first_name} {a.last_name}
                  </label>
                ))}
                <FieldHint>Two or more holders make it a joint account. Names on the statement aren&apos;t read.</FieldHint>
              </fieldset>
              <Label className="text-xs">
                Label (optional)
                <Input
                  value={choice.label}
                  maxLength={100}
                  placeholder="e.g. Current account"
                  onChange={(e) => onChoice({ ...choice, label: e.target.value })}
                />
              </Label>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function AccountCard(props: {
  account: BankAccount;
  caseId: string;
  applicants: Applicant[];
  nameOf: (id: string) => string;
  onChanged: () => void;
}) {
  const { account, caseId, applicants, nameOf, onChanged } = props;
  const toast = useToast();
  const others = applicants.filter((a) => !account.holders.some((h) => h.applicant_id === a.id));
  const [adding, setAdding] = useState("");

  async function addHolder() {
    if (!adding) return;
    const result = await postJson(`cases/${caseId}/bank-accounts/${account.id}/holders`, { applicant_id: adding });
    if (result.ok) {
      toast("success", "Holder added.");
      setAdding("");
      onChanged();
    } else {
      toast("error", problemMessage(result.status, result.problem));
    }
  }

  return (
    <section className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-center gap-2">
        <Landmark className="size-4 text-brand-600" />
        <span className="font-medium">{accountTitle(account)}</span>
        {account.joint && <Badge tone="info">Joint</Badge>}
        {!account.fingerprinted && <Badge tone="warning">Not verified by account number</Badge>}
      </div>
      <p className="text-slate-600">
        {holdersText(account, nameOf)} · {account.statement_document_ids.length} confirmed statement
        {account.statement_document_ids.length === 1 ? "" : "s"}
      </p>
      {others.length > 0 && (
        <div className="flex items-end gap-2">
          <Label className="flex-1 text-xs">
            Add a holder
            <Select value={adding} onChange={(e) => setAdding(e.target.value)}>
              <option value="">Choose an applicant…</option>
              {others.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.first_name} {a.last_name}
                </option>
              ))}
            </Select>
          </Label>
          <Button size="sm" variant="secondary" disabled={!adding} onClick={() => void addHolder()}>
            Add
          </Button>
        </div>
      )}
    </section>
  );
}

function TransactionsTable(props: {
  rows: Transaction[];
  breakRow: number | null;
  selected: number | null;
  hovered: number | null;
  onSelect: (ordinal: number) => void;
  onHover: (ordinal: number | null) => void;
}) {
  const { rows, breakRow, selected, hovered, onSelect, onHover } = props;
  const money = (v: number | null) => (v === null ? "" : formatMoney(v));
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">
        Transactions · {rows.filter((r) => r.kind === "transaction").length} as printed
      </h3>
      <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-left text-slate-500">
            <tr>
              <th className="px-2 py-1.5 font-medium">Date</th>
              <th className="px-2 py-1.5 font-medium">Description</th>
              <th className="px-2 py-1.5 text-right font-medium">Paid out</th>
              <th className="px-2 py-1.5 text-right font-medium">Paid in</th>
              <th className="px-2 py-1.5 text-right font-medium">Balance</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((r) => {
              const ordinal = ROW_ORDINAL_BASE + r.row_number;
              const forward = r.kind === "brought_forward" || r.kind === "carried_forward";
              return (
                <tr
                  key={r.row_number}
                  data-row={ordinal}
                  onClick={() => onSelect(ordinal)}
                  onMouseEnter={() => onHover(ordinal)}
                  onMouseLeave={() => onHover(null)}
                  className={cn(
                    "scroll-my-4 cursor-pointer align-top",
                    selected === ordinal ? "bg-brand-50" : hovered === ordinal ? "bg-slate-50" : "",
                    r.kind === "unparsed" && "bg-correct-soft/60",
                    breakRow === r.row_number && "outline-2 -outline-offset-2 outline-reject",
                    forward && "text-slate-500 italic",
                  )}
                >
                  <td className="px-2 py-1.5 whitespace-nowrap tabular-nums">
                    {r.posted_on ? formatDate(r.posted_on) : ""}
                    {r.cells.date_inherited && <span className="text-slate-400"> (same day)</span>}
                  </td>
                  <td className="px-2 py-1.5 whitespace-pre-line">
                    {r.description}
                    {r.kind === "unparsed" && (
                      <span className="mt-0.5 flex items-center gap-1 text-correct not-italic">
                        <CircleAlert className="size-3" /> Couldn&apos;t be read:{" "}
                        {[r.cells.paid_out, r.cells.paid_in, r.cells.balance, ...r.cells.other]
                          .filter((c) => c !== null)
                          .map((c) => `“${c?.text}”`)
                          .join(", ")}
                      </span>
                    )}
                    {r.description_truncated && <span className="text-slate-400"> (shortened)</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{money(r.paid_out_minor)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{money(r.paid_in_minor)}</td>
                  <td
                    className={cn(
                      "px-2 py-1.5 text-right tabular-nums",
                      breakRow === r.row_number && "font-semibold text-reject",
                    )}
                  >
                    {money(r.balance_minor)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
