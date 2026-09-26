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
