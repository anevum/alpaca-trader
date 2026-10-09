# ANEVUM.RHEN.BUILD.2026-10-09.002.LIVE-MEMBER-EXECUTION-FOUNDATION

**State:** Draft, no production deployment, no member broker-write authority, no changes to personal RHEN champion.  
**Objective:** Build the separate RHEN Cloud engine for authenticated, member-owned **live Alpaca Connect trading** directly, without requiring a paper-trading product rollout first.

## Locked architecture

- Live trading belongs to an independently authenticated ANEVUM member account, not the owner/company RHEN Railway executor.
- The existing owner/ANEVUM RHEN bot, Alpaca account, volume, algorithms, positions and operator endpoints are excluded from member services.
- Alpaca Connect OAuth is the third-party integration. Individual members authorize their own **live** account through Alpaca. Do not solicit personal Alpaca API keys in the website or chat. OAuth client credentials/access tokens live only in audited, encrypted backend storage.
- Alpaca requires a registered and approved Connect app for third-party live order placement, including written commercial-app approval where applicable. The `trading` OAuth scope is separate from read-only access.
- A normal Better Auth session or Stripe payment never grants live broker-write. Provider, regulatory/legal, security, operator release, backend feature flag, verified grant, explicit current member consent and member arm are independent requirements. Users may disable new entries without automatically disabling risk-reducing exits.
- Begin with **whole-share U.S. equity/ETF limit DAY orders during regular session**, long-only and unlevered. No extended hours, market orders, short sales, margin expansion, options or crypto in initial live product. Fractional notional orders require a separate bounded design and validation.
- Strategies use immutable approved/versioned releases; no LLM in order placement, user arbitrary scripts or auto-promotion.
- A shared signal/research layer can serve members, but each member's OAuth, policies, positions, order journal and current risk observations are isolated and checked at execution.

## What this draft implements

`app/member_live/contracts.py`: validated binding, immutable strategy release metadata, policy, limit order intent and account/market observation contracts. `validate_live_intent` fails closed on missing independent permissions, wrong broker account, stale feed/signal, closed market, excessive spread, per-order/buying-power/exposure/position/daily-loss caps or attempted short/unsupported trade. A paused member may exit an existing long position, subject to valid continuing broker grant and market/risk checks.

`app/member_live/alpaca_connect.py`: a LIVE-only Trading API adapter using the fixed `https://api.alpaca.markets` hostname, backend-resolved OAuth bearer token, broker account identity/ACTIVE-status check, restricted whole-share limit/day payloads, and read-by-`client_order_id` reconciliation endpoint. All requests are behind the gateway; responses and exceptions do not reveal credentials.

`app/member_live/gateway.py`: an account- and member-scoped **prototype SQLite journal**, replay checks against immutable payload digest, deterministic `rhcl-` client order IDs, explicit reserved/uncertain/confirmed/blocked states. An outbound call transitions to UNCERTAIN **before** touching the broker, preventing blind retries after a timeout/crash. Reconciliation queries Alpaca by client order ID. Broker/account mismatch prevents order placement.

`tests/test_member_live_gateway.py`: offline negative tests with a fake broker transport. No broker credentials or live requests. The gateway defaults OFF even if the upstream permissions are simulated as complete.

## Production-critical missing work (not optional)

1. **Alpaca authorization:** Register/obtain Alpaca Connect app live approval and commercial use approval. Confirm exact allowed OAuth scopes and data license for the service, callback and revocation semantics.
2. **Member OAuth gateway:** Server-side expiring OAuth state bound to a verified Better Auth session, exact callback allowlist, token exchange, encrypted per-member token vault, revocation, account identity mapping and incident audit. No credential export, plaintext D1 storage, cross-member identity query, browser-selected broker ID, or owner-account association.
3. **Durable infrastructure:** Replace prototype SQLite with separately deployed, tenant-scoped PostgreSQL or equivalent and fenced worker leases. NEVER share owner RHEN DB or use local Railway filesystem as multi-worker durable state. Add reliable queued execution and an outbox with complete broker reconciliation.
4. **Broker-generated risk observations:** Integrate account/positions, open/pending orders, official clock, asset properties, quotes/market data, daily realized loss and buying power from fresh canonical broker data. These values must **never** be supplied by the HTTP client. Verify broker session, equity permissions, streaming constraints and daylight-saving/market holidays.
5. **Risk controls:** Daily loss requires reliable fills, commissions, state transitions and mark-to-market monitoring. Add intraday drawdown, account kill-switch/cancellation, fills, partial fills, stale feed handling, exchange halts, bracket/exit recovery and broker side rejection. A limit sell may fail to fill; exits cannot be represented as guaranteed.
6. **Security:** Independent attestation that member A cannot access member B or company account. Use server-derived binding and verified signed strategy release. SHA-256 content digest by itself is **not** a signature or a complete promotion gate. Separate deployment key/authority from member-controlled settings.
7. **Legal/compliance:** Have qualified U.S. securities counsel assess whether running common company-controlled strategies automatically in client brokerage accounts is investment-adviser activity and what registration/exemption, marketing/performance, recordkeeping and disclosure duties apply. Obtain Alpaca approval before other users can place orders from ANEVUM.
8. **Release process:** Multi-account staging with **fake transport and explicit operator-reviewed test orders**, independent approved live-application integration, two-account boundary pen tests, chaos/idempotency and broker 429/rate limits, recovery drill and cost capacity. A deliberate owner-signed production rollout is required separately. No implicit enablement via checkout or website deploy.

## Development release gates

- Production `member_live` is never imported into existing `app/main.py` or the owner executor.
- `LiveMemberGateway(network_writes_enabled=False)` is the default.
- The only way to attempt broker submission is an injected verified `AlpacaConnectLiveBroker` **plus** an explicitly enabled gateway and every independent live authority check.
- No action is executed in this PR; the live transport is exercised through a fake adapter in CI.
- Paper simulator PR #463 stays an optional internal test artifact, not an end-user onboarding requirement.
- The website’s `/api/member/brokerage` remains disabled until its own secure integration/release acceptance.

### Provider references

- https://docs.alpaca.markets/us/docs/about-connect-api
- https://docs.alpaca.markets/us/docs/using-oauth2-and-trading-api
- https://docs.alpaca.markets/us/reference/postorder
- https://docs.alpaca.markets/us/reference/getorderbyclientorderid

## 2026-10-09 — Member risk draft → live risk policy compiler (draft-only)

The existing per-user RHEN configuration draft on ANEVUM Web is the **only** proposed member preference surface. Do not create a second member risk configuration system. The new `app/member_live/policy.py` `compile_member_live_policy` function accepts an independently verified member draft, an operator-approved hard risk ceiling, the verified member ID, and the latest authenticated broker equity in integer cents. It derives the **more restrictive** per-order, gross-exposure, position-count, and single-symbol limits, keeping operator daily-loss, spread and chase caps unchanged. An account mismatch or low/invalid equity fails closed.

The `LivePolicy.max_symbol_exposure_cents` limit is now mandatory. `BrokerObservation` includes both filled **and pending** exposure for the current symbol; `validate_live_intent` rejects additions that would exceed that position cap, even if each individual order is below its own notional ceiling. This prevents breaking a single-position limit by stacking multiple small orders. Quotes, positions, orders and equity must still be fetched server-side from the member's bound brokerage connection, not from UI data.

**Crucial distinction:** this compiler is an isolated, tested primitive only. It is not yet connected to a live order service or website settings. A saved draft, SHA-256 strategy digest, simulated authority object, or passing unit test does **not** authorize live execution. Staging must prove signed/authenticated member-to-service policy delivery, broker-sourced per-symbol pending exposure, transaction-level reservation across workers, and manual execution release. Member settings may only lower the independent operator ceiling.
