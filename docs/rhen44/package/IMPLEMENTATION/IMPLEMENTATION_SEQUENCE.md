# RHEN 4.4 implementation sequence

## Slice 0 — freeze final 4.3

1. Confirm 4.3 live production completion gate.
2. Record final main SHA, Railway deployment, protected configuration fingerprint, account/risk state, and health/reconciliation baseline.
3. Run complete backend/frontend baseline tests.
4. Freeze this as rollback anchor.

## Track A — Real-Time Market Fabric foundation

### Slice 1 — pure stream/feed primitives

Build event types, feed/session router, entitlement validation, per-symbol state, source-time freshness, and tests. No execution behavior change.

### Slice 2 — market WebSocket shadow ingestion

Connect IEX/overnight streams under Basic entitlement and subscribe the configured 24-symbol universe.

Keep existing 4.3 polling path authoritative initially. Compare:

- latest quote/bar values;
- freshness;
- candidate prerequisites;
- symbol coverage;
- disconnect behavior.

No broker-intent change.

### Slice 3 — broker `trade_updates`

Make order/fill/cancel/rejection updates event-driven and reconcile them into the canonical ledger. Verify idempotence and no lost events.

### Slice 4 — persistent warm-start/recovery

Implement historical bootstrap + stream reconstruction. Exercise restart/deploy during active observation. New entries remain blocked until required state is ready.

### Slice 5 — scanner/rejection telemetry

Add evaluable rate, candidate rate, canonical rejection taxonomy, minute/session aggregates, and broken-pipeline diagnostics.

### Slice 6 — Command live stream

Add protected backend WebSocket and frontend single-socket consumer. Target 100-250 ms visual delta flush. Keep REST snapshot as fallback diagnostics only.

### Slice 7 - Visual Intelligence foundation

Implement canonical visual provenance/schema, server-side visual state projection, typed append/patch/reset events, live candlesticks, scanner micro-sparklines, feed-health ribbon, order/fill overlays, and bounded client state. No execution behavior change.

### Slice 8 - Visual Intelligence advanced views

Add forecast bands, performance/equity/drawdown/exposure views, system-health visuals, and VELUM replay using the same primitives. Verify no fabricated market data and no browser-side execution logic.

### Slice 9 - promote stream cache as regular-session market-state source

Only after shadow equivalence. Preserve the exact 4.3 strategy formulas and hard gates. REST remains reconciliation/backup.

### Slice 10 - extended 24/5 observation

Run per-session feed routing and capability map:

- 04:00-08:00 Basic: capability blocked;
- 08:00-09:30 IEX: observe/shadow;
- 09:30-16:00 IEX: preserve 4.3 live;
- 16:00-17:00 IEX: observe/shadow;
- 17:00-20:00 Basic: capability blocked;
- 20:00-04:00 overnight: observe/shadow.

Collect session-specific data without changing live authority.

## Track B — Adaptive Policy Control

### Slice 11 - policy primitives

Build profile parsing/fingerprinting, hard-limit validation, hysteresis/dwell, fallbacks, immutable snapshots.

### Slice 12 - NOSTRA runtime state

Point-in-time live classifier using canonical stream state and completed observations. Persist bounded regime evidence.

### Slice 13 - capital governor

Hard-versus-effective envelope, drawdown/health/evidence throttles, no-margin cash constraint, binding-cap diagnostics.

### Slice 14 - adaptive shadow integration

`ADAPTIVE_POLICY_MODE=shadow`. Execution must still use exact 4.3 values. Persist counterfactual policy/allocation decisions.

### Slice 15 - combined Command integration

Expose real-time fabric + adaptive state in the same existing Operate/Discover/Review/System design.

## Track C — validation and promotion

### Slice 16 - VELUM/GRAEN/ASC validation

Evaluate:

- stream-vs-poll semantic equivalence;
- session-specific candidate/rejection economics;
- fixed-vs-adaptive profiles;
- execution-cost stress;
- transition stability;
- restart/reconnect resilience.

### Slice 17 - conservative adaptive ACTIVE canary

Only BASELINE_LOCKED / DEFENSIVE / NORMAL profiles initially. No extended-session authority expansion.

### Slice 18 - extended-session promotion one lane at a time

Each lane requires independent promotion gates. No package-level blanket "24/5 live" switch.

### Slice 19 - assertive profiles only with evidence

FAST_SCALP / TREND_EXTEND / ASSERTIVE_TREND remain shadow until their own evidence lineage passes.

## Merge invariant

At every merge, production behavior must remain either exact 4.3 behavior or strictly safer unless an explicit validated gate is deliberately promoted.
