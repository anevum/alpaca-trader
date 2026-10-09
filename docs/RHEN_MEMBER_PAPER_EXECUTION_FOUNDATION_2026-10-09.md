# ANEVUM.RHEN.BUILD.2026-10-09.001.PAPER-MEMBER-ISOLATION-FOUNDATION

**Status:** Draft, isolated paper-only simulation kernel. No production RHEN behavior change. This is a bounded implementation slice of https://github.com/anevum/rhen/issues/462, not a release-ready brokerage integration.

## Purpose and boundaries

RHEN's existing production supervisor stays attached exclusively to the company owner's live Alpaca account. No member identity, balance, policy, cash or position from the new kernel is connected to the production supervisor, broker, shared protected runtime database, or web operator APIs.

The new `app/member_paper` module uses only Python standard library and an independent SQLite database. It contains **no network call, Alpaca/IBKR SDK, credential storage, secret, broker order method, or live trading gate**. It is a paper *accounting simulation* for RHEN Cloud architecture tests, not a functioning Alpaca paper broker, market execution model or tradeable RHEN Cloud service.

## Implementation contract

- An application gateway must authenticate a member via verified ANEVUM identity, then resolve its own internal member ID. The `verified_scope` method **does not authenticate** that value and must never be called with a browser-supplied ID. Only authenticated server code may use it.
- `PaperExecutionKernel(db_path)` requires an isolated database path; never point it at the owner's `/data/rhen-core.db`.
- `create_paper_account(verified_member_id,initial_cash,policy)` is atomic and rejects any duplicate member instead of resetting a balance.
- `PaperPolicy` pins one validated strategy version, U.S. equity/ETF symbol allowlist, max position count, gross cost-basis exposure, and maximum order/position notional. Long-only, unlevered, paper-only; no live authorization.
- `PaperSignal` uses an immutable ID, signed/approved-version contract to be enforced by an upstream service, completed-bar timestamp and input price; invalid or stale signals are rejected.
- `PaperAccountScope.submit_signal` simulates instantaneous fractional buy and full-position sell at the supplied signal price; no bid/ask, slippage, broker queue, actual order placement, distributed trade confirmation, broker stop order, mark-to-market, cash transfer, fees, or fill promise.
- Each receipt is unique by **(member ID, signal ID)** and stores a SHA-256 digest of the full immutable signal payload. Accepted and rejected decisions are persisted atomically with resulting account/cash/position state. `BEGIN IMMEDIATE` serializes concurrent SQLite writers; identical replays return the original receipt while conflicting payloads using an existing signal ID are rejected without changing account state, including after restart. This is a new draft-only schema, not a production migration.
- Pause prevents new simulated buys. Explicit long exits remain possible while paused. Balances/positions/receipts are query-scoped by member ID; no global all-accounts API.
- Flat sell only, no short/negative quantity. The engine rejects unknown symbols, stale and future data, unapproved strategy versions, excessive notional, missing policy and missing accounts.

## Tests

`pytest -q tests/test_member_paper_execution.py` verifies:
1. Independent accounts, identical signal IDs per tenant, full-payload replay conflict rejection and persistence across restarts.
2. Duplicate account creation refusal.
3. Signal freshness, version, allowlist and paused-account denials.
4. Fractional buying and notional/gross exposure limits, cash nonnegativity.
5. Risk-reducing exits while paused, no shorting, and realized simulated P/L.
6. Invalid inputs, no member setup overwrites, and no brokerage-write authority.

## Before connecting a paper broker

1. Integrate a distinct ANEVUM member auth assertion and signed, time-limited backend-to-backend token; never use email or client-picked `member_id` as authorization.
2. Put member data and broker OAuth secrets into appropriately isolated production services, ideally a durable central database and token vault; do not share the owner's RHEN volume.
3. Add an Alpaca **paper-account-only** adapter with brokerage account identity verification, stream/REST reconciliation and idempotent order state; never turn a simulator receipt into an actual order just because it exists.
4. Add market-data licensing, decimal tick/lot constraints, accurate commissions/fees/spread/slippage, quotes for mark-to-market risk, daily loss breakers, position recovery, exit/cancel semantics, broker rate limiting and canonical position reconciliation.
5. Test two distinct paper broker accounts end-to-end, cross-account authorization attacks, crash/recovery, high concurrency, rate limits, missing feeds, stale auth, and observability. Prove the owner production live broker path is unchanged.
6. Only after explicit operational and external broker/regulatory approvals consider limited live-member trading. Subscriber payment does not alter any RHEN execution authorization.

**No deployment/promotion authorized.** Changes should remain in draft PR until isolated paper contract tests and independent security review pass.
