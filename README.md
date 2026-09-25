# Alpaca Trading Bot

Private ANEVUM service for Alpaca account monitoring and deterministic automated execution.

## Current build

The service runs guarded long-only intraday scanners for a configured universe of US equities or ETFs. It supports both the original opening-range/VWAP mode and a rolling momentum/VWAP mode intended for shorter, repeatable intraday trades. It can:

- read live or paper account cash, buying power, equity, positions, market clock and order state;
- scan up to 30 configured symbols using batched Alpaca one-minute bars;
- build a separate five-minute opening range and session VWAP for every candidate;
- require a fresh completed-bar breakout plus configured market confirmations;
- reject excessively wide opening ranges and overextended breakouts;
- hold and manage multiple bounded fractional long positions concurrently;
- verify the selected asset is active, tradable and fractionable before submission;
- enforce allowlisting, maximum entry/position size, an account-wide daily entry limit and daily-loss circuit breaker;
- size qualified entries from prior-close equity, stop distance, remaining cash, remaining position slots, and an account-level gross-exposure ceiling;
- score qualified opportunities from momentum, VWAP edge, confirmations, spread, freshness, relative volume, and trend persistence, then rank stronger setups first;
- reject a lower-ranked candidate when its recent aligned one-minute returns are too positively correlated with an already-open or already-planned position;
- avoid averaging down and shorting;
- manage per-position stop, target, time, and end-of-day exits independently; current fractional exits are bot-managed rather than broker-resident bracket legs;
- expose safe public health state plus protected account, position, order and scanner telemetry.

## Execution gates

Nothing trades merely because credentials are present.

Paper execution requires:

```text
TRADING_MODE=paper
EXECUTION_ENABLED=true
BOT_ARMED=true
```

Live execution additionally requires:

```text
TRADING_MODE=live
LIVE_TRADING=true
I_ACKNOWLEDGE_LIVE_TRADING=YES
```

Every candidate in `SCAN_SYMBOLS` must also be in `ALLOWED_SYMBOLS`.

## Strategy

Two deterministic modes are supported through `STRATEGY_NAME`.

### `opening_range_vwap`

For each candidate:

1. Use completed one-minute regular-session bars only.
2. Build the first `OPENING_RANGE_MINUTES` minutes as the opening range.
3. Reject the candidate when opening-range width exceeds `MAX_OPENING_RANGE_PCT`.
4. Require a fresh close from at/below the opening high to above the opening high.
5. Reject a breakout extended more than `MAX_BREAKOUT_EXTENSION_PCT` above the opening high.
6. Require the candidate to close above its session VWAP.
7. Require each independent confirmation symbol to be above its own session VWAP, not below its prior close, and not making a fresh one-bar low.
8. When multiple candidates qualify on the same cycle, `SCAN_SYMBOLS` order is the deterministic priority.
9. Submit one bracket entry only after all account-wide risk checks pass.

### `rolling_momentum_vwap`

For each candidate, the rolling mode:

1. Uses completed one-minute regular-session bars.
2. Requires the `FAST_WINDOW` close average to exceed the `SLOW_WINDOW` average.
3. Requires the latest completed close to be rising versus the prior close.
4. Requires short-term momentum of at least `MIN_MOMENTUM_PCT`.
5. Requires price above session VWAP by at least `MIN_VWAP_EDGE_PCT`.
6. Requires at least `MIN_CONFIRMATIONS` configured market confirmations to pass.
7. Can re-enter after a prior position has fully exited, subject to account-wide order/loss limits and `REENTRY_COOLDOWN_MINUTES`.
8. Applies market-quality gates for fresh bars, fresh confirmations, and maximum spread before execution.
9. Can hold multiple different symbols concurrently up to `MAX_CONCURRENT_POSITIONS` and `MAX_TOTAL_POSITION_NOTIONAL`.
10. Re-entry cooldown is measured from the prior completed exit rather than from the prior entry.
11. Uses bot-managed stop/target exits and, when configured, a `MAX_HOLD_MINUTES` time stop.

The small-account profile in `.env.example` uses a 3/8-minute fast/slow structure, 0.35% stop, 0.50% target, 15-minute maximum hold, 2-minute post-exit same-symbol cooldown, entries through 3:30 PM ET, up to 3 concurrent positions, up to 2 new entries in one scan cycle, and up to 12 entry orders while retaining the $1 daily-loss circuit breaker. Entry notional is now equity-aware: the allocator budgets 0.10% of prior-close equity per trade, caps total long exposure at 80% of prior-close equity, divides remaining exposure capacity across the remaining position slots, and still obeys hard per-order, per-position, cash, and $250 gross-notional ceilings. These are implementation choices, not a claim of profitability.

## Risk controls

New entries are blocked when:

- live/paper execution is not fully authorized;
- a candidate is outside `SCAN_SYMBOLS` or `ALLOWED_SYMBOLS`;
- the account reports trading/account blocked;
- the same symbol already has a long position or open order;
- the configured concurrent-position limit has been reached;
- the order exceeds `MAX_ORDER_NOTIONAL`, `MAX_POSITION_NOTIONAL`, or `MAX_TOTAL_POSITION_NOTIONAL`;
- equity-risk sizing would take gross exposure above `MAX_GROSS_EXPOSURE_PCT` of prior-close equity;
- enough return observations exist and pairwise correlation with an open/planned position is at or above `MAX_PAIRWISE_CORRELATION`;
- the candidate bar/confirmation data is stale or its quoted spread exceeds `MAX_SPREAD_PCT`;
- the account-wide daily ANEVUM entry limit has been reached;
- cash is insufficient;
- equity decline versus Alpaca `last_equity` reaches `MAX_DAILY_LOSS`;
- the asset is not active, tradable and fractionable;
- the market is closed.

A risk-reducing end-of-day exit may close a bot-managed position even if that symbol has since been removed from `SCAN_SYMBOLS`, as long as it remains allowlisted.

Market orders can fill away from their reference prices because of spread, gaps and slippage. The current fractional stop/target protection is managed by the running service, so process or network failure remains an operational risk until broker-resident protection is added.

## Market data

The service uses the configured Alpaca feed. On the Basic plan, real-time equity data is IEX rather than the consolidated SIP tape. The scanner batches symbols through Alpaca's multi-symbol historical-bars endpoint to stay well below normal request-rate limits.

## API

Public:

- `GET /health`

Administrator bearer token required:

- `GET /v1/status`
- `GET /v1/account`
- `GET /v1/positions`
- `GET /v1/orders`
- `POST /v1/run-once`
- `POST /v1/pause`
- `POST /v1/resume-paper`

`/v1/status` includes cash, buying power, equity, prior-close equity, day P&L, full scanner state, selected signal, last order and error state.

## Secrets

Never commit Alpaca keys or the administrator token to GitHub. Configure them only in Railway.

## Replay laboratory

The replay CLI runs the current production strategy and allocator against historical one-minute Alpaca bars without importing any broker-order submission path. A single run is limited to 21 calendar days.

The simulator reproduces the current strategy gates, quality ranking, correlation filter, concurrent-position cap, daily entry limit, equity-based sizing, daily-loss breaker, re-entry cooldown, stop/target/time exits, and end-of-session flattening. Historical bar data does not contain historical bid/ask quotes, so the caller supplies explicit spread and per-side slippage assumptions. If one historical one-minute bar touches both stop and target, replay resolves the bar stop-first because intrabar ordering is unknown.

Replay output includes trade-level results, an equity curve, win rate, profit factor, expectancy per trade, maximum drawdown, per-symbol results, correlation/risk block counts, and the assumptions used. Replay results are research evidence, not a forecast of future returns.\n\nRun it with:\n\n```bash\npython scripts/replay.py --start 2026-09-01 --end 2026-09-18 --initial-equity 100 --spread-bps 5 --slippage-bps 2 --output replay.json\n```\n\nThe CLI imports market-data and strategy modules only. It does not import the broker client or execution engine.

## Deployment

The repository includes `Dockerfile` and `railway.toml`. Railway service: `alpaca-trader`.


## Durable canonical trading ledger

Production trading telemetry is written through the authenticated `trading-ingest` backend into ANEVUM's private Supabase schema.

The execution path records:

`signal -> order intent -> broker order -> fill -> position -> exit -> account snapshot`

Safety behavior:

- New BUY entries fail closed if the durable order intent cannot be acknowledged.
- Protective SELL exits fail open if persistence is unavailable; storage must never trap an open position.
- Broker reconciliation uses Alpaca orders, positions, account state, and `FILL` account activities.
- Reconciliation is idempotent and replays bot orders chronologically.
- `TRADING_RUN_STARTED_AT` is a fixed run boundary and must not change across Railway restarts. Reconciliation ignores orders/fills older than that timestamp.
- `TRADING_INGEST_TOKEN` is backend-only and must never be exposed to browser code.
- Ledger reconciliation does not modify strategy signals, sizing, or risk rules.

Required persistence variables are documented in `.env.example`.
