// The case page's "what to do next" strip, worked out from the case's real state.

import type { DocumentOut, IncomeSource } from "./types";

export type StepState = "done" | "current" | "upcoming";

export interface Step {
  key: "income" | "upload" | "review";
  title: string;
  detail: string;
  state: StepState;
}

export function caseSteps(sources: IncomeSource[], documents: DocumentOut[]): Step[] {
  const hasSource = sources.some((s) => !s.withdrawn_at);
  const usable = documents.filter((d) => d.status !== "failed" && d.status !== "unrecognised");
  const hasUpload = usable.length > 0;
  const toReview = usable.filter((d) => d.status === "extracted").length;
  const processing = usable.filter((d) => d.status === "received" || d.status === "ready").length;
  const reviewed = usable.filter((d) => d.status === "reviewed").length;

  const done = [hasSource, hasUpload, hasUpload && toReview === 0 && processing === 0 && reviewed > 0];
  const firstOpen = done.findIndex((d) => !d);
  const state = (i: number): StepState =>
    done[i] ? "done" : i === firstOpen ? "current" : "upcoming";

  let reviewDetail = "Check each extracted figure against the payslip";
  if (toReview > 0) reviewDetail = `${toReview} payslip${toReview === 1 ? "" : "s"} waiting for review`;
  else if (processing > 0) reviewDetail = `${processing} payslip${processing === 1 ? "" : "s"} still processing`;
  else if (reviewed > 0) reviewDetail = `${reviewed} reviewed`;

  return [
    {
      key: "income",
      title: "Add an income source",
      detail: hasSource ? "Employment income recorded" : "The payslip's figures are saved against it",
      state: state(0),
    },
    {
      key: "upload",
      title: "Upload a payslip",
      detail: hasUpload ? `${usable.length} uploaded` : "PDF, synthetic documents only",
      state: state(1),
    },
    { key: "review", title: "Review and save", detail: reviewDetail, state: state(2) },
  ];
}
