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


## Historical corpus v1

The research corpus is frozen in `research/edge-corpus-v1.json`.

Candidate panel: 36 liquid stocks and ETFs spanning mega-cap technology,
semiconductors, financials, energy, healthcare, consumer, industrials, sector
ETFs and broad-market ETFs. Context references are SPY, QQQ and SMH.

Chronological roles:

- development: six windows from January 5 through May 8, 2026;
- validation: two windows from May 18 through June 26, 2026;
- holdout: July 13 through July 31, 2026;
- quarantine: August 3 through September 25, 2026, because ANEVUM research has
  already inspected those dates.

The holdout loader is stage-gated: July data is not fetched by the corpus
orchestrator unless at least one frozen family passes development and validation.

A preflight Alpaca IEX daily-history check on September 25, 2026 confirmed all
39 frozen symbols (36 candidates plus SPY/QQQ/SMH) have 144 daily bars from
January 5 through July 31. No symbol fell below the 80% coverage threshold.

Raw one-minute corpus files are cached locally under `.edge_corpus/`, keyed by
manifest hash, window, feed, timeframe and symbol panel. They are intentionally
excluded from Git.

## Staged elimination

Development requires every cost scenario to satisfy:

- at least 120 events;
- positive expectancy;
- profit factor >= 1.20;
- at least four positive development windows;
- worst-window expectancy no worse than -0.15%.

Validation is evaluated only for frozen development survivors and requires:

- at least 40 events;
- positive expectancy;
- profit factor >= 1.15;
- both validation windows positive;
- worst-window expectancy no worse than -0.05%.

Only validation survivors unlock the historical holdout.

The holdout requires positive expectancy under every cost scenario, at least 20
events, profit factor >= 1.10 and non-negative worst-period expectancy. Passing
the holdout still does not authorize production or capital scaling; it only
authorizes forward shadow validation.

If every family is rejected at any stage, the pipeline emits a structured
next-generation research slate rather than mutating rejected thresholds.
