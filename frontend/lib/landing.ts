// Content of the public landing page (components/landing/Landing.tsx), kept separate so it can be tested.

export const DEMO_EMAIL = "azeez@jadasquad.com";
export const DEMO_HREF = `mailto:${DEMO_EMAIL}?subject=${encodeURIComponent("Builda+ Mortgage demo")}`;

export const STAGES = ["Applicant", "Payslips", "Bank statements", "Profile", "Assessment", "Decision"] as const;

export type Step = { key: string; label: string; title: string; body: string; image: string; alt: string };

export const STEPS: Step[] = [
  {
    key: "evidence",
    label: "Read the evidence",
    title: "Every figure, beside the page it came from",
    body: "Payslips are read in seconds. The broker checks each extracted figure against the original PDF and accepts, corrects or rejects it.",
    image: "/landing/06-payslip-review.webp",
    alt: "Payslip review: the original PDF beside the extracted fields, with accepted figures ticked",
  },
  {
    key: "bank",
    label: "Match to the bank",
    title: "Salaries matched to the bank statement",
    body: "Bank statements are confirmed against the account, and each payslip's net pay is looked for on the day it should arrive.",
    image: "/landing/08-bank-statements-matched.webp",
    alt: "Bank statements stage with three confirmed statements",
  },
  {
    key: "profile",
    label: "Approve the profile",
    title: "One verified financial position",
    body: "Income, commitments, the loan and the deposit in one place, each marked verified, declared or inferred. Approving the profile is not approving the mortgage.",
    image: "/landing/09-profile-review.webp",
    alt: "Financial profile with the consolidated position and the approval panel",
  },
  {
    key: "assessment",
    label: "Agent assessment",
    title: "The agent investigates and shows its working",
    body: "Pre-qualification, affordability and scenarios, with every step the agent took, the evidence behind each finding and what is still missing.",
    image: "/landing/12-assessment-complete.webp",
    alt: "Mortgage assessment: the agent's conclusion, its activity trail and the pre-qualification result",
  },
  {
    key: "decision",
    label: "Broker decision",
    title: "It recommends. You decide.",
    body: "Accept, override with a reason, request information or escalate. The decision is recorded with your name, and every assessment version is kept.",
    image: "/landing/15-decision.webp",
    alt: "Broker decision page with the agent's outcome, the decision form and the version history",
  },
  {
    key: "checks",
    label: "Catch problems",
    title: "When the documents don't add up, it says so",
    body: "A year-to-date total that doesn't follow on, or a salary that arrives £100 short, is flagged with the figures behind it. Approval waits until a person has reviewed each one.",
    image: "/landing/17c-issues-profile.webp",
    alt: "Two discrepancies flagged: a year-to-date mismatch and a salary near match",
  },
];
