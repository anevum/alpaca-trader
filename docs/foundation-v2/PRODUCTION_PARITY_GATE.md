# Foundation v2 — Production Parity Gate

Status: ARCHIVAL — superseded by [current production state](CURRENT_STATE.md)
Date: 2026-10-01

This document preserves the original migration gate. Its Supabase-canonical and shadow-activation instructions are historical and must not be reapplied to the current production runtime.

## Purpose

This gate verifies that RHEN production evidence can be copied into ANEVUM Core PostgreSQL without changing trading behavior and without losing or altering events.

Supabase remains canonical throughout this gate.

## Protected scope

This gate must not change:
- strategy logic;
- risk limits;
- position sizing;
- capital allocation;
- broker execution behavior;
- crypto execution authorization;
- Alpaca credentials or order ownership;
- existing Supabase canonical persistence.

## Required production activation

Enable exactly one additional persistence path:

```
RHEN event
  -> existing Supabase canonical persistence
  -> Foundation durable outbox
  -> authenticated Foundation ingest
  -> Railway PostgreSQL
```

The Foundation path is shadow-only. Delivery failure must never block or alter execution.

## Required production storage

Mount persistent storage at `/data` on the live RHEN service and use:

```
FOUNDATION_OUTBOX_PATH=/data/rhen-foundation-evidence.sqlite3
```

The outbox must survive process restarts.

## Required variables

```
FOUNDATION_SHADOW_ENABLED=true
FOUNDATION_INGEST_URL=https://foundation-ingest-staging.up.railway.app/v1/events
FOUNDATION_INGEST_TOKEN=<migration token>
FOUNDATION_OUTBOX_PATH=/data/rhen-foundation-evidence.sqlite3
FOUNDATION_FLUSH_SECONDS=1.0
FOUNDATION_BATCH_SIZE=50
```

Do not modify any existing trading variable during this change.

## Observation window

Minimum promotion sample:
- at least 100 canonical RHEN events; and
- at least one full live market observation period with normal scanning; and
- if any order-intent/order/fill/position events occur naturally, they must be included.

Do not generate trades to satisfy the sample.

## Parity comparison

Use `foundation.parity.compare_event_sets` on the same event-key window from:
1. Supabase `private.trading_events`; and
2. Railway PostgreSQL `rhen.events`.

Canonical comparison fields:
- event_key
- run_id
- strategy_version_id
- event_type
- occurred_at
- symbol
- correlation_id
- source
- payload

The verifier hashes canonical JSON for each event and reports:
- missing target keys;
- unexpected target keys;
- digest mismatches;
- per-event-type counts;
- total match ratio.

## PASS criteria

All must be true:
- sample >= 100 events;
- match ratio = 1.000000;
- missing target events = 0;
- unexpected target events = 0;
- digest mismatches = 0;
- Foundation outbox queued = 0 after catch-up;
- Foundation delivery error = null;
- legacy Supabase persistence remains healthy;
- RHEN health remains healthy;
- no strategy/risk/broker configuration drift;
- restart test confirms outbox persistence.

## FAIL / rollback criteria

Immediately disable `FOUNDATION_SHADOW_ENABLED` if any of the following occur:
- RHEN health changes because of Foundation;
- Foundation code affects execution timing or broker behavior;
- unbounded outbox growth;
- repeated Foundation delivery errors;
- process crash/restart loop;
- unexpected CPU/memory pressure;
- canonical Supabase writes regress;
- any trading/risk variable changes unintentionally.

Disabling the shadow variable is sufficient to stop new Foundation mirroring. Existing Supabase operation remains canonical.

Do not delete the Foundation outbox after rollback. Preserve it for diagnosis/replay.

## Promotion after PASS

A parity PASS does not itself authorize removing Supabase.

After PASS:
1. record the evidence window and verifier output;
2. keep dual-write running for an additional observation period;
3. migrate IREN state/scheduling next;
4. only later consider making Railway PostgreSQL canonical;
5. remove Supabase dependencies only after all dependent systems migrate and rollback verification is complete.

## Current verified prerequisites

Already passed in staging:
- PostgreSQL schema migration;
- database readiness;
- durable SQLite/WAL spool;
- restart persistence;
- event-key idempotency;
- duplicate replay;
- authenticated cross-project transport;
- negative authentication test;
- RHEN TradingEventSink shadow integration;
- CI.

The only remaining prerequisite for this gate is execution of the production shadow activation itself.
