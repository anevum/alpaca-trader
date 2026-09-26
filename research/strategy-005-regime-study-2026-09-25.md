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
