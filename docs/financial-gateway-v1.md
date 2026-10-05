# ANEVUM Financial Gateway v1

Status: IMPLEMENTATION
Date: 2026-10-04
Scope: sandbox/internal financial control plane only

## Objective

Create a bank-ready financial layer for Command without making ANEVUM a custodian,
money transmitter, bank, broker, or investment adviser by software architecture alone.

The gateway owns ANEVUM's internal financial records and permissions. External custody
and money movement remain provider responsibilities. RHEN remains the trading execution
system and receives only explicit, bounded allocations.

## Locked boundaries

- Foundation v2 remains the canonical operational data spine.
- Railway PostgreSQL remains the canonical operational store.
- Cloudflare Access remains the private Command identity boundary.
- RHEN remains the broker execution authority for ANEVUM-owned trading logic.
- Alpaca remains an external broker/custody authority where integrated.
- The Financial Gateway does not grant live trading authority.
- v1 does not initiate ACH, wire, card, or production provider transfers.
- v1 does not store bank-login credentials or raw online-banking passwords.
- v1 allocation modes are SANDBOX/PAPER only.
- Supabase is not reintroduced.

## System position

```text
Command
  |
  | Cloudflare Access identity
  v
Financial Gateway
  |
  +-- PostgreSQL anevum.financial_*  (ledger, accounts, transfers, audit)
  |
  +-- provider adapters             (sandbox first; production later)
  |
  +-- RHEN allocation contract      (bounded permission, no custody)
  |
  +-- IREN reconciliation           (future control-plane consumer)
```

## Accounting model

The canonical money record is an append-only double-entry ledger.

A deposit simulation is represented as:

```text
Debit   SYSTEM:SANDBOX:CUSTODIAN_CASH:USD
Credit  CUSTOMER:<id>:AVAILABLE_CASH:USD
```

A RHEN allocation is a liability reclassification, not a transfer into an ANEVUM-owned
wallet:

```text
Debit   CUSTOMER:<id>:AVAILABLE_CASH:USD
Credit  CUSTOMER:<id>:RHEN_ALLOCATION:USD
```

Reducing an allocation reverses those sides.

Every transaction must have:
- an immutable transaction key;
- one currency;
- a canonical payload hash;
- at least one debit and one credit;
- exactly balanced debit and credit totals;
- immutable ledger entries;
- a correlation ID when linked to an external/provider workflow.

Balances are projections from ledger entries. No mutable `balance` column is authoritative.

## Data model

### Identity and accounts

- `anevum.financial_customers`
- `anevum.financial_accounts`
- `anevum.financial_provider_accounts`

### Canonical ledger

- `anevum.financial_ledger_accounts`
- `anevum.financial_ledger_transactions`
- `anevum.financial_ledger_entries`

### Money-movement workflow

- `anevum.financial_transfers`
- `anevum.financial_transfer_events`
- `anevum.financial_provider_webhooks`

### RHEN boundary

- `anevum.rhen_allocations`

### Reconciliation

- `anevum.financial_reconciliations`

## v1 Command API

The private Command-facing API is authenticated by the existing Cloudflare Access
assertion. The authenticated email/subject is the customer identity source.

Read:

`GET /v1/command/finance`

Returns:
- customer identity;
- available cash;
- RHEN allocation;
- reserve;
- sandbox/provider account state;
- current RHEN allocation limits;
- recent transfer activity;
- `external_money_movement_enabled=false`;
- `live_execution_authorized=false`.

Write:

`POST /v1/command/finance`

Supported v1 actions:

1. `initialize`
   - creates the Command financial customer and USD account set idempotently.

2. `sandbox_deposit`
   - posts a simulated deposit to the double-entry ledger;
   - never calls an external provider.

3. `set_rhen_allocation`
   - moves internal ledger value between AVAILABLE_CASH and RHEN_ALLOCATION;
   - refuses negative available cash;
   - creates/updates a SANDBOX/PAPER allocation record;
   - cannot set external execution authority.

Production deposits, withdrawals, bank linking, customer KYC submission, and provider
webhooks are intentionally absent from this first API surface.

## Security invariants

- Never accept arbitrary `customer_id` from Command as the identity authority.
- Resolve the customer from the verified Cloudflare Access identity.
- Never log bank credentials, provider secrets, full account numbers, or KYC documents.
- Use idempotency keys for every financial mutation.
- Reject unsupported currencies and non-positive amounts.
- Fail closed when ledger balance cannot be established.
- Production money movement requires a separate provider adapter plus explicit rollout.
- RHEN must not infer permission from account balance alone.

## RHEN allocation contract

An allocation is permission metadata plus an accounting reclassification.

Required fields:
- customer;
- financial account;
- provider account when available;
- execution mode;
- maximum allocation;
- maximum position fraction;
- maximum daily loss fraction;
- strategy version;
- status.

v1 hard requirement:

`external_execution_enabled = false`

Promotion to external execution is a later controlled change and must not be inferred
from `status=ACTIVE`.

## Provider adapter contract

Future adapters implement provider-specific operations behind one boundary:

```text
create_customer_account
get_account
create_bank_link
create_transfer
get_transfer
list_activities
verify_webhook
```

v1 includes no production adapter invocation. Alpaca Broker Sandbox is the first planned
external adapter.

## IREN reconciliation contract

IREN will eventually compare:

```text
ANEVUM ledger totals
vs provider account cash
vs pending/settled transfers
vs RHEN allocation permission
vs RHEN broker exposure
```

Any unexplained mismatch must produce a financial incident. The intended safety response
is to block new withdrawals and new risk-increasing execution while preserving
risk-reducing exits.

## Delivery stages

### Stage A - current

- database migration;
- gateway domain module;
- private Command API;
- sandbox deposit;
- internal RHEN allocation;
- tests;
- no external money movement.

### Stage B - Alpaca Broker Sandbox

- provider adapter;
- sandbox account opening/KYC contract;
- tokenized bank-link contract;
- simulated transfer lifecycle;
- provider webhook ingestion;
- reconciliation.

### Stage C - closed internal beta

- owner/test identities only;
- full reconciliation;
- IREN incidents;
- RHEN multi-account paper execution contract.

### Stage D - regulated production path

Requires separate commercial/legal/compliance approval before enabling external users or
real customer money.

## Non-goals

This work does not:
- create a chartered bank;
- create FDIC insurance;
- make ANEVUM a regulated broker or adviser;
- authorize public customer asset management;
- modify the active crypto strategy;
- change RHEN production execution gates.
