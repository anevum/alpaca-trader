# RHEN replay fidelity and rule-set evolution — October 8, 2026

**Session:** `ANEVUM.RHEN.BUILD.2026-10-08.001.REPLAY-FIDELITY-RULESET-RESEARCH`  
**Status:** Research-only draft. No live strategy, risk, order, deployment, or capital authorization changes.  
**Control:** RHEN 4.3.2, `rolling_momentum_vwap`, trading strategy `LIVE-2026-09-25-003`.

## Objective

Improve reproducibility and explore bounded **structural trading rules** rather
than optimizing parameters against the same historical sample. Start with a
single additional entry condition while preserving the production algorithm as
the control. All tests must report both rejected opportunity cost and net
economic sensitivity. Use existing GRAEN/ASC, VELUM and IREN components; do
not build another live model worker.

## Existing evidence — October 8

The companion investigation package
`ANEVUM.GRAEN.PACKAGE.2026-10-08.001.RHEN-EXPERIMENT-INVESTIGATION.zip`
contains preliminary session aggregates, order **submission** notifications,
an audit of replay/live mismatches, and three **specified, not executed**
experimental proposals. It does **not** supply the full broker-fill ledger,
point-in-time candidate cohort, execution quotes, or validated NOSTRA outcomes.

Do not treat Slack submissions as fills, preliminary aggregate P&L as audited
execution truth, or IEX bars as consolidated executable prices. The observed
thesis-failure exit pattern warrants investigation, not relaxed stops.

## Implemented research-only facilities

1. `app/research_agent/package.py`: daily v1.7 metrics extracted from
   `report["metrics"]`, including `trade_count`, `realized_pnl`,
   `profit_factor`, `expectancy`, and `max_realized_drawdown`.
   Legacy `performance` fallback and zero values are preserved. Surface
   report fingerprint, runtime commit, warnings, and next offline action.
2. `app/replay.py`: enforce the `min_quality_score` floor *after*
   historical market-quality checks, record rule/quality rejection counts,
   honor rolling momentum's no-fixed-target/no-generic-max-hold policy, use
   distinct completed health-bar keys for thesis failure and prior-bar
   profit-protection floors, and preserve forced-session precedence.
   These are approximations, **not** broker-order matching. The output
   explicitly says `live_execution_parity=UNVERIFIED` and
   `same_strategy_logic=false`.
3. Dynamic universe replay requires `universe_snapshots`, a mapping of
   timezone-aware point-in-time snapshot timestamps to candidate-symbol lists.
   Missing symbols/bars are rejected rather than silently reconstructed from
   future membership. Static universe replay remains available.
4. `app/rule_set_lab.py`: trusted, AND-combined entry rules for
   `relative_volume` and `trend_persistence`, calculated exclusively
   from completed visible bars. Bounded to eight challengers, each with
   one to three conditions. Every comparison replays the frozen production
   control and emits test/input fingerprints, a full tested slate, conservative
   broker-isolated assumptions and non-promotion declarations. Rule families
   enter ASC-009 only as `SPEC_ONLY`.
5. `app/research_agent/offline_experiment_queue.py`: separate SQLite
   proposal/event history with immutable experiment identity, source report
   fingerprint, idempotent proposes, and review/evidence-gated transitions.
   No connection to live Core's single-writer database, credentials or broker.
   `ELIGIBLE_FOR_HUMAN_VALIDATION` is **not** production promotion.
6. `app/research_agent/strategy_router.py`: accept both registry `status`
   and legacy `validation_status` fields; routing remains research-only.

## Usage

Export **decision-time** bars and as-of universe membership from the
authorized canonical source first. Preserve the source report fingerprint.
Never download future-corrected membership and label it "as-of".

Prepare a bounded challenger JSON list, for example:

```json
[
  {
    "name": "volume-confirmed",
    "hypothesis": "Point-in-time above-baseline volume improves momentum selection after costs",
    "entry_rules": [
      {"indicator": "relative_volume", "threshold": "1.5", "min_bars": 8}
    ]
  }
]
```

Use the same frozen Settings values as the live comparison cohort, but run with
non-trading test credentials and paper/dry-run authorization. The offline CLI
does not construct an execution/broker client. Supply the actual as-of
snapshots if `DYNAMIC_UNIVERSE_ENABLED=true`:

```sh
python scripts/rule_set_lab.py \
  --bars-json frozen_bars.json \
  --rules-json challenger_rules.json \
  --universe-snapshots-json asof_universe.json \
  --initial-equity 100 \
  --spread-bps 5 \
  --slippage-bps 2 \
  --min-trades 20 \
  --output exploratory_rule_report.json
```

This CLI intentionally fails closed if the dynamic universe is enabled but
as-of snapshots are not supplied. Do not substitute `SCAN_SYMBOLS` for
dynamic membership and claim a faithful historical replay.

For the existing, unexecuted October 8 experiment specs, supply the exact
source-report fingerprint before putting them in the isolated queue:

```sh
python scripts/research_experiment_queue.py \
  --db isolated_research.sqlite propose \
  --specs-json experiment_specs.json \
  --source-fingerprint <64-character-source-sha256>
python scripts/research_experiment_queue.py \
  --db isolated_research.sqlite list
```

Queue events are persistent only in the selected SQLite file. The production
Command and canonical Core collections do **not** automatically ingest this
separate queue. Production integration is a separate reviewed change.

## Research decisions and explicit gates

| Experiment | Status | Reason |
|---|---|---|
| Quality score 80 vs 85 | Screening proposal | Read existing ASC-005 15-minute counterfactual first; require completed candidate cohort, 5 independent sessions, >=30 differential candidates, >=95% coverage and existing multiplicity tests |
| Same-symbol reentry restriction | Exploratory hypothesis | One NFLX double entry is not a representative sample; requires fill-linked repeated opportunities |
| Thesis-failure 2 vs 3 bars | Blocked for promotion | Requires confirmed protective-order/exit parity and explicit decision about live holding-time policy |
| Relative-volume structural entry rule | New research-only challenger | Requires complete event-time volume and quote data; compare against unchanged production control, include foregone winners |

A replay improvement alone does not satisfy any production gate.

## Acceptance protocol

1. Verify the actual running commit, safety config, broker exposure, and
   source report schema independently. Do not interfere with concurrent
   profiling, database, 4.4 observer, or website work.
2. Run regression tests for summary, quality score, rolling target/time,
   thesis/bar deduplication, stop ratchet, dynamic as-of data and trust
   boundaries. CI must pass.
3. Get exact broker fills and per-candidate signal/quote data using the
   protected canonical evidence surfaces; avoid copying credentials into
   research packages or PRs.
4. Add a golden live-vs-replay fixture with actual event-time universe,
   quality rejection reasons, two-bar thesis deterioration, stops, and
   reconciliation. Record unreconstructable events as missing, not pass.
5. Compare same-cohort baseline and one-variable challenger on separate
   development, walk-forward validation and untouched holdout periods.
   Track all tested variants (including failures), run cost stress, and
   assess portfolio P&L and drawdown rather than only trade hit rate.
6. If evidence passes *all* protected ASC/GRAEN requirements, request
   human review. Nothing in this change automatically merges, promotes
   strategies, changes risk, places orders, or increases capital.
7. To deploy changes to the production repository, separately verify
   live exposure and deploy safety; merging even research-only code may
   restart Railway's single RHEN service.

## Known limitations

- Simulated stop handling is intentionally conservative about gap-through,
  same-bar stop/target ordering, and newly ratcheted protection. It does
  not recreate broker-native stop order timing.
- Replay does not reproduce every live execution gate, dynamic market-data
  feed nuance, fractional fill or IREN reconciliation transition.
- Exact broker fills, quote spreads, historical NOSTRA forecasts and the
  October 8 canonical candidate export were not included in the package.
- The new experiment queue runs offline and is not yet the canonical
  production scheduler/read model.
- Research-rule comparisons are hypothesis-screening outputs. Do not claim
  proven edge, live performance gains, or readiness for 4.4 promotion.

**Final boundary:** No live RHEN strategy, broker-write, capital, short,
leverage, options, crypto or automatic-promotion changes are authorized by
this research workload.
