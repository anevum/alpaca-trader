# Edge Discovery v1 — structural research plan

Status: **OFFLINE RESEARCH ONLY**

## Problem being fixed

Strategy 004 originally searched for better entries only after the existing
rolling momentum/VWAP strategy had already generated a BUY signal. That creates
selection bias: research is restricted to one signal family that has not shown
positive historical expectancy.

Edge Discovery v1 separates edge discovery from production signal generation.

## Independent setup families

The lab currently observes five long-only families using completed one-minute
bars only:

1. controlled continuation;
2. pullback and fast-average reclaim;
3. compression breakout with volume expansion;
4. relative-strength impulse versus SPY/QQQ/SMH context;
5. opening-range breakout followed by a limited-excursion retest.

These are deliberately structurally different hypotheses, not small threshold
variations of the same entry.

## Falsification protocol

Every family is evaluated across multiple development periods and multiple
execution-cost assumptions.

Default friction scenarios:

- base: 5 bps spread, 2 bps slippage per side;
- moderate: 8 bps spread, 3 bps slippage per side;
- stress: 12 bps spread, 5 bps slippage per side.

A family does not survive historical discovery unless every configured cost
scenario has:

- at least 60 events;
- positive modeled expectancy;
- profit factor >= 1.20;
- at least two positive development periods.

Results are also segmented by:

- up/flat/down market context;
- low/normal/high short-horizon volatility;
- open/midday/late session.

The lab reports session-level expectancy dispersion and a lower 95% confidence
bound as a diagnostic. The confidence bound is intentionally not the only gate
because intraday observations are clustered and not independent.

## Promotion sequence

A historical discovery pass is not live authorization.

The required sequence remains:

development discovery -> freeze candidate -> untouched holdout replay ->
forward shadow -> clean live sample -> capital/exposure review.

A family that fails an untouched holdout is rejected without retuning against
that holdout.

PR #28 remains blocked by this work.
