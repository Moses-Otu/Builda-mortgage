"use client";

// The broker decision workspace (journey stage 6): the agent's overall assessment, findings,
// risks, discrepancies, evidence position, recommendations and limitations, beside the
// decision form and its history. The agent never decides: accepting, overriding (with a
// reason), requesting evidence, escalating and the final decision are the broker's.

import { Gavel } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  AgentSummary,
  BrokerDecision,
  EvidenceIntelligence,
  History,
  Overview,
} from "@/components/assessment/parts";
import { JourneyBar, useJourney } from "@/components/journey/JourneyBar";
import { Breadcrumbs, Loading, PageContainer, RequireFirm } from "@/components/Page";
import { caseTitle, useCaseSummary } from "@/components/useCaseSummary";
import { Alert } from "@/components/ui/alert";
import { get } from "@/lib/api";
import { type Assessment, type AssessmentSummary, type EvidenceRequest } from "@/lib/assessment";
import { problemMessage } from "@/lib/problems";

export default function DecisionRoute() {
  const { caseId } = useParams<{ caseId: string }>();
  return <RequireFirm>{() => <DecisionPage caseId={caseId} />}</RequireFirm>;
}

function DecisionPage({ caseId }: { caseId: string }) {
  const summary = useCaseSummary(caseId);
  const { journey, reload: reloadJourney } = useJourney(caseId);
  const [list, setList] = useState<AssessmentSummary[] | null>(null);
  const [current, setCurrent] = useState<Assessment | null>(null);
  const [requests, setRequests] = useState<EvidenceRequest[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (id?: string) => {
      const [l, r] = await Promise.all([
        get<AssessmentSummary[]>(`cases/${caseId}/assessments`),
        get<EvidenceRequest[]>(`cases/${caseId}/evidence-requests`),
        reloadJourney(),
      ]);
      if (!l.ok) {
        setError(problemMessage(l.status, l.problem));
        return;
      }
      setList(l.data);
      if (r.ok) setRequests(r.data);
      // The decision is made on the newest completed assessment unless another is picked.
      const target = id ?? l.data.find((a) => a.status === "completed")?.id;
      if (target) {
        const a = await get<Assessment>(`cases/${caseId}/assessments/${target}`);
        if (a.ok) setCurrent(a.data);
        else setError(problemMessage(a.status, a.problem));
      } else {
        setCurrent(null);
      }
    },
    [caseId, reloadJourney],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const crumbs = [
    { label: "Cases", href: "/" },
    { label: caseTitle(summary, caseId), href: `/cases/${caseId}` },
    { label: "Broker decision" },
  ];
  if (error) {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <Alert tone="error" title="The decision can't be shown">
          {error}
        </Alert>
      </PageContainer>
    );
  }
  if (list === null) return <Loading />;

  const result = current?.result ?? null;
  const newestCompleted = list.find((a) => a.status === "completed")?.id;
  return (
    <PageContainer wide>
      <Breadcrumbs items={crumbs} />
      <JourneyBar caseId={caseId} journey={journey} showing="decision" />
      <div className="mb-5">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Gavel className="size-6 text-brand-600" /> Broker decision
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          The agent recommends; the decision is yours and is recorded with your name. Overriding needs a reason.
        </p>
      </div>

      {!current || !result ? (
        <Alert tone="info" title="No completed assessment to decide on yet">
          The decision follows the mortgage assessment.{" "}
          <Link href={`/cases/${caseId}/assessment`} className="font-medium text-brand-700 hover:underline">
            Go to the assessment
          </Link>
          .
        </Alert>
      ) : (
        <>
          {journey?.reassessment_required && (
            <Alert tone="warning" title="This assessment is out of date" className="mb-4">
              New evidence arrived after version {current.version}. Reassess before recording a final decision.
            </Alert>
          )}
          {current.id !== newestCompleted && (
            <Alert tone="info" title={`Viewing version ${current.version}`} className="mb-4">
              An earlier version, kept unchanged in the history.
            </Alert>
          )}
          <Overview assessment={current} result={result} requests={requests} />
          <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <div className="flex min-w-0 flex-col gap-6">
              <AgentSummary assessment={current} result={result} />
              <EvidenceIntelligence result={result} requests={requests} caseId={caseId} />
            </div>
            <div className="flex min-w-0 flex-col gap-6">
              <BrokerDecision caseId={caseId} assessment={current} result={result} onDecided={() => void load(current.id)} />
              <History list={list} currentId={current.id} onSelect={(id) => void load(id)} />
            </div>
          </div>
        </>
      )}
      <p className="mt-8 text-xs text-slate-500">
        Indicative only. Not a lending decision, a mortgage offer or advice. Synthetic data only.
      </p>
    </PageContainer>
  );
}
