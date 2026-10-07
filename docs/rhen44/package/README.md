# ANEVUM.RHEN.PACKAGE.2026-10-06.003.V4-4-VISUAL-INTELLIGENCE

Target release: **RHEN 4.4**  
Parent package: `ANEVUM.RHEN.PACKAGE.2026-10-06.002.V4-4-REALTIME-EXTENDED`  
Activation point: only after RHEN 4.3 is confirmed live, reconciled, healthy, and frozen as the rollback baseline.

## Purpose

RHEN 4.4 is now one coordinated release with three layers:

1. **Real-Time Market Fabric + Extended 24/5 Observation** — event-driven Alpaca market/broker streams, persistent stream state, explicit feed/session capability, live candidate/rejection telemetry, and sub-second Command updates.
2. **Visual Intelligence Layer** - real-time candlesticks, scanner motion, execution overlays, forecast bands, performance charts, replay visuals, and system-health graphics driven only by canonical data.
3. **Adaptive Policy Control** - bounded NOSTRA-informed selection among separately validated policy profiles plus a capital governor under existing hard risk limits.

The real-time layer fixes the observability/state foundation. The visual layer makes that state continuously inspectable without inventing activity. The adaptive layer changes conviction only after validation. Neither layer may expand protected live authority by itself.

## Canonical runtime

```text
Alpaca market WebSocket
        |
        v
Real-Time Market Fabric
  feed/session health
  per-symbol state
  incremental features
  candidate/rejections
        |
        +--> RHEN 4.3 strategy champion
        |
        +--> NOSTRA regime state
                    |
                    v
          Adaptive Policy Controller
                    |
             Capital Governor
                    |
                    v
        existing risk/execution
                    |
             Alpaca broker
                    |
          trade_updates stream
                    |
       ledger + IREN + Command
                    |
                    v
       Visual Intelligence Layer
   candles / scanner / forecast / replay
```

## Locked decisions

1. RHEN 4.3 remains the production champion and rollback anchor until 4.4 gates pass.
2. No crypto work is reintroduced.
3. Options remain research-only; broker-write authority stays disabled.
4. Short equities remain hard-disabled.
5. Leverage/margin expansion remains hard-disabled.
6. Existing RHEN hard risk checks remain authoritative.
7. Market ingestion becomes event-driven; REST is retained for bootstrap/reconciliation/recovery, not primary live polling.
8. Engine processing is event-by-event. Command rendering is coalesced at ~100-250 ms, not 5-second polling.
9. Basic/free-resource mode is a supported first-class configuration. Paid SIP/BOATS is an optional future capability upgrade, not a 4.4 dependency.
10. No feed outage, stale state, subscription mismatch, configuration drift, or stream lag may increase authority.
11. Extended sessions promote independently. Overnight remains observe/shadow by default in 4.4.
12. Adaptive runtime may only select approved policy profiles; it may not freely rewrite live strategy settings.
13. No LLM/model call belongs in the live order path.
14. No new Railway service is required for core 4.4.
15. Command must display real state/reasons; no fake animation or fabricated market motion.
16. Every dynamic visual must be tagged as OBSERVED, DERIVED, FORECAST, or OPERATIONAL.
17. Candles, fills, volume, and missing intervals may never be fabricated for visual continuity.
18. Forecast curves require explicit issue time, horizon, model/methodology version, expiry, and uncertainty state.
19. Visual smoothness may interpolate screen position only; it may never create synthetic market observations.
20. The browser is read-only with respect to trading decisions and may not recompute execution-critical strategy state.

## Free-resource operating map

```text
04:00-08:00  DATA_CAPABILITY_BLOCKED for live execution
08:00-09:30  IEX real-time; observe/shadow until promoted
09:30-16:00  IEX real-time; preserve 4.3 regular live authority
16:00-17:00  IEX real-time; observe/shadow until promoted
17:00-20:00  DATA_CAPABILITY_BLOCKED for live execution
20:00-04:00  Alpaca overnight feed; observe/shadow only
```

Future paid-data mode swaps the adapters to SIP (04:00-20:00) and BOATS (20:00-04:00) without redesigning RHEN.

## New package contents

In addition to the original Adaptive Policy Control package:

- `RESEARCH/EXTENDED_24_5_RESEARCH_REPORT.md`
- `DESIGN/EVENT_DRIVEN_MARKET_FABRIC.md`
- `DESIGN/EXTENDED_24_5_RUNTIME.md`
- `DESIGN/LIVE_SCANNER_AND_REJECTION_ENGINE.md`
- `DESIGN/STREAM_STATE_AND_RECOVERY.md`
- `IMPLEMENTATION/COMMAND_REALTIME_STREAM.md`
- `IMPLEMENTATION/STREAM_EVENT_SCHEMA.json`
- `DESIGN/VISUAL_INTELLIGENCE_LAYER.md`
- `IMPLEMENTATION/COMMAND_VISUALIZATION_SPEC.md`
- `IMPLEMENTATION/VISUAL_EVENT_SCHEMA.json`
- `IMPLEMENTATION/VISUAL_ACCEPTANCE_TESTS.md`

Existing architecture, code map, implementation sequence, telemetry, tests, handoff prompt, definition of done, manifest, and release notes have been expanded to make these capabilities part of the canonical 4.4 implementation.

## Release principle

> Stream the market immediately. Visualize only canonical truth. Forecast with explicit uncertainty. Adapt only from validated evidence. Never adapt safety boundaries.
