# RHEN 4.4 Discover -> Review research pipeline

Canonical session: ANEVUM.RHEN.UPDATE.2026-10-07.002.V4-4-CROSSOVER-VALIDATION

## Locked ownership

RHEN 4.4 does not have a separate operator-facing "shadow observation" workflow.

- **Ingestion infrastructure** owns read-only market and broker observation.
- **Discover / GRAEN** owns candidate observation, live market evidence, counterfactual policy evidence, coverage, hotset rotation, and NOSTRA research projections.
- **Review** owns reconciliation, replay/VELUM evidence, holdout evidence, profile approval evidence, restart/recovery evidence, and release/promotion decisions.
- **Operate** owns current broker/live-trading truth only.
- Observation and research processes have no broker-write authority and cannot promote themselves.

Legacy `shadow_*` SQLite names and legacy HTTP aliases may remain temporarily for evidence-preserving migration only. They are not a product, subsystem, or promotion lane.

## Discover contract

Discover consumes bounded read-only observation and emits evidence with explicit provenance.

Required evidence:
- canonical 100-symbol discovery universe and rotating <=24-symbol stream hotset;
- source-timestamped quotes and completed bars;
- asset eligibility;
- candidate/rejection evidence;
- NOSTRA projections against point-in-time observed references;
- research-only policy/capital counterfactuals;
- exact configuration fingerprint and strategy version;
- coverage denominators, source gaps, restarts, and warmup exclusions.

Missing or stale source data must remain a rejection. No forward fill may make a symbol evaluable.

## Overnight research ingestion

The existing comment that the free overnight feed has no historical endpoint is obsolete.

Current Alpaca behavior:
- `feed=overnight` supports latest bars and real-time indicative quotes on the free plan;
- `feed=boats` supplies historical overnight bars on a 15-minute delay for the free plan.

Implementation rule:
1. Keep the live `v1beta1/overnight` quote stream as the real-time observation source.
2. Use `overnight` latest-bar REST only for current completed-bar acquisition/warmup when websocket bars are absent.
3. Poll at the bar cadence, not quote cadence. Retain provider timestamps and deduplicate by source bar timestamp.
4. BOATS historical bars may bootstrap charts/replay and delayed context, but must be labeled delayed and must not satisfy a current/live bar freshness gate.
5. After restart, current candidate evaluation remains blocked until enough fresh current-source bars exist. Do not bridge the delay with fabricated bars.
6. Record the warmup interval in coverage evidence so restart downtime is visible rather than silently removed.

This preserves real-time quote scanning while keeping the bar-based feature path point-in-time valid.

## Review contract

Review consumes compact evidence from Discover and canonical RHEN sources.

Required gates:
- canonical order/fill reconciliation;
- raw stream coverage and known-gap classification;
- independent session coverage;
- VELUM replay and recovery tests;
- forward outcomes and untouched holdout evidence;
- cost/risk attribution;
- NOSTRA calibration evidence where used;
- approved profile-release evidence;
- authenticated Command visual acceptance;
- restart and stream recovery validation;
- manual promotion authorization.

No single engineering test, observer uptime metric, forecast, or counterfactual profile can authorize live promotion.

## Canonical ledger parity methodology

The current observer archive survives restarts, while the canonical ledger read is scoped to the current RHEN run. Comparing the whole retained broker archive against one run creates false "unattributed" account events.

Replace `canonical-observation-parity-v1` with a run-window methodology:

1. Require a verified current champion `run_id`.
2. Define the comparison interval from canonical current-run event timestamps and the observer's raw-stream-live timestamp.
3. Compare canonical current-run orders/fills only with observed trade updates in that same interval.
4. Broker events outside the current run remain account observations and are reported separately as `external_or_prior_run_events`; they do not count as current-run parity failures.
5. Canonical events that occurred before the raw broker stream became live are classified as `pre_observation_gap`, not as a raw-stream failure.
6. Canonical events inside the live overlap that were not observed remain true `missing_observed_*` gaps.
7. Reconciliation may prove canonical account truth but never rewrite `raw_stream_parity_complete`.
8. Promotion evidence requires clean independent live-overlap sessions after the methodology is activated.

## Migration order

1. Restore/retain the last known-good read-only observation deployment.
2. Fix the syntax defect on the Railway-connected pre-crossover branch before its next deploy.
3. Expose primary `/v1/command/research/*` routes while retaining hidden `/shadow/*` aliases temporarily.
4. Move Command market-evidence UI from Operate to Discover.
5. Feed the compact validation summary into Review.
6. Implement overnight current-bar acquisition and delayed BOATS context.
7. Implement run-window ledger parity v2.
8. Collect independent research sessions.
9. Complete replay, restart/recovery, holdout, visual, and approval gates.
10. Only then consider 4.4 broker-write crossover.

## Current safety state

RHEN 4.3.2 remains the live champion and rollback baseline.
The isolated 4.4 observation deployment remains broker-write disabled.
No LLM belongs in the live order path.
No research result may automatically authorize promotion.
