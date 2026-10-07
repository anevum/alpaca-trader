# Command contract

## Operating principle

Command is a real-time view of RHEN's canonical state, not a 3-5 second polling approximation and not a decorative simulation.

Backend trading logic remains event-driven. The browser receives coalesced state updates at roughly 100-250 ms through one protected WebSocket, with critical broker/risk events sent immediately.

## Operate

### Live Market Fabric

Show:

- session and active feed;
- connection state;
- intended/subscribed symbols;
- events/sec;
- source quote age p50/p95;
- transport latency p50/p95;
- evaluable fraction;
- reconnect/coalescing counters;
- explicit capability block reason;
- restart/warmup state.

### 24-symbol scanner board

For each symbol show real fields only:

- bid/ask or midpoint;
- spread bps;
- quote age;
- activity state;
- WARMING / EVALUABLE / BLOCKED;
- HOLD / WATCH / NEAR / CANDIDATE / ORDER_INTENT;
- most recent rejection reason;
- open position/order marker.

### Adaptive Control

Show:

- mode: DISABLED / SHADOW / ACTIVE / FALLBACK / DEFENSIVE_LOCK;
- current policy profile and fingerprint;
- primary NOSTRA regime/confidence;
- time in profile / upgrade eligibility;
- current allocation multiplier;
- effective target/stop/hold/re-entry values;
- baseline and hard-limit comparison;
- transition reasons;
- stale/fallback warnings.

## Discover

Show:

- session-by-session evaluable rate;
- candidate rate per evaluable symbol-hour;
- evaluations/candidates/intents/fills;
- rejection distribution;
- spread/freshness distributions;
- current regime probability distribution;
- policy shadow decisions versus actual 4.3 decisions;
- per-regime candidate/execution economics;
- capital utilization by policy;
- recent net-edge distribution.

## Review

Show:

- extended-session promotion state independently for premarket, after-hours, overnight;
- profile candidates in validation;
- GRAEN/VELUM/ASC progress;
- promotion blockers;
- protected configuration drift items;
- explicit `NO REVIEW REQUIRED` when none exists.

## System

Show:

- stream heartbeat/connection generation;
- feed entitlement/expected-vs-actual match;
- per-session capability map;
- reconstruction/warmup state;
- controller/NOSTRA freshness;
- policy-library fingerprint;
- persistence health;
- latest incidents/exceptions.

## Transport

Implement the protected WebSocket contract in `IMPLEMENTATION/COMMAND_REALTIME_STREAM.md`. Keep existing REST Command endpoints for initial/fallback diagnostic reads, not as the primary live refresh mechanism.

## Graph behavior

- append real points as they arrive;
- avoid giant visual flatline gaps outside relevant observation windows;
- do not interpolate missing market data;
- preserve timestamps/gaps for audit;
- mark stale/disconnected state explicitly;
- historical points remain queryable.


## Visual Intelligence contract

Command must implement the requirements in `DESIGN/VISUAL_INTELLIGENCE_LAYER.md` and `IMPLEMENTATION/COMMAND_VISUALIZATION_SPEC.md`.

### Required visual surfaces

- LIVE: 24-symbol scanner wall, selected-symbol candlestick chart, bid/ask/spread, strategy overlays, execution tape, account/exposure state, feed-health ribbon.
- SYMBOL: candles, volume where authoritative, VWAP, strategy features, candidate thresholds, rejection timeline, position/stop/target/fill overlays, and current forecast when valid.
- FORECAST: observed history plus clearly distinct projected path and uncertainty band, issue time, horizon, model version, age, expiry, and forecast-vs-actual history.
- PERFORMANCE: equity, normalized return, realized/unrealized result, drawdown, exposure, candidate-to-fill conversion, slippage/fill quality, and session performance.
- SYSTEM: heartbeat, feed match, freshness, latency, reconnects, backpressure, reconstruction, persistence/reconciliation, incidents, and authority matrix.
- REPLAY: VELUM uses the same chart primitives under replay time with no future leakage and no broker-write controls.

### Truth rules

- Every series/overlay must expose OBSERVED, DERIVED, FORECAST, or OPERATIONAL provenance.
- No missing candle, quote, volume, fill, or signal may be invented.
- Observed and forecast data must be visually distinguishable.
- A forecast is hidden or explicitly expired after its valid horizon.
- Visual interpolation is presentation-only and cannot alter exact labels/tooltips or canonical data.
- A local chart failure cannot affect RHEN runtime.
