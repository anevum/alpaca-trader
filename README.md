# Alpaca Trading Bot

Private ANEVUM service for Alpaca account monitoring and deterministic automated execution.

## Current build

The service now contains the full paper-trading execution path and the guarded live execution path.

It can:

- read live or paper account cash, buying power, positions, market clock and order state;
- detect when the configured funding threshold has been reached;
- read Alpaca stock bars;
- evaluate one deterministic long-only SMA crossover strategy;
- submit fractional market buys by notional;
- sell only an existing long position to flat;
- prevent duplicate orders when an order for the symbol is already open;
- enforce symbol allowlisting, maximum entry size, maximum position size, a daily entry-order limit and a daily-loss circuit breaker;
- expose protected status/run/pause controls for a mobile control surface.

Alpaca documents order submission through `POST /v2/orders`; the bot uses that endpoint only after every execution gate passes.

## Execution gates

Nothing trades merely because credentials are present.

### Paper execution requires all of:

```text
TRADING_MODE=paper
EXECUTION_ENABLED=true
BOT_ARMED=true
STRATEGY_SYMBOL=<symbol>
ALLOWED_SYMBOLS=<same symbol>
```

### Live execution additionally requires:

```text
TRADING_MODE=live
LIVE_TRADING=true
I_ACKNOWLEDGE_LIVE_TRADING=YES
```

If any gate is absent, the strategy can be inspected but an order is rejected by the execution layer.

The deployed service should remain live-read-only or paper-only until paper behavior has been observed and reviewed.

## Strategy

The initial strategy is intentionally simple and auditable:

**long-only SMA crossover**

- fresh fast-SMA crossing above slow-SMA + no position → buy;
- fresh fast-SMA crossing below slow-SMA + existing long position → sell the position to flat;
- otherwise → hold.

The bot does not short. It also does not enter just because it starts up while the fast SMA is already above the slow SMA; a fresh crossover is required.

Defaults:

```text
FAST_WINDOW=20
SLOW_WINDOW=50
BAR_TIMEFRAME=5Min
DATA_FEED=iex
ORDER_NOTIONAL=5.00
```

These are implementation defaults, not a claim that the strategy will be profitable.

## Risk controls

New entries are blocked when:

- execution is not fully authorized;
- the symbol is not allowlisted;
- the account reports trading/account blocked;
- the order exceeds `MAX_ORDER_NOTIONAL`;
- the position would exceed `MAX_POSITION_NOTIONAL`;
- the configured daily entry limit has been reached;
- cash is insufficient;
- the account's equity decline versus `last_equity` reaches `MAX_DAILY_LOSS`;
- another order for the symbol is already open;
- the market is closed.

A sell-to-flat remains permitted by the daily-entry and daily-loss limits because it reduces exposure. It still requires the normal execution authorization and an existing long position.

## Secrets

Never commit Alpaca keys or the administrator token to GitHub.

Configure these only in Railway:

```text
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ADMIN_TOKEN=...
```

Use separate paper credentials while testing paper execution.

## API

Public:

- `GET /health`

Administrator bearer token required:

- `GET /v1/status`
- `GET /v1/account`
- `GET /v1/positions`
- `POST /v1/run-once`
- `POST /v1/pause`
- `POST /v1/resume-paper`

`/v1/resume-paper` deliberately refuses to resume a live bot. Live arming must be done through deployment configuration rather than a browser button.

## Deployment

The repository includes `Dockerfile` and `railway.toml`.

Railway service: `alpaca-trader`

The Railway GitHub App must have access to this private repository before the first deployment can pull the source.

## Rollout

1. Deploy with execution disabled and verify `/health`.
2. Add **paper** Alpaca credentials.
3. Select exactly one symbol and add it to both `STRATEGY_SYMBOL` and `ALLOWED_SYMBOLS`.
4. Enable paper execution and observe fills, logs, risk behavior and restart behavior.
5. Keep the real account read-only until the paper behavior is acceptable.
6. Live execution is a separate deliberate configuration change using all live gates above.

Funding availability never automatically authorizes a trade.
