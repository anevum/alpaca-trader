# RHEN 4.4 - Real-Time Market Fabric + Visual Intelligence + Adaptive Policy Control

Status: DEVELOPMENT PACKAGE / NOT RELEASED

RHEN 4.4 upgrades the system from periodic live observation to an event-driven market/broker state fabric and adds a truthful real-time Visual Intelligence Layer and bounded adaptive policy selection above the existing RHEN 4.3 strategy/risk/execution core.

## New real-time capabilities

- Alpaca market WebSocket ingestion instead of 5-second live polling;
- event-by-event market-state processing;
- Alpaca broker `trade_updates` for immediate order/fill/cancel/rejection state;
- session/feed capability router for premarket, regular, after-hours, and overnight;
- persistent warm-start/recovery across deploys/restarts;
- explicit quote/bar freshness and stream health;
- evaluable-rate and candidate-rate diagnostics;
- canonical rejection taxonomy;
- live 24-symbol Command scanner;
- protected Command WebSocket with ~100-250 ms visual delta publication;
- real graph point appends without fake market movement/flatline padding.

## Visual Intelligence capabilities

- real-time candlesticks from canonical bars;
- 24-symbol scanner wall with micro-sparklines, spread/freshness, candidate state and rejection reason;
- VWAP/strategy overlays sourced from backend canonical calculations;
- broker-grounded order/fill/position/stop/target markers;
- live equity, normalized return, drawdown, exposure and conversion charts;
- NOSTRA projected path with explicit uncertainty, horizon, issue time, version and expiry;
- feed freshness/latency/reconnect/warmup visual ribbons;
- VELUM replay using the same chart primitives with no future leakage;
- explicit OBSERVED/DERIVED/FORECAST/OPERATIONAL provenance;
- no fabricated candles, volume, fills, projections or market motion.

## Adaptive capabilities

- live point-in-time NOSTRA regime observations;
- versioned adaptive policy library;
- profile selection with hysteresis/dwell and safety vetoes;
- capital governor under existing hard limits;
- policy/regime provenance on trading evidence;
- VELUM/ASC/GRAEN validation and protected promotion.

## Current-resource design

4.4 supports Alpaca Basic as the default deployment. The configured 24-symbol universe fits the current 30-symbol Basic stock WebSocket limit.

Basic feed capability is represented honestly rather than stretched beyond coverage. Full real-time 24/5 consolidated coverage remains a future SIP + BOATS entitlement upgrade, not a release dependency.

## Still disabled / protected

- crypto;
- options broker writes;
- short equities;
- leverage expansion;
- blanket 24/5 live authority;
- model/LLM calls in the live order path;
- automatic promotion of unvalidated profiles or sessions.

## Release principle

> Stream the market immediately. Visualize only canonical truth. Forecast with explicit uncertainty. Adapt conviction only from validated evidence. Never adapt safety boundaries.
