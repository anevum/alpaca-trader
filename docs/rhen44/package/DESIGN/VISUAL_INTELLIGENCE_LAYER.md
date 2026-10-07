# Command Visual Intelligence Layer

## Purpose

RHEN 4.4 must convert canonical live market, broker, signal, risk, forecast, and system-health state into a high-fidelity real-time visual interface without inventing activity.

This is a production subsystem, not cosmetic animation.

Every rendered mark must be one of four provenance classes:

1. OBSERVED - directly sourced from market/broker/account events.
2. DERIVED - deterministic transformation of observed state.
3. FORECAST - model output with explicit horizon, timestamp, version, and uncertainty.
4. OPERATIONAL - system/session/feed/risk/authority state.

No unlabeled synthetic price paths, random oscillation, fake volume, interpolated missing candles, decorative projections, or activity pulses are permitted.

## Runtime placement

```text
Market/Broker streams
        |
        v
Canonical RHEN state
        |
        +--> feature/signal/risk state
        +--> NOSTRA forecast state
        +--> execution/position/account state
        +--> IREN/system state
        |
        v
Visual State Projector
        |
        v
Command live WebSocket
        |
        v
Single client state store
        |
        +--> Live Market
        +--> Symbol Detail
        +--> Performance
        +--> Forecast
        +--> System
        +--> VELUM Replay
```

The visual layer is read-only with respect to trading decisions. Browser cadence never controls RHEN trading cadence.

## Visual provenance contract

Every chart series and overlay carries:

- `provenance`: OBSERVED | DERIVED | FORECAST | OPERATIONAL;
- `source`: feed/model/subsystem identifier;
- `as_of`: authoritative timestamp;
- `session`;
- `symbol` when applicable;
- `units`;
- `quality_state`: LIVE | STALE | DEGRADED | WARMING | UNAVAILABLE;
- `methodology_version` for derived/forecast series;
- `horizon_seconds` for forecasts;
- `confidence_level` for forecast bands when available.

Forecast visuals must never be styled identically to observed market data.

## Core Command views

### 1. COMMAND / LIVE

Purpose: immediate market and execution awareness.

Must include:

- active session/feed/authority strip;
- 24-symbol live scanner wall;
- ranked WATCH/NEAR/CANDIDATE list;
- selected-symbol real-time candlestick chart;
- bid/ask/mid/spread state;
- current VWAP and approved strategy overlays;
- candidate threshold and rejection state;
- live order/fill tape;
- open-position markers;
- feed freshness/latency ribbon;
- evaluable/candidate/intention/fill pulse;
- account equity/exposure mini-panels.

### 2. COMMAND / SYMBOL

Purpose: explain exactly what RHEN sees and why it acts or does not act.

Required layers:

- OHLC candlesticks;
- volume bars when authoritative volume is available;
- bid/ask midpoint and spread history;
- VWAP;
- rolling return/momentum features used by the active strategy;
- volatility or realized-range bands when methodologically valid;
- candidate thresholds;
- entry, partial fill, fill, stop, target, exit markers;
- active position average price;
- MAE/MFE path when available;
- rejection timeline;
- stale/warmup/feed-gap shading;
- forecast curve/band only when a current validated forecast exists.

### 3. COMMAND / FORECAST

Purpose: display NOSTRA outputs as forecasts rather than facts.

Required:

- observed price history;
- projected central path or expected-return path;
- confidence/credible interval as a band;
- horizon marker;
- regime probabilities;
- forecast issued-at timestamp;
- methodology/model version;
- forecast age;
- forecast-vs-actual error history;
- automatic stale/expired state.

No forecast is rendered beyond its valid horizon.

### 4. COMMAND / PERFORMANCE

Required:

- account equity curve;
- normalized return curve;
- realized and unrealized PnL traces;
- drawdown curve;
- rolling win/loss and expectancy statistics;
- candidate -> intent -> fill conversion;
- fill quality/slippage distributions;
- session-specific performance;
- strategy/policy comparison;
- exposure and concentration;
- feed/latency incidents aligned to performance timeline.

Graphs must preserve real temporal gaps for audit but may compress closed/inactive market windows visually when explicitly labeled.

### 5. COMMAND / SYSTEM

Required:

- stream heartbeat ribbon;
- expected-vs-actual feed;
- connection generation and reconnect markers;
- events/sec;
- source freshness percentiles;
- transport latency percentiles;
- queue/backpressure state;
- warm-start/reconstruction progress;
- persistence/reconciliation health;
- active incidents;
- current authority matrix;
- protected configuration fingerprint and drift state.

### 6. COMMAND / REPLAY

VELUM replay must use the same rendering primitives as live Command, driven by replay time rather than wall-clock time.

Replay must support:

- candle-by-candle reconstruction;
- signal evolution;
- candidate/rejection transitions;
- order/fill overlays;
- forecast-vs-actual overlays where historical point-in-time forecasts exist;
- pause, speed, scrub, and reset;
- no future information leakage into the current replay frame.

## Candle model

Candles are built from canonical completed bars. A forming candle may be displayed only when its source and incomplete status are explicit.

Rules:

- never fabricate missing candles;
- never forward-fill OHLC values across data gaps;
- session boundaries are explicit;
- overnight/premarket/regular/after-hours may use separate background bands;
- feed changes are marked;
- split/corporate-action-adjusted history must be handled consistently with the underlying analytical method.

## Live scanner visualization

Each symbol card/row should expose:

- symbol;
- latest price/midpoint;
- micro sparkline from real observations;
- spread bps;
- quote age;
- event/activity rate;
- session;
- EVALUABLE/WARMING/BLOCKED;
- HOLD/WATCH/NEAR/CANDIDATE/ORDER_INTENT;
- top rejection reason;
- open position/order marker;
- signal score or distance-to-threshold only when defined by strategy methodology.

Scanner sort order may change in real time using a stable rank score, but movement must be rate-limited enough to remain readable.

## Forecast visualization rules

Forecast output is visualized only if:

- model state is current;
- input feature timestamp is point-in-time valid;
- model/methodology version is known;
- horizon is known;
- uncertainty is available or the UI explicitly states its absence;
- forecast has not expired;
- the forecast does not grant execution authority by itself.

Recommended encoding:

- solid observed series;
- visually distinct dashed/dotted central forecast;
- translucent uncertainty band;
- explicit horizon boundary;
- tooltip/legend provenance.

## Execution visualization

Order lifecycle markers must come from broker/canonical ledger state:

- order intent;
- submitted;
- accepted;
- partial fill(s);
- filled;
- canceled;
- rejected;
- exit submitted/filled;
- stop/target state.

Position chart must show:

- average entry;
- size/exposure;
- active risk level;
- stop and target;
- unrealized result;
- elapsed holding time;
- realized exit result after close.

No guessed fill marker is allowed.

## Account and portfolio visualization

Use broker/reconciled account state. Required metrics:

- equity;
- cash/buying power as authorized;
- gross exposure;
- per-position exposure;
- sector/ETF concentration when classified;
- realized/unrealized PnL;
- drawdown;
- current session and daily loss usage;
- risk throttle and capital-governor multiplier.

## Motion and rendering

Market events are processed immediately by RHEN. Command publishes coalesced state deltas at approximately 100-250 ms.

Frontend guidance:

- render market motion at animation-frame cadence when new data exists;
- interpolate screen position only between two known rendered states for visual smoothness, never create synthetic market observations;
- preserve exact authoritative values in labels/tooltips;
- use transitions under 250 ms for rank/line movement;
- disable or reduce non-essential motion under reduced-motion preference;
- freeze values and visibly mark stale when live transport is lost.

## Chart performance budgets

Target for a normal Command session on modern desktop/mobile hardware:

- one protected WebSocket;
- maximum 8-10 normal visual state publications/sec;
- critical execution/risk events immediate;
- no full-series replacement on each tick;
- append/ring-buffer updates for live series;
- bounded live points per series;
- historical downsampling by viewport/resolution;
- no O(N symbols x full-history) recomputation per event;
- no browser-side strategy recomputation used for trading decisions.

## Recommended frontend primitive hierarchy

Use the existing frontend stack and current chart library where capable. If current charting cannot support real-time candles/overlays efficiently, adopt one well-maintained library rather than multiple overlapping chart engines.

Preferred logical primitives:

- `RealtimeCandlestickChart`
- `MiniSparkline`
- `ForecastBand`
- `ExecutionOverlay`
- `FeedHealthRibbon`
- `RejectionPulse`
- `EquityDrawdownChart`
- `ExposureMap`
- `RegimeProbabilityChart`
- `ReplayTimeline`

All primitives consume typed canonical visual-series contracts, not raw ad hoc API payloads.

## Failure behavior

- missing/stale market data -> freeze last known mark and show stale shading;
- feed mismatch -> remove execution-grade styling and show capability block;
- forecast expired -> hide projection or mark expired; never extend it;
- broker stream degraded -> execution markers show reconciliation pending;
- WebSocket loss -> entire live surface enters stale state;
- replay missing historical evidence -> show gap, never synthesize;
- chart exception -> fail panel locally; never affect RHEN runtime.

## Release requirement

RHEN 4.4 is not visually complete until a user can watch the scanner, selected symbol, orders/fills, account state, feed quality, and forecast/replay state evolve from real canonical data without manual refresh and without fake activity.
