# Command real-time visualization implementation specification

## Objective

Implement a single coherent visual data plane over the existing protected Command live WebSocket.

Do not create separate polling loops per graph. Do not let chart code call broker APIs directly.

## Backend visual projection

Add a lightweight visual-state projector within the existing RHEN/Command service boundary.

Responsibilities:

- transform canonical RHEN state into typed chart/scanner deltas;
- attach provenance/quality metadata;
- maintain bounded live ring buffers where server-side aggregation is required;
- publish only changed series/points;
- emit full snapshot on connection/reconnect;
- emit critical execution markers immediately;
- keep trading engine independent from frontend availability.

Suggested domains:

- `market_series`
- `scanner_state`
- `execution_events`
- `account_series`
- `performance_series`
- `forecast_series`
- `system_series`
- `replay_state`

## Client state

Use one normalized client store keyed by:

- symbol;
- series id;
- session;
- stream generation;
- methodology/model version when applicable.

The store applies ordered deltas and retains bounded buffers.

## Required message extensions

Command live messages should support:

- `series_append`
- `series_patch_last`
- `series_reset`
- `scanner_patch`
- `execution_event`
- `forecast_replace`
- `system_patch`
- `critical_event`

A reconnect/generation change requires a new complete snapshot and discards incompatible prior live state.

## Live candlestick chart

Minimum data:

```text
time
open
high
low
close
volume|null
complete
feed
session
quality_state
```

Rules:

- completed bars append once;
- forming bar patches only the last candle;
- no fake candles for missing intervals;
- session/feed boundaries marked;
- bounded in-memory live history;
- older history fetched through existing bounded REST/history route.

## Overlay series

Supported overlays must be typed and versioned:

- VWAP;
- momentum/return feature(s) actually used by current strategy;
- volatility/range bands;
- signal score/threshold;
- bid/ask/mid;
- spread bps;
- stop/target/entry levels;
- forecast central path;
- forecast lower/upper band;
- feed quality ribbon.

The backend provides authoritative derived values when they affect trading logic. Frontend-only calculations are presentation-only.

## Forecast contract

A forecast replacement includes:

```text
forecast_id
symbol
issued_at
feature_as_of
horizon_seconds
model_version
methodology_version
central_path[]
lower_path[]|null
upper_path[]|null
confidence_level|null
expires_at
quality_state
```

Client automatically removes or marks projection expired after `expires_at`.

## Scanner wall

The scanner wall should update using `scanner_patch` rather than complete rerenders.

Minimum per-symbol fields:

- last/mid;
- sparkline delta;
- spread bps;
- quote age;
- events/min;
- evaluable state;
- candidate state;
- rejection code;
- distance to candidate threshold when methodologically defined;
- position/order flags;
- rank score and rank reason.

UI may animate rank changes, but ranking values must be real.

## Order/fill tape

Use canonical broker lifecycle state. Each item includes:

- timestamp;
- symbol;
- side;
- event type;
- quantity;
- price when known;
- order id (internal-safe display id may be shortened);
- position linkage;
- rejection reason when relevant.

Never infer fills from market price crossing an order.

## Account/performance charts

Append from reconciled account snapshots and ledger events.

Series:

- account equity;
- normalized equity;
- realized PnL;
- unrealized PnL;
- drawdown;
- gross exposure;
- risk throttle;
- capital allocation multiplier;
- candidate/intention/fill counts;
- slippage/fill quality summaries.

Closed market periods may be visually compressed only with an explicit axis/session treatment. Raw timestamps remain preserved.

## Visual interaction

Required:

- select symbol from scanner -> symbol chart updates;
- time-range controls;
- overlay toggles;
- live/follow mode;
- pause visual following without pausing RHEN;
- tooltips showing timestamp, source, provenance, and exact values;
- forecast toggle distinct from observed indicators;
- stale/degraded banners;
- mobile layout that prioritizes scanner + selected chart + execution state.

## VELUM replay integration

Reuse chart primitives. Replay controller publishes the same visual schema under `source=VELUM_REPLAY` with `replay_time`.

No live broker actions are permitted from replay controls.

## Performance engineering

- prefer incremental chart APIs;
- avoid React state replacement of full arrays on each tick;
- memoize static axes/legend configuration;
- cap live point buffers;
- downsample historical series outside active viewport;
- batch normal updates at 100-250 ms;
- process critical broker/risk events immediately;
- maintain responsiveness with all 24 scanner symbols active.

## Accessibility/readability

- color cannot be the sole carrier of state;
- candidate/rejection/status text remains visible;
- forecast is labeled in legend/tooltips;
- stale/degraded state uses text/icon treatment as well as color;
- reduced-motion preference is honored;
- touch targets are usable on mobile.

## Non-goals

- HFT/order-book heatmaps without authoritative depth data;
- simulated Level II depth;
- fake tick animations;
- browser-side live strategy decisions;
- rendering every raw quote as a persistent historical record;
- adding options/short/leverage controls.
