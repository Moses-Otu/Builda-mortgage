"use client";

import { ChevronRight, FolderOpen, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { NewCaseDialog } from "@/components/NewCaseDialog";
import { Loading, PageContainer, RequireFirm } from "@/components/Page";
import { Alert } from "@/components/ui/alert";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { get } from "@/lib/api";
import { caseStatusLabel, formatDate, purposeLabel } from "@/lib/labels";
import { problemMessage } from "@/lib/problems";
import type { CaseSummary, Membership, Page } from "@/lib/types";

const STATUS_TONE: Record<string, BadgeTone> = {
  draft: "neutral",
  collecting_evidence: "info",
  in_review: "warning",
  verified: "success",
  closed: "neutral",
};

export default function Home() {
  return <RequireFirm>{(firm) => <CaseList firm={firm} />}</RequireFirm>;
}

function CaseList({ firm }: { firm: Membership }) {
  const router = useRouter();
  const [cases, setCases] = useState<CaseSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const result = await get<Page<CaseSummary>>("cases?limit=100");
      if (result.ok) setCases(result.data.items);
      else setError(problemMessage(result.status, result.problem));
    })();
  }, []);

  return (
    <PageContainer>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cases</h1>
          <p className="mt-1 text-sm text-slate-500">{firm.tenant_name}</p>
        </div>
        <NewCaseDialog />
      </div>

      {error && <Alert tone="error">{error}</Alert>}
      {!cases && !error && <Loading />}
      {cases && cases.length === 0 && (
        <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
          <div className="grid size-12 place-items-center rounded-full bg-slate-100 text-slate-500">
            <FolderOpen className="size-6" />
          </div>
          <div>
            <p className="font-medium">No cases yet</p>
            <p className="text-sm text-slate-500">Create a case to start collecting payslips.</p>
          </div>
          <NewCaseDialog />
        </Card>
      )}
      {cases && cases.length > 0 && (
        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50 text-left text-xs font-medium tracking-wide text-slate-500 uppercase">
              <tr>
                <th className="px-5 py-3">Reference</th>
                <th className="px-5 py-3">Purpose</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Applicants</th>
                <th className="px-5 py-3">Created</th>
                <th className="w-10" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {cases.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => router.push(`/cases/${c.id}`)}
                  className="cursor-pointer hover:bg-slate-50"
                >
                  <td className="px-5 py-3.5">
                    <Link
                      href={`/cases/${c.id}`}
                      onClick={(e) => e.stopPropagation()}
                      className="font-medium text-slate-900 hover:underline"
                    >
                      {c.external_reference ?? `Case ${c.id.slice(0, 8)}`}
                    </Link>
                  </td>
                  <td className="px-5 py-3.5 text-slate-600">{purposeLabel(c.purpose)}</td>
                  <td className="px-5 py-3.5">
                    <Badge tone={STATUS_TONE[c.status] ?? "neutral"}>{caseStatusLabel(c.status)}</Badge>
                  </td>
                  <td className="px-5 py-3.5 text-slate-600">
                    <span className="inline-flex items-center gap-1.5">
                      <Users className="size-3.5 text-slate-400" /> {c.applicant_count}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-slate-600">{formatDate(c.created_at)}</td>
                  <td className="pr-4 text-slate-400">
                    <ChevronRight className="size-4" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </PageContainer>
  );
}
