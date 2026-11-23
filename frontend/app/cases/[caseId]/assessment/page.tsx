"use client";

// The assessment workspace (journey stage 5): what the Mortgage Assessment Agent did, step by
// step as it happens, and what it found, in three distinct parts: pre-qualification,
// affordability and scenario analysis. Runs start by themselves when the profile is approved;
// a reassessment is started from the journey's single next action. The broker's decision has
// its own page (stage 6).

import { Bot } from "lucide-react";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  Activity,
  AgentSummary,
  Criteria,
  EvidenceIntelligence,
  FinancialAssessment,
  History,
  Overview,
  Running,
  ScenarioAnalysis,
  Section,
} from "@/components/assessment/parts";
import { JourneyBar, useJourney } from "@/components/journey/JourneyBar";
import { Breadcrumbs, Loading, PageContainer, RequireFirm } from "@/components/Page";
import { caseTitle, useCaseSummary } from "@/components/useCaseSummary";
import { Alert } from "@/components/ui/alert";
import { get } from "@/lib/api";
import { type Assessment, type AssessmentSummary, type EvidenceRequest, isRunning } from "@/lib/assessment";
import { problemMessage } from "@/lib/problems";

export default function AssessmentRoute() {
  const { caseId } = useParams<{ caseId: string }>();
  return <RequireFirm>{() => <Workspace caseId={caseId} />}</RequireFirm>;
}

function Workspace({ caseId }: { caseId: string }) {
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
      const target = id ?? l.data[0]?.id;
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

  // Follow a run in progress: the activity below is the agent's real steps as they're saved.
  const running = isRunning(current);
  const latestId = list?.[0]?.id;
  useEffect(() => {
    if (!running && !journey?.assessment_running) return;
    // A run started from the journey bar: switch to it.
    const timer = setInterval(() => void load(running ? current?.id : undefined), 2000);
    return () => clearInterval(timer);
  }, [running, journey?.assessment_running, current?.id, load]);
  // A new version appeared (e.g. a reassessment just started): show it.
  useEffect(() => {
    if (journey?.latest_assessment_id && latestId && journey.latest_assessment_id !== latestId) void load();
  }, [journey?.latest_assessment_id, latestId, load]);

  const crumbs = [
    { label: "Cases", href: "/" },
    { label: caseTitle(summary, caseId), href: `/cases/${caseId}` },
    { label: "Mortgage assessment" },
  ];
  if (error) {
    return (
      <PageContainer>
        <Breadcrumbs items={crumbs} />
        <Alert tone="error" title="The assessment can't be shown">
          {error}
        </Alert>
      </PageContainer>
    );
  }
  if (list === null) return <Loading />;

  const result = current?.result ?? null;
  const viewingOld = current && latestId && current.id !== latestId;
  return (
    <PageContainer wide>
      <Breadcrumbs items={crumbs} />
      <JourneyBar caseId={caseId} journey={journey} showing="assessment" />
      <div className="mb-5">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Bot className="size-6 text-brand-600" /> Mortgage assessment
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          The assessment agent investigates the approved profile, runs the pre-qualification and affordability rules,
          tests scenarios and explains what it found. It recommends; you decide.
        </p>
      </div>

      {!current && (
        <Alert tone="info" title="No assessment yet">
          {journey && journey.assessment_blockers.length > 0 ? (
            <ul className="list-disc pl-4">
              {journey.assessment_blockers.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          ) : (
            "The assessment starts by itself when the profile is approved."
          )}
        </Alert>
      )}
      {viewingOld && (
        <Alert tone="info" title={`Viewing version ${current.version}`} className="mb-4">
          An earlier version, kept unchanged in the history. Select the newest version in the history to return to it.
        </Alert>
      )}
      {current?.status === "failed" && (
        <Alert tone="error" title="The assessment didn't finish" className="mb-4">
          {current.error_code === "stale"
            ? "It stopped before finishing."
            : `The agent stopped (${current.error_code}).`}{" "}
          Use the action above to run it again; earlier versions are unaffected.
        </Alert>
      )}
      {current && running && <Running assessment={current} />}

      {current && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-8">
            {result && <AgentSummary assessment={current} result={result} />}
            {result && (
              <Section number={1} title="Pre-qualification">
                <Overview assessment={current} result={result} requests={requests} />
                <Criteria result={result} />
              </Section>
            )}
            {result && (
              <Section number={2} title="Affordability">
                <FinancialAssessment result={result} />
              </Section>
            )}
            {result?.affordability && (
              <Section number={3} title="Scenario analysis">
                <ScenarioAnalysis caseId={caseId} policy={current.policy_code} initial={result.scenarios} />
              </Section>
            )}
            {result && <EvidenceIntelligence result={result} requests={requests} caseId={caseId} />}
          </div>
          <div className="flex min-w-0 flex-col gap-6">
            <Activity steps={current.steps} running={running} />
            <History list={list} currentId={current.id} onSelect={(id) => void load(id)} />
          </div>
        </div>
      )}
      <p className="mt-8 text-xs text-slate-500">
        Indicative only: computed by versioned rules from the evidence on file and stated assumptions. Not a lending
        decision, a mortgage offer or advice. Synthetic data only.
      </p>
    </PageContainer>
  );
}
