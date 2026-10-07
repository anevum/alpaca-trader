# Primary implementation workload

Canonical workload: `ANEVUM.RHEN.BUILD.2026-10-06.002.V4-4-REALTIME-EXTENDED`

Continue ANEVUM/RHEN from the latest actual GitHub/Railway state **after RHEN 4.3 is fully complete, live, reconciled, healthy, and frozen as rollback baseline**. Do not restart architecture analysis and do not undo completed 4.3 work.

Repositories:
- backend/runtime: `anevum/rhen`
- frontend/Command: `anevum/anevum-web`

Railway RHEN project:
- `808098a9-937e-4ca4-ac98-dd2dcfef5d0c`

Use this expanded RHEN 4.4 package as the implementation contract.

## Primary objective

Build RHEN 4.4 as one coordinated release:

1. event-driven Alpaca market/broker ingestion with real stream health, persistent warm-start/recovery, candidate/rejection telemetry, session/feed capability, and sub-second Command updates;
2. a read-only Visual Intelligence Layer with real-time candles, scanner motion, execution overlays, performance/system charts, NOSTRA forecast bands, and VELUM replay, all driven by canonical data and explicit provenance;
3. bounded Adaptive Policy Control that selects among validated profiles under existing hard risk limits.

## Before editing

1. verify final 4.3 main SHA, CI, production deployment, health/reconciliation and protected configuration;
2. record final 4.3 commit/deployment/config fingerprint as rollback anchor;
3. inspect current code paths and map package module names onto current abstractions rather than duplicating functionality;
4. preserve all production behavior behind shadow/equivalence gates;
5. confirm current Alpaca data entitlement and do not assume Plus/SIP/BOATS access.

## Mandatory invariants

- no crypto reintroduction;
- long-only U.S. equities/ETFs remain the only broker-write scope;
- options research only; no options write path;
- short equities disabled;
- no leverage/margin expansion;
- existing hard risk ceilings authoritative;
- no LLM/model call in the live order path;
- no new Railway service for core 4.4;
- market stream failure/staleness/capability mismatch can only reduce authority;
- Basic/free-resource mode must work without a paid subscription;
- engine is event-driven; Command may coalesce UI deltas but may not control trading cadence;
- REST remains bootstrap/reconciliation/recovery, not primary 5-second live polling;
- broker fills/cancels/rejections must never be dropped by market-data backpressure;
- stream restart requires deterministic reconstruction/warmup before new entries;
- current 4.3 strategy formulas remain champion until explicit validation/promotion;
- extended sessions promote independently;
- overnight remains observe/shadow by default;
- adaptive profiles cannot expand hard limits or self-promote;
- SHADOW adaptive mode produces no broker-intent difference from 4.3;
- Command shows real feed/session/candidate/rejection/policy state and reasons only;
- all dynamic visuals declare OBSERVED/DERIVED/FORECAST/OPERATIONAL provenance;
- no missing candles/volume/fills are fabricated;
- forecasts require issue time, horizon, version, expiry, and uncertainty state;
- browser visualization code has no broker-write authority and no execution-critical strategy ownership.

## Implementation order

Follow `IMPLEMENTATION/IMPLEMENTATION_SEQUENCE.md` exactly unless current main proves a safer equivalent ordering is required.

Priority is:

1. freeze 4.3;
2. stream/feed primitives;
3. market stream shadow ingestion;
4. broker trade_updates;
5. warm-start/recovery;
6. scanner/rejection telemetry;
7. Command live WebSocket;
8. Visual Intelligence core: candles, scanner, execution/account/feed visuals;
9. Visual Intelligence advanced: forecast/performance/system/replay;
10. stream-state promotion for regular session after equivalence;
11. extended 24/5 observation/capability map;
12. adaptive policy/NOSTRA/capital governor;
13. combined validation;
14. conservative canary only after gates.

## Free-resource runtime target

```text
04:00-08:00  capability blocked for live entry
08:00-09:30  IEX real-time, observe/shadow
09:30-16:00  IEX real-time, preserve 4.3 live
16:00-17:00  IEX real-time, observe/shadow
17:00-20:00  capability blocked for live entry
20:00-04:00  overnight feed, observe/shadow
```

Do not "fix" unavailable windows by relaxing freshness thresholds.

## Completion output

Return:

- merged PRs/SHAs;
- Railway deployment IDs/status;
- full CI/test results;
- final data entitlement and active feed routing;
- stream connection/coverage/freshness metrics;
- Command WebSocket verification and observed UI cadence;
- screenshot/recorded evidence that live candles/scanner/execution/feed visuals are driven by real canonical state;
- visual provenance and forecast-expiry verification;
- VELUM replay no-lookahead visual verification;
- restart/recovery test result;
- regular-session stream-vs-4.3 equivalence evidence;
- candidate/evaluable/rejection telemetry verification;
- feature-flag state;
- adaptive controller mode and library/profile fingerprints;
- extended-session promotion states;
- remaining evidence-dependent blockers;
- tested rollback procedure to final 4.3 anchor.

Do not expand live authority merely to complete the workload.
