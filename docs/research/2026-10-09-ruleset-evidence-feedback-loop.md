# RHEN ruleset rebuild — evidence-complete research program

**Session:** `ANEVUM.RHEN.BUILD.2026-10-09.001.STRATEGY-RULESET-EVIDENCE-LOOP`  
**Status:** Research-only draft. No live strategy, broker, capital, options, short, leverage, crypto or automatic-promotion changes.

## Decision

The current live champion remains `LIVE-2026-09-25-003 / rolling_momentum_vwap` (RHEN 4.3.2). Multiple weak sessions motivate a full causal evaluation of rule sets, *not* automatic parameter optimization. Do not claim decaying alpha is statistically established from a handful of live trades.

The October 8 investigation found report-only research with no executed promotion-grade alternative. PR #459 subsequently merged replay fidelity improvements, report mapping, a restricted rule-set tournament and offline proposal queue. **This change extends those real components** rather than creating another execution path.

## Single traceable data set

Every evaluation must carry stable `session`, event/candidate IDs, trading strategy version, runtime commit, as-of universe membership, completed bars, quote/source timestamp and spread, entry signal / all rejection gates, at-decision NOSTRA regime, order intent/client ID, broker order, every partial fill, stop/exit reason, costs, forward outcomes, and canonical replay identity. A missing link remains missing rather than an invented zero.

Broker session orders and fill activities now use bounded **read-only pagination**:
- Orders: Alpaca Trading API `GET /v2/orders` with `status=all` and stable `before_order_id` cursor (without incompatible time-based cursor parameters). Retrieve until crossing the earliest requested local calendar date; preserve a conservative 20-page ceiling; any cursor anomaly, missing ID/timestamp or saturation fails the report instead of claiming completeness.
- FILL activities: `GET /v2/account/activities/FILL` with session date, `page_size=100`, `page_token` equal to the final activity ID. A repeated cursor or page ceiling raises and must surface as a failed IREN research job.
- Reconstruction warns when same-window sells cannot pair to opening lots. A carry position needs earlier fills; do not compute round-trip expectancy from such a window.

These reader changes **do not prove** candidate, quotes, slippage or replay execution parity. Coverage and storage retention must be audited separately.

## Daily deterministic feedback

`app/research_agent/rule_set_agenda.py` creates a reproducible **agenda**, based on the durable daily report and source fingerprint; the report contains it in `rule_set_research_agenda`. It is a triage step, *not* a new running model or an automated experiment runner.

- `AWAITING_COMPLETE_EVIDENCE`: broker-history pagination, source, candidate readiness, outcome coverage, storage shedding, carry lots or replay reconstructability have blockers. Repair evidence; do not interpret net improvements or run parameter sweeps.
- `READY_FOR_BOUNDED_OFFLINE_SCREEN`: specific input readiness is confirmed, allowing an independent offline search (which **still** has unverified live execution parity and no validation or promotion authorization).
- All slates, source report fingerprints, hypotheses and search counts are frozen. Research note text cannot mutate runtime settings. Even an apparently profitable exploratory replay stays `SPEC_ONLY` until human review.

## Predeclared first round

Use the *same exact as-of cohort*, fixed strategy settings, transaction-cost model, and completed bars. Track both added/rejected candidates and foregone winners.

| Variant | Testable rule | Why | Scope |
| --- | --- | --- | --- |
| **Production control** | Existing 4.3.2 rolling momentum/VWAP unchanged | Ground truth reference | Frozen |
| Relative volume | `relative_volume >= 1.5` using 8 completed bars | Are thin-tape breakouts the loss driver? | Offline entry gate |
| Trend persistence | `trend_persistence >= 0.67` using 8 completed bars | Are single-bar bursts lacking follow-through? | Offline entry gate |
| Combined confirmation | Both the above | Does confirmation add incremental net value after lost opportunities? | Offline, count all comparisons |
| Quality 80 vs 85 | Existing ASC-005 one-parameter counterfactual | Is entry selectivity causing churn? | Screening only; not a substitute for execution simulation |
| Opening-range/VWAP | Separate existing family | Potential replacement if momentum premise fails across regimes | Distinct family validation; not part of entry AND-gate tournament |
| Thesis-failure exit | Existing 2-bar vs experimental 3-bar | Loss attribution warrants review | **Blocked until actual broker stop and replay parity** |

Do not search dozens of strategy families and parameters at once on 2–3 bad sessions.

## Validation and statistics

1. **Data quality:** complete broker pagination; match fills to orders; audit candidate rejection + 15-minute forward outcome coverage; preserve original (not future-corrected) universe and signal features; use identical live/replay cost and exit semantics where reconstructable. Report missing data separately.
2. **Development:** lock initial slate *before* evaluating outcomes, and evaluate bounded baselines under actual order sizes and friction. Cost stress must be reported, not ignored.
3. **Walk-forward:** split by entire sessions and market regimes, not randomized overlapping one-minute samples. Count test multiplicity; use session-level uncertainty so repeated trades within one day are not called independent experiments.
4. **Untouched holdout:** hold out future sessions entirely; judge after-cost average expectancy, profit factor, max drawdown, net risk-adjusted improvement, trade frequency and missed winners. A smaller trade count is not automatically a better rule.
5. **Preliminary screening gate:** existing ASC-005 requires >=5 independent sessions, >=30 affected candidates, >=95% forward coverage, exact sign test with multiplicity adjustment and minimum positive screening effect; structural challengers need independently specified stricter portfolio/replay validation and an untouched holdout, not ASC screening alone.
6. **Forward/shadow:** review multiple sessions with broker-equivalent event-time evidence. Any nonidentical exits, stale market data, bad fills, ambiguous order attribution or resumed storage shedding blocks a proposed live release.
7. **Promotion:** require a separately signed/authorized strategy version, a rollback, kill switch, capacity/cost evidence, and explicit owner approval. Do not auto-promote; LLMs never choose or submit live orders.

## Operations / cadence

**Today:** fix broker history and run completeness audit on Oct 7–9 with the actual authenticated `/v1/research/package` export, broker fills, as-of candidate data and reporter fingerprints. Current connector access **does not contain that private export**, so no claim of a winning candidate is justified.

**After each close:** IREN owns the deterministic daily report. The new in-report agenda records blockers and a frozen slate. An independent offline GRAEN process should *only after verified input snapshots* enqueue controlled experiments using `app/research_agent/offline_experiment_queue.py`. Its current SQLite queue is not wired into production Core: make that explicit. Use the existing VELUM replay/lab and immutable file artifacts; do not add a 24/7 paid model worker.

**Weekly:** compare the fixed challengers across sessions/regimes, examine false positive/negative opportunities, and publish an operator-only scorecard. Inspect the causal rule and execution chain; do not chase yesterday's optimum. Only a human can authorize a champion replacement.

## Implemented D2 offline research flow

1. At 16:05 ET, the existing daily reporter gathers bounded broker fills and order history; **new source identity includes broker pagination, reconstruction and storage warnings**. The report attaches \`candidate_forward_evidence.cohort_audit\`, with explicit 15-minute coverage, event-time strategy identity, duplicate IDs, candidate timestamps and data status.
2. \`build_rule_set_agenda()\` marks the frozen three-rule slate **AWAITING_COMPLETE_EVIDENCE** if the 15-minute cohort is absent, fewer than 20 sampled candidates, below 95% measured coverage, not causally timed, version drifted, or a broker/retention error exists. This is only an *offline-screen readiness* designation; a complete daily sample does not prove the database had no omitted event rows.
3. The existing authenticated read-only \`/v1/research/package\` can be saved outside the repository as a JSON file; this workstream does **not** fetch any secret, broker record, or private package for public GitHub.
4. From a controlled private environment, run:

\`\`\`sh
python -m scripts.daily_rule_set_handoff --report-json /private/rhen-daily.json
python -m scripts.daily_rule_set_handoff --report-json /private/rhen-daily.json --db /private/offline-rhen.sqlite --commit
\`\`\`

The first call prints a sanitized dry-run; the second creates at most three immutable proposals in a **separate SQLite research file**, idempotently transitions each only to \`AWAITING_EVIDENCE\`, and prints only experiment IDs and blockers. It never infers readiness from claimed summary text, changes live settings, imports broker modules or opens research authority beyond that state.

5. Offline bar/universe exports and an explicit reviewed evidence freeze must come **after** the handoff, and a separate operator must approve state transitions. Use \`python -m scripts.rule_set_lab\` on same-cohort inputs per independent session. Bundle the outputs as:

\`\`\`json
[
  {
    "session": "2026-10-09",
    "phase": "DEVELOPMENT",
    "source_fingerprint": "<immutable 64-character daily source hash>",
    "market_regime": "UNKNOWN",
    "tournament": "<whole JSON object from rule_set_lab, not a string>"
  }
]
\`\`\`

The angle-bracket values above are explanatory placeholders; provide actual values and a JSON object in an actual input file, not the literal quoted placeholder.

6. Run \`python -m scripts.rule_set_scorecard --sessions-json /private/frozen-tournaments.json --output /private/rhen-paired-scorecard.json\`. This combines one frozen tournament per U.S. equity session, rejects duplicate periods/fingerprints, weekend pseudo-sessions, change of control/rule set/friction model, unauthorized promotion claims, and baseline-versus-challenger P/L inconsistencies. It reports paired net deltas and exact **session-level** two-sided sign tests, with Bonferroni multiplicity correction, in separately declared DEVELOPMENT, WALK_FORWARD and HOLDOUT partitions. Ranking uses walk-forward, not holdout. **Even 100% positive results are descriptive research**, not verified executable returns or live promotion authority.

7. Publish the operator's scorecard only after checking session independence and a provably pre-registered holdout, real spreads/fills, actual retained candidate cohort and historical as-of universe snapshots. An independent human-reviewed release, with risk/rollback checks, is needed before any replacement of the current champion.

**Current limitation:** The available GitHub, Railway and Alpaca market-data connectors do not expose the private \`/v1/research/package\` payload or authenticated trade fills to this workstream. No actual dated challenger tournament was performed; all current offline-lab tests are deterministic fixtures. The queue is not attached to IREN's production scheduler and must remain broker-isolated.

## Integration with ANEVUM Commons 2.0

The owner RHEN bot stays private. Member RHEN Cloud strategies and journals are separate from founder trading. When review reports are published to Commons, emit only opt-in, sanitized research summaries and methodology, never live private orders, customer broker data, credentials, or strategy details that are not approved for disclosure. A Commons badge, supporter payment or GitHub merge does not grant broker execution permissions.

## Release gate and non-goals

- Changes are isolated to *read-only broker-history research methods*, daily-report research projection, and tests/docs.
- No modifications to execution, sizing, risk, protective stops, strategy behavior, Railway variables, or active strategies.
- **Do not merge** until all relevant CI tests, two concrete paginated history fixtures, data-scope audit and user-approved maintenance deployment. Because Railway deploys `main`, even a research-only merge may restart the live trader.
- This PR neither implements a complete automatic experiment lifecycle nor demonstrates improved profitability. Those remain separate acceptance jobs that depend on private canonical data and validated replay.

## References

- `docs/research/2026-10-08-ruleset-replay-fidelity.md` and PR #459
- `app/rule_set_lab.py`, `app/research_agent/offline_experiment_queue.py`, ASC-005
- https://docs.alpaca.markets/us/reference/getallorders-1
- https://docs.alpaca.markets/us/reference/getaccountactivitiesbyactivitytype-1
