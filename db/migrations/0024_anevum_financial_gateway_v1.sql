-- ANEVUM Financial Gateway v1
-- Additive, execution-neutral financial ledger and control-plane records.
-- This migration does not move external funds or grant RHEN live execution authority.

begin;

create table if not exists anevum.financial_customers (
    customer_id uuid primary key default gen_random_uuid(),
    command_subject text not null unique,
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE','SUSPENDED','CLOSED')),
    display_label text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists anevum.financial_accounts (
    financial_account_id uuid primary key default gen_random_uuid(),
    customer_id uuid not null references anevum.financial_customers(customer_id),
    account_kind text not null
        check (account_kind in ('AVAILABLE_CASH','RHEN_ALLOCATION','RESERVE')),
    currency text not null default 'USD' check (currency ~ '^[A-Z]{3,8}$'),
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE','FROZEN','CLOSED')),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (customer_id, account_kind, currency)
);

create table if not exists anevum.financial_provider_accounts (
    provider_account_id uuid primary key default gen_random_uuid(),
    customer_id uuid not null references anevum.financial_customers(customer_id),
    provider text not null,
    provider_account_ref text not null,
    provider_environment text not null
        check (provider_environment in ('SANDBOX','PAPER','PRODUCTION')),
    status text not null default 'PENDING'
        check (status in ('PENDING','ACTIVE','RESTRICTED','CLOSED')),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (provider, provider_account_ref)
);

create table if not exists anevum.financial_ledger_accounts (
    ledger_account_id uuid primary key default gen_random_uuid(),
    account_code text not null unique,
    owner_type text not null check (owner_type in ('SYSTEM','CUSTOMER')),
    customer_id uuid references anevum.financial_customers(customer_id),
    financial_account_id uuid unique references anevum.financial_accounts(financial_account_id),
    classification text not null
        check (classification in ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE','MEMO')),
    normal_side text not null check (normal_side in ('DEBIT','CREDIT')),
    currency text not null check (currency ~ '^[A-Z]{3,8}$'),
    status text not null default 'ACTIVE'
        check (status in ('ACTIVE','FROZEN','CLOSED')),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    check (
        (owner_type='SYSTEM' and customer_id is null)
        or (owner_type='CUSTOMER' and customer_id is not null)
    )
);

create table if not exists anevum.financial_ledger_transactions (
    transaction_id uuid primary key default gen_random_uuid(),
    transaction_key text not null unique,
    transaction_type text not null,
    currency text not null check (currency ~ '^[A-Z]{3,8}$'),
    payload_hash text not null,
    reversed_transaction_id uuid references anevum.financial_ledger_transactions(transaction_id),
    provider text,
    provider_reference text,
    correlation_id text,
    occurred_at timestamptz not null default now(),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists financial_ledger_transactions_time_idx
    on anevum.financial_ledger_transactions (occurred_at desc);
create index if not exists financial_ledger_transactions_correlation_idx
    on anevum.financial_ledger_transactions (correlation_id)
    where correlation_id is not null;

create table if not exists anevum.financial_ledger_entries (
    entry_id bigint generated always as identity primary key,
    transaction_id uuid not null
        references anevum.financial_ledger_transactions(transaction_id) on delete restrict,
    ledger_account_id uuid not null
        references anevum.financial_ledger_accounts(ledger_account_id) on delete restrict,
    sequence integer not null check (sequence >= 1),
    side text not null check (side in ('DEBIT','CREDIT')),
    amount numeric(38,12) not null check (amount > 0),
    memo text,
    created_at timestamptz not null default now(),
    unique (transaction_id, sequence)
);

create index if not exists financial_ledger_entries_account_idx
    on anevum.financial_ledger_entries (ledger_account_id, entry_id);

create table if not exists anevum.financial_transfers (
    transfer_id uuid primary key default gen_random_uuid(),
    idempotency_key text not null unique,
    customer_id uuid not null references anevum.financial_customers(customer_id),
    financial_account_id uuid not null references anevum.financial_accounts(financial_account_id),
    provider_account_id uuid references anevum.financial_provider_accounts(provider_account_id),
    direction text not null check (direction in ('DEPOSIT','WITHDRAWAL','INTERNAL')),
    provider text not null,
    provider_environment text not null
        check (provider_environment in ('SANDBOX','PAPER','PRODUCTION')),
    currency text not null check (currency ~ '^[A-Z]{3,8}$'),
    amount numeric(38,12) not null check (amount > 0),
    status text not null default 'DRAFT'
        check (status in ('DRAFT','REQUESTED','PENDING','SETTLED','FAILED','CANCELED')),
    provider_reference text,
    ledger_transaction_id uuid references anevum.financial_ledger_transactions(transaction_id),
    failure_code text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    settled_at timestamptz
);

create index if not exists financial_transfers_customer_time_idx
    on anevum.financial_transfers (customer_id, created_at desc);

create table if not exists anevum.financial_transfer_events (
    event_id bigint generated always as identity primary key,
    event_key text not null unique,
    transfer_id uuid not null references anevum.financial_transfers(transfer_id),
    event_type text not null,
    status text,
    provider_reference text,
    observed_at timestamptz not null default now(),
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists anevum.rhen_allocations (
    allocation_id uuid primary key default gen_random_uuid(),
    customer_id uuid not null references anevum.financial_customers(customer_id),
    financial_account_id uuid not null references anevum.financial_accounts(financial_account_id),
    provider_account_id uuid references anevum.financial_provider_accounts(provider_account_id),
    execution_mode text not null check (execution_mode in ('PAPER','SANDBOX')),
    status text not null default 'DRAFT'
        check (status in ('DRAFT','ACTIVE','PAUSED','RETIRED')),
    currency text not null default 'USD' check (currency ~ '^[A-Z]{3,8}$'),
    max_allocation numeric(38,12) not null check (max_allocation > 0),
    max_position_fraction numeric(12,8) not null default 0.10
        check (max_position_fraction > 0 and max_position_fraction <= 1),
    max_daily_loss_fraction numeric(12,8) not null default 0.05
        check (max_daily_loss_fraction > 0 and max_daily_loss_fraction <= 1),
    strategy_version_id text,
    external_execution_enabled boolean not null default false
        check (external_execution_enabled is false),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (customer_id, financial_account_id)
);

create table if not exists anevum.financial_provider_webhooks (
    webhook_id bigint generated always as identity primary key,
    provider text not null,
    provider_event_id text not null,
    payload_hash text not null,
    event_type text,
    received_at timestamptz not null default now(),
    processed_at timestamptz,
    processing_status text not null default 'RECEIVED'
        check (processing_status in ('RECEIVED','PROCESSED','IGNORED','FAILED')),
    payload jsonb not null,
    unique (provider, provider_event_id)
);

create table if not exists anevum.financial_reconciliations (
    reconciliation_id uuid primary key default gen_random_uuid(),
    customer_id uuid references anevum.financial_customers(customer_id),
    provider text not null,
    provider_account_ref text,
    currency text not null check (currency ~ '^[A-Z]{3,8}$'),
    ledger_total numeric(38,12) not null,
    provider_total numeric(38,12) not null,
    difference numeric(38,12) not null,
    status text not null check (status in ('MATCHED','MISMATCH','UNAVAILABLE')),
    observed_at timestamptz not null default now(),
    evidence jsonb not null default '{}'::jsonb
);

create or replace function anevum.reject_financial_ledger_mutation()
returns trigger
language plpgsql
as $$
begin
    raise exception 'financial_ledger_is_append_only';
end;
$$;

drop trigger if exists financial_ledger_transactions_append_only
    on anevum.financial_ledger_transactions;
create trigger financial_ledger_transactions_append_only
before update or delete on anevum.financial_ledger_transactions
for each row execute function anevum.reject_financial_ledger_mutation();

drop trigger if exists financial_ledger_entries_append_only
    on anevum.financial_ledger_entries;
create trigger financial_ledger_entries_append_only
before update or delete on anevum.financial_ledger_entries
for each row execute function anevum.reject_financial_ledger_mutation();

drop trigger if exists financial_provider_webhooks_append_only
    on anevum.financial_provider_webhooks;
create trigger financial_provider_webhooks_append_only
before update or delete on anevum.financial_provider_webhooks
for each row execute function anevum.reject_financial_ledger_mutation();

create or replace function anevum.validate_financial_ledger_entry()
returns trigger
language plpgsql
as $$
declare
    v_transaction_currency text;
    v_account_currency text;
    v_account_status text;
begin
    select currency into v_transaction_currency
    from anevum.financial_ledger_transactions
    where transaction_id = new.transaction_id;

    select currency, status into v_account_currency, v_account_status
    from anevum.financial_ledger_accounts
    where ledger_account_id = new.ledger_account_id;

    if v_transaction_currency is null or v_account_currency is null then
        raise exception 'financial_ledger_reference_missing';
    end if;
    if v_transaction_currency <> v_account_currency then
        raise exception 'financial_ledger_currency_mismatch';
    end if;
    if v_account_status <> 'ACTIVE' then
        raise exception 'financial_ledger_account_not_active';
    end if;
    return new;
end;
$$;

drop trigger if exists financial_ledger_entry_validate
    on anevum.financial_ledger_entries;
create trigger financial_ledger_entry_validate
before insert on anevum.financial_ledger_entries
for each row execute function anevum.validate_financial_ledger_entry();

create or replace function anevum.assert_balanced_financial_transaction()
returns trigger
language plpgsql
as $$
declare
    v_debits numeric(38,12);
    v_credits numeric(38,12);
begin
    select
        coalesce(sum(amount) filter (where side='DEBIT'),0),
        coalesce(sum(amount) filter (where side='CREDIT'),0)
    into v_debits, v_credits
    from anevum.financial_ledger_entries
    where transaction_id = new.transaction_id;

    if v_debits <= 0 or v_credits <= 0 or v_debits <> v_credits then
        raise exception 'financial_transaction_unbalanced: debits=%, credits=%',
            v_debits, v_credits;
    end if;
    return null;
end;
$$;

drop trigger if exists financial_ledger_balance_check
    on anevum.financial_ledger_entries;
create constraint trigger financial_ledger_balance_check
after insert on anevum.financial_ledger_entries
deferrable initially deferred
for each row execute function anevum.assert_balanced_financial_transaction();

insert into anevum.financial_ledger_accounts (
    account_code, owner_type, classification, normal_side, currency, metadata
)
values (
    'SYSTEM:SANDBOX:CUSTODIAN_CASH:USD',
    'SYSTEM',
    'ASSET',
    'DEBIT',
    'USD',
    '{"external_custody":false,"sandbox_only":true}'::jsonb
)
on conflict (account_code) do nothing;

commit;
