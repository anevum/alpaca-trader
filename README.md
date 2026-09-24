# Alpaca Trading Bot

Private ANEVUM service for Alpaca account monitoring and, later, deterministic automated execution.

## Stage 1: funding monitor

The current code is deliberately **read-only**. It can connect to either the Alpaca paper account or the live brokerage account, read account cash/buying power and positions, and report `funding_ready=true` once cash reaches `MIN_READY_CASH`.

There is **no order-placement method or endpoint in this stage**. This lets the live account be monitored for the incoming wire without granting the service trading capability.

## Safety model

- `TRADING_MODE=paper` by default.
- `TRADING_MODE=live` may be used for read-only monitoring of the funded live account.
- No order-placement code exists in Stage 1.
- `BOT_ARMED=false` by default.
- Empty `ALLOWED_SYMBOLS` means nothing is approved for a future strategy.
- Hard risk limits are already modeled for the future execution layer.
- Alpaca credentials live only in the deployment host's secret manager.
- Protected endpoints require `ADMIN_TOKEN`.
- Never commit credentials, tokens, screenshots containing secrets, or `.env` files.

## Environment

Copy `.env.example` locally or configure the same values in the deployment host:

```text
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
ADMIN_TOKEN=...
TRADING_MODE=paper
LIVE_TRADING=false
I_ACKNOWLEDGE_LIVE_TRADING=NO
BOT_ARMED=false
MIN_READY_CASH=10.00
ALLOWED_SYMBOLS=
```

When monitoring the actual funded brokerage account, set `TRADING_MODE=live` while leaving `LIVE_TRADING=false` and `BOT_ARMED=false`.

## API

- `GET /health` — public health/funding state; no secrets.
- `GET /v1/status` — protected bot/funding/risk state.
- `GET /v1/account` — protected Alpaca account response.
- `GET /v1/positions` — protected Alpaca positions response.

Protected requests use:

```text
Authorization: Bearer <ADMIN_TOKEN>
```

## Deployment

`Dockerfile` and `railway.toml` are included for Railway deployment. The service listens on port `8080` and exposes `/health` for health checks.

## Stage 2: paper strategy

After account connectivity is verified, implement exactly one deterministic strategy and a single execution gateway. The execution gateway must run the risk checks in `app/risk.py` before any order is sent. Paper-test before adding live order capability.

## Stage 3: live execution

Live execution is a separate change. It should require all of the following at runtime: an explicitly armed bot, an allowlisted symbol, order/position/daily-loss limits, and an explicit live-trading acknowledgement. Do not add live credentials until the paper strategy has been reviewed.
