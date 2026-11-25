"use client";

// The public front door for signed-out visitors: what Ceda does, shown with real
// screens from the product (synthetic data). Structure after the B2B "digital worker" pattern:
// hero with the pipeline, what it takes off the desk, a step-through of a real case, the
// agent's capabilities, the principle (the broker decides), the film, and one call to action.
// No customer logos, metrics or testimonials: the product is in pilot and nothing is invented.

import { ArrowRight, Check, FileSearch, Gavel, Landmark, Scale, ShieldCheck, Sparkles, TriangleAlert, Wallet } from "lucide-react";
import { Atkinson_Hyperlegible } from "next/font/google";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

import { CedaLogo } from "@/components/CedaLogo";
import { DEMO_EMAIL, DEMO_HREF, STAGES, STEPS } from "@/lib/landing";

const atkinson = Atkinson_Hyperlegible({ subsets: ["latin"], weight: ["400", "700"], display: "swap" });

const CAPABILITIES = [
  { icon: FileSearch, title: "Reads the documents", body: "Payslips and bank statements, page by page, with each figure traced to where it was found." },
  { icon: TriangleAlert, title: "Cross-checks them", body: "Month-to-month payslip totals, pay dates and salary credits against the bank." },
  { icon: ShieldCheck, title: "Pre-qualifies", body: "Loan to value, loan to income, debt to income and age at end of term against the lending policy." },
  { icon: Wallet, title: "Tests affordability", body: "Stressed repayments after commitments and living costs, and the maximum it indicates." },
  { icon: Scale, title: "Runs scenarios", body: "A rate rise, a longer term or a bigger deposit, side by side with the request." },
  { icon: Sparkles, title: "Explains itself", body: "Every step it took, what each finding rests on, and the evidence still to collect." },
];

const PRINCIPLES = [
  "The agent recommends. A broker records the decision, with their name and the time.",
  "Overriding the outcome needs a reason, every time.",
  "New evidence marks the assessment out of date, and the next version runs by itself.",
  "Every version is kept unchanged, so the file shows how each decision was reached.",
];

function DemoButton({ children = "Book a demo", light = false }: { children?: string; light?: boolean }) {
  return (
    <a
      href={DEMO_HREF}
      className={`inline-flex h-12 items-center gap-2 rounded-full px-6 text-base font-bold transition-colors ${
        light ? "bg-[#6FB497] text-[#081726] hover:bg-[#8FCBB2]" : "bg-[#112B45] text-white hover:bg-[#1A3A5C]"
      }`}
    >
      {children} <ArrowRight className="size-4" />
    </a>
  );
}

function Shot({ src, alt, priority = false }: { src: string; alt: string; priority?: boolean }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-[#C9D0D8] bg-white shadow-[0_30px_80px_-20px_rgba(8,23,38,0.35)]">
      <div className="flex h-8 items-center gap-1.5 border-b border-[#E1E5EA] bg-[#F4F2EC] px-3" aria-hidden="true">
        <span className="size-2.5 rounded-full bg-[#C9D0D8]" />
        <span className="size-2.5 rounded-full bg-[#C9D0D8]" />
        <span className="size-2.5 rounded-full bg-[#C9D0D8]" />
      </div>
      <Image src={src} alt={alt} width={1920} height={1080} priority={priority} className="h-auto w-full" sizes="(min-width: 1024px) 900px, 100vw" />
    </div>
  );
}

export function Landing() {
  const [step, setStep] = useState(0);
  const current = STEPS[step]!;

  return (
    <div className={`${atkinson.className} bg-[#FBFAF7] text-[#1D2939]`}>
      {/* Navigation */}
      <header className="sticky top-0 z-40 border-b border-white/10 bg-[#081726]/95 backdrop-blur">
        <nav className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-6" aria-label="Main">
          <CedaLogo light className="text-2xl" />
          <div className="ml-auto hidden items-center gap-6 text-sm text-[#A9B8C8] md:flex">
            <a href="#how" className="hover:text-white">How it works</a>
            <a href="#agent" className="hover:text-white">The agent</a>
            <a href="#control" className="hover:text-white">Broker control</a>
            <a href="#film" className="hover:text-white">Watch</a>
          </div>
          <Link href="/sign-in" className="ml-auto text-sm font-bold text-[#E9EEF4] hover:text-white md:ml-0">
            Sign in
          </Link>
          <a href={DEMO_HREF} className="hidden h-10 items-center rounded-full bg-[#6FB497] px-5 text-sm font-bold text-[#081726] hover:bg-[#8FCBB2] sm:inline-flex">
            Book a demo
          </a>
        </nav>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden bg-[#081726] text-[#E9EEF4]">
        <div className="pointer-events-none absolute -top-40 right-[-10%] size-[720px] rounded-full bg-[#6FB497]/15 blur-[140px]" aria-hidden="true" />
        <div className="relative mx-auto max-w-6xl px-6 pt-20 pb-16 md:pt-28">
          <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#6FB497]">Meet Ceda · for UK mortgage brokers</p>
          <h1 className="mt-5 max-w-4xl text-5xl leading-[1.04] font-bold tracking-tight md:text-7xl">
            The pre-qualification
            <br />
            <em className="text-[#6FB497]">and affordability agent.</em>
          </h1>
          <p className="mt-7 max-w-2xl text-lg leading-relaxed text-[#A9B8C8] md:text-xl">
            Ceda is the mortgage pre-qualification and affordability agent. It reads payslips and bank statements, checks every
            figure against its source, then tests pre-qualification, affordability and scenarios. The broker makes the call.
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-4">
            <DemoButton light />
            <a href="#film" className="inline-flex h-12 items-center gap-2 rounded-full border border-white/20 px-6 font-bold text-[#E9EEF4] hover:bg-white/5">
              Watch the 30-second film
            </a>
          </div>

          {/* Pipeline */}
          <ol className="mt-16 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-label="The six stages of a case">
            {STAGES.map((s, i) => (
              <li key={s} className="flex items-center gap-3 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3">
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[#6FB497] text-sm font-bold text-[#081726]">{i + 1}</span>
                <span className="text-sm font-bold">{s}</span>
              </li>
            ))}
          </ol>
          <div className="mt-10">
            <Shot src="/landing/12-assessment-complete.webp" alt="The mortgage assessment: the agent's conclusion and its activity trail" priority />
          </div>
        </div>
      </section>

      {/* What's built in (no invented metrics) */}
      <section className="border-b border-[#E1E5EA] bg-white">
        <div className="mx-auto grid max-w-6xl gap-6 px-6 py-10 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Six stages", "One clear next step at each"],
            ["Every figure traced", "To the page and line it came from"],
            ["Cross-document checks", "Payslips against each other and the bank"],
            ["Every decision versioned", "Who, when and why, kept unchanged"],
          ].map(([a, b]) => (
            <div key={a}>
              <p className="text-xl font-bold text-[#0C2135]">{a}</p>
              <p className="mt-1 text-sm text-[#4F5D6E]">{b}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Two jobs off the desk */}
      <section className="mx-auto max-w-6xl px-6 py-24">
        <h2 className="max-w-3xl text-4xl leading-tight font-bold tracking-tight text-[#0C2135] md:text-5xl">
          Two jobs it takes off your desk.
        </h2>
        <div className="mt-12 grid gap-8 lg:grid-cols-2">
          <article className="rounded-3xl border border-[#E1E5EA] bg-white p-8">
            <Landmark className="size-7 text-[#376D58]" />
            <h3 className="mt-5 text-2xl font-bold text-[#0C2135]">Collecting and checking the evidence</h3>
            <p className="mt-3 text-[#4F5D6E]">
              No more retyping payslips into spreadsheets. Documents are read, matched to the bank and cross-checked, and the
              journey tells you exactly what is still missing.
            </p>
            <div className="mt-6">
              <Shot src="/landing/06-payslip-review.webp" alt="Payslip review with the PDF beside the extracted fields" />
            </div>
          </article>
          <article className="rounded-3xl border border-[#E1E5EA] bg-white p-8">
            <Gavel className="size-7 text-[#376D58]" />
            <h3 className="mt-5 text-2xl font-bold text-[#0C2135]">Assessing the case</h3>
            <p className="mt-3 text-[#4F5D6E]">
              Once you approve the profile, the agent runs the lending policy, tests affordability and scenarios, and writes up
              what it found, ready for your decision.
            </p>
            <div className="mt-6">
              <Shot src="/landing/15-decision.webp" alt="Broker decision page with the agent's recommendation" />
            </div>
          </article>
        </div>
      </section>

      {/* Step through a case */}
      <section id="how" className="scroll-mt-16 bg-[#F4F2EC] py-24">
        <div className="mx-auto max-w-6xl px-6">
          <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#376D58]">How it works</p>
          <h2 className="mt-3 max-w-3xl text-4xl leading-tight font-bold tracking-tight text-[#0C2135] md:text-5xl">
            Follow one case from payslip to decision.
          </h2>
          <div className="mt-10 flex flex-wrap gap-2" role="tablist" aria-label="Steps of a case">
            {STEPS.map((s, i) => (
              <button
                key={s.key}
                type="button"
                role="tab"
                id={`tab-${s.key}`}
                aria-selected={i === step}
                aria-controls="step-panel"
                onClick={() => setStep(i)}
                className={`rounded-full px-4 py-2 text-sm font-bold transition-colors ${
                  i === step ? "bg-[#112B45] text-white" : "bg-white text-[#3B4859] hover:bg-[#E4EFE9]"
                }`}
              >
                {i + 1}. {s.label}
              </button>
            ))}
          </div>
          <div id="step-panel" role="tabpanel" aria-labelledby={`tab-${current.key}`} className="mt-8 grid items-start gap-10 lg:grid-cols-[minmax(0,2fr)_minmax(0,5fr)]">
            <div className="lg:pt-6">
              <h3 className="text-3xl leading-tight font-bold text-[#0C2135]">{current.title}</h3>
              <p className="mt-4 text-lg leading-relaxed text-[#4F5D6E]">{current.body}</p>
              <div className="mt-6 flex gap-3">
                <button
                  type="button"
                  onClick={() => setStep((step + STEPS.length - 1) % STEPS.length)}
                  className="rounded-full border border-[#C9D0D8] px-4 py-2 text-sm font-bold text-[#3B4859] hover:bg-white"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => setStep((step + 1) % STEPS.length)}
                  className="rounded-full bg-[#112B45] px-4 py-2 text-sm font-bold text-white hover:bg-[#1A3A5C]"
                >
                  Next step
                </button>
              </div>
            </div>
            <Shot src={current.image} alt={current.alt} />
          </div>
        </div>
      </section>

      {/* The agent */}
      <section id="agent" className="mx-auto max-w-6xl scroll-mt-16 px-6 py-24">
        <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#376D58]">The pre-qualification and affordability agent</p>
        <h2 className="mt-3 max-w-3xl text-4xl leading-tight font-bold tracking-tight text-[#0C2135] md:text-5xl">
          One pre-qualification and affordability agent. Every step on record.
        </h2>
        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {CAPABILITIES.map(({ icon: Icon, title, body }) => (
            <article key={title} className="rounded-2xl border border-[#E1E5EA] bg-white p-6">
              <span className="grid size-11 place-items-center rounded-xl bg-[#E4EFE9] text-[#2D5C4A]">
                <Icon className="size-5" />
              </span>
              <h3 className="mt-4 text-lg font-bold text-[#0C2135]">{title}</h3>
              <p className="mt-2 text-[#4F5D6E]">{body}</p>
            </article>
          ))}
        </div>
      </section>

      {/* The broker stays in charge */}
      <section id="control" className="scroll-mt-16 bg-[#0C2135] py-24 text-[#E9EEF4]">
        <div className="mx-auto grid max-w-6xl gap-12 px-6 lg:grid-cols-2">
          <div>
            <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#6FB497]">Broker control</p>
            <h2 className="mt-3 text-4xl leading-tight font-bold tracking-tight md:text-5xl">
              It recommends.
              <br />
              <em className="text-[#6FB497]">You decide.</em>
            </h2>
            <p className="mt-6 text-lg leading-relaxed text-[#A9B8C8]">
              Regulated advice stays with your advisers. The agent prepares the file; people approve the evidence and make the
              decision.
            </p>
          </div>
          <ul className="flex flex-col gap-4">
            {PRINCIPLES.map((p) => (
              <li key={p} className="flex gap-4 rounded-2xl border border-white/10 bg-white/[0.04] p-5">
                <Check className="mt-0.5 size-5 shrink-0 text-[#6FB497]" />
                <span className="text-[#E9EEF4]">{p}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Film */}
      <section id="film" className="mx-auto max-w-5xl scroll-mt-16 px-6 py-24 text-center">
        <p className="text-sm font-bold uppercase tracking-[0.2em] text-[#376D58]">Watch</p>
        <h2 className="mt-3 text-4xl font-bold tracking-tight text-[#0C2135] md:text-5xl">Ceda in 30 seconds.</h2>
        <div className="mt-10 overflow-hidden rounded-2xl shadow-[0_30px_80px_-20px_rgba(8,23,38,0.35)]">
          <video controls preload="none" poster="/landing/film-poster.webp" className="aspect-video w-full bg-[#081726]">
            <source src="/landing/ceda-demo.mp4" type="video/mp4" />
          </video>
        </div>
      </section>

      {/* Closing call to action */}
      <section className="px-6 pb-24">
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-3xl bg-[#081726] px-8 py-16 text-center text-[#E9EEF4] md:px-16">
          <div className="pointer-events-none absolute -bottom-40 left-1/2 size-[600px] -translate-x-1/2 rounded-full bg-[#6FB497]/15 blur-[120px]" aria-hidden="true" />
          <h2 className="relative text-4xl font-bold tracking-tight md:text-5xl">See it on a case like yours.</h2>
          <p className="relative mx-auto mt-5 max-w-2xl text-lg text-[#A9B8C8]">
            We&apos;ll walk you through a full case, from the first payslip to the recorded decision.
          </p>
          <div className="relative mt-9 flex flex-col items-center gap-3">
            <DemoButton light />
            <a href={DEMO_HREF} className="text-sm text-[#A9B8C8] hover:text-white">
              {DEMO_EMAIL}
            </a>
          </div>
        </div>
      </section>

      <footer className="border-t border-[#E1E5EA] bg-white">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10 md:flex-row md:items-center">
          <CedaLogo className="text-2xl" />
          <p className="max-w-xl text-sm text-[#677587] md:ml-auto md:text-right">
            Pilot product, shown with synthetic data. Outcomes are indicative only: not a lending decision, a mortgage offer or
            advice.
          </p>
        </div>
      </footer>
    </div>
  );
}
