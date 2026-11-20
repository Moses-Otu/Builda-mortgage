# Delivery Roadmap

Revised 2026-11-20 (the previous version, of 2026-11-19, had four stages). It is organised
around capabilities, not document types, following the
[financial domain assessment](phases/FINANCIAL-DOMAIN-ASSESSMENT.md) (decisions A1, A2, R1
and A3 approved).

**Product principle:** documents are evidence; the financial profile is the product. The
profile is independent of the documents that populate it: several documents give different
evidence about one underlying position (an income, a liability, an expenditure, later an
asset or deposit), and the platform reconciles them rather than creating unrelated figures.

**Vision:** from raw borrower documents to a broker-approved financial profile.
- **Evidence sources:** payslips, bank statements, P60, SA302, credit reports, accounts, other
  evidence.
- **Document intelligence:** classification, extraction, normalisation, validation.
- **Reconciliation:** cross-document checks, discrepancies, missing evidence, provenance.
- **Broker-approved profile:** income, liabilities, expenditure, assets, deposit.
- **Evidence pack and assessment support:** traceable records, pre-qualification support,
  affordability preparation.

**Working principles (unchanged):**
- **MVP, not infrastructure:** if a simpler implementation is secure and correct, use it.
  Keep tenant isolation, access control, auditability and data integrity. Write future needs
  down here instead of building them.
- **Keep the existing financial domain intact** unless a specific requirement shows a change
  is necessary.
- **Each step:** plan → approve → build → test → report → commit.
- **Synthetic data only** until Phase 4's safeguards are in place.

## Phases

| Phase | Outcome | Status |
|---|---|---|
| **1. Core financial domain and platform foundation** | Tenancy and RLS, roles, cases, applicants, audit; the financial model (items, typed observations with immutable measures, supersession); hardening. Phases 1, 1.1, 1.2 | Done |
| **2. Document intelligence and reconciliation** | **Payslips** end to end: upload and storage, background processing and safe PDF check, classification and extraction (deterministic synthetic provider behind `LayoutProvider`; a real provider is still to connect), broker review into the financial model, review screen, UI redesign. **Reconciliation:** `payslip-checks/1` (arithmetic, coverage, duplicates, year to date, declared versus evidenced basic). **Bank statements** ([plan](phases/STAGE-2B-BANK-STATEMENTS-PLAN.md), local and test only): extraction with field-level provenance and keyed account fingerprints; broker confirmation, accounts and holders; `bank-checks/1` (totals, running balance, gaps, salary credits with four outcomes) | Done |
| **3. Unified financial profile, evidence traceability and broker approval** | [Plan](phases/STAGE-3-BROKER-EXPERIENCE-PLAN.md). **3-A** profile with per-figure provenance (done) · **3-A2** (done) "Supported by" for each item: declared, documentary, corroborating, findings, approval, kept separate, with no count implying reliability (assessment section 3) · **3-B** (done) approval: versioned immutable snapshot, case `verified`, reopening supersedes · **3-C** evidence pack built only from the snapshot | In progress |
| **4. Minimum production safeguards and a controlled broker pilot** | Before **any** real borrower data, and before any new document type (decision R1). Safeguards and pilot below | Next |
| **5. Broader evidence coverage and financial assessment support** | New evidence types (P60, SA302, credit reports, accounts, other evidence), chosen in the order the pilot shows matters most. Domain additions as each type requires them, from the assessment: per-decision item binding (G1, with credit reports), per-type definitions (G2, with the third type), document-to-item links for evidence without figures (G3), assets and deposit (G4). **Assessment support:** positions per item as an explicit, versioned rule over the observations (never a "current" flag); missing-evidence rules; pre-qualification and affordability preparation, labelled indicative and never advice | Planned |
| **6. Integrations, operational readiness and commercial rollout** | Integrations and export (CRM and sourcing systems); borrower upload links (D4); operational readiness beyond the pilot minimum (scale, monitoring and alerting, support processes, disaster recovery, Docker verification); firm onboarding and commercial rollout | Planned |

### Phase 4 in detail

**Minimum production safeguards** (from the earlier Stage 4 list; each to be planned and
approved like any step):

| Area | Safeguard |
|---|---|
| Data protection | DPIA; retention and erasure policy and process (D10, retention brief R1–R17); processor terms with the extraction provider (training and retention exclusions); data processing agreements with pilot firms |
| Extraction | A **real extraction provider** connected behind `LayoutProvider` and **measured on an evaluation corpus** before pilot use. The synthetic provider is refused outside local and test |
| Identity and access | Entra live sign-in (D1) |
| Hosting | Azure deployment: IaC, Key Vault, managed identity, private endpoints; storage without delete for the app identity, and account hardening (R15, R16) |
| Documents | Malware scanning; resource isolation for the PDF process (**no memory limit today**); download access log (R17) |
| Database | A dedicated worker database role (the worker shares the API role today, an MVP compromise) |
| Resilience | Backup and restore drill |
| Assurance | Penetration test |
| **Bank statements** (refused in code until done, or kept out of the pilot) | A DPIA covering statements; retention for statement originals, raw output and extracted transactions (R8a); the account fingerprint key in Key Vault with a rotation plan; a real table-capable provider measured on an evaluation corpus |

**Controlled broker pilot:**
- a small number of firms, under agreement;
- limited volume and document types: payslips, and bank statements only if their gate is
  passed;
- success criteria agreed beforehand: extraction accuracy on real documents, review time,
  how often discrepancies arise and what they turn out to be, broker trust in the profile
  and evidence pack;
- the results decide Phase 5's order of document types.

## Decisions

| # | Decision | Needed by | Recommendation or status |
|---|---|---|---|
| D1 | Managed auth provider | Phase 4 (live integration) | Microsoft Entra External ID |
| D2 | Python packaging tool | — | uv |
| D3 | Job queue | — | Postgres `SKIP LOCKED` queue behind an interface (built) |
| D4 | Borrower access model (portal login or magic-link upload) | Phase 6 | Time-limited, case-scoped magic links; no borrower accounts |
| D5 | LLM model and region | Phase 4 (if the real provider uses one) or Phase 5 | Azure OpenAI in UK South or the EU Data Zone, subject to model availability |
| D6 | Synthetic test document corpus | — | Generated synthetic documents; **no real customer documents in the repo** (in place) |
| D10 | Retention and erasure | Phase 4 | See the retention brief |

## Cross-cutting risks

| Risk | Impact | Mitigation |
|---|---|---|
| Credit report terms of use (statutory or consumer reports may restrict commercial reuse) | Legal | Legal review before credit reports are built (Phase 5); explicit borrower consent; data processing agreements with firms |
| Regulatory drift into advice (FCA MCOB, Consumer Duty) | Regulatory | No recommendations or pass/fail; assessment support labelled indicative; broker approval required |
| UK GDPR: DPIA, data minimisation, retention | Regulatory | DPIA and the erasure process before the pilot (Phase 4) |
| Reliability read from quantity | Accuracy, trust | Support is shown by kind, each stating what it does and doesn't establish; no counts or scores (assessment section 3) |
| Bank statement PDF variety (layouts, scans, multiple accounts) | Accuracy | Layout-agnostic extraction; balance continuity checks to detect missed rows |
| Tampered documents | Fraud | Detection out of scope; originals and metadata preserved so later checks are possible; arithmetic inconsistencies flagged |
| Model availability in UK regions | Residency | Provider interface; a documented fallback decision, never silent |
| Tenant isolation bug | Severe | RLS defence in depth, plus a dedicated isolation test suite in every phase |
| Over-trusting AI confidence | Accuracy | Status driven only by deterministic corroboration or broker action |
