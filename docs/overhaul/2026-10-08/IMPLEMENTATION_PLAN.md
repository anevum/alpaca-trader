# Implementation Plan

This plan begins only after the website overhaul publication gate in `README.md` is satisfied.

## Phase 0 — Align and freeze

1. Record final website production SHA and published route/product contract.
2. Rebase this workload branch onto the then-current RHEN `main`.
3. Reconcile any 4.4 work completed after this package was prepared.
4. Decide the exact execution baseline:
   - promoted 4.4 execution behavior, if validated; or
   - current validated champion behavior with 4.4 research components retained behind gates.
5. Freeze strategy parameters for the architecture migration.
6. Capture the pre-cutover baseline in `VALIDATION_AND_ROLLBACK.md`.

Exit gate: no ambiguity about website truth, production SHA, live strategy identity, or the role of the current shadow.

## Phase 1 — Separate live-critical code from resident architecture

Inventory every supervisor child and classify it:

- LIVE_TRADING_CRITICAL
- EVIDENCE_CRITICAL
- OPTIONAL_RESEARCH
- LEGACY

Expected result:

### Resident
- execution
- canonical core/evidence store
- router
- minimal scheduler/IREN functions necessary for health and market-relative work

### Embedded library
- NOSTRA forecast/scoring
- deterministic evidence calculations
- pre-open features if validated

### On demand
- VELUM
- semantic research review
- GRAEN Research Director / external research
- experiment compiler where no continuous state is needed

### Remove
- dead legacy services/topology records
- crypto runtime/config paths no longer in scope
- unused independent-service compatibility aliases

Exit gate: a process manifest exists before code is deleted.

## Phase 2 — Build the deterministic evidence compiler

Make the post-session evidence package the center of the improvement loop.

Inputs:

- canonical trading ledger;
- scans/candidates/rejections;
- fills/exits;
- MFE/MAE;
- execution costs where known;
- runtime incidents;
- strategy/config identity;
- NOSTRA predictions and matured outcomes;
- prior experiment state.

Outputs:

- versioned JSON artifact;
- operator-readable Markdown summary;
- deterministic status: KEEP / INVESTIGATE / IMPLEMENTATION_DEFECT;
- zero automatic live mutation.

The compiler must be callable by the scheduler and manually.

Exit gate: one real completed session can be reconstructed without an LLM.

## Phase 3 — Embed NOSTRA

Refactor NOSTRA service logic into a library usable from RHEN.

Preserve:

- forecast contracts;
- `shrunken_drift`;
- naive baseline;
- point-in-time training boundary;
- persistence;
- scoring/evaluation.

Remove requirement for:

- a dedicated NOSTRA HTTP server;
- NOSTRA autorun process.

Add:

- direct forecast hook at the canonical candidate/observation point;
- direct outcome maturation/scoring job;
- calibration report in the evidence package.

Exit gate: same or better forecast evidence with no NOSTRA resident process.

## Phase 4 — Reduce IREN to deterministic ops

Keep:

- canonical scheduler;
- health/readiness;
- drift/configuration identity;
- incidents;
- recovery/restart observations;
- notifications;
- operator protected actions.

Disable/remove production reliance on:

- IREN model execution;
- model budgets;
- model autopilot identity;
- reasoning-based execution routing.

Prefer direct Python calls/shared runtime state over internal HTTP where process isolation no longer exists.

Exit gate: IREN can detect/configure/report failures with no model key.

## Phase 5 — Convert GRAEN and Research Agent

Fold deterministic Research Agent evidence work into the evidence compiler.

Move semantic/model reasoning to GRAEN's episodic workflow.

Create a stable export/import contract:

`evidence package -> research decision -> experiment spec -> persistent proposal`

The production service should expose the evidence/proposal state needed by Command, but it should not need to run a permanent semantic reviewer.

Exit gate: a Work/operator session can review an evidence package and return a bounded experiment without production broker credentials.

## Phase 6 — Convert VELUM to on-demand

Refactor VELUM so its core replay engine is callable without a permanent API process.

Scheduler behavior:

- no automatic daily replay unless an active experiment specifically requests it;
- experiments may enqueue replay jobs;
- weekly or incident replay is explicit.

If Railway job/function execution is used, ensure it terminates.

Exit gate: replay evidence can be produced on demand and no VELUM server is permanently resident.

## Phase 7 — Collapse the supervisor/runtime

Replace the current multi-uvicorn topology with the minimum process set.

Preferred end state:

- one main ASGI process for RHEN API/router/control surfaces;
- one market/execution loop/task where isolation is actually needed;
- background scheduler/evidence tasks in-process where safe;
- CPU-heavy replay isolated only while running.

Do not force a single process if doing so makes broker safety/restart behavior worse. The objective is minimum justified residency, not an aesthetic process count.

Exit gate: process topology passes restart/recovery and execution-equivalence tests.

## Phase 8 — Production configuration cleanup

Apply `CONFIG_RETIREMENT_MATRIX.md`.

Order:

1. remove code dependency;
2. deploy with variable still present;
3. verify no runtime read/need;
4. remove variable;
5. verify configuration identity;
6. accept new protected baseline.

Do not mass-delete variables first.

Exit gate: production configuration is comprehensible and contains no known dead agent/crypto/model surface.

## Phase 9 — 4.4 shadow decision and teardown

For `rhen44-shadow`:

- harvest final evidence;
- merge/promote the useful code or explicitly reject it;
- persist reproducibility metadata;
- delete/stop the permanent shadow service and volume when no longer needed.

Future shadows are temporary promotion instruments.

Exit gate: one permanent Railway service remains unless a time-bounded active promotion test is documented.

## Phase 10 — Website/Command truth patch

After backend topology is live:

- update architecture copy only;
- update Command System topology;
- remove "Research Agent is operational" language;
- mark GRAEN/VELUM as on-demand where appropriate;
- describe NOSTRA as numerical forecasting;
- preserve the new website design/routes;
- publish a Field Note/release record.

Exit gate: public/private UI matches reality.

## Phase 11 — Observe before optimizing strategy

Run the lean architecture for at least several trading sessions without changing strategy solely because of the migration.

Measure:

- trading reliability;
- evidence completeness;
- NOSTRA forecast capture;
- resource usage;
- cost trend.

Only then resume the normal improvement loop:

`evidence -> experiment -> VELUM -> promotion decision`

## Deliverables

Implementation should produce:

- process manifest before/after;
- config manifest before/after;
- scheduler registry update;
- evidence package schema;
- NOSTRA library migration;
- GRAEN experiment schema;
- VELUM on-demand runner;
- tests;
- resource comparison;
- final release/Field Note;
- updated website architecture truth.
