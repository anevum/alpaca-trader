# RHEN v3 — Unified Runtime

Status: IMPLEMENTATION / CUTOVER
Date: 2026-10-05
Branch: `architecture/anevum-core-v3-20261005`

## Decision

RHEN is the only system/product name.

The previous names remain only as internal module aliases during migration:

- IREN -> Control
- GRAEN -> Research
- VELUM -> Replay
- NOSTRA -> Forecast
- Foundation -> Store/API
- Research Agent -> Research Worker

They are no longer independent top-level systems and are not intended to remain separate Railway services.

## Why this rebuild exists

The previous deployment was over-fragmented:

- 10 long-lived RHEN application services plus scratch CI.
- 4 ANEVUM Core application services plus PostgreSQL.
- Most services used the same repository and called each other over HTTP.
- PostgreSQL reached ~4.99 GB on a 5 GB Hobby volume and crashed during WAL recovery.
- Railway metrics showed growth from ~3.45 GB to ~4.99 GB in roughly 24 hours.
- High-frequency decision-cycle telemetry repeatedly stored large nested candidate/replay payloads.

The root failure was architecture, not simply insufficient disk.

## Final Railway topology

### One production application service: `RHEN`

The existing `alpaca-trader` service is converted in place and keeps its existing 5 GB `/data` volume.

Inside the RHEN container:

- Execution
  - Alpaca execution
  - equities and crypto lanes
  - positions/orders/fills
  - risk controls
  - reconciliation
  - pre-open/session handling
- Core
  - bounded SQLite state/evidence store
  - canonical gateway APIs
  - scheduler state
  - research state
- Research
  - strategy evaluation
  - V15 research executor
  - crypto edge discovery
  - forward shadow
- Replay
  - deterministic replay/simulation
- Forecast
  - baseline and calibration workflows
- Control
  - health
  - incidents
  - scheduler/orchestration
  - protected-action gating
- Research Worker
  - evidence review/model-assisted research
- Command/API routing

The existing `crypto-symbols-ci-20261004` remains a one-shot CI scratch service only.

## Runtime isolation

One Railway service does not mean every module receives trading authority.

The supervisor constructs a separate environment for each subprocess:

- Execution receives the existing broker configuration.
- Research/Replay/Forecast/Control processes have execution flags forced off.
- Pure Core processes receive `ALPACA_API_KEY=DISABLED` and `ALPACA_API_SECRET=DISABLED`.
- Modules that currently require Alpaca only for market-data/replay keep market-data access during the compatibility phase, but execution flags remain disabled.
- RHEN remains the only externally reachable service.
- Internal module traffic uses loopback HTTP only.

Longer-term cleanup can replace remaining direct market-data credentials with one internal read-only market-data adapter without changing the external topology.

## Storage

RHEN v3 uses the existing RHEN 5 GB `/data` volume.

Primary store:

`/data/rhen-core.db`

SQLite configuration:

- WAL mode
- synchronous=NORMAL
- foreign keys enabled
- busy timeout
- automatic WAL checkpointing
- one canonical Core writer

The failed PostgreSQL database is no longer required for RHEN v3 operation.

The old PostgreSQL service and its volume are not deleted during cutover.

## Storage budget

Normal target: < 500 MB

Warning threshold: 500 MB

Analytics shedding threshold: 750 MB

When the shedding threshold is reached:

- routine analytics are dropped
- critical execution/order/fill/reconciliation records continue
- health reports storage pressure

The system must never allow routine telemetry to consume the remaining volume.

## Evidence contract

### Durable

Keep long-term:

- orders
- fills
- position lifecycle/reconciliation
- promoted strategy definitions
- protected research decisions
- incidents/human approvals
- final replay results
- deployment/source provenance

### Retained

- compact decision-cycle summaries: 7 days
- normalized candidate observations: 7 days
- position metrics: 14 days
- routine evidence/forecast records: 30 days

### Not stored as a warehouse

- raw Alpaca bars that can be fetched again
- entire scan dictionaries every poll
- duplicate nested candidate metadata
- routine heartbeats with no state change
- full replay time series when a summary/spec/source hash is sufficient

## Compaction implemented

A `decision_cycle` no longer persists the full candidate list.

RHEN Core stores:

- one compact cycle summary
- all qualified/selected candidates
- at most three rejected samples per cycle
- a reduced feature vector

Position metrics are bucketed to five-minute identities instead of producing a new durable row every poll.

## Research/Forecast continuity

The SQLite Core exposes compatibility routes for:

- evidence ingestion
- GRAEN/research problem state
- scheduler ledger
- research-agent canonical evidence
- NOSTRA work
- protected crypto-promotion status

V15 remains frozen as:

`V15-R1-BTC-R2H-BREAKOUT-42-15`

The rebuild does not change its strategy definition.

V15 remains shadow/paper only until fresh forward evidence and protected promotion requirements are satisfied.

## Safety

During migration:

- live V15 promotion remains disabled
- research code has no live promotion authority
- no broker-order authority is granted by Core
- code-promotion workflow is explicitly gated during store migration
- legacy services are disabled only after RHEN v3 is healthy
- legacy services/volumes are not deleted without explicit approval

## Validation

The exact RHEN v3 branch has passed:

- full staging suite: 1,208 passed, 8 skipped
- focused RHEN v3/research/NOSTRA/V15 suite: 54 passed
- GitHub CI: green
- Foundation runtime audit: green

## Cutover sequence

1. Merge validated RHEN v3.
2. Convert existing `alpaca-trader` service in place to the RHEN supervisor.
3. Keep the existing `/data` volume.
4. Route all internal persistence/gateways to localhost RHEN Core.
5. Verify Execution + Core critical health.
6. Verify Research, Replay, Forecast, Control, and Research Worker module health.
7. Verify SQLite storage growth and evidence compaction.
8. Resume V15 isolated forward shadow.
9. Disable legacy Railway services one by one.
10. Leave old services and PostgreSQL volume undeleted until explicit retirement approval.

## Definition of done

The rebuild is operationally complete when:

- one production Railway application service, RHEN, is running
- one persistent RHEN volume is used
- PostgreSQL is no longer an operational dependency
- old subsystem services are stopped
- Command reports real module health from inside RHEN
- routine telemetry is bounded
- V15 forward shadow is active through RHEN Core
- execution authority remains isolated from research/control logic
