# Visual intelligence acceptance tests

## Truth/provenance

- every visible dynamic series exposes provenance and source metadata;
- observed and forecast series are visually distinguishable;
- no forecast renders after expiry without explicit expired state;
- missing market intervals remain gaps, not fabricated candles;
- no fill marker appears without canonical broker/ledger evidence;
- no quote/trade/volume is invented for visual smoothness.

## Live behavior

- selected symbol candle patches within normal 100-250 ms Command publication cadence when source state changes;
- scanner rows patch independently without replacing full board;
- execution events appear immediately outside normal coalescing cadence;
- lost socket freezes last values and marks them stale;
- reconnect generation forces a fresh snapshot before deltas;
- visual pause/follow controls never pause RHEN itself.

## Candle/overlay integrity

- completed candle appended once;
- forming candle patches only current interval;
- VWAP/strategy overlays match backend canonical calculations;
- session/feed transitions are visible;
- stale/warmup/feed-gap periods are visible;
- stop/target/entry overlays match active canonical position/risk state.

## Forecast integrity

- issued_at/feature_as_of/horizon/model version present;
- confidence band matches returned lower/upper arrays;
- absent uncertainty is labeled rather than fabricated;
- forecast does not grant execution authority;
- forecast-vs-actual uses only point-in-time historical forecasts.

## Performance

- one socket serves the Command visual surface;
- 24-symbol scanner remains responsive during bursty regular-session data;
- chart updates append/patch rather than retransmitting full history;
- bounded ring buffers prevent unbounded client memory growth;
- historical downsampling does not change exact raw data available for audit;
- chart render failure cannot affect RHEN process/trading state.

## Replay

- replay uses same visual primitives as live;
- replay time controls do not call broker-write paths;
- no data later than replay clock is rendered as current decision evidence;
- gaps remain gaps;
- restart/reset restores deterministic replay state.

## Mobile/readability

- core scanner, selected chart, execution state usable on narrow viewport;
- status meaning is not color-only;
- reduced-motion mode remains fully informative;
- tooltips/labels expose exact values and timestamps.
