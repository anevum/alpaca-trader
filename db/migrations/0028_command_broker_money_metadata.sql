-- ANEVUM Command Platform Core v1
-- Broker funding/transfer metadata only.
-- Alpaca remains the source of truth for customer brokerage cash and transfer settlement.
-- This migration grants no external money-movement authority.

begin;

create table if not exists anevum.broker_funding_relationships (
    funding_relationship_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null,
    broker_account_id uuid not null,
    provider text not null default 'ALPACA',
    provider_relationship_id text not null,
    relationship_kind text not null default 'ACH',
    display_name text,
    account_type text,
    status text not null default 'PENDING',
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    unique (provider, provider_relationship_id),
    unique (tenant_id, funding_relationship_id),
    check (provider = 'ALPACA'),
    check (relationship_kind in ('ACH','CRYPTO_WALLET','OTHER')),
    check (status in ('PENDING','ACTIVE','DISABLED','CLOSED'))
);

create index if not exists anevum_broker_funding_relationships_tenant_idx
    on anevum.broker_funding_relationships (tenant_id, broker_account_id, status);

create table if not exists anevum.broker_transfer_intents (
    transfer_intent_id uuid primary key default gen_random_uuid(),
    idempotency_key text not null unique,
    tenant_id uuid not null,
    broker_account_id uuid not null,
    funding_relationship_id uuid,
    direction text not null,
    currency text not null default 'USD',
    amount numeric(38,12) not null,
    status text not null default 'REQUESTED',
    provider text not null default 'ALPACA',
    provider_transfer_id text,
    initiated_by text not null,
    correlation_id text,
    failure_code text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    settled_at timestamptz,
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    foreign key (tenant_id, funding_relationship_id)
        references anevum.broker_funding_relationships(tenant_id, funding_relationship_id),
    check (direction in ('DEPOSIT','WITHDRAWAL')),
    check (currency ~ '^[A-Z]{3,8}$'),
    check (amount > 0),
    check (provider = 'ALPACA'),
    check (status in (
        'REQUESTED','PENDING','SETTLED','FAILED','CANCELLED','REJECTED'
    ))
);

create index if not exists anevum_broker_transfer_intents_tenant_time_idx
    on anevum.broker_transfer_intents (tenant_id, created_at desc);

create index if not exists anevum_broker_transfer_intents_provider_idx
    on anevum.broker_transfer_intents (provider_transfer_id)
    where provider_transfer_id is not null;

create table if not exists anevum.broker_transfer_events (
    transfer_event_id bigint generated always as identity primary key,
    event_key text not null unique,
    tenant_id uuid not null,
    transfer_intent_id uuid not null,
    event_type text not null,
    status text,
    provider_transfer_id text,
    payload jsonb not null default '{}'::jsonb,
    observed_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    foreign key (tenant_id, transfer_intent_id)
        references anevum.broker_transfer_intents(tenant_id, transfer_intent_id)
);

create index if not exists anevum_broker_transfer_events_tenant_time_idx
    on anevum.broker_transfer_events (tenant_id, observed_at desc);

create table if not exists anevum.broker_provider_events (
    broker_provider_event_id bigint generated always as identity primary key,
    provider text not null default 'ALPACA',
    provider_event_id text not null,
    event_type text,
    payload_hash text not null,
    payload jsonb not null,
    received_at timestamptz not null default now(),
    unique (provider, provider_event_id),
    check (provider = 'ALPACA')
);

create or replace function anevum.reject_broker_event_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'broker_event_records_are_append_only';
end;
$$;

drop trigger if exists anevum_broker_transfer_events_append_only
    on anevum.broker_transfer_events;
create trigger anevum_broker_transfer_events_append_only
before update or delete on anevum.broker_transfer_events
for each row execute function anevum.reject_broker_event_mutation();

drop trigger if exists anevum_broker_provider_events_append_only
    on anevum.broker_provider_events;
create trigger anevum_broker_provider_events_append_only
before update or delete on anevum.broker_provider_events
for each row execute function anevum.reject_broker_event_mutation();

comment on table anevum.broker_transfer_intents is
    'Customer-authorized broker transfer intent metadata. Does not represent an ANEVUM cash balance and does not itself grant external transfer authority.';

comment on table anevum.broker_provider_events is
    'Sanitized provider event evidence only. Never persist raw credentials, bank credentials, or unfiltered sensitive KYC payloads.';

commit;
