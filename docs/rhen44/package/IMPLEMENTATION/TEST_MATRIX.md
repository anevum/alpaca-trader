# Test matrix

## Market stream lifecycle

- authenticates correct market feed;
- subscribes exactly intended universe;
- Basic entitlement with 24 symbols is accepted under configured 30-symbol cap;
- over-capacity configuration fails closed before live use;
- reconnect reauthenticates/resubscribes;
- stream generation increments after reconnect;
- stale/disconnected stream disables new entries for affected scope;
- feed mismatch fails closed;
- out-of-order events do not overwrite newer state;
- duplicate events are idempotent where applicable.

## Freshness / state

- source timestamp drives quote/bar age;
- UI receipt time cannot make stale data appear fresh;
- quote/bar thresholds remain fail-closed;
- forming/completed bars update correctly;
- no synthetic observations are created during quiet periods;
- per-symbol WARMING -> EVALUABLE transition is deterministic.

## Backpressure

- quote bursts may coalesce to newest state with counter increment;
- distinct completed bars are not lost;
- broker fills/rejections/cancels are never dropped;
- overload of never-drop queue disables new entries and creates incident.

## Recovery

- restart restores open positions/orders and reconciles broker;
- recent bars rebuild required rolling windows;
- entry stays disabled before warmup completion;
- data gaps remain explicit when authoritative backfill unavailable;
- repeated deploys cannot silently reset signal state into false inactivity;
- no duplicate order after reconnect/restart.

## Session/feed routing

- correct PREMARKET/REGULAR/AFTER_HOURS/OVERNIGHT boundaries;
- Basic 04:00-08:00 live entry capability blocked;
- Basic 08:00-17:00 routes IEX;
- Basic 17:00-20:00 live entry capability blocked;
- Basic 20:00-04:00 routes overnight feed but remains shadow by default;
- Plus mode routes SIP/BOATS only when entitlement verified;
- overnight eligibility/halt flags enforced.

## Scanner/rejections

- every evaluation has one terminal classification;
- evaluable rate denominator correct;
- candidate rate segmented by session/feed;
- rejection counts stable and bounded;
- missing reason code fails test;
- zero candidates + low evaluable rate raises pipeline diagnostic;
- candidate -> intent -> fill counters reconcile.

## Command live stream

- initial complete snapshot before deltas;
- ordered sequence handling;
- generation change forces fresh snapshot;
- default publish cadence 100-250 ms while engine remains event-driven;
- critical broker/risk events bypass normal visual coalescing;
- disconnect marks UI stale rather than fabricating continuity;
- browser reconnect does not create duplicate sockets/listeners;
- no 5-second polling fallback masquerades as live.


## Visual intelligence

- every dynamic series includes provenance/source/as-of/quality metadata;
- observed and forecast series are visibly distinct;
- missing intervals remain gaps;
- forming candle only patches the current candle;
- completed candle is appended once;
- no fill marker without canonical broker/ledger evidence;
- scanner patch does not replace the full 24-symbol board;
- forecast expires at contract expiry and is never extended by the client;
- forecast band matches backend lower/upper arrays exactly;
- visual smoothing changes screen position only, not canonical values;
- one socket serves all live panels;
- 24-symbol scanner and selected chart remain responsive during burst load;
- ring buffers remain bounded;
- chart exception cannot affect RHEN trading runtime;
- VELUM replay displays no evidence later than the replay clock;
- replay controls cannot reach broker-write paths;
- reduced-motion mode preserves all information.

## 4.3 semantic equivalence

Before stream promotion:

- identical recorded inputs produce same regular-session candidate/order intent semantics;
- stream ingestion may improve freshness but cannot change strategy formula silently;
- shadow mode creates no broker-intent difference;
- REST/poll path remains usable for rollback.

## Adaptive controller

- disabled mode returns exact baseline snapshot;
- active mode selects only approved profiles;
- invalid library fails closed;
- safety downgrade bypasses dwell;
- upgrade requires dwell/confidence/confirmations;
- UNKNOWN/stale regime cannot upgrade aggressiveness;
- controller state restores deterministically or safely falls back.

## Hard-risk invariants

For randomized profile/session/feed contexts:

- effective order/position/gross/stop-risk caps <= hard caps;
- no-margin invariant enforced;
- adaptive/session code cannot disable daily-loss breaker;
- cannot bypass execution authorization, symbol/session/tradability, or reconciliation gates;
- options writes remain impossible;
- short-equity entries remain impossible;
- leverage expansion remains impossible.

## NOSTRA / capital governor

- point-in-time features only;
- no future observation access;
- stale/missing features produce UNKNOWN/fallback;
- research/runtime classifier parity for identical feature vector;
- throttle stays [0,1];
- worse safety factor cannot increase capital expression.

## Storage/performance

- no per-quote durable storage flood;
- minute/session summaries bounded;
- rolling checkpoints bounded;
- Command history reads bounded;
- stream processing remains within measured latency budget;
- storage growth remains below configured safety thresholds during full session.

## Full regression

All existing strategy, sizing, risk, execution, extended equity, VELUM, NOSTRA, Research Agent, IREN, persistence, reconciliation, protected configuration, and Command suites remain green.
