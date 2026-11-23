# Agentic Pre-qualification and Affordability Assessment: Report

Date: 2026-11-23 · [Plan](AGENTIC-ASSESSMENT-PLAN.md) · Not yet committed

## What was built

| Area | Where |
|---|---|
| Versioned UK policies (`uk-resi-standard/1`, `uk-resi-cautious/1`) | `backend/src/meip/assessment/policy.py` |
| Deterministic affordability engine and scenarios | `assessment/affordability.py` |
| Inputs from the approved/current profile, with verification status | `assessment/inputs.py` |
| Pre-qualification criteria and four outcomes | `assessment/eligibility.py` |
| Contextual evidence gaps | `assessment/evidence.py` |
| 16 tools with schemas, validation, permissions, idempotency | `assessment/tools.py` |
| Rules and OpenAI planners; reasoning–action–observation loop | `assessment/planners.py`, `assessment/agent.py` |
| State: assessments, steps, evidence requests, broker decisions | migration `0021`, `assessment/models.py` |
| API (start, list, read, decide, requests, scenarios, policies) | `api/routers/assessment.py` |
| Assessment workspace, loan details form, case page link | `frontend/app/cases/[caseId]/assessment/page.tsx`, `lib/assessment.ts` |

Fixed in passing: the frontend forwarder had no PATCH handler, so the Team page's "remove
access" call could never reach the API.

## Brief's acceptance tests (§10)

| # | Scenario | Test |
|---|---|---|
| 1 | Complete assessment | `test_a_complete_assessment_end_to_end` |
| 2 | Missing documents | `test_missing_statements_are_requested_then_new_evidence_is_reassessed` |
| 3 | Conflicting salary | `test_a_salary_discrepancy_is_investigated_recorded_and_escalated` |
| 4, 5 | Multiple incomes, loans | `test_multiple_incomes_are_counted_once_and_commitments_reduce_affordability` |
| 6, 7, 8 | Insufficient income, failed criteria, scenarios | `test_an_unaffordable_loan_is_not_currently_eligible_with_alternatives` |
| 9, 10 | New evidence, reassessment | as 2 |
| 11 | Broker overrides | `test_broker_decisions_overrides_and_duplicate_runs` |
| 12, 13 | Failed and duplicate tool calls | `test_failed_and_repeated_tool_calls_are_recorded_and_the_run_completes` |
| 14 | Versioning, traceability | `test_finished_assessments_are_final_and_isolated` |
| — | Model planner and fallback | `test_the_model_planner_drives_a_run_and_falls_back_when_unavailable` |
| — | Planner chooses different actions | `tests/unit/test_assessment.py` (rules planner section) |

## Known limitations
- The OpenAI planner is tested against recorded responses, not the live model. Set
  `MEIP_AGENT_PLANNER=openai` on the QA backend to use it; it falls back to the rules planner
  (and says so) if the model is unavailable.
- Credit history and deposit evidence are "not assessed" (no supported document types yet).
- The latest salary can only be verified once a statement covers a few days after its pay date
  (existing `bank-checks/1` window); the evidence request says which dates.
- A run interrupted by a restart is marked stale after 15 minutes.
