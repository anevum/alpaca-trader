# ANEVUM.OPS.PACKAGE.2026-10-08.001.LEAN-SYSTEMS-OVERHAUL

Status: PREPARED / BLOCKED ON WEBSITE OVERHAUL COMPLETION  
Owner: ANEVUM / OPS  
Prepared: 2026-10-08  
Implementation target: post-website publication  
Repository baseline observed: `anevum/rhen@f9dad71c079aa246df1baecf5ec0a24786ca1318`  
Website dependency observed: `anevum/anevum-web:web/2026-10-08-studio-portfolio-overhaul`

## Purpose

Reduce ANEVUM from a collection of continuously resident pseudo-agent services into a lean trading system with deterministic evidence collection and episodic AI-assisted research.

The business objective is simple:

```
trade -> measure -> build evidence -> propose experiment -> replay/validate -> operator promotion -> trade
```

Persistent compute is reserved for work that must happen while the market is being observed or traded.

## Locked architectural direction

- RHEN is the only continuously resident product runtime.
- The live order path remains deterministic. No LLM or external reasoning service may be required for order submission, sizing, exits, or safety.
- NOSTRA remains, but becomes an embedded numerical forecasting component rather than an independently resident "AI" service.
- IREN remains, but becomes lightweight deterministic supervision, scheduling, configuration identity, incident detection, and recovery.
- GRAEN remains the research methodology and experiment-design function, but advanced-model reasoning is episodic and operator-invoked rather than a permanent Railway dependency.
- VELUM remains the replay/counterfactual laboratory, but runs only when evidence or an experiment requires it.
- The Research Agent is not a public/internal product. Its deterministic evidence work is folded into the evidence pipeline; semantic/model review moves outside the always-on production runtime.
- Promotion remains explicit and manual.
- Architecture cleanup and strategy changes must be measured separately so performance attribution is not destroyed.

## Hard prerequisite

Do not begin the runtime cutover while the current major website overhaul is still in flight.

Implementation may begin only after:

1. the website overhaul branch has been merged to the intended production branch;
2. the published site and Command have passed runtime/browser QA;
3. the final website product registry and Command route contract are known;
4. the current RHEN release/authority copy is truthful after publication;
5. a post-publication Git SHA is recorded in this package.

At preparation time, the website overhaul branch was 53 commits ahead of `main` and 0 behind. Recheck this at execution time; do not assume that snapshot remains current.

## Workload documents

- `CURRENT_STATE.md` — observed production/runtime baseline and architectural debt.
- `TARGET_ARCHITECTURE.md` — exact target responsibilities and process topology.
- `IMPLEMENTATION_PLAN.md` — ordered cutover sequence.
- `AI_AND_RESEARCH_POLICY.md` — where advanced models may and may not exist.
- `RUNTIME_AND_COST_BUDGET.md` — compute targets, measurements, and cost gates.
- `CONFIG_RETIREMENT_MATRIX.md` — environment/configuration cleanup categories.
- `WEBSITE_ALIGNMENT.md` — post-overhaul public/Command contract.
- `VALIDATION_AND_ROLLBACK.md` — required checks, rollback boundaries, and completion definition.

## Non-goals

This package does not:

- claim a profitable trading edge;
- authorize additional live instruments, shorting, options, margin, or leverage;
- change live strategy parameters merely to make an architecture migration appear successful;
- remove canonical evidence, reconciliation, safety controls, or operator authority;
- require a new database, CMS, Railway project, or paid always-on AI worker.

## Completion definition

The overhaul is complete when:

- one permanent RHEN Railway service contains the required live trading/runtime functions;
- no permanent shadow service exists solely for observation;
- no production trading process requires an OpenAI/API-model call;
- deterministic post-session evidence is produced automatically;
- GRAEN/AI research can consume a stable evidence package without broker credentials;
- VELUM can be invoked on demand against a defined experiment;
- NOSTRA forecasts are point-in-time, scored against later outcomes, and measurable against simple baselines;
- IREN health/configuration/schedule safety is preserved without acting like a reasoning agent;
- the public website and Command describe the same real architecture;
- measured Railway usage is materially lower than the pre-overhaul baseline without degrading execution reliability.
