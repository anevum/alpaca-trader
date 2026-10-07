# Live Scanner, Candidate Frequency, and Rejection Engine

## Goal

Make every scan outcome explainable in real time. "0 candidates" is not sufficient telemetry.

## Evaluation model

The scanner is event-triggered, not timer-polled. Each material event can update one or more symbol states. A candidate evaluation occurs only when prerequisites are satisfied and a change could alter qualification.

Each evaluation ends with exactly one terminal classification:

- `NOT_EVALUABLE` with a rejection reason;
- `EVALUABLE_REJECTED` with a strategy/liquidity/risk reason;
- `CANDIDATE`;
- `ORDER_INTENT_BLOCKED` with an authority/risk reason;
- `ORDER_INTENT_CREATED`.

## Core rates

### Evaluable rate

```text
evaluable_rate = fresh_valid_evaluations / eligible_evaluations
```

This distinguishes a quiet market from a broken data path.

### Candidate rate

```text
candidate_rate = qualified_candidates / evaluable_symbol_hours
```

Also retain candidates per 1,000 evaluations for short-window monitoring.

### Execution conversion

```text
intent_rate = order_intents / candidates
fill_rate   = filled_orders / order_intents
```

All rates are segmented by session, feed, symbol, strategy version, policy profile, and regime where sample size supports it.

## Canonical rejection taxonomy

### Feed / data

- `FEED_UNAVAILABLE`
- `FEED_MISMATCH`
- `STREAM_DISCONNECTED`
- `STREAM_WARMING`
- `SUBSCRIPTION_MISSING`
- `STALE_QUOTE`
- `STALE_BAR`
- `STALE_TRADE`
- `NO_BID`
- `NO_ASK`
- `LOCKED_OR_CROSSED`
- `OUT_OF_ORDER_STATE`
- `DATA_GAP`

### Asset / session

- `ASSET_INELIGIBLE`
- `OVERNIGHT_NOT_TRADABLE`
- `OVERNIGHT_HALTED`
- `SESSION_NOT_AUTHORIZED`
- `DATA_CAPABILITY_BLOCKED`

### Warmup / activity / liquidity

- `INSUFFICIENT_OBSERVATIONS`
- `INSUFFICIENT_ACTIVITY`
- `SPREAD_TOO_WIDE`
- `LIQUIDITY_TOO_LOW`
- `VOLATILITY_TOO_LOW`
- `VOLATILITY_TOO_HIGH`

### Strategy

- `SIGNAL_BELOW_THRESHOLD`
- `MOMENTUM_FAIL`
- `RELATIVE_STRENGTH_FAIL`
- `REGIME_FAIL`
- `CONFIRMATION_FAIL`
- `VWAP_EXTENSION_FAIL`
- `NET_EDGE_FAIL`

### Portfolio / risk

- `ALREADY_POSITIONED`
- `DUPLICATE_ORDER`
- `CORRELATION_CAP`
- `SYMBOL_EXPOSURE_CAP`
- `GROSS_EXPOSURE_CAP`
- `PORTFOLIO_RISK_CAP`
- `BUYING_POWER`
- `DAILY_LOSS_BREAKER`
- `COOLDOWN`

### Authority / execution

- `EXECUTION_NOT_AUTHORIZED`
- `POLICY_NOT_PROMOTED`
- `BROKER_WRITE_DISABLED`
- `ORDER_CONSTRAINT`
- `LIMIT_PRICE_INVALID`
- `BROKER_REJECTED`
- `RECONCILIATION_UNSAFE`

Every reason may include structured details, but UI should display stable codes plus a short operator-readable explanation.

## Real-time Command scanner board

Command should expose:

```text
24 STREAMING
18 EVALUABLE
 3 WARMING
 2 SPREAD BLOCKED
 1 STALE

AAPL   WATCH      spread 1.8 bp  quote age 73 ms
NVDA   NEAR       momentum 0.19%  confirmation pending
SPY    HOLD       signal below threshold
AMD    BLOCKED    spread 47 bp
...
```

This is a state board, not an animation. Rows change when real state changes.

## Rejection aggregation

Maintain bounded counters by:

- current 1-minute window;
- current session;
- current trading day;
- symbol;
- strategy version;
- feed;
- policy profile.

Persist minute/session summaries rather than every UI refresh. Candidate/order/fill evidence remains durable at event granularity.

## Broken-pipeline diagnostics

Raise explicit diagnostics when:

- candidate count is zero **and** evaluable rate is low;
- candidate count suddenly drops relative to recent comparable sessions while data health degrades;
- >X% of symbols share one feed rejection;
- feed activity is present but feature updates stop;
- feature updates occur but evaluation counters stop;
- evaluations occur but reason codes become missing/unknown;
- order intents stop despite qualified candidates and live authority.

Do not infer a strategy failure until data/evaluation health is confirmed.
