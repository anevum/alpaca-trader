# Alpaca Trading Bot

Private ANEVUM service for Alpaca account monitoring and deterministic automated execution.

## Current build

The service runs guarded long-only intraday scanners for a configured universe of US equities or ETFs. It supports both the original opening-range/VWAP mode and a rolling momentum/VWAP mode intended for shorter, repeatable intraday trades. It can:

- read live or paper account cash, buying power, equity, positions, market clock and order state;
- scan up to 30 configured symbols using batched Alpaca one-minute bars;
- build a separate five-minute opening range and session VWAP for every candidate;
- require a fresh completed-bar breakout plus configured market confirmations;
- reject excessively wide opening ranges and overextended breakouts;
- submit at most one position at a time as a fractional bracket order;
- verify the selected asset is active, tradable and fractionable before submission;
- enforce allowlisting, maximum entry/position size, an account-wide daily entry limit and daily-loss circuit breaker;
- avoid averaging down and shorting;
- preserve protective bracket exits, optionally time-stop positions after `MAX_HOLD_MINUTES`, and force bot-managed positions flat near the end of the regular session;
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
8. Uses the normal protective bracket and, when configured, a `MAX_HOLD_MINUTES` time stop.

The aggressive small-account profile in `.env.example` uses a 3/8-minute fast/slow structure, 0.35% stop, 0.50% target, 15-minute maximum hold, 2-minute same-symbol re-entry cooldown, entries through 3:30 PM ET, and up to 12 entry orders while retaining the $1 daily-loss circuit breaker. These are implementation choices, not a claim of profitability.

## Risk controls

New entries are blocked when:

- live/paper execution is not fully authorized;
- a candidate is outside `SCAN_SYMBOLS` or `ALLOWED_SYMBOLS`;
- the account reports trading/account blocked;
- any position is already open;
- any account order is already open;
- the order exceeds `MAX_ORDER_NOTIONAL` or `MAX_POSITION_NOTIONAL`;
- the account-wide daily ANEVUM entry limit has been reached;
- cash is insufficient;
- equity decline versus Alpaca `last_equity` reaches `MAX_DAILY_LOSS`;
- the asset is not active, tradable and fractionable;
- the market is closed.

A risk-reducing end-of-day exit may close a bot-managed position even if that symbol has since been removed from `SCAN_SYMBOLS`, as long as it remains allowlisted.

Market orders and stops can fill away from their reference prices because of spread, gaps and slippage.

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

## Deployment

The repository includes `Dockerfile` and `railway.toml`. Railway service: `alpaca-trader`.
