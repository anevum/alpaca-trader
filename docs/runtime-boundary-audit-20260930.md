# Production runtime audit and safe migration — 2026-09-30

## Scope and evidence

Read-only Railway inventory/configuration/logs, GitHub source and CI, live private
Supabase schema/control-state/incident records and Edge Function inventory.
No shell/checkout execution capability is exposed in this session; tests run in
GitHub Actions, including the repository's network-denied staging harness.
No AGENTS.md is present in either repository tree.

Protected RHEN baseline:
- Project RHEN: 808098a9-937e-4ca4-ac98-dd2dcfef5d0c
- Environment production: 63a64723-574d-497b-b01b-a9fef7ea78ab
- Service alpaca-trader: f933a669-8591-4233-8510-e0db1548e463
- Deployment 0e4deb0e-7417-445e-9446-26a699c01b00
- Commit 24df64cbe4a1d60e0efc6a03928867fbb1cb4337
- Strategy LIVE-2026-09-25-003; crypto execution false
- Observed protected fingerprint sha256:7ba29278e14b19e8e89c639b9d0dd0fb95e74748ee04c92d5ae33ea442f5afd6
- 14:11 UTC: reconciled, safe, scanning 100 symbols; telemetry drops 3586.
- 14:27 UTC: same strategy/commit, reconciled, telemetry drops 5236.
- No configuration.drift incident. Baseline dates to 11:30:43 UTC.

Dropped telemetry and intermittent delivery errors are pre-existing production
defects. The preflight incident recovered at 14:17:46 UTC. These findings are not
silenced by the architecture work.

## Factual topology before this change

All six services are separate Railway containers in sfo, one replica each,
with separate logs, deployment and restart lifecycles. No Railway cron schedules,
volumes, buckets or shared environment variables were configured.
Provider CPU/memory limits are not explicitly exposed in this configuration;
separate containers do not eliminate project quota/shared-provider failure risk.

Every start command below is prefixed with uvicorn and suffixed with
--host 0.0.0.0 --port 8080.

| Logical responsibility | Classification | Railway service / app target | Initial deployment |
| --- | --- | --- | --- |
| RHEN execution | INDEPENDENT SERVICE, still hosts research/evidence modules | alpaca-trader / app.main:app | 0e4deb0e-7417-445e-9446-26a699c01b00 |
| IREN supervision + canonical scheduler | INDEPENDENT SERVICE; scheduler is a subsystem in this process | rhen-research-scheduler / app.iren.service:app | de1a113a-e385-40b4-b988-c49829afc940 |
| VELUM replay | INDEPENDENT WORKER with HTTP control surface | rhen-velum / app.velum_service:app | 7de9b91c-ed8a-481b-8b8a-4ab2f8cf8381 |
| GRAEN crypto v5 | INDEPENDENT WORKER; not all GRAEN computation | rhen-crypto-edge-discovery / graen.crypto.service_v5:app | bc80fbcd-38b5-452c-85d3-4c0685e5d476 |
| Research agent | INDEPENDENT WORKER, on demand; observed SLEEPING | rhen-research-agent / app.research_agent.service:app | 5781e454-ffab-4ba4-9171-b5be5a7aa3eb |
| Preopen shadow capture | INDEPENDENT WORKER; not activated NOSTRA | rhen-preopen-state / app.preopen_state.service:app | e3460644-a8a2-4fea-b549-941705cf5340 |
| NOSTRA regime/session/transition calculations | SUBSYSTEM / functions in RHEN research reporting; five independent scheduler hooks disabled | No NOSTRA runtime | none |
| GRAEN adaptive validation, promotion assessment | MODULES / functions still invoked by RHEN research reporting | No separate runtime for this scope | none |
| Counterfactual replay research | MODULES / functions within RHEN reporting, alongside independent VELUM | No separate runtime for this scope | none |

RHEN Archive contains rhen-prehistory-archive, image python:3.13-slim,
with no deployment. It is not a running process.

### Process internals and triggers

RHEN lifespan starts the event sink, Slack notifier, mobile live activities,
research report scheduler, equity monitor, crypto monitor and Slack market
observer. It does not import/start app.iren.service or orchestration_scheduler.
ResearchReportScheduler continues crypto evidence/promotion observations.
RHEN_CANONICAL_SCHEDULER_ENABLED gates its older daily/weekly clock loop, not
all research computation; its value is redacted, so presence alone is not proof
that the gate is enabled.

IREN lifespan starts a scheduler task and deterministic supervisor task.
It probes RHEN, VELUM, preopen and crypto GRAEN via private HTTP; reads RHEN's
protected configuration fingerprint; uses scheduler-gateway for the durable job
ledger and IREN state. It never imports RHEN's execution runtime or broker client.
No in-process IREN loop exists in RHEN to disable.

The registry schedules RHEN preflight/open/session-close/weekly review,
VELUM equity at close+35m, VELUM crypto hourly at :05, research-agent daily
at close+25m, GRAEN checkpoint at close+50m, and IREN self-health every 15m.
IREN supervision polls every 60 seconds. Five NOSTRA hooks are disabled.
Preopen and crypto GRAEN have their own worker loops. Research Agent allows sleep.
VELUM supports its existing autorun gate and authenticated scheduler triggers;
its current health reported idle. Do not infer the redacted autorun value.

All use /health. IREN has authenticated /v1/iren/status and /v1/iren/policy,
plus scheduler /v1/status, /v1/diagnostics and /v1/registry.
RHEN exposes provenance and reconciliation in /health.
Deployed preopen commit f0335375c9811ab3650cde2a8cc33441f4e9f0a6 reports last_error-based health only. Main has a worker freshness check that is not deployed to that older branch.
VELUM/GRAEN expose limited legacy health; missing version/deployment data must
remain null, never populated from latest main or a historical deployment.

### Other execution infrastructure

Supabase Edge Functions are request-driven runtimes: trading-ingest (v3),
trading-reconcile (v3), trading-public-feed (v19), trading-report-read (v19),
research-agent-gateway (v6), agent-support-gateway (v4), iren-mobile-registry (v3),
scheduler-gateway (v3), command-operator (v1), and release-delivery-test (v2).
The only observed Postgres cron entry, anevum-deploy-worker (* * * * *), is disabled.

anevum-web uses Vite/React plus a Cloudflare Worker, with main deployment through
.github/workflows/deploy-production.yml and PR preview verification through
anevum-verify.yml. Monthly rebuild: 06:17 UTC on day 1. Vercel also supplies a PR
check; it is not evidence that the canonical site is served by Vercel.

## Data ownership and implicit calls

Keep the existing private schema. No table duplication, message broker or schema
migration is needed for this phase.

| Owner | Existing storage / contract |
| --- | --- |
| Shared identifiers/evidence | trading_events envelope: event_key, run_id, strategy_version_id, event_type, occurred_at, correlation_id, payload |
| RHEN | trading_runtime_instances, trading_scan_cycles, trading_candidate_evaluations, trading_signals, trading_order_intents/orders/fills/positions/exits, account snapshots, candidate forward outcomes |
| IREN | iren_control_state and iren_control_events; canonical anevum_scheduler_runs |
| NOSTRA | nostra_snapshots/forecasts/outcomes/scores already exist; table existence does not prove a deployed worker |
| GRAEN | trading_experiments/results/windows/symbol_coverage, research hypotheses/questions/decisions/leases/agent_runs/search_events and dependence/multiplicity plans |
| VELUM | Existing trading_events replay evidence and experiment outputs; no duplicate replay queue is introduced |
| Preopen | trading_preopen_snapshots/outcomes/model_artifacts |
| Public projection | public trading_public_*; operational topology stays private |

RHEN persistence synchronously computes ADS shadow attribution from candidate
metadata. RHEN research_scheduler calls NOSTRA session/transition functions,
GRAEN validation, counterfactual lab, strategy health/router and promotion gate.
Those formulas and call sites remain unchanged. Extracting them later requires
immutable input snapshots and parity checks before removing their original path.

TradingEventSink.emit uses a bounded in-memory queue and can drop evidence;
emit_critical retries durable ingest before new entries. Do not claim every
existing event is lossless. This phase's IREN observation and incident transition
are persisted together through the existing revision-fenced transaction.
Reuse the incident outbox and job lease ledger, not a second event system.

## Contracts and boundaries added

app/contracts/service_health.py defines service_observation.v1 and
service_heartbeat.v1 with strict flags, UTC timestamps and optional provenance.
app/iren/topology.py normalizes legacy HTTP health without importing observed
implementations. The resulting runtime_topology.v1 is persisted inside the
existing IREN state JSON. IREN is its only writer.

HTTP observations are identified as iren_http_probe; their last-success and
heartbeat mean successful observation, not completed research jobs.
Only IREN emits its own native durable heartbeat in this phase. Unknown fields
remain null. The inventory is explicitly an audited snapshot, not continuous
Railway inventory monitoring.

IREN /ready requires fresh durable state; /health remains process liveness to
avoid restart loops caused by a dependency outage. Status reads recompute stale
state at 180 seconds. A stopped writer cannot leave Command displaying healthy.

The new iren-command Edge Function reads only the canonical private state in a
read-only transaction. It verifies the user's token with Supabase Auth, checks
trusted app_metadata administrator claims, accepts GET only and suppresses
provider errors. Cloudflare's private proxy independently authorizes the user.
No ingest token or broker credential is sent to Command. iren_command.v1 includes
topology, open incidents, scheduler, freshness and action-required state.
The browser also expires snapshots and marks failed refreshes stale.

## Secret and permission audit

Railway OAuth exposes names only. No values were printed, copied, changed,
rotated or invalidated. Scope/equality of keys cannot be proven from names.

- RHEN: Alpaca key/secret, ADMIN_TOKEN, ingest token, Slack webhook, Pushward key.
- IREN: ingest token, research-review token, Slack webhook; no Alpaca credentials,
  ADMIN_TOKEN, Supabase service-role key or Railway-management credentials.
  SchedulerRuntime explicitly rejects Alpaca/admin credentials.
- VELUM, crypto GRAEN, preopen: Alpaca credential names plus ingest; VELUM/GRAEN
  also Slack. Their code reports no order authority, but these credentials'
  provider privilege needs independent verification before calling it least privilege.
- Research Agent: research gateway/admin token, OpenAI key, Slack; no Alpaca.
- Supabase private tables have RLS/revoked public access. The Edge Function uses
  the existing platform database credential with a read-only transaction; it is
  not yet a separate database role. Narrow role provisioning is future hardening.
- Ingest authentication is shared and is not yet per-subsystem table ownership
  enforcement. Logical ownership above is not a claim of SQL role isolation.

## Failure isolation and notification behavior

IREN, deployed VELUM, crypto GRAEN and Research Agent have separate process,
memory and restart lifecycles from RHEN. IREN failure cannot terminate RHEN.
IREN observes RHEN independently; Command reads persistent state independently
of RHEN and IREN process availability, while detecting stale observations.
NOSTRA and remaining in-process research calculations are not fully isolated.

One existing incident authority is retained. Revision fencing prevents two
rolling instances committing the same observation; outbox leases prevent
concurrent dispatch. Existing delivery is at-least-once: a crash after Slack
accepts but before acknowledgement persistence can still duplicate a message.
This phase does not promise exactly-once Slack delivery.
No synthetic alert is necessary: existing OPEN/RECOVERED events show one attempt,
delivered:fallback:iren-control. Dedicated routing is not configured.

## Safe deployment and rollback

Do not merge this backend PR during the active session: /app/** on main triggers
RHEN even though no RHEN code is modified. Pin only IREN to the tested branch
commit. Inspect staged changes and require that they concern service
8f52c715-c4fa-453f-a120-2183b5ace5c4 only. Never accept unrelated staged changes.
Preserve scheduler variables, start command, DNS and one logical owner.
Prefer a time with running_job=null. Leases remain durable across replacement.
The additive Command reader can deploy without touching RHEN or scheduler-gateway.

Rollback condition: IREN cannot commit two fresh observations; auth/readiness
checks fail; incidents/counters reset; scheduler fails to recover; or duplicate
active owners are observed. Re-run only the prior IREN deployment build
de1a113a-e385-40b4-b988-c49829afc940 using Railway redeploy for the IREN service.
Restore IREN source to main only after a safe window or pin the prior commit.
Do not reset durable state, delete incidents, replay completed jobs or restart RHEN.
Command tolerates absent topology; roll back frontend if its private read fails.

## Exact post-market procedure

No RHEN restart is required to establish IREN independence: that boundary already
exists. The risky deferred operation is merging this backend branch into main
because of existing Railway watch patterns.

1. Verify Alpaca exchange clock closed (including early-close calendar), no active
   execution cycle or unresolved protective-order issue, and operator-safe window.
   Recheck RHEN deployment, LIVE-2026-09-25-003 and the fingerprint above.
2. Review backend diff: no execution, strategy, formulas, risk, sizing, broker,
   universe or scheduler-registry changes. Require current CI green.
3. Confirm IREN is the sole control-plane service, fresh durable revisions and
   functioning incident outbox. Do not start a second service.
4. If merging main will rebuild RHEN, explicitly treat it as a RHEN deployment:
   affected service f933a669-8591-4233-8510-e0db1548e463. No environment-variable
   change is needed; start command remains uvicorn app.main:app ... .
   Merge only in this verified window. Do not remove research_reports or toggle
   RHEN_CANONICAL_SCHEDULER_ENABLED as part of this phase.
5. RHEN restart is expected only from main autodeploy, not from IREN cutover.
   Verify startup reconciliation, healthy scan/position lifecycle, unchanged
   strategy/fingerprint, source/deployment identity and evidence delivery.
6. IREN must remain alive through RHEN replacement. Require two fresh observations
   and incident recovery after three healthy observations. Inspect existing
   Slack delivery records; at most one synthetic operational test if still needed.
7. Preserve concurrent authorized work: capture the immediately preceding RHEN build again before any future deployment; do not blindly restore the initial audit baseline. Roll back RHEN only if reconciliation fails, protected identity changes, or
   execution cannot resume safely: redeploy prior build
   captured immediately before that cutover to the RHEN service, preserve all credentials
   and database evidence, and verify reconciliation before declaring recovery.
   Keep IREN running to observe the rollback.
8. Once merged runtime is verified, unpin IREN/reconnect its intended source and
   include /app/contracts/** in its watch patterns (or use infra/iren.railway.toml).
   Avoid an unnecessary RHEN restart merely to tidy deployment metadata.

## Next extraction, based on actual dependencies

1. Complete VELUM boundary hardening: extend the already deployed replay worker
   with native heartbeat/provenance and verified data-only credentials; move the
   remaining counterfactual replay invocation from RHEN behind the existing
   durable job ledger. Freeze input snapshots, strategy/config IDs, timestamp
   semantics and output parity before disabling the RHEN path post-market.
2. Move RHEN's research-report/validation/evidence calculations into GRAEN,
   keeping promotion read-only and formulas unchanged; preserve existing event
   and experiment keys. Resolve existing telemetry loss before relying on
   candidate events as complete extraction inputs.
3. Activate/extract NOSTRA only after the pending foundation and forecast
   methodology are separately validated. Its existing tables/hooks alone are
   not readiness. No forecasting formula or activation change in this phase.
4. RHEN remains the protected execution core. Do not migrate its broker loop.

This is a partial multi-runtime migration, not full isolation of every branded
subsystem. Verification results and final deployment identities are recorded in
the completion report alongside this audit.

## Concurrent changes observed during the pause

At the original IREN rollout, RHEN remained on 0e4deb0e-7417-445e-9446-26a699c01b00,
started 12:55:49 UTC. An independent main deployment at 15:34 UTC replaced it with
6b9d38f7-1fab-48e9-bc3e-3cf7bade637c, commit
b8f98fe1d8db8be1b65765b26a30ac60933b24ad. Its commit changes Alpaca cash-flow
reference-window handling in app/alpaca_client.py and app/cash_flow.py.
This migration did not create, merge or deploy that change.

Unchanged strategy ID/configuration fingerprint is NOT evidence that all RHEN
behavior stayed identical over the whole elapsed session. The broader
unchanged-runtime claim cannot be made. LIVE-2026-09-25-003 and protected
fingerprint still match the initial baseline; RHEN remains reconciled.

Crypto GRAEN also changed independently to service_v6:app, deployment
91c695ce-affb-48d7-8379-968419f501ec, pinned commit
a4b5147c55c1ffcf46c1b1640b9affde2403fedc, at 15:53 UTC.
The topology adapter reads its reported program version and no longer hardcodes
v5 in the scope description. No GRAEN formula or lifecycle is changed here.

IREN deployment 794eed38-e878-4e74-866e-014a7e95353b continued across the
independently triggered RHEN replacement. Revision 254 at 16:04:47 UTC showed
RHEN's new identity, IREN's original 14:47:51 start time and healthy scheduler
ticks. This is observed production isolation evidence; no RHEN restart was
initiated for testing.

Command production: frontend merge 1462a2c4e92d6f541f8224dbc75118bf5a23867c,
Cloudflare version 41a94d49-3b4d-4a8a-9aff-c9740b87e94e, deployed 14:50 UTC.
Production workflow 36732002793 and ANEVUM Verify 36732002519 succeeded.
Private production route rejects anonymous reads with HTTP 401. A signed-in
administrator browser session is not available to this agent; successful
administrator reads are covered by isolated tests, not claimed as an observed
browser session. The separate Vercel deployment status fails on both the base
commit and this PR; the Vercel connector repeatedly failed authentication, so
its root cause remains unverified. Cloudflare is the canonical deployment.
