# Architecture decision 002: RHEN is the integrated engine

**Program:** `ANEVUM.V5.FOUNDATION.2026-10-09.001`  
**Decision:** APPROVED direction for V5 development; clarified 2026-10-10  
**Supersedes:** Design assumptions that GRAEN, VELUM, NOSTRA, or IREN must exist as separate always-on applications/services.  
**Production authorization:** NONE. The existing founder-owned live RHEN stays running independently and unchanged.

## Why the architecture changes

GRAEN, VELUM, and NOSTRA are exploratory **prototypes**. Their evidence, replay and prediction concepts are valuable; their separate names, service lifecycle, networking, health status and deployment obligations are not. ANEVUM's near-term actual application is **RHEN**, and its website is **Commons**. Do not design, provision, sell, or imply production-ready standalone GRAEN / VELUM / NOSTRA applications.

A modular monolith keeps one versioned codebase and shared deterministic contracts without pretending that unrelated components are independently healthy products. Isolation is a **permissions/process** decision rather than the need for new brands and always-on servers.

## Canonical product and module map

| Historical name | New V5 scope inside RHEN | Runtime | Release claim |
| --- | --- | --- | --- |
| GRAEN prototype | `rhen.research`: hypotheses, rule-set lab, experiment queue, statistical verification | On-demand / bounded offline job; **no live broker writes** | Not production-validated |
| VELUM prototype | `rhen.replay`: point-in-time deterministic replay, fill/stop parity, paper shadow evaluation | On-demand / bounded offline job; shares pure RHEN strategy/risk code | Not production-validated |
| NOSTRA prototype | `rhen.forecast`: optional research-only regime features and forecast calibration | Library/offline job behind evidence and safety gates | Not a source of live trading decisions without separately approved validation |
| Legacy research agent / scheduler | `rhen.jobs`: same-process scheduler / bounded job launcher with persistent jobs | Lightweight scheduler; expensive work in separate **ephemeral process** | Scheduled completion never substitutes for evidence PASS |
| IREN prototype | No standalone service in V5. Keep essential `rhen.ops` deterministic health, alerts, integrity, risk/status surfaces. | Lightweight always-present RHEN operations code | Future IREN is an **optional separate project**, revisited later on local GPU or affordable hosted compute |

**IREN is postponed, not an excuse to postpone RHEN safety.** Crash monitoring, protective broker controls, archival checks, cost budgets, security audit logs, job failure signals, reconciliation and Slack alerts remain required and do **not** need a GPU. Future IREN may become a broader local AI/operator and cross-application control plane if/when useful; it is never a dependency for live order execution.

**The Evidence Vault is RHEN's data store/capture boundary**, not another product/service. R2, SQLite WAL, optional Postgres, and on-demand DuckDB are storage/compute dependencies, not separate branded apps.

## Target footprint

```text
ANEVUM Commons — Cloudflare Worker + member D1
  public feed, accounts, paid support, learning
  authenticated private RHEN workspace UI
       |
       | scoped versioned API; no raw private fill publication
       v
RHEN V5 — one successor application/codebase on Railway (initially PAPER ONLY)
  live-safe core: market feed, universe, complete scan decisions,
     deterministic strategy + portfolio/risk + paper broker adapter
  RHEN Evidence Vault: append-only WAL/outbox => private R2 archive
     => reference-data and outcome maturation => source verifier
  RHEN Research (formerly GRAEN): experiments, rule structures, scorecards
  RHEN Replay (formerly VELUM): same-engine counterfactual simulator
  RHEN Forecast (formerly NOSTRA): optional offline evaluation
  RHEN Operations: schedule, integrity, alerts, cost and risk
       |
       +----> ephemeral offline research/replay jobs (no broker-write token)
       +----> authenticated, workspace-scoped read surfaces for Commons
       
Legacy founder RHEN 4.3.2 — independent running Railway production
  NO import, restart, data migration or broker changes from V5 drafting

Future IREN — *not part of this release*
  reconsider local model/GPU/hosted inference after RHEN is reliable
```

"One RHEN" means **one application and codebase**, not that research and broker execution run in the same process or share credentials. Separate offline jobs with bounded CPU/RAM are an internal execution mode. Multiple worker replicas for scale may be added later with account-scoped leases; never provision a full always-on stack per subsystem/member.

## Module interfaces and scientific acceptance

- `rhen.capture` persists the **entire** pre-compaction candidate population and point-in-time market/universe references. Missing input is `AWAITING_EVIDENCE`, not a successful research result.
- `rhen.research` consumes only verified immutable session Evidence Passports; can suggest/challenge rule *structures*, never auto-push a live strategy.
- `rhen.replay` imports the same pure strategy/risk engine but replaces broker IO with simulated fills under replayed cash, stops, costs and session calendar; provenance parity tests must pass.
- `rhen.forecast` outputs recorded pre-outcome predictions and calibration baselines; any influence over actual entries requires isolated tests, unseen holdout and manual approval.
- `rhen.ops` owns health, archive backlog and failed jobs. Do not claim an IREN server is green when there is no independent IREN deployment.
- Role and evidence status contracts are shared with Commons; each user only accesses their own RHEN workspace. The founder's legacy terminal remains strictly owner-only.
- Failed backtests and evidence-blocked runs are legitimate explicit outcomes. No alpha claims until full accepted/rejected source evidence, same-engine replay and independent holdout have been proven.

## Migration — preserve functionality, remove needless complexity

1. **Inventory** existing prototype imports, CLI commands, routes, tests, datasets and published pages. Record owners and consumers; no mass deletion while legacy trading operates.
2. **Build** future RHEN V5 `research/`, `replay/`, `forecast/`, `ops/` modules in the isolated successor. Promote individually only after parity/source tests; leave unproven prototypes disabled.
3. **Change** Commons descriptions, docs and private Command menus to refer to RHEN Research, RHEN Replay, and RHEN Forecast as *capabilities*. Show honest unavailable states until functional. Preserve published historical docs and safe redirects. Do not market separate paid products or invented live activity.
4. **Remove** duplicate deployment/schedules/status monitors and obsolete standalone configs **only after** dependency inventory, working replacements, and owner release approval. No deletion of forensic history or evidence.
5. **Defer** future IREN launch, independent runtime, GPU purchases and autonomous operations until research quality, economics, and operator controls have justified them.

## Revised foundation build gates

- **F0:** preserve user/account privacy and versioned Commons↔RHEN schema; no API/broker permission drift.
- **F1:** local lossless RHEN decision journal, full candidate/universe event, checksum and restart tests.
- **F2:** private verified off-host Evidence Vault (R2), restore and independent upstream cycle-count attestation.
- **F3:** integrated RHEN Research + Replay (formerly prototypes) producing one fully reproducible real paper session and a meaningful negative-or-positive experiment with real cost assumptions.
- **F4:** optional RHEN Forecast evaluation, deterministic Operations and five-session reliability gate.
- **F5:** per-member paper workspace, separate privacy/tenant tests, deliberate live trading approval; no IREN release dependency.
- **Later:** evaluate IREN as a separately chartered local AI operator using owned GPU or scalable hosted model, with no role in safety-critical order paths.

## Integration and safety

- Parent locked architecture: https://github.com/anevum/rhen/pull/469
- First RHEN V5 code: https://github.com/anevum/rhen/pull/471
- Evidence Vault backlog: https://github.com/anevum/rhen/issues/472
- Main tracker: https://github.com/anevum/rhen/issues/468
- Commons workstream: https://github.com/anevum/anevum-web/issues/257
- Website V5.1 shell: https://github.com/anevum/anevum-web/pull/256

**This decision changes V5 roadmap and labels, not live execution.** The old components may still exist as code in legacy RHEN production until a fully reviewed migration. Nothing here asserts they have already been merged or retired.
