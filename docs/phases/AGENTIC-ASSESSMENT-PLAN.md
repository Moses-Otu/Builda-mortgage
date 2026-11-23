# Agentic Pre-qualification and Affordability Assessment: Plan

Date: 2026-11-23 · Status: **Agreed direction; built straight through (decision P3)** ·
Baseline: `ee75cb2`

## 1. What this adds

A **Mortgage Assessment Agent** on top of the existing evidence platform. The platform already
turns documents into a reviewed, reconciled, broker-approved financial profile (Phases 1–3).
The agent uses that profile to drive a mortgage assessment: it inspects the case, decides what
to do next, calls tools, interprets what they return, requests missing evidence, investigates
discrepancies, runs a deterministic pre-qualification and affordability assessment, explores
scenarios, explains its findings, and escalates to the broker. **The broker makes every final
decision.**

## 2. Decisions

| # | Decision |
|---|---|
| P1 | **UK market (£).** Policies are UK residential, indicative. Currency stays GBP |
| P2 | **Built now**, ahead of the remaining Phase 4 safeguards (ROADMAP updated) |
| P3 | **One overall plan, then built straight through**, reported at the end |
| P4 | **Agent model: OpenAI** (already integrated), behind a planner interface |
| P5 | **Three layers never mix** (ARCHITECTURE §2, unchanged): the model chooses actions and writes explanations; **every figure, ratio and outcome comes from deterministic, versioned, tested Python**. The model never does arithmetic and cannot set an outcome |
| P6 | **Outcomes are indicative, never a lending decision or advice.** The pre-qualification produces one of four labelled outcomes; the broker accepts, overrides (with a reason) or escalates. This rewords ARCHITECTURE's "no pass/fail outputs" rule (see §8) |
| P7 | **Policies are code-defined, versioned rule sets** with visible parameters (like `payslip-checks/1`), never in a prompt. Two to start: `uk-resi-standard/1` and `uk-resi-cautious/1` |
| P8 | **Two planners behind one interface**: `openai` (the model reasons and picks tools) and `rules` (a deterministic planner, used in local and test, and as the fallback when no model is configured). The same pattern as the synthetic and OpenAI extraction providers |
| P9 | **The agent acts as a system actor** (`agent`, on behalf of the user who started it) with an explicit capability allowlist: it reads the case and writes only assessment records. It can't change financial data, reviews, documents or approvals |
| P10 | **Data minimisation to the model:** applicants are "Applicant 1/2", no dates of birth, no account numbers; requests use `store: false` |

## 3. Components

| Component | Where | What |
|---|---|---|
| Policy engine | `assessment/policy.py` | Versioned policies: LTI cap, LTV cap, stress rate, DTI cap, minimum income, buffer, default living costs, income inclusion rates (variable pay, rental), eligible employment bases, minimum evidence months |
| Inputs | `assessment/inputs.py` | Reads the profile (`profile.service.snapshot`, unchanged) and derives per-applicant income by source with its **verification status** (verified · evidenced · declared · missing), commitments and expenditure, normalised to monthly. Gross pay is used as a whole, never added to its own components (no double counting) |
| Affordability engine | `assessment/affordability.py` | Pure functions over integers (pence) and `Decimal` rates: repayment (annuity), stressed repayment, disposable income, DTI, LTV, LTI, maximum affordable repayment and loan, total repayable, binding constraint. Scenarios re-run the same functions |
| Pre-qualification | `assessment/eligibility.py` | Criteria (income, employment basis and tenure, probation, LTV and deposit, LTI, affordability, DTI, term and age, property use, credit history) → met · not met · unknown, each with reasons and evidence. Four outcomes: **potentially eligible**, **eligible subject to further verification**, **not currently eligible**, **insufficient information** |
| Evidence gaps | `assessment/evidence.py` | Contextual requirements from the policy and the case: recent payslips per employment, statements covering the same months, salary corroboration, evidence for declared-only income, deposit evidence, credit history, unacknowledged discrepancies. Each gap says what, for whom, and why |
| Tools | `assessment/tools.py` | 16 tools, each with a Pydantic input and output schema, validation, error handling, a permission, logging and an idempotency key |
| Agent | `assessment/agent.py`, `planner_openai.py`, `planner_rules.py` | The reasoning–action–observation loop; step limit, repeat limit, schema validation, cached repeats, failure handling |
| State | migration `0021`, `assessment/models.py` | `assessments` (versioned per case), `assessment_steps` (every tool call, input, output, short rationale), `evidence_requests`, `assessment_decisions` (broker) |
| API | `api/routers/assessment.py` | Start (runs in the background), list, read with steps, broker decision, evidence requests, scenario calculator, policies |
| UI | `frontend/app/cases/[caseId]/assessment` | Workspace: overview, agent activity, financial assessment and scenarios, evidence intelligence, broker decision |

### 3.1 Tools
`get_assessment_context` · `get_applicant_profile` · `get_financial_evidence` ·
`identify_evidence_gaps` · `reconcile_financial_evidence` · `validate_income` ·
`calculate_monthly_commitments` · `calculate_disposable_income` · `calculate_affordability` ·
`evaluate_eligibility` · `simulate_mortgage_scenarios` · `generate_evidence_request` ·
`record_finding` · `escalate_to_broker` · `update_assessment_status` · `generate_assessment_report`
(finishes the run).
Reuse: `reconcile_financial_evidence` is the existing checks; the profile, provenance and
"Supported by" are reused as they are.

### 3.2 Controls
- **Duplicate assessments:** one queued or running assessment per case (partial unique index).
  Starting again with an unchanged profile, policy and case inputs returns the latest result
  unless the broker forces a re-run.
- **Duplicate tool calls:** a repeated call with the same input in one run returns the recorded
  output (idempotency key = SHA-256 of tool and canonical input).
- **Failures:** a failed tool is recorded with an error code and the agent continues or
  escalates; the model being unavailable falls back to the rules planner, recorded as such.
- **Never fabricate:** a missing figure is reported as missing; scenarios are labelled
  hypothetical and never replace the base result; the report's outcome comes from the
  eligibility tool, not from the model.
- **Versioning:** assessments are insert-once results with version numbers; a new assessment
  never changes an earlier one. Each records the profile fingerprint, the policy version and
  the planner. Approved profiles are untouched.
- **Reassessment:** each run compares itself with the previous assessment (documents, figures,
  discrepancies, case inputs) and states which earlier findings are affected; evidence
  requests whose gap has closed are marked fulfilled.
- **Audit:** started, completed, evidence requested, escalated and broker decisions are audit
  events.

## 4. Broker controls
Review the assessment, its evidence and the agent's activity; **accept** the recommendation,
**override** the outcome (reason required), **request information** (creates an evidence
request) or **escalate**. Approving the financial profile stays where it is (Stage 3-B).

## 5. Left out (and why)
- **Credit reports, P60, SA302, asset statements:** not yet supported document types
  (Phase 5). Credit history is "not assessed" and deposit evidence is "not evidenced"; both are
  stated as limitations and evidence requests, never assumed.
- **Lender sourcing and product selection:** out of scope; policies are generic UK indicative
  rule sets.
- **Buy-to-let rental cover:** a buy-to-let case is referred to the broker (criterion
  "property use"), not assessed.
- **A separate job queue:** the run uses the API's background tasks; a run interrupted by a
  restart is detected as stale after 15 minutes and can be started again.
- **Multiple agents:** one agent with tools, as the brief asks.

## 6. Tests (brief §10)
Unit: policy, affordability arithmetic, eligibility outcomes, evidence gaps, rules planner
choosing different actions for different cases, OpenAI planner against recorded responses
(tool calls, malformed output, unavailable model), duplicate tool calls, failed tools.
Integration (real PostgreSQL): complete assessment; missing documents; conflicting salary;
multiple income sources; existing loans; insufficient disposable income; failed criteria;
scenarios; new evidence and reassessment; broker override; duplicate assessments; versioning
and audit; isolation (RLS); permissions matrix; erasure.

## 7. Definition of done
The brief's 12-step journey works end to end on the QA site with synthetic documents.

## 8. Changes to ARCHITECTURE.md
"No automated advice/decisions" becomes: no lender selection and **no final lending
decisions**; the pre-qualification outcome and affordability figures are **indicative**,
labelled so in the API and the UI, computed by versioned rules from visible assumptions, and
always subject to the broker's decision.

## 9. Guided journey (2026-11-23, second brief)
The case page is the hub of a six-stage journey, shown on every stage's page with the current
stage highlighted, done stages ticked, stages passed with something outstanding in amber, and
**one** primary action. Everything comes from `GET /cases/{id}/journey`
(`assessment/journey.py`), derived from the case's real state; the screens add nothing.

| # | Stage | Where | Done when |
|---|---|---|---|
| 1 | Applicant and loan details | case page | a borrower, an income source (employment needs a start date), property value, loan amount and term |
| 2 | Payslips (or "Income evidence") | case page | the evidence the policy needs for each income is reviewed |
| 3 | Bank statements | case page | statements confirmed and each salary seen arriving; optional when no payroll income. Once every statement on file is confirmed, a month still unshown stays listed (amber) but doesn't hold up approval |
| 4 | Approve financial profile | `/profile` | the profile is approved (case `verified`) |
| 5 | Mortgage assessment | `/assessment` | an assessment completed for the **current** approval |
| 6 | Broker decision | `/decision` | an accept or override recorded on it |

**Backend-enforced transitions.** The first upload moves a draft case to `collecting_evidence`
(in the upload's own audit event). Approval needs only genuine work (documents reviewed,
discrepancies acknowledged): no manual "ready for approval" step. Approving starts the
assessment by itself, bound to that approval (`assessments.approval_id`, migration 0022);
`POST …/assessments` refuses with 409 `assessment-blocked` when the profile isn't approved or
the loan details are missing. Adding evidence to a verified case reopens it (with a reason),
superseding the approval; the journey then shows "New evidence received — Reassessment
required." and the single action "Reassess Mortgage Application". Approving again starts the
next version; earlier versions and decisions stay in the history.

**Case details.** Cases carry an optional product rate (`product_rate_percent`; blank uses the
policy's assumed rate, stated in the results) and a lending policy (`policy_code`). The New
case form captures the applicant, their income type, employer and start date, and the loan in
one step. `GET /cases/{id}/financial-position` gives the consolidated position for the profile
review, with each line's status (verified, extracted, declared, inferred, unverified).

**Screens.** Profile review: the position, discrepancies prominent, and "approving the profile
is not approving the mortgage". Assessment: the agent's real steps as they're saved, then
pre-qualification, affordability and scenario analysis as three separate sections; no manual
run button. Decision: the overall assessment, findings, risks, discrepancies, evidence,
recommendations and limitations beside the decision form (override and every non-accept
decision need a reason) and its history.
