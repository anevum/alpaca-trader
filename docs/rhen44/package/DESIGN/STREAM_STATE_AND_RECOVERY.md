# Stream State, Persistence, and Recovery

## Problem

RHEN 4.3 extended logic can lose rolling signal history when the process restarts. Repeated deploys can therefore repeatedly force warmup and make a healthy strategy appear inactive.

RHEN 4.4 fixes this at the data-state layer.

## Persisted state

Do not persist every quote. Persist enough deterministic state to recover safely:

- stream generation and last clean disconnect/reconnect metadata;
- latest authoritative source timestamps per symbol/event class;
- completed bars required for current strategy windows;
- current session identity;
- rolling feature checkpoints where reconstruction is expensive;
- asset eligibility/halt snapshot with fetched-at timestamp;
- candidate/open-order/position provenance already required by canonical ledger;
- adaptive policy/regime snapshot lineage.

## Startup recovery

1. Load canonical open positions/orders and reconcile with broker.
2. Resolve current market session and intended feed.
3. Fetch recent authoritative bars needed to reconstruct rolling windows.
4. Establish WebSocket connection and subscriptions.
5. Merge bootstrap data and incoming events using source timestamps.
6. Verify freshness, symbol coverage, and feature readiness.
7. Transition each symbol from `WARMING` to `EVALUABLE` independently.
8. New entries remain disabled until the session-level minimum evaluable threshold is satisfied and all safety gates are healthy.

## Deploy/restart behavior

A deploy during an active session creates a visible recovery epoch. Command must show:

- restart timestamp;
- current stream generation;
- symbols ready / warming / blocked;
- reconstruction source;
- data gap duration;
- whether new entries are currently allowed.

## Idempotence

Reconnect and recovery paths must tolerate duplicate broker and market events. Order/fill persistence uses broker IDs plus event identity for idempotence. Bar state keys use symbol + timeframe + source timestamp.

## Gap policy

If a stream gap is too large for safe reconstruction:

- mark affected symbols `DATA_GAP`;
- backfill only from an authoritative feed available under current entitlement;
- do not synthesize missing observations;
- if unavailable, remain observation-blocked until enough fresh state accumulates naturally.

## Recovery test

A release cannot promote extended live authority until an automated test and one production/shadow exercise prove:

- restart during active stream;
- state reconstruction;
- no duplicate orders;
- no lost open-position management;
- no premature entry before warmup;
- Command accurately reflects the recovery state.
