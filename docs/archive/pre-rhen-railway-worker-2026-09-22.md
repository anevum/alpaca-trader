# Pre-RHEN Railway Worker Archive

Status: historical predecessor only. Not production.

This note preserves the known behavior of the Alpaca-era worker that existed inside the old Railway service formerly named `ibkr-runner` before the canonical `anevum/alpaca-trader` repository was initialized.

## Provenance

- Observed active before the current repository bootstrap.
- The worker was attempting Alpaca paper-trading orders on 2026-09-22.
- The current `anevum/alpaca-trader` repository was initialized on 2026-09-24.
- The old Railway service therefore contains pre-repository RHEN ancestry.

## Embedded modules

The old Railway service reconstructs these modules from protected environment variables:

- `APP_ENTRY_B64` -> `app_entry.py`
- `FINANCE_API_B64` -> `finance_api.py`
- `ALPACA_WORKER_B64` -> `alpaca_worker.py`

The encoded values remain protected in Railway and are not reproduced here.

## Finance API behavior

The finance API was a FastAPI service that exposed a public health endpoint and an authenticated finance snapshot endpoint, queried IBKR account/portfolio telemetry in read-only mode, used Supabase only for authentication/authorization checks, kept snapshot caching in memory, and did not write trading data to Supabase or another persistent database.

This IBKR telemetry portion is obsolete.

## Alpaca worker behavior

The embedded Alpaca worker was an active trading implementation built around an opening-range-breakout strategy.

Known behavior included polling the Alpaca market clock; fetching 1-minute bars; monitoring account state, positions, fills, and open orders; selecting entries using opening-range breakout and volume conditions; calculating position quantity from configured capital and risk limits; submitting market entries with bracket stop-loss and take-profit exits; canceling orders and flattening positions when required; and enforcing market-hours, entry-window, daily-loss, trade-count, capital, position, and arming gates.

Known configuration families included paper/live mode, armed/disarmed state, strategy universe, maximum position sizing, risk per trade, daily loss limit, maximum trades per day, stop percentage, target R multiple, breakout buffer, maximum chase distance, volume multiplier, entry window, forced-flat time, capital cap, cash reserve, commission buffer, position budget, and planned-loss limits.

State was maintained in memory. No database persistence by the Alpaca worker was identified.

## Relationship to modern RHEN

This worker predates the canonical RHEN repository and is historical lineage, not current production architecture.

Current production authority remains the canonical `anevum/alpaca-trader` repository and its separately tracked Railway production service.

The obsolete IBKR gateway and noVNC services are not part of RHEN and may be removed independently.

## Preservation rule

Do not revive this historical worker as a live strategy. Any useful ideas must pass current RHEN research, validation, risk, and provenance standards before being reintroduced.
