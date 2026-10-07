# RHEN 4.4 Real-Time Market Fabric

## Purpose

Replace periodic live-market polling as RHEN's primary observation mechanism with event-driven Alpaca WebSocket ingestion, while preserving REST polling for bootstrap, reconciliation, recovery, and audit.

The trading engine processes each accepted market/broker event as it arrives. Command is intentionally decoupled from engine speed and receives coalesced state deltas at a human-readable high frequency (default 125 ms / 8 Hz) plus immediate critical broker-state changes.

This document does **not** authorize new live trading sessions. It establishes the data and observability substrate required for later session-specific promotion.

## Locked principles

1. No artificial 5-second scan loop in the live decision path.
2. Market-data source timestamps are authoritative for freshness; UI receipt time is not.
3. Every accepted event is processed by the engine; UI redraws may be coalesced.
4. Quotes may be coalesced under backpressure; broker fills/rejections/cancels may never be dropped.
5. REST endpoints remain recovery/reconciliation tools, not the primary current-price path.
6. A disconnected, stale, lagging, unauthorized, or mismatched feed can only reduce trading authority.
7. No fake market activity is rendered in Command. If the market is quiet, the display is quiet and quote age increases.
8. The fabric runs inside the existing RHEN Railway service; no second polling daemon/service is introduced.
9. Existing 4.3 strategy formulas remain behaviorally unchanged until an explicit 4.4 validation gate promotes a new signal implementation.
10. Basic-plan constraints are first-class capabilities, not errors to hide.

## Runtime topology

```text
Alpaca market-data WebSocket
  IEX (Basic) / SIP (Plus)
  overnight (Basic) / BOATS (Plus)
            |
            v
   MarketStreamManager
     auth / subscribe
     reconnect / health
            |
            v
      EventNormalizer
 source_ts + receive_ts
 feed + session + symbol
            |
            v
      MarketStateStore  <------ REST bootstrap/reconciliation
      per-symbol state
      sequence / freshness
            |
            +----> FeedHealth / stale kill switch
            |
            +----> Incremental Feature Engine
            |        quote/activity/spread gates
            |        completed/forming bars
            |
            +----> Candidate/Rejection Engine
            |        deterministic reason codes
            |
            +----> Existing RHEN strategy/risk/execution
            |        4.3 semantics until promoted
            |
            +----> RealTimeTelemetryPublisher
                     100-250 ms coalesced deltas
                              |
                              v
                       Command WebSocket

Alpaca Trading WebSocket (trade_updates)
            |
            +----> Order/position reconciler
            +----> canonical ledger
            +----> immediate Command critical event
```

## WebSocket sources

### Market data

Canonical feed endpoints are selected from capability + session:

- Basic stocks: `v2/iex`
- Plus stocks: `v2/sip`
- Basic overnight: `v1beta1/overnight`
- Plus overnight: `v1beta1/boats`

Basic equities currently allow 30 WebSocket symbols. RHEN's 24-symbol extended universe fits under that ceiling. The runtime must still expose subscription capacity and fail clearly if configured symbols exceed entitlement.

### Trading/account updates

Use Alpaca `trade_updates` for order state, fills, partial fills, cancels, rejections, and related broker changes. This stream is safety-critical and separate from market-data quote coalescing.

## Core event contract

Every normalized market event carries:

```text
schema_version
stream_generation
sequence
feed
session
symbol
event_type
source_timestamp
received_timestamp
source_age_ms
transport_latency_ms
payload
```

`stream_generation` changes after a reconnect/re-auth cycle. `sequence` is RHEN-local and monotonic within a generation.

## Per-symbol live state

Maintain one atomic state object per symbol containing at least:

- bid / ask / bid_size / ask_size when present;
- midpoint and spread in dollars, percent, and bps;
- latest quote source/receive timestamps and age;
- latest trade and age when that feed supplies a meaningful real-time trade;
- forming and completed bar state;
- quote rate and trade rate over bounded windows;
- rolling return/activity statistics required by strategy features;
- current session/feed;
- asset eligibility / overnight eligibility / halt state;
- evaluable flag;
- candidate state;
- most recent rejection reason and details;
- position/order linkage if present.

Out-of-order events may be recorded for diagnostics but must not overwrite a newer authoritative symbol state.

## Incremental processing

RHEN should not recalculate the entire strategy graph on every tick. Use three levels:

1. **Per-event cheap updates** — bid/ask, midpoint, spread, age, activity counters, micro returns, feed health.
2. **Triggered candidate evaluation** — only when a state change can alter qualification: price move, spread threshold crossing, new completed bar, session transition, relevant regime/policy change, risk/account change.
3. **Slow features** — NOSTRA regime, expensive research features, and persistence summaries on their own bounded cadence.

This provides true event responsiveness without turning a small-capital system into an unnecessary HFT design.

## Backpressure and overload policy

Use bounded queues and explicit priority classes.

### Never-drop class

- fills;
- partial fills;
- cancellations;
- broker rejections;
- order lifecycle events;
- session/authority changes;
- risk breaker changes;
- feed disconnect/reconnect events.

If this path cannot keep up, RHEN disables new entries and raises an incident.

### Coalescible class

Quotes may be reduced to the newest not-yet-processed quote per symbol when backlog exceeds threshold. Count every coalesced event in telemetry. Bar-close events should not be coalesced across distinct bar timestamps.

## Connection state machine

```text
DISCONNECTED
  -> CONNECTING
  -> AUTHENTICATING
  -> SUBSCRIBING
  -> WARMING
  -> HEALTHY

HEALTHY
  -> DEGRADED     (lag/silence/partial symbol failure)
  -> RECONNECTING (socket loss)
  -> CLOSED       (operator shutdown)

DEGRADED / RECONNECTING
  -> WARMING
  -> HEALTHY
```

While not `HEALTHY`, new live entries are disabled unless the active session/authority contract explicitly proves an alternate healthy source. Exits/risk-reduction remain managed under existing rules.

Reconnect behavior:

- exponential backoff with bounded jitter;
- re-authenticate and re-subscribe the exact intended universe;
- increment `stream_generation`;
- REST bootstrap/reconcile current snapshot;
- reconstruct required rolling state;
- remain `WARMING` until required freshness/coverage gates pass;
- never assume continuity across a socket gap.

## Freshness and health

Freshness is computed from source timestamps. Required health fields:

- `last_event_received_at`;
- `last_quote_source_at` per symbol;
- `last_bar_source_at` per symbol;
- event throughput;
- source-age p50/p95/max;
- transport-latency p50/p95/max;
- active subscriptions / intended subscriptions;
- reconnect count;
- coalesced quote count;
- out-of-order count;
- feed mismatch count;
- silence duration;
- evaluable-symbol fraction.

The current ~45-second quote-age and ~120-second bar-age limits remain initial fail-closed defaults until session-specific evidence supports a deliberate change. They must not be loosened merely to mask an unavailable feed.

## REST responsibilities

REST is retained for:

- startup snapshots;
- historical warm-start bars;
- post-reconnect reconciliation;
- asset eligibility refresh;
- broker/account reconciliation;
- periodic low-frequency audit against stream state;
- research/history retrieval.

REST must not be scheduled at 5-second cadence merely to imitate streaming.

## Definition of success

The fabric is successful when:

- all 24 configured symbols can remain subscribed within Basic entitlement;
- event-to-engine processing is continuous without periodic polling dependency;
- broker updates reach the ledger immediately;
- quote freshness and feed health are explicit;
- reconnects recover deterministically;
- rolling state survives/reconstructs across deploys;
- Command visibly updates sub-second without fabricating events;
- shadow equivalence shows no unintended 4.3 broker-intent change before promotion.
