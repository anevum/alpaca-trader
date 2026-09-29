# IREN Adaptive Strategy Control v1 — Rollout Contract

Status: DRAFT / POST-CLOSE DEPLOYMENT ONLY

This document defines how IREN ASC v1 may be introduced without changing the
currently authorized RHEN production strategy.

The implementation branch is intentionally not merged or deployed while RHEN
is operating during the regular market session.

## Preconditions

All of the following must be true before rollout begins:

- the regular US equity session is closed;
- RHEN reports no open position requiring active management;
- broker/canonical reconciliation is safe;
- the ASC pull request is mergeable;
- the full repository CI suite is green against current main;
- the current production commit and deployment IDs are recorded for rollback;
- no unrelated production incident is active.

A failed precondition stops the rollout.

## Stage A — Freeze production baseline

Record:

- current main commit;
- current live strategy version;
- current Railway trader deployment;
- current Research Agent deployment;
- current research scheduler deployment;
- current trading-report-read Edge Function version;
- current live strategy parameter values.

This is the rollback anchor.

No strategy value changes at this stage.

## Stage B — Deploy canonical read-surface extension

Deploy only the reviewed trading-report-read change that exposes:

- candidate key;
- candidate session;
- candidate rejection reason codes;
- stored decision-time strategy checks;
- candidate-forward-v2 forward outcomes.

Then verify the evidence-session endpoint against a completed session.

Required checks:

- candidates are returned;
- checks are present where retained;
- forward outcomes are returned only when they exist;
- incomplete/unmatured outcomes remain missing or incomplete rather than being
  fabricated;
- the endpoint remains read-only;
- no public Data API permissions are widened.

If verification fails, restore the previous Edge Function version before
continuing.

## Stage C — Merge and deploy research code

Only after Stage B passes:

1. merge the reviewed ASC PR to current main;
2. deploy the research/reporting components that consume the new evidence;
3. deploy the Research Agent status integration;
4. allow the trader service to restart only after market close if the normal
   repository deployment model requires it.

The merged code still has:

- no ASC broker calls;
- no automatic parameter application;
- no automatic challenger promotion;
- no risk/sizing mutation authority;
- no production strategy-family routing.

## Stage D — Verify production RHEN unchanged

After deployment verify independently:

- trader health endpoint is healthy;
- current live strategy version is unchanged;
- execution authorization state is unchanged;
- live strategy parameters match the Stage A baseline;
- broker/canonical reconciliation is safe;
- no unexpected open orders or positions exist;
- Research Agent remains isolated from broker credentials and live mutation.

Any mismatch triggers rollback.

## Stage E — Generate the September 29 canonical daily report

Run the normal completed-session post-close report path.

The report must contain:

- candidate forward evidence;
- ADS-002 v2;
- counterfactual_lab;
- NOSTRA session-state timeline;
- NOSTRA transition research/calibration;
- adaptive_strategy_control;
- Strategy Health;
- parameter pressure;
- control transition;
- fixed-vs-adaptive shadow state;
- GRAEN validation;
- promotion previews;
- strategy-family registry/routing.

Every ASC/NOSTRA/GRAEN research artifact must state that it has no execution
authority.

## Stage F — Expected first-session interpretation

The first deployed ASC report is expected to contain substantial COLLECTING
states.

That is correct.

Examples:

- NOSTRA calibration stays COLLECTING below 100 realized forecasts;
- parameter pressure stays COLLECTING below 5 validated observations;
- ASC-005 cannot satisfy cross-session evidence gates from one session;
- ASC-007 cannot pass before at least 10 independent evaluation sessions;
- GRAEN frozen holdout remains unpassed until an untouched holdout exists;
- ASC-008 cannot create an activation contract without all research gates plus
  proposal-specific human authorization.

No missing sample should be converted into a weaker gate.

## Stage G — Operational notifications

Slack may receive:

- ASC state transitions;
- defensive blockers;
- research/report failures or recovery;
- a future promotion-ready human-review event.

Slack must not receive:

- every scan;
- every health recomputation;
- every NOSTRA probability update;
- routine counterfactual rows.

A promotion-ready Slack message explicitly means research review is warranted,
not that deployment occurred.

## Rollback

If the rollout causes a research/reporting regression:

1. restore the prior trading-report-read Edge Function version if relevant;
2. redeploy the Stage A application commit/deployments;
3. verify RHEN health and reconciliation;
4. leave live strategy configuration unchanged;
5. retain failed ASC evidence for debugging without using it for adaptation.

Because ASC v1 has no production mutation authority, a research rollback should
not require reconstructing trading parameters.

## Production adaptation remains a future separately authorized action

A future bounded parameter change may be considered only after:

- ASC-005 controlled counterfactual evidence;
- ASC-007 fixed-vs-adaptive walk-forward validation;
- GRAEN frozen holdout validation;
- evidence-quality gates;
- ASC-008 proposal-specific human authorization;
- a separate operator activation/deployment action;
- an explicit rollback value.

Until then, the existing RHEN production champion remains unchanged.
