# ANEVUM Command Commercial Platform v1

Status: LOCKED architecture
Locked: 2026-10-04
Implementation program: ANEVUM Command Platform Core v1

## Product definition

ANEVUM Command is the paid customer product. Customers receive isolated ANEVUM tenants, connect or open an Alpaca brokerage account, fund that account through broker-supported rails, allocate capital to RHEN, and use Command to observe and control automated crypto trading.

Alpaca remains the broker/custodian and source of truth for brokerage cash, positions, orders, fills, account status, and broker-side transfers. ANEVUM does not pool customer funds or maintain a customer-money liability ledger.

The mature product targets Alpaca Broker API for embedded account opening, funding, trading, and withdrawals. An earlier commercial stage may connect existing Alpaca accounts through approved OAuth/Connect flows.

## Locked subsystem responsibilities

- GRAEN: research and hypothesis generation only.
- VELUM: replay, simulation, and validation only.
- NOSTRA: forecasting and regime intelligence only.
- RHEN: deterministic broker execution after all account and platform gates pass.
- IREN: platform control plane, release governance, incident handling, fleet risk state, and execution freezes.
- Command: authenticated customer/admin interface. It does not become a source of truth for broker state.

Research services have no withdrawal authority. Funding authority is separate from trading authority. Research cannot self-promote to unrestricted customer live execution.

## Tenant model

Customers receive tenants, not independent software deployments.

Every customer-owned persistent object is scoped by tenant_id. Broker account identities are also tenant-scoped. A broker account, allocation, risk profile, strategy assignment, signal decision, order intent, order, fill, position projection, transfer request, and performance record must be attributable to exactly one tenant before customer production trading exists.

One shared strategy signal can fan out into independent account-level execution decisions. A signal is not an order.

## Customer account lifecycle

REGISTERED
-> SUBSCRIBED
-> BROKER_SETUP_REQUIRED
-> BROKER_PENDING
-> BROKER_ACTIVE
-> FUNDING_REQUIRED
-> FUNDED
-> TRADING_CONFIGURATION_REQUIRED
-> READY
-> ACTIVE

Additional non-active states include PAUSED, RESTRICTED, BROKER_BLOCKED, PAYMENT_PAST_DUE, RISK_HALTED, SYSTEM_HALTED, CLOSING, and CLOSED.

ACTIVE requires all required subscription, broker, allocation, strategy, consent, risk, and IREN fleet gates to pass. Credentials alone never enable execution.

## Capital authority

Broker account equity and RHEN trading authority are separate values.

RHEN may use only the customer's current ANEVUM allocation. Initial platform contracts support percentage allocation with an absolute cap. Account equity outside the allocation remains unavailable to RHEN even though it remains visible for total-account risk calculations.

## Execution model

Canonical flow:

MARKET DATA
-> VERSIONED STRATEGY
-> IMMUTABLE SIGNAL
-> TENANT ACCOUNT ELIGIBILITY
-> RISK DECISION
-> IDEMPOTENT ORDER INTENT
-> BROKER REQUEST
-> BROKER ACKNOWLEDGEMENT
-> ORDER/FILL RECONCILIATION

Order submission uses deterministic idempotency identities. An ambiguous network result must reconcile with Alpaca before retrying.

RHEN may manage RHEN-owned positions. It must include outside/manual holdings in account-level risk but must not liquidate unrelated customer positions.

## Risk hierarchy

An entry requires all four layers:

1. Strategy risk permits it.
2. Tenant/account risk permits it.
3. Fleet risk permits it.
4. Platform risk permits it.

A higher-level denial always wins.

Customer controls:
- Pause: no new entries; existing RHEN risk remains managed.
- Resume: allowed only after reconciliation and gates pass.
- Stop and close: cancel RHEN entry orders and close RHEN-managed positions.
- Emergency stop: immediate risk-reduction mode.

No customer action can bypass platform risk controls.

## Strategy release model

Canonical lifecycle:

OBSERVE
-> HYPOTHESIZE
-> PREREGISTER
-> DEVELOPMENT
-> VALIDATION
-> HOLDOUT
-> VELUM
-> FORWARD SHADOW
-> PAPER
-> HUMAN LIVE DECISION
-> RELEASE CANDIDATE
-> LIVE CANARY
-> STAGED FLEET
-> STABLE

Production strategy releases are immutable and identify code revision, strategy/configuration hashes, evidence references, approval state, and rollback target.

Customer fleet deployment is staged. A serious anomaly halts progression or rolls back to a prior approved release.

## Funding model

Command may initiate and display broker-supported funding operations, but the underlying funds remain at Alpaca.

Target rails:
- bank -> brokerage;
- brokerage -> bank;
- approved crypto wallet -> brokerage;
- brokerage -> approved/whitelisted crypto wallet;
- broker-supported same-owner transfers.

Customer-to-customer payments are explicitly out of scope.

Withdrawals always require customer authorization and are structurally separated from RHEN/GRAEN/VELUM/NOSTRA authority.

## Security boundaries

Broker access tokens and partner secrets are never stored in frontend storage, analytics, logs, GitHub, or ordinary plaintext database fields. Platform records hold only secret-manager references or encrypted secret envelopes.

Step-up authentication is required for withdrawals, adding funding destinations, broker disconnection, sensitive security changes, and account closure.

Admin/support access is auditable and must not implicitly grant trading or withdrawal authority.

## Source-of-truth rules

- Alpaca: brokerage cash, buying power, positions, orders, fills, broker account status, broker transfers.
- Billing provider: subscription payment state.
- ANEVUM: tenant identity, entitlements, RHEN capital authority, risk configuration, strategy releases, execution intent, system events, release assignment.
- Command: projection only.

After restart, execution remains closed until broker/account/order/position/release/risk reconciliation completes.

## Commercial launch gates

Customer paper beta precedes customer live trading.

Unrestricted live customer launch remains blocked until:
- strategy evidence is adequate;
- tenant isolation is verified;
- commercial Alpaca approval is complete;
- applicable legal/regulatory structure is resolved;
- production security review passes;
- reconciliation/failure testing passes;
- agreements/disclosures are versioned;
- staged live canary succeeds.

## Platform Core v1 scope

This implementation program begins with:

- tenant identities;
- user-to-tenant memberships;
- subscription entitlements;
- broker-account abstraction;
- secret-reference-only broker authorization metadata;
- capital allocations;
- account risk profiles;
- strategy release registry;
- tenant strategy assignments;
- idempotent tenant-scoped order-intent identity;
- append-only audit events for protected platform actions;
- pure execution-eligibility contracts and tests.

Platform Core v1 does not grant customer live execution or withdrawal authority.

## Preserve current work

Current BTC research/paper work, Foundation v2, truthful Command activity semantics, and the autonomous GRAEN lifecycle continue. Platform Core v1 is additive and must not interrupt or weaken those systems.

## Product overhaul v2 consolidation

Platform Core is the single canonical customer-domain model.

The following identities must not be duplicated by a separate financial subsystem:

- tenant/principal/membership;
- Alpaca broker account;
- RHEN capital allocation;
- account risk profile;
- broker reconciliation;
- customer trading controls.

The earlier Financial Gateway draft is superseded as a parallel product architecture.
Useful provider patterns are folded into Platform Core instead.

Canonical migration sequence on this branch:

- 0024: Command Platform Core identities and isolation;
- 0025: broker reconciliation evidence;
- 0026: tenant trading controls;
- 0027: paper-beta OAuth/encrypted token envelopes;
- 0028: broker funding/transfer metadata.

Migration 0028 intentionally stores broker transfer intent and event metadata only. It
does not create an ANEVUM customer cash ledger, a second provider-account identity, or
a second RHEN allocation model.

Alpaca remains authoritative for customer brokerage cash and transfer settlement.

The broker-money provider adapter is fail-closed by default. The Alpaca Broker adapter
present in Platform Core is sandbox-only and is not wired to customer transfer
mutations. Deposits, withdrawals, production Broker API account opening, and customer
LIVE trading remain unavailable in Platform Core v1.

This consolidation preserves the autonomous GRAEN/VELUM/IREN research and release
pipeline and the isolated BTC paper-canary program. It changes product boundaries, not
research authority or live-risk gates.

