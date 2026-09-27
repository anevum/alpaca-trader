# RHEN Pre-Open State v1

## Purpose

Pre-Open State v1 is RHEN's market-state and price-discovery research layer. It observes what is knowable before the U.S. cash open, records a point-in-time state vector, and later attaches forward outcomes. It does not place orders, alter the live strategy, change risk, change sizing, select capital, or authorize promotion.

The design separates four questions that are often incorrectly collapsed into one:

1. Where does the market open relative to the prior close?
2. What direction does it move after 09:30 ET?
3. How large is the early-session move/range?
4. Which market state or data source adds incremental information beyond a simple base rate?

A feature that helps one target is not assumed to help another.

## Deployment boundary

The service runs separately from the production trader as `rhen-preopen-state`.

It imports only its own read-only market-data client. It does not import `app.execution`, `app.alpaca_client`, `app.strategy`, `app.risk`, `app.sizing`, or `app.main`. The production trader does not import the pre-open package.

All forecasts are shadow-only. Even a model artifact with status `SHADOW_APPROVED` has zero execution authority.

## Data-source hierarchy

### Tier 1 — direct data available to the first build

The current implementation directly measures SPY and QQQ premarket state from the configured Alpaca equity feed.

For each checkpoint it records prior close, current premarket price, gap versus prior close, full premarket return, premarket VWAP, distance from VWAP, range, volume, bar count, data freshness, and 5/15/30/60-minute returns.

### Tier 2 — explicit proxies

Until direct institutional sources are connected, v1 may observe FEZ, EWJ, EEM, TLT, UUP, USO, GLD, and VXX. These are labeled `PROXY` in every snapshot. They must never be presented as equivalent to European/Asian futures, direct Treasury yields, DXY/spot FX, commodity futures, VIX spot, or the VX curve.

Sparse or stale premarket proxy data is recorded as degraded or unavailable rather than silently imputed.

### Tier 3 — unavailable institutional sources

The source catalog explicitly tracks the higher-value feeds that are not yet connected: ES, NQ, synchronized Asia/Europe index or futures data, 2Y/10Y Treasury yields, VIX/VX term structure, Nasdaq NOII, NYSE opening imbalance, and full-depth signed order flow.

The architecture is provider-neutral so those sources can be added later without relabeling proxies as direct observations.

## Checkpoints

The shadow service captures six pre-open checkpoints in America/New_York time:

- 08:00
- 08:30
- 09:00
- 09:15
- 09:25
- 09:29

The multiple checkpoints let research distinguish a stable overnight state from late information arriving near the open. The 08:30 checkpoint is especially useful for days with scheduled U.S. macro releases, but the collector itself does not infer or trade on news.

Each snapshot uses a deterministic key so process restarts are idempotent.

## Outcomes

After the open, the service records SPY and QQQ outcomes at 5, 30, 60, and 120 minutes:

- open-to-horizon return;
- binary direction;
- absolute return;
- realized high-low range;
- actual opening gap versus prior close.

These are post-event evidence only.

## Canonical storage

The existing authenticated `trading-ingest` endpoint accepts the new generic event types:

- `preopen_state_snapshot`
- `preopen_state_outcome`

The existing live projector ignores unknown event types. A separate trigger projects only these two event types into:

- `private.trading_preopen_snapshots`
- `private.trading_preopen_outcomes`

Model artifacts have their own private registry:

- `private.trading_preopen_model_artifacts`

All three tables have RLS enabled and no anonymous or authenticated-client grants.

## Research model

v1 intentionally starts with a small deterministic L2-regularized logistic model rather than a large black-box learner. The objective is to prove that a feature set adds incremental probability information, not maximize an in-sample backtest.

Historical rows are point-in-time. Features are cut off at the selected pre-open checkpoint. Forward returns are attached only as labels.

Dataset splits are chronological and include a session embargo between train, validation, and holdout. Random train/test shuffling is not supported by the canonical CLI.

Primary probability metrics are Brier score and log loss, both compared with a constant base-rate forecast. Direction accuracy is reported but cannot justify a model by itself; majority-class accuracy is reported beside it.

## First frozen research question

The first recommended experiment is deliberately narrow:

**Question:** Does a compact 09:25 ET market-state vector improve the probability estimate for SPY open-to-30-minute direction versus the development-sample base rate?

Candidate initial features:

- `SPY.gap_vs_prior_close_pct`
- `SPY.return_15m_pct`
- `QQQ.gap_vs_prior_close_pct`
- `QQQ.return_15m_pct`
- `FEZ.premarket_return_pct`
- `TLT.premarket_return_pct`
- `VXX.premarket_return_pct`

The feature list must be frozen before performance is examined. If proxy completeness is inadequate, the experiment fails the corpus gate rather than silently dropping difficult days.

A separate experiment should later target realized range/volatility. Direction and volatility must not share a success criterion.

## Promotion gates

A research model can move to shadow observation only after all of the following are true:

1. The corpus passes point-in-time completeness and pagination checks.
2. The feature list, target, checkpoint, costs/assumptions, and split policy are frozen before protected evaluation.
3. Validation improves probability scoring versus the base-rate benchmark without relying on one short regime.
4. Holdout remains unopened until validation passes.
5. Holdout confirms the effect.
6. A shadow period demonstrates live data availability, forecast reproducibility, calibration, and no operational incidents.
7. Any future use by a trading strategy requires a separate explicit production authorization and implementation review.

No status in the pre-open subsystem automatically changes RHEN's production behavior.

## Deliberately excluded from v1

Named candlestick patterns, hand-drawn trendlines, generic RSI/MACD crossovers, support/resistance labels, and chart-pattern names are not first-class signals. If they are researched later, they should be represented as numerical geometry or state variables and must prove incremental out-of-sample value after the stronger market-state features are accounted for.

No LLM chooses trades or modifies model weights in this layer.

## Future connectors

The preferred expansion order is:

1. ES/NQ direct futures price discovery.
2. Nasdaq/NYSE opening-auction imbalance data.
3. Direct Treasury yields, DXY/FX, VIX/VX term structure.
4. Synchronized Asia/Europe direct indexes or futures.
5. Full-depth signed order-flow and market-depth data.

Each source must arrive with point-in-time historical coverage sufficient to reproduce the same feature in development, validation, holdout, and live shadow operation.

## Operational commands

Inspect the source catalog:

```bash
python scripts/preopen_state.py catalog
```

Capture a one-off shadow snapshot:

```bash
python scripts/preopen_state.py snapshot --checkpoint 09:25
```

Build a historical point-in-time dataset:

```bash
python scripts/preopen_dataset.py \
  --start 2024-01-02 \
  --end 2026-08-31 \
  --checkpoint 09:25 \
  --dataset-output preopen-dataset.json
```

Training is optional and explicit. A model remains `RESEARCH_ONLY` when created. The Railway shadow service can load a checksum-bound artifact through `PREOPEN_MODEL_JSON`; an invalid or modified artifact is rejected.

### Protected holdout rule

The canonical training CLI evaluates validation by default and records the holdout as `LOCKED`. It will not score that holdout unless `--open-holdout` is supplied explicitly. That flag should be used only after the frozen validation gate has passed and the decision to open holdout has been recorded. Multi-year minute history is downloaded in bounded chunks so the research corpus does not silently fail on a single pagination ceiling.
