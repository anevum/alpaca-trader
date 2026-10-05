-- ANEVUM Command Platform Core v1
-- Additive commercial-platform identities and safety contracts.
-- This migration intentionally grants no customer live-trading or withdrawal authority.

begin;

create extension if not exists pgcrypto;

create table if not exists anevum.tenants (
    tenant_id uuid primary key default gen_random_uuid(),
    tenant_key text not null unique,
    display_name text not null,
    status text not null default 'REGISTERED',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (status in (
        'REGISTERED',
        'SUBSCRIBED',
        'BROKER_SETUP_REQUIRED',
        'BROKER_PENDING',
        'BROKER_ACTIVE',
        'FUNDING_REQUIRED',
        'FUNDED',
        'TRADING_CONFIGURATION_REQUIRED',
        'READY',
        'ACTIVE',
        'PAUSED',
        'RESTRICTED',
        'BROKER_BLOCKED',
        'PAYMENT_PAST_DUE',
        'RISK_HALTED',
        'SYSTEM_HALTED',
        'CLOSING',
        'CLOSED'
    ))
);

create table if not exists anevum.principals (
    principal_id uuid primary key default gen_random_uuid(),
    external_subject text not null unique,
    email text,
    status text not null default 'ACTIVE',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (status in ('ACTIVE','DISABLED','CLOSED'))
);

create table if not exists anevum.tenant_memberships (
    tenant_id uuid not null references anevum.tenants(tenant_id),
    principal_id uuid not null references anevum.principals(principal_id),
    role text not null,
    status text not null default 'ACTIVE',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (tenant_id, principal_id),
    check (role in ('OWNER','ADMIN','MEMBER','SUPPORT_READONLY')),
    check (status in ('INVITED','ACTIVE','SUSPENDED','REMOVED'))
);

create table if not exists anevum.entitlements (
    entitlement_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references anevum.tenants(tenant_id),
    product_key text not null,
    status text not null,
    source text not null,
    external_subscription_id text,
    effective_at timestamptz not null,
    expires_at timestamptz,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (status in ('ACTIVE','PAST_DUE','CANCELLED','EXPIRED','TRIAL')),
    check (expires_at is null or expires_at > effective_at)
);

create unique index if not exists anevum_entitlements_external_subscription_uidx
    on anevum.entitlements (source, external_subscription_id)
    where external_subscription_id is not null;

create index if not exists anevum_entitlements_tenant_idx
    on anevum.entitlements (tenant_id, status, effective_at desc);

create table if not exists anevum.broker_accounts (
    broker_account_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null references anevum.tenants(tenant_id),
    provider text not null default 'ALPACA',
    provider_account_id text not null,
    environment text not null,
    account_status text not null,
    crypto_enabled boolean not null default false,
    trading_blocked boolean not null default false,
    withdrawals_blocked boolean not null default false,
    last_reconciled_at timestamptz,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (provider, environment, provider_account_id),
    unique (tenant_id, broker_account_id),
    check (provider = 'ALPACA'),
    check (environment in ('PAPER','LIVE'))
);

create index if not exists anevum_broker_accounts_tenant_idx
    on anevum.broker_accounts (tenant_id, environment, account_status);

create table if not exists anevum.broker_authorizations (
    broker_authorization_id uuid primary key default gen_random_uuid(),
    broker_account_id uuid not null references anevum.broker_accounts(broker_account_id),
    authorization_kind text not null,
    secret_reference text not null,
    scopes jsonb not null default '[]'::jsonb,
    status text not null default 'ACTIVE',
    issued_at timestamptz,
    expires_at timestamptz,
    last_validated_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (authorization_kind in ('OAUTH','BROKER_API')),
    check (status in ('ACTIVE','REVOKED','EXPIRED','INVALID')),
    check (length(trim(secret_reference)) > 0),
    check (expires_at is null or issued_at is null or expires_at > issued_at)
);

comment on column anevum.broker_authorizations.secret_reference is
    'Reference to an external secret/envelope store. Never store raw Alpaca tokens or partner secrets here.';

create index if not exists anevum_broker_authorizations_account_idx
    on anevum.broker_authorizations (broker_account_id, status);

create unique index if not exists anevum_broker_authorizations_active_uidx
    on anevum.broker_authorizations (broker_account_id)
    where status = 'ACTIVE';

create table if not exists anevum.capital_allocations (
    allocation_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null,
    broker_account_id uuid not null,
    allocation_mode text not null default 'PERCENT_WITH_CAP',
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    allocation_fraction numeric(9,8) not null,
    absolute_cap numeric(30,10) not null,
    status text not null default 'ACTIVE',
    effective_at timestamptz not null default now(),
    superseded_at timestamptz,
    created_at timestamptz not null default now(),
    check (allocation_mode = 'PERCENT_WITH_CAP'),
    check (allocation_fraction > 0 and allocation_fraction <= 1),
    check (absolute_cap > 0),
    check (status in ('ACTIVE','PAUSED','SUPERSEDED')),
    check (superseded_at is null or superseded_at >= effective_at)
);

create unique index if not exists anevum_capital_allocations_active_uidx
    on anevum.capital_allocations (tenant_id, broker_account_id)
    where status = 'ACTIVE';

create table if not exists anevum.risk_profiles (
    risk_profile_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null,
    broker_account_id uuid not null,
    max_position_fraction numeric(9,8) not null,
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    max_gross_exposure_fraction numeric(9,8) not null,
    max_daily_loss_fraction numeric(9,8) not null,
    max_drawdown_fraction numeric(9,8) not null,
    max_concurrent_positions integer not null,
    status text not null default 'ACTIVE',
    effective_at timestamptz not null default now(),
    superseded_at timestamptz,
    created_at timestamptz not null default now(),
    check (max_position_fraction > 0 and max_position_fraction <= 1),
    check (max_gross_exposure_fraction > 0 and max_gross_exposure_fraction <= 1),
    check (max_daily_loss_fraction > 0 and max_daily_loss_fraction <= 1),
    check (max_drawdown_fraction > 0 and max_drawdown_fraction <= 1),
    check (max_concurrent_positions > 0),
    check (status in ('ACTIVE','PAUSED','SUPERSEDED')),
    check (superseded_at is null or superseded_at >= effective_at)
);

create unique index if not exists anevum_risk_profiles_active_uidx
    on anevum.risk_profiles (tenant_id, broker_account_id)
    where status = 'ACTIVE';

create table if not exists anevum.strategy_releases (
    strategy_release_id text primary key,
    strategy_key text not null,
    semantic_version text not null,
    channel text not null,
    lifecycle_state text not null,
    source_commit text not null,
    strategy_hash text not null,
    configuration_hash text not null,
    risk_policy_version text not null,
    evidence jsonb not null default '{}'::jsonb,
    approved_by text,
    approved_at timestamptz,
    rollback_release_id text references anevum.strategy_releases(strategy_release_id),
    created_at timestamptz not null default now(),
    unique (strategy_key, semantic_version),
    check (channel in ('INTERNAL','CANARY','STABLE')),
    check (lifecycle_state in (
        'RESEARCH',
        'VALIDATED',
        'PAPER_PASSED',
        'HUMAN_DECISION_REQUIRED',
        'APPROVED',
        'CANARY',
        'STABLE',
        'HALTED',
        'RETIRED'
    )),
    check (
        lifecycle_state not in ('APPROVED','CANARY','STABLE')
        or (approved_by is not null and approved_at is not null)
    )
);

create table if not exists anevum.tenant_strategy_assignments (
    assignment_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null,
    broker_account_id uuid not null,
    strategy_release_id text not null references anevum.strategy_releases(strategy_release_id),
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    status text not null default 'ACTIVE',
    assigned_at timestamptz not null default now(),
    unassigned_at timestamptz,
    assigned_by text not null,
    check (status in ('ACTIVE','PAUSED','ROLLED_BACK','SUPERSEDED')),
    check (unassigned_at is null or unassigned_at >= assigned_at)
);

create unique index if not exists anevum_tenant_strategy_assignments_active_uidx
    on anevum.tenant_strategy_assignments (tenant_id, broker_account_id)
    where status = 'ACTIVE';

create table if not exists rhen.account_order_intents (
    order_intent_id text primary key,
    tenant_id uuid not null,
    broker_account_id uuid not null,
    strategy_release_id text not null references anevum.strategy_releases(strategy_release_id),
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    signal_id text not null,
    symbol text not null,
    side text not null,
    status text not null,
    requested_qty numeric,
    requested_notional numeric,
    risk_decision jsonb not null,
    client_order_id text not null unique,
    broker_order_id text,
    correlation_id text,
    created_at timestamptz not null default now(),
    submitted_at timestamptz,
    resolved_at timestamptz,
    check (side in ('BUY','SELL')),
    check (status in (
        'CREATED',
        'RISK_REJECTED',
        'READY',
        'SUBMITTING',
        'ACKNOWLEDGED',
        'AMBIGUOUS',
        'RECONCILED',
        'CANCELLED',
        'FAILED'
    )),
    check (requested_qty is null or requested_qty > 0),
    check (requested_notional is null or requested_notional > 0)
);

create unique index if not exists rhen_account_order_intents_identity_uidx
    on rhen.account_order_intents (
        tenant_id,
        broker_account_id,
        strategy_release_id,
        signal_id,
        symbol,
        side
    );

create index if not exists rhen_account_order_intents_tenant_status_idx
    on rhen.account_order_intents (tenant_id, status, created_at desc);

create table if not exists anevum.protected_audit_events (
    audit_event_id bigint generated always as identity primary key,
    event_key text not null unique,
    tenant_id uuid references anevum.tenants(tenant_id),
    actor_type text not null,
    actor_id text,
    action text not null,
    object_type text not null,
    object_id text,
    correlation_id text,
    payload jsonb not null default '{}'::jsonb,
    occurred_at timestamptz not null default now()
);

create or replace function anevum.reject_protected_audit_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'protected audit events are append-only';
end;
$$;

drop trigger if exists anevum_protected_audit_events_append_only
    on anevum.protected_audit_events;
create trigger anevum_protected_audit_events_append_only
before update or delete on anevum.protected_audit_events
for each row execute function anevum.reject_protected_audit_mutation();

create index if not exists anevum_protected_audit_tenant_time_idx
    on anevum.protected_audit_events (tenant_id, occurred_at desc);

commit;
