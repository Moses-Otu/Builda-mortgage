"use client";

import { useEffect, useState } from "react";

import { get } from "@/lib/api";
import type { CaseSummary, Page } from "@/lib/types";

/** The case's reference and status for headers and breadcrumbs. Read from the case list,
 * which the forwarder already allows (no extra API route; fine at pilot scale). */
export function useCaseSummary(caseId: string): CaseSummary | null {
  const [summary, setSummary] = useState<CaseSummary | null>(null);
  useEffect(() => {
    void (async () => {
      const result = await get<Page<CaseSummary>>("cases?limit=100");
      if (result.ok) setSummary(result.data.items.find((c) => c.id === caseId) ?? null);
    })();
  }, [caseId]);
  return summary;
}

export function caseTitle(summary: CaseSummary | null, caseId: string): string {
  return summary?.external_reference ?? `Case ${caseId.slice(0, 8)}`;
}
