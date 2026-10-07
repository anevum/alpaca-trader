# Command Real-Time Stream Contract

## Goal

Command must display RHEN state in near-real time without coupling browser rendering frequency to trading-engine event frequency.

## Transport

Add one protected backend WebSocket using the existing Command authentication/authorization model. Suggested route name:

`/api/command/stream`

Do not create a new Railway service.

## Server behavior

On connect:

1. authenticate using the existing protected Command mechanism;
2. send `snapshot` containing current canonical state;
3. send ordered `delta_batch` messages when state becomes dirty;
4. send heartbeat at low cadence even when no market event occurs;
5. send critical broker/risk events immediately;
6. on reconnect, send a new complete snapshot before deltas;
7. publish typed visual series deltas without retransmitting full chart history.

Default visual delta flush: 125 ms (8 Hz), configurable within 100-250 ms. This is a UI publishing cadence only. Trading logic remains event-driven.

## Message envelope

```json
{
  "schema_version": "command-live.v1",
  "message_type": "snapshot|delta_batch|series_append|series_patch_last|series_reset|scanner_patch|execution_event|forecast_replace|system_patch|critical_event|heartbeat",
  "server_time": "...",
  "stream_generation": "...",
  "sequence": 12345,
  "payload": {}
}
```

Clients reject older sequence numbers within a generation. A generation change forces snapshot replacement.

## Required live panels

### Market fabric

- connection state;
- feed/session;
- symbols subscribed / intended;
- events/sec;
- quote age p50/p95;
- transport latency p50/p95;
- reconnect count;
- coalesced quote count;
- evaluable percentage;
- current capability block if any.

### Scanner

For 24 symbols:

- bid/ask or midpoint;
- spread bps;
- quote age;
- activity state;
- evaluable/warming/blocked;
- candidate state;
- rejection reason;
- current position/order marker.

### Candidate/rejection pulse

- evaluations/min;
- evaluable rate;
- candidates/min;
- top rejection reasons;
- candidate -> intent -> fill conversion.

### Account/execution

Broker `trade_updates` must update open orders/fills/positions promptly. Account graphs must append actual points and avoid manufacturing flatline space during inactive windows.

## Browser behavior

- maintain one socket, not one socket per panel;
- coalesce rendering to animation frame / received batch;
- compute quote age locally from authoritative timestamp between messages if desired;
- never interpolate price/volume;
- on connection loss, freeze last values and visibly mark them stale;
- reconnect with bounded backoff;
- request/accept fresh snapshot after reconnect;
- do not silently fall back to 5-second polling as if equivalent. A low-frequency REST fallback may show degraded read-only status only.

## Storage

UI refreshes are ephemeral. Persist only meaningful market/session summaries, candidate/rejection summaries, adaptive transitions, and order/position evidence.


## Visual series transport

Use `IMPLEMENTATION/VISUAL_EVENT_SCHEMA.json`. Normal chart/scanner changes may be coalesced into the 100-250 ms publisher cadence. Critical broker/risk events bypass that cadence.

Do not send complete historical arrays on each tick. Use append/patch/reset semantics with bounded buffers. A forecast is replaced atomically as a versioned object rather than incrementally guessed by the client.

The frontend may use requestAnimationFrame or short transitions for smooth rendering, but exact canonical values/timestamps remain authoritative.
