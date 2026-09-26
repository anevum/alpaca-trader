# Residual Downshock Rebound v2.1 — Frozen Methodology

Status: **FROZEN / READY TO RUN**
Scope: offline research only. This freeze does not alter RHEN live production behavior, sizing, risk, universe, execution, deployment, or website behavior.

## Formal hypothesis

The unit of observation is a cooldown-filtered stock/session/five-minute residual downshock event. A residual downshock is not ordinary negative momentum: it is a sufficiently negative five-minute stock return after removing synchronized broad-market and mapped-sector movement using coefficients estimated only from prior completed sessions.

Null: the conditional 30-minute residual rebound is non-positive or the 30-minute executable return after the frozen STRESS friction model is non-positive.

Alternative: the conditional 30-minute residual rebound is positive and the 30-minute executable return after the frozen STRESS friction model is positive.

Primary endpoint: 30-minute forward residual rebound and executable 30-minute long return. Secondary 5-, 15-, and 60-minute horizons are descriptive and cannot rescue a failed primary result.

## Frozen universe

The source panel is the already frozen edge-corpus-v1-frozen-36 universe. The v2.1 tradable panel is the 23 single-stock names only:

AAPL, MSFT, NVDA, AMD, AMZN, META, GOOGL, TSLA, AVGO, MU, JPM, BAC, XOM, CVX, LLY, UNH, COST, WMT, CAT, BA, NKE, MRVL, PLTR.

SPY is the broad-market benchmark. Sector/context ETFs are not tradable observations in this experiment. The complete sector mapping is frozen in the machine-readable manifest.

The fixed panel is not survivorship-bias-free for the full US equity market, so inference is limited to this already-frozen liquid panel.

## Windows

Development: six non-overlapping windows from 2026-01-05 through 2026-05-08.
Validation: 2026-05-18 through 2026-05-26? No. The machine-readable manifest is authoritative: val-01 is 2026-05-18 through 2026-06-05 and val-02 is 2026-06-08 through 2026-06-26. Validation remains locked until development passes.
Holdout: 2026-07-13 through 2026-07-31. Holdout remains locked until validation passes.
August 3 through September 25, 2026 remains quarantined and is never accessible under v2.1.

Expected US equity sessions are enumerated in the manifest. Corpus completeness is based on expected-session representation and exact timestamp synchronization, not raw IEX bar density.

## Five-minute construction and timing

Raw Alpaca IEX one-minute bars are aggregated into fixed America/New_York regular-session bins: [09:30,09:35), [09:35,09:40), ... [15:55,16:00). OHLCV is first open, maximum high, minimum low, last close, and summed volume.

No forward filling or interpolation is permitted. A sparse five-minute bin may exist if at least one real source bar exists, but its raw-minute count is recorded. Halts remain missing rather than synthetic.

Signal synchronization requires the stock, SPY, and the stock's mapped sector benchmark to have both current and immediately prior five-minute closes at identical timestamps. A signal at T may use information available by T only.

The first eligible shock bar ends at 09:40 ET. The last eligible shock bar ends at 15:00 ET so the primary 30-minute outcome and exact next-interval entry remain inside the regular session.

## Returns and residualization

All returns are arithmetic close-to-close five-minute returns.

For each stock and session, coefficients are estimated using synchronized returns from the most recent 20 completed sessions strictly before the event session, with at least 10 distinct training sessions and 600 synchronized observations.

Residual model: r_stock = alpha + beta_market*r_SPY + beta_sector_excess*(r_sector-r_SPY) + epsilon.

No future bars enter coefficient estimation. If the model is unavailable, that stock/session cannot generate events.

Residual standardization uses the median and 1.4826*MAD of prior-session training residuals in the same frozen time-of-day bucket, with a minimum scale of 1e-6.

## Event definition and grid

An event requires residual <= -absolute_residual_floor AND residual_z <= -residual_z_threshold.

Only a one-bar residual shock is used. No positive-momentum, VWAP-strength, breakout, reclaim, or continuation condition is permitted.

The bounded development grid is exactly six configurations:
- residual z threshold: 2.0, 2.5, 3.0
- absolute residual floor: 0.30%, 0.50%

No other signal parameter is tunable under v2.1.

## Entry, overlap, and costs

Decision time is the end of the shock bar T. Same-bar close execution is prohibited. Hypothetical entry is the raw one-minute open timestamped exactly T, corresponding to the first minute of the next five-minute interval. If that price is unavailable, the event has no executable entry; there is no delayed fill substitution.

Same-symbol events less than 30 minutes after a kept event are suppressed. Simultaneous events in different symbols remain observations. Statistical uncertainty is clustered by trading day.

Costs:
- BASE: 5 bps full spread + 2 bps slippage per side = nominal 9 bps round trip.
- NORMAL: 8 bps full spread + 3 bps slippage per side = nominal 14 bps round trip.
- STRESS: 12 bps full spread + 5 bps slippage per side = nominal 22 bps round trip.

Survival gates are governed by STRESS.

## Uncertainty and multiple testing

The bootstrap unit is trading day. Use 10,000 deterministic resamples with seed 21020261 and percentile intervals.

Family-wise multiple-testing control uses Bonferroni one-sided alpha 0.05/6 = 0.008333333333333333 over the six frozen configurations. The clustered sign-flip test uses seed 21020262.

Negative controls: deterministic matched non-shock observations and sign-flipped positive residual shocks. Negative-control seed: 21020263.

## Development gate

A configuration is rejected irreversibly if any required development criterion fails. The full numeric gate is authoritative in the machine-readable manifest. Core requirements include at least 240 events, at least 50 distinct event sessions, at least 20 events in at least four development windows, at least four positive STRESS-expectancy windows, aggregate positive STRESS expectancy, STRESS profit factor >= 1.15, worst-window STRESS expectancy >= -0.10%, positive day-cluster bootstrap lower 95% bound, concentration caps, adverse-tail floors, positive control uplifts, and survival after removing the best symbol and best sector.

Failed configurations are not retuned. If no configuration survives, validation stays locked.

If multiple configurations survive, one is selected by the predefined ordering in the manifest. Only that frozen configuration proceeds.

## Validation and holdout

Validation receives no parameter changes. It requires at least 80 events, at least 20 distinct event sessions, at least 20 events in each validation window, positive STRESS expectancy in every validation window and aggregate, STRESS profit factor >= 1.15, positive clustered lower bound, concentration/tail controls, positive control uplifts, and leave-best-symbol/sector robustness. Failure is terminal and keeps holdout locked.

Holdout remains untouched until validation passes. It requires at least 40 events, at least 10 distinct event sessions, positive aggregate STRESS expectancy, at least two of three frozen chronological holdout blocks with positive STRESS expectancy, STRESS profit factor >= 1.10, positive clustered lower bound, tail/concentration controls, positive control uplifts, and leave-best-symbol/sector robustness.

Passing holdout is historical evidence only. It does not authorize live deployment, capital scaling, risk changes, or strategy promotion.

## Data-quality and no-lookahead gates

Before performance evaluation, each symbol/window must report expected and represented sessions, first/last timestamp, raw one-minute and five-minute counts, missing/partial sessions, synchronization completeness, market completeness, sector completeness, and pagination completion.

The gate requires complete expected-session representation, complete pagination, >=90% synchronized five-minute completeness per symbol/window, and >=95% SPY and mapped-sector benchmark completeness. Raw IEX density remains diagnostic only.

Forward outcome tables, MFE/MAE, future returns, and candidate-forward-outcome telemetry are analytics-only and prohibited as predictors.

## Reproducibility and execution

Experiment key: edge-discovery-v2-residual-downshock-rebound-v2.1
Semantic ID: residual-downshock-rebound-v2.1
Canonical experiment UUID: 1e0e2f4a-6fef-4b2b-923f-630fa830c458
Manifest: research/residual-downshock-rebound-v2.1.json
Runner entrypoint: scripts/residual_downshock_rebound_v2_1.py

The frozen runner currently supports stage-access and checksum verification with --dry-run; the real historical evaluator is intentionally not executed or silently introduced during this freeze task.

Future development execution must first verify the manifest checksum, run the data-quality gate, and open development only. Validation and holdout access require prior-stage PASS artifacts carrying the exact same manifest checksum and selected configuration ID.

No temporary Railway research service should be created until the execution session. If required then, create an execution-disabled isolated service from the frozen research commit, run development once, persist the artifact, and remove or disable that service afterward.

Any semantic change to the manifest creates a new methodology version rather than modifying v2.1 in place.
