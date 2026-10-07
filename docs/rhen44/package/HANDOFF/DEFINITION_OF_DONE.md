# Definition of done

RHEN 4.4 engineering is complete when the following machinery is built, deployed behind appropriate gates, verified, and rollback-safe. Extended/assertive live promotion may remain evidence-blocked after engineering completion.

## Real-Time Market Fabric

- market WebSocket ingestion exists in the current RHEN service;
- intended 24 symbols subscribe within verified Basic entitlement;
- source timestamps drive freshness;
- per-symbol state is event-driven;
- broker `trade_updates` updates ledger/order state promptly;
- bounded backpressure never drops broker lifecycle events;
- REST is bootstrap/reconciliation/recovery, not primary live polling;
- disconnect/stale/feed mismatch disables new entries appropriately;
- stream health/freshness/capability is visible in Command;
- no synthetic market events or fake activity are rendered.

## Recovery

- rolling state reconstructs after restart/deploy;
- open orders/positions reconcile safely;
- warmup is explicit and blocks premature entry;
- no duplicate order on reconnect;
- recovery exercise has been run successfully.

## Scanner / observability

- evaluable rate exists;
- candidate rate exists;
- every evaluation has a terminal rejection/candidate classification;
- minute/session rejection summaries exist;
- Command shows all 24 symbols with freshness/spread/state/reason;
- broken-pipeline diagnostics distinguish quiet market from bad data.

## Command

- one protected live socket supplies canonical snapshots/deltas;
- normal visual updates are ~100-250 ms when state changes;
- critical execution/risk events are immediate;
- generation/sequence/reconnect semantics are tested;
- loss of live socket is clearly marked stale/degraded;
- graphs append real points and do not fabricate flatline continuity.


## Visual Intelligence

- live selected-symbol candlesticks update from canonical bar state without page refresh;
- 24-symbol scanner updates incrementally with micro-sparklines, spread/freshness, candidate state, and rejection reason;
- observed, derived, forecast, and operational series are explicitly distinguishable;
- VWAP/strategy overlays match backend canonical values;
- order/fill/position/stop/target markers come only from canonical broker/ledger state;
- NOSTRA forecast path/band includes issue time, horizon, methodology/model version, expiry, and uncertainty state;
- forecast-vs-actual uses stored point-in-time forecasts only;
- account/performance views include equity, normalized return, realized/unrealized result, drawdown, exposure, conversion, and fill-quality evidence;
- system view visualizes feed health, latency, reconnects, warmup/reconstruction, incidents, and authority;
- VELUM replay reuses live visual primitives with no future leakage and no broker-write controls;
- one protected socket supplies the visual surface;
- normal visual cadence remains approximately 100-250 ms when state changes;
- all live buffers are bounded and historical loading/downsampling is efficient;
- socket loss freezes last values and visibly marks stale;
- no fake candles, fake volume, fake fills, fake projections, or decorative market motion exist.

## 4.3 preservation

- final 4.3 baseline is recorded;
- initial stream shadow mode creates no unintended broker-intent difference;
- regular-session promotion of stream state is backed by equivalence tests;
- rollback to 4.3 data/decision path is tested/documented.

## Extended 24/5

- session router exposes PREMARKET/REGULAR/AFTER_HOURS/OVERNIGHT separately;
- Basic capability gaps are represented explicitly;
- overnight eligibility/halt state is checked;
- extended/overnight remain independently gated;
- no freshness threshold is weakened to mask feed gaps;
- paid SIP/BOATS support is adapter-ready but not required for release.

## Adaptive control

- controller, NOSTRA runtime wrapper and capital governor exist with tests;
- disabled mode is exact 4.3 policy behavior;
- shadow mode does not change broker intent;
- active mode can use only approved/versioned profiles;
- hard risk limits cannot be weakened;
- no-margin invariant enforced;
- stale/invalid controller state falls back safely;
- decisions/orders retain policy/regime provenance;
- VELUM/ASC/GRAEN profile validation remains no-lookahead and protected.

## Hard-disabled authorities

The following remain impossible through ordinary configuration:

- crypto execution;
- options broker writes;
- short-equity entries;
- leverage/margin expansion beyond existing authority;
- unvalidated profile/session self-promotion.

## Production promotion

A conservative 4.4 ACTIVE adaptive canary is permitted only after shadow equivalence, clean stream/recovery behavior, stable storage/reconciliation, and approved safe profile library.

Premarket/after-hours/overnight live entry each require separate promotion evidence. A generic "24/5 live" flag does not satisfy this definition.
