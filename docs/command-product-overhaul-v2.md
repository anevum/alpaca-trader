# ANEVUM Command Product Overhaul v2

Status: PREPARED FOR IMPLEMENTATION  
Date: 2026-10-04  
Scope: product, platform, customer account, brokerage, money controls, trading controls, research visibility, release governance  
Canonical product: ANEVUM Command

## 1. Product definition

ANEVUM Command is the customer product.

A customer should be able to:

1. create an ANEVUM account;
2. connect or open an Alpaca brokerage account;
3. fund the brokerage account using broker-supported rails;
4. choose how much brokerage capital RHEN may use;
5. enable or pause automated trading when the account and strategy are eligible;
6. see positions, orders, transfers, performance, risk state, strategy release, and system status;
7. withdraw through broker-supported rails with explicit customer authorization.

Everything else is internal machinery that supports this experience.

Customer-facing product sentence:

> Connect your brokerage account, allocate capital to RHEN, start automated trading, and monitor or control it from Command.

## 2. Locked system responsibilities

These boundaries remain canonical.

- Command: authenticated customer/admin interface and projection layer.
- Alpaca: broker/custodian and source of truth for brokerage cash, positions, orders, fills, account status, and broker-side transfers.
- RHEN: deterministic account-level execution after all eligibility/risk gates pass.
- GRAEN: autonomous research and hypothesis generation.
- VELUM: replay, simulation, and validation.
- NOSTRA: forecasting and regime intelligence.
- IREN: platform control plane, release governance, fleet safety, incident handling, reconciliation policy, and execution freezes.
- Foundation/PostgreSQL: ANEVUM operational data spine and durable internal evidence store.

Research systems do not receive brokerage funding or withdrawal authority.

## 3. Simplification rule

Internal architecture must not become customer product architecture.

Customers do not manage:

- Foundation;
- GRAEN campaigns;
- VELUM replay jobs;
- NOSTRA model internals;
- IREN work queues;
- deployment topology;
- strategy candidate promotion;
- financial-provider adapter internals;
- internal ledgers.

Those remain visible only in System/Admin/Transparency surfaces when useful.

## 4. Current-state consolidation

### 4.1 Preserve from Command Platform Core v1

Preserve these concepts from PR #317:

- tenants;
- principals;
- tenant memberships;
- entitlements;
- broker accounts;
- broker authorization references;
- capital allocations;
- risk profiles;
- immutable strategy releases;
- tenant strategy assignments;
- deterministic tenant-scoped RHEN order intents;
- broker reconciliation snapshots;
- tenant trading controls;
- protected audit events;
- fail-closed execution eligibility.

### 4.2 Do not create parallel Financial Gateway identities

Do not introduce a second customer/account/allocation identity model when Platform Core already owns those concepts.

Retire or fold these planned PR #318 concepts before merge:

- financial_customers -> tenant/principal;
- financial_provider_accounts for Alpaca -> broker_accounts;
- rhen_allocations -> capital_allocations;
- financial_reconciliations for Alpaca -> broker_reconciliations.

### 4.3 Migration-number collision

PR #317 and PR #318 both currently introduce migration 0024 from the same main base.

No overhaul implementation may merge with duplicate migration numbers.

Canonical order after rebasing/consolidation:

- 0024 command platform core;
- 0025 broker reconciliation;
- 0026 tenant trading controls;
- later additive migrations begin at 0027 or the next free canonical number after main is refreshed.

### 4.4 Customer money ledger decision

For the first commercial brokerage product, ANEVUM does not maintain a customer-money liability ledger as the source of truth.

Alpaca remains authoritative for customer brokerage cash.

Command may persist:

- transfer request metadata;
- broker transfer IDs;
- statuses;
- timestamps;
- idempotency keys;
- destination aliases/last-four where permitted;
- reconciliation observations;
- audit records.

Command must not fabricate an ANEVUM cash balance that competes with broker truth.

A full ANEVUM double-entry customer funds ledger is deferred until there is a concrete need such as:

- multiple custodians;
- stored-value balances;
- customer-to-customer payments;
- internal cash movement outside a broker account;
- a regulated ANEVUM financial product that requires it.

Internal accounting ledgers for ANEVUM's own corporate accounting are a separate concern.

## 5. Canonical customer domain model

### 5.1 Tenant

One customer or organization account.

Key responsibilities:

- ownership boundary;
- billing entitlement;
- user membership;
- brokerage associations;
- risk profile;
- strategy assignment;
- audit scope.

### 5.2 Principal

An authenticated human or service identity.

A principal may belong to one or more tenants through memberships.

### 5.3 Broker account

A tenant-scoped external brokerage account.

Minimum canonical fields:

- broker_account_id;
- tenant_id;
- broker = ALPACA;
- environment = PAPER | LIVE;
- external_account_id;
- status;
- crypto_trading_eligible;
- transfers_eligible;
- authorization_reference;
- last_reconciled_at;
- reconciliation_state.

Credentials remain server-side secret references or encrypted envelopes. Raw secrets never enter Command storage, browser storage, logs, analytics, or Git.

### 5.4 Capital allocation

The amount of broker equity RHEN is authorized to use.

Initial contract:

- allocation percentage;
- optional absolute cap;
- effective authorized amount derived from fresh broker equity;
- status;
- updated_by;
- updated_at.

RHEN may never infer capital authority directly from total account equity.

### 5.5 Risk profile

Tenant/account-level limits above strategy-level risk.

Initial controls:

- max allocation;
- max position fraction;
- max concurrent exposure;
- max daily loss fraction;
- max order notional;
- optional minimum reserve;
- pause behavior;
- emergency-stop behavior.

Higher-level IREN/fleet limits always override customer risk limits.

### 5.6 Strategy release

Immutable production identity for an approved RHEN strategy.

Minimum identity:

- strategy_release_id;
- semantic/display version;
- code revision;
- strategy/config hashes;
- evidence references;
- approval state;
- rollout state;
- rollback target;
- created_at.

Customers normally receive the current STABLE release. Advanced per-tenant release assignment is an operator capability, not normal customer configuration.

### 5.7 Trading controls

Canonical customer intent:

- enabled;
- paused;
- stop_and_close_requested;
- emergency_stop_requested;
- consent_version;
- acknowledged_risk_version;
- updated_by;
- updated_at.

LIVE authority is a separate platform gate. Customer enabled=true does not by itself authorize execution.

## 6. Canonical account lifecycle

Use one lifecycle in backend and UI.

- REGISTERED
- SUBSCRIBED
- BROKER_SETUP_REQUIRED
- BROKER_PENDING
- BROKER_ACTIVE
- FUNDING_REQUIRED
- FUNDED
- CONFIGURATION_REQUIRED
- READY
- ACTIVE

Non-active states:

- PAUSED
- RESTRICTED
- PAYMENT_PAST_DUE
- BROKER_BLOCKED
- RISK_HALTED
- SYSTEM_HALTED
- CLOSING
- CLOSED

The lifecycle must be derived from canonical facts rather than manually mutated into contradictory states.

## 7. Execution eligibility

A new risk-increasing RHEN order requires all of the following.

1. tenant active;
2. entitlement active;
3. broker account active;
4. required crypto permissions active;
5. fresh successful broker reconciliation;
6. positive RHEN allocation;
7. risk profile valid;
8. strategy assignment valid;
9. strategy release approved for the requested execution mode;
10. consent/disclosure version current;
11. customer trading enabled;
12. account not paused/restricted;
13. IREN fleet state permits new risk;
14. service/platform state healthy enough for execution;
15. environment authority permits the requested PAPER/LIVE action.

Any denial wins.

Risk-reducing exits may remain available under a narrower safety path when new entries are blocked.

## 8. Order flow

Canonical path:

MARKET DATA
-> VERSIONED STRATEGY
-> IMMUTABLE SIGNAL/DECISION
-> TENANT ELIGIBILITY
-> ACCOUNT RISK
-> FLEET/PLATFORM RISK
-> IDEMPOTENT ORDER INTENT
-> BROKER SUBMISSION
-> BROKER ACK
-> ORDER/FILL RECONCILIATION
-> CUSTOMER ACTIVITY PROJECTION

A shared strategy decision may fan out to many tenant accounts.

A signal is never itself an order.

Order client identity must remain deterministic and retry-stable.

Ambiguous submission results must reconcile against Alpaca before retrying.

## 9. Brokerage and money model

### 9.1 Source of truth

Alpaca is authoritative for:

- brokerage cash;
- buying power;
- equity;
- positions;
- orders;
- fills;
- account status;
- broker-supported deposits;
- broker-supported withdrawals;
- transfer settlement state.

ANEVUM is authoritative for:

- tenant identity;
- entitlement;
- RHEN allocation permission;
- customer trading intent;
- risk configuration;
- strategy release assignment;
- execution intent;
- protected audit history;
- system/release state.

### 9.2 Money actions

Target customer experience:

Deposit:
Bank/wallet -> Alpaca -> reconciliation -> Command projection.

Withdrawal:
Command request -> step-up auth -> Alpaca -> reconciliation -> Command projection.

ANEVUM does not pool customer funds.

### 9.3 Money module

Money is a module inside Command, not a second product.

Minimum read model:

- broker cash/equity;
- buying power;
- RHEN allocated amount;
- reserve/unallocated amount;
- pending transfers;
- settled recent transfers;
- account restrictions;
- last reconciliation time.

Initial mutation surface:

- initiate supported broker deposit flow;
- initiate supported broker withdrawal flow;
- set RHEN allocation;
- refresh/reconcile;
- disconnect brokerage only through protected flow.

Until production broker funding integration is approved, these operations remain paper/sandbox or read-only.

## 10. Research and autonomous operation

The autonomous research program remains intact.

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

GRAEN may autonomously continue safe research.

GRAEN must not:

- mutate source code at runtime;
- merge PRs;
- deploy;
- change credentials;
- initiate spend;
- weaken statistical safeguards;
- increase production risk;
- bypass holdout;
- add brokers;
- authorize unrestricted live trading.

Engineering requirements create a manual ChatGPT/Codex handoff.

Protected live-risk decisions remain human-authorized.

## 11. Strategy release experience

Customers should not operate the research pipeline.

Normal customer display:

RHEN
Status: Active
Release: Stable vX.Y.Z
Updated: <timestamp>
Risk state: Normal

Optional expandable detail:

- release evidence summary;
- deployment stage;
- prior stable version;
- rollback state;
- research transparency link.

No normal customer UI for selecting arbitrary experimental candidates.

## 12. IREN role

IREN owns platform-wide execution permission above the customer account.

IREN may:

- freeze new risk;
- halt rollout;
- require reconciliation;
- mark releases unavailable;
- open incidents;
- roll back approved releases;
- classify engineering-required work;
- distinguish liveness from productive mission state.

IREN may not invent customer withdrawal authorization.

Command must expose one simple customer-facing interpretation:

- Trading available;
- Trading paused by you;
- Trading temporarily unavailable;
- Account action required.

Detailed IREN topology stays in System/Admin.

## 13. Command API surface

Prefer a small customer API surface rather than subsystem-shaped APIs.

Suggested versioned surface:

GET /v1/command/account
GET /v1/command/overview
GET /v1/command/trading
GET /v1/command/money
GET /v1/command/activity
GET /v1/command/system

POST /v1/command/broker/connect
POST /v1/command/broker/disconnect
POST /v1/command/allocation
POST /v1/command/trading/pause
POST /v1/command/trading/resume
POST /v1/command/trading/stop
POST /v1/command/money/deposit
POST /v1/command/money/withdraw
POST /v1/command/reconcile

Server resolves tenant from authenticated principal/session. Customer-supplied tenant_id must never be trusted as the authorization source.

Mutations require idempotency keys.

Sensitive mutations require step-up authentication according to policy.

## 14. Projection model

The frontend should consume customer-oriented projections, not assemble product meaning from raw subsystem internals.

Overview projection:

- lifecycle state;
- paper/live mode;
- RHEN state;
- brokerage equity;
- RHEN allocation;
- open RHEN exposure;
- current strategy release;
- today/period normalized performance;
- action required;
- last reconciled.

Trading projection:

- enabled/paused/system halted;
- strategy release;
- current RHEN positions;
- active orders;
- account-level risk usage;
- recent RHEN decisions;
- safe control actions.

Money projection:

- cash/equity;
- allocation;
- reserve;
- transfer state;
- funding/withdrawal eligibility.

Activity projection:

- customer-relevant timeline combining transfers, orders, fills, strategy release changes, safety events, and user control changes.

System projection:

- advanced subsystem state;
- GRAEN/VELLUM/NOSTRA/IREN/RHEN activity;
- research progress;
- engineering requirements;
- incidents;
- release pipeline.

## 15. Customer navigation

Primary product navigation is fixed to:

1. Overview
2. Trading
3. Money
4. Activity
5. Settings

Optional advanced navigation:

6. System

Admin/operator-only tools can live under System or a separate Admin route.

Do not make GRAEN, VELUM, NOSTRA, IREN, Foundation, or Financial Gateway first-class customer navigation items.

## 16. Onboarding

The normal flow should be no longer than:

CREATE ACCOUNT
-> CONNECT ALPACA
-> FUND ACCOUNT
-> SET RHEN ALLOCATION
-> REVIEW RISK/DISCLOSURE
-> START PAPER TRADING

When live eligibility exists, paper-to-live promotion should reuse the same account and control model.

The UI must always distinguish PAPER from LIVE.

## 17. Security

Required invariants:

- no raw Alpaca secrets in browser storage;
- no raw provider secrets in PostgreSQL ordinary fields;
- no secrets in analytics/logs/Git;
- tenant isolation enforced at application and database boundaries;
- protected actions audited append-only;
- step-up authentication for withdrawals, broker disconnect, adding/changing destinations, account closure, and other policy-sensitive mutations;
- session identity resolves tenant server-side;
- cross-tenant object references rejected by database constraints;
- restart fails closed until required reconciliation is complete.

## 18. Public product positioning

Public website should sell one product: ANEVUM Command.

Recommended structure:

- Product / Command
- How It Works
- Performance / Evidence
- Research / Field Notes
- Company / Founder
- Sign In

Subsystems appear as the system underneath Command:

GRAEN researches
-> VELUM validates
-> NOSTRA forecasts
-> IREN governs
-> RHEN executes
-> Command gives the customer control.

Do not market each subsystem as though it is a separate SaaS product.

## 19. PR disposition

### PR #317 - Command Platform Core v1

Disposition: PRESERVE AS THE PRIMARY BACKEND FOUNDATION, but update/rebase before merge.

Required before merge:

- confirm migration numbering against current main;
- ensure Platform Core remains the single tenant/broker/allocation identity model;
- keep customer live authority default-deny;
- preserve broker reconciliation and deterministic order identity;
- align docs with this v2 product contract.

### PR #318 - Financial Gateway v1

Disposition: DO NOT MERGE AS CURRENT PARALLEL PRODUCT ARCHITECTURE.

Salvage only components that still fit after consolidation, such as:

- provider adapter patterns;
- transfer idempotency patterns;
- webhook verification patterns;
- transfer activity/audit concepts;
- sandbox testing helpers.

Remove or replace duplicate customer, broker-account, allocation, and Alpaca reconciliation identities.

Do not make its double-entry customer cash ledger the brokerage cash source of truth for v1 Command.

### PR #313 - Autonomous Operating Loop v1

Disposition: PRESERVE.

This is internal competitive infrastructure. It should feed the System/Admin surface and release pipeline without becoming normal customer configuration.

### PR #303 - BTC paper canary

Disposition: PRESERVE AS RESEARCH/PAPER EVIDENCE.

It remains isolated from customer LIVE authority. Its useful evidence eventually informs an immutable strategy release.

## 20. Implementation program

### Phase 0 - freeze architecture

- adopt this document as canonical;
- stop creating parallel customer/account/allocation abstractions;
- do not merge PR #318 as-is;
- preserve research/canary work;
- refresh both current platform branches against main before implementation.

Exit criterion:
one accepted product/domain model.

### Phase 1 - consolidate Platform Core

- rebase PR #317;
- resolve migration numbering;
- make tenant/principal/broker_account/capital_allocation/risk_profile/trading_controls canonical;
- ensure database isolation and tests;
- add customer-oriented projection contracts.

Exit criterion:
paper customer identity + brokerage + allocation + risk model is coherent and tested.

### Phase 2 - broker reconciliation and paper onboarding

- connect existing Alpaca paper account using approved auth boundary;
- server-side secret resolution;
- account-ID verification;
- reconciliation;
- funding-state projection;
- allocation setup;
- customer lifecycle derivation.

Exit criterion:
a tenant can reach READY in paper mode without direct database changes.

### Phase 3 - Command customer API

Implement minimal API surface for:

- overview;
- trading;
- money;
- activity;
- settings/account;
- pause/resume;
- allocation.

Exit criterion:
frontend no longer needs raw internal topology to render the normal customer product.

### Phase 4 - Command UX overhaul

Replace system-first UI with customer-first navigation:

Overview / Trading / Money / Activity / Settings.

Move current operational terminal into System.

Exit criterion:
a new customer can understand and use paper mode without knowing ANEVUM subsystem names.

### Phase 5 - RHEN multi-account paper execution

- fan one approved strategy decision into tenant-specific eligibility/risk/order intents;
- reconcile orders/fills independently;
- preserve deterministic idempotency;
- include manual/external holdings in account risk without taking ownership of them.

Exit criterion:
two isolated paper tenants can run concurrently without cross-tenant state leakage.

### Phase 6 - release pipeline integration

Connect autonomous research output to immutable releases:

GRAEN -> VELUM -> shadow -> paper -> human decision -> release candidate -> staged deployment.

Exit criterion:
Command shows one current stable RHEN release and customers do not manage experimental candidates.

### Phase 7 - broker money controls

After the required Alpaca/commercial/legal path is available:

- deposit initiation;
- withdrawal initiation;
- transfer status;
- reconciliation;
- step-up auth;
- protected audit.

Exit criterion:
money actions remain broker-custodied and customer-authorized.

### Phase 8 - closed live canary

Only after strategy, legal/commercial, security, isolation, failure, reconciliation, and disclosure gates pass.

Start with owner/internal controlled identities.

No unrestricted public live trading launch until the canary succeeds.

## 21. Acceptance test matrix

### Tenant isolation

- tenant A cannot read tenant B broker account;
- tenant A cannot assign tenant B account to allocation;
- tenant A cannot mutate tenant B risk/trading state;
- tenant A order identity cannot collide with tenant B;
- API ignores/rejects forged tenant identifiers.

### Execution safety

- customer enable alone cannot authorize LIVE;
- stale reconciliation denies new entries;
- IREN halt denies new entries;
- paused tenant denies new entries;
- risk-reducing exit path remains testable separately;
- ambiguous broker submission reconciles before retry.

### Money safety

- Command balance reflects broker observations, not fabricated ledger totals;
- withdrawal requires authenticated customer intent;
- trading systems cannot call withdrawal authority;
- unsupported transfer state fails closed;
- duplicate mutation idempotency key does not duplicate transfer intent.

### Release safety

- mutable strategy configuration cannot masquerade as the same release;
- experimental candidate cannot be assigned as STABLE without protected promotion;
- rollback target is explicit;
- tenant execution records identify exact release.

### UX

- first-time user can identify account mode immediately;
- first-time user can determine whether trading is active;
- first-time user can determine allocated vs unallocated capital;
- first-time user can pause trading without entering System;
- internal subsystem failure is translated into a clear customer action/state;
- advanced system details remain available without cluttering normal workflow.

## 22. Things intentionally deferred

Do not build these into the first overhaul:

- ANEVUM as a chartered bank;
- pooled customer funds;
- customer-to-customer payments;
- generalized wallet product;
- ANEVUM-issued stored-value balances;
- multi-broker routing before Alpaca path works;
- arbitrary strategy marketplace;
- customer strategy editor;
- public experimental-candidate selection;
- separate deployments per customer;
- runtime AI with source-code mutation authority;
- automated unrestricted live promotion.

## 23. Canonical architecture summary

Customer:

Command
-> Account
-> Alpaca brokerage
-> RHEN allocation
-> Trading controls
-> Activity / performance

Execution:

Approved strategy release
-> tenant eligibility
-> tenant risk
-> IREN fleet permission
-> RHEN order intent
-> Alpaca
-> reconciliation

Research:

GRAEN
-> VELUM
-> NOSTRA intelligence
-> forward shadow
-> paper
-> protected human live decision
-> immutable release
-> IREN staged rollout
-> RHEN

This is one product with sophisticated internal systems, not a collection of separate products.
