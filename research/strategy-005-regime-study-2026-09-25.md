# Strategy 005 regime study — 2026-09-25

Status: **OFFLINE RESEARCH ONLY. NO CANDIDATE D IS DEFINED.**

## Why this phase exists

Strategy 004 tested three static entry ideas and all failed unseen data.

- Candidate A demanded more VWAP separation and confirmation.
- Candidate B demanded two consecutive valid setup bars.
- Candidate C capped mature momentum and trend persistence.

Candidate C was selected only from two development periods, frozen, and then
tested once on the locked 2026-08-31 through 2026-09-04 holdout. It made the
entry-quality labels worse: target-before-stop fell from 25.42% to 17.07% while
stop-before-target rose from 40.68% to 46.34%.

That failure is evidence that a universal entry threshold may be the wrong
model. The next question is whether the same entry shape behaves differently
under different observable broad-market conditions.

## Decision-time regime data

For each production-like BUY opportunity the offline study now records, using
only bars visible at the decision:

- number and fraction of configured market references in a constructive regime;
- each reference's regime-window return;
- each reference's distance from session VWAP;
- recent average absolute one-minute return as a volatility measure;
- dispersion between reference returns;
- average reference return and average VWAP distance;
- a predeclared agreement band: none, partial, or full;
- a predeclared time band: open, midday, or afternoon.

The constructive test mirrors the existing production regime concept: current
price must be at or above the recent mean and session VWAP with a nonnegative
regime-window return.

## Research protocol

This phase is descriptive first.

1. Run the same development periods through the expanded feature study.
2. Compare target-before-stop, stop-before-target, MFE, MAE, and close return by
   broad-market agreement and time band.
3. Within each regime, examine quartiles of momentum, VWAP edge, latest-bar
   return, relative volume, volume acceleration, and trend persistence.
4. Require the same conditional relationship to appear in more than one
   development period.
5. Only then define a simple Candidate D before opening another reserved
   holdout.

Do not search hundreds of regime thresholds. The purpose is to determine
whether there is a stable interaction worth modeling, not to manufacture a
profitable backtest.

## Production boundary

This branch does not change live execution, Railway variables, sizing, exits,
dynamic-universe behavior, or capital limits. Scalable-capital PR #28 remains
gated.


## Exact-parity research configuration

Source-control review confirms the live rolling strategy was introduced with
`CONFIRMATION_SYMBOLS=QQQ,SMH` and `MIN_CONFIRMATIONS=1`. Strategy 005 uses
QQQ/SMH as the production-parity market-reference set. SPY may remain a broader
market diagnostic in future work, but it must not silently become an additional
production confirmation.

A parity audit of rejected Candidate C using QQQ/SMH reached the same rejection
decision, so Candidate C remains closed.

## Preliminary development observation

Using production-parity QQQ/SMH context on the two existing development
windows, time of day was more stable than broad-market agreement.

- Aug 17–21: open-period opportunities were 1/12 target-first and 11/12
  stop-first; midday was 5/43 target-first and 22/43 stop-first.
- Sep 8–11: open-period opportunities were 7/26 target-first and 17/26
  stop-first; midday was 8/32 target-first and 8/32 stop-first.

This is not yet a strategy rule. It motivates one additional development test.

## Strategy 005 data protocol

Additional development window: **2026-08-24 through 2026-08-28**.

Reserved holdout, locked before viewing: **2026-09-21 through 2026-09-23**.

The reserved holdout must not be queried until a Candidate D rule is frozen.
If the opening-period effect does not repeat in the additional development
window, no Candidate D should be created from this hypothesis.


## Additional development window result

The predeclared additional development window, Aug 24–28, repeated the
time-of-day effect using production-parity QQQ/SMH context.

| Segment | N | Target before stop | Stop before target | Mean MFE | Mean MAE | Mean 15m close |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| All eligible | 106 | 16.04% | 39.62% | +0.3182% | -0.4447% | -0.0520% |
| 09:31–10:29 | 31 | 6.45% | 61.29% | +0.3143% | -0.7944% | -0.3362% |
| 10:30–15:30 | 75 | 20.00% | 30.67% | +0.3198% | -0.3001% | +0.0655% |

Across the three development windows, removing the pre-10:30 entry period
improved target-minus-stop balance each time:

- Aug 17–21: 61 later opportunities, 11.48% target-first / 49.18% stop-first;
- Sep 8–11: 39 later opportunities, 20.51% target-first / 33.33% stop-first;
- Aug 24–28: 75 later opportunities, 20.00% target-first / 30.67% stop-first.

The mechanism is plausible and simpler than the rejected static feature
thresholds: the existing strategy is especially vulnerable to early-session
noise, volatility, gap digestion, and rapid reversals.

## Candidate D frozen before holdout

Candidate: `strategy_005_candidate_d_delay_open`.

Frozen rule:

**Do not permit new entries before 10:30 ET.**

Nothing else changes. Candidate D keeps the production strategy, quality floor,
VWAP/momentum requirements, confirmations, regime checks, sizing, correlation,
stops, targets, maximum hold, cooldowns, and exits.

The 10:30 boundary was predeclared in the Strategy 005 time-band design before
the Aug 24–28 development window was opened. It is now frozen.

Reserved holdout remains **2026-09-21 through 2026-09-23** and has not been
queried as part of this phase. If Candidate D fails that holdout, it is rejected
rather than retuned.


## Candidate D reserved-holdout result — REJECTED

The locked Sep 21–23 holdout was opened only after Candidate D was frozen and
its implementation passed CI.

Production-parity opportunity-level result:

| Sample | N | Target before stop | Stop before target | Mean MFE | Mean MAE | Mean 15m close |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Production-like 09:31–15:30 | 52 | 23.08% | 40.38% | +0.2841% | -0.4353% | -0.0119% |
| Candidate D 10:30–15:30 | 36 | 19.44% | 36.11% | +0.2457% | -0.4210% | -0.0606% |

Candidate D reduced stop-first frequency by 4.27 percentage points, but it also
reduced target-first frequency by 3.63 points, reduced MFE, and made final
15-minute return materially worse. Target-minus-stop balance improved by less
than one percentage point and did not translate into stronger forward price
behavior.

Decision: **REJECTED.**

The 10:30 threshold must not be retuned against Sep 21–23.

## Strategy 005 conclusion

Broad-market agreement was not stable across the development periods, and the
opening-time effect that looked consistent in three development windows did not
produce a meaningful holdout improvement.

At this point the project has rejected static VWAP-strength, multi-bar
persistence, mature-momentum suppression, and delayed-open hypotheses on unseen
data.

The next engineering priority should not be Candidate E. It should be research
parity:

1. reconstruct or persist the production dynamic universe so historical replay
   does not depend on a five-symbol fixed universe;
2. record decision-time bid/ask spread and realized fill slippage in the
   canonical ledger rather than relying on a fixed 5-bps historical assumption;
3. ensure every offline run pins the exact production strategy identity,
   confirmation set, universe rules, risk settings, and exit-engine settings;
4. collect clean forward live/shadow telemetry across additional sessions before
   attempting another strategy candidate.

No Strategy 005 result authorizes a production change or scalable-capital merge.
