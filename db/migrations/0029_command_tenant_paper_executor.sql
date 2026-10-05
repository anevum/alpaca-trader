-- ANEVUM Command Platform Core v1
-- Tenant-isolated RHEN paper execution runtime and immutable signal queue.
-- This migration does not authorize customer LIVE execution.

begin;

create table if not exists rhen.strategy_signals (
    signal_id text primary key,
    strategy_release_id text not null
        references anevum.strategy_releases(strategy_release_id),
    action text not null,
    symbol text not null,
    reference_price numeric(38,12) not null,
    target_allocation_fraction numeric(12,10),
    stop_price numeric(38,12),
    stop_limit_price numeric(38,12),
    take_profit_price numeric(38,12),
    observed_at timestamptz not null,
    expires_at timestamptz not null,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    check (action in ('ENTER_LONG','EXIT_LONG')),
    check (position('/' in symbol) > 0),
    check (reference_price > 0),
    check (
        (action = 'ENTER_LONG'
         and target_allocation_fraction is not null
         and target_allocation_fraction > 0
         and target_allocation_fraction <= 1
         and stop_price is not null
         and stop_price > 0
         and stop_price < reference_price
         and stop_limit_price is not null
         and stop_limit_price > 0
         and stop_limit_price <= stop_price)
        or
        (action = 'EXIT_LONG'
         and target_allocation_fraction is null)
    ),
    check (take_profit_price is null or take_profit_price > reference_price),
    check (expires_at > observed_at)
);

create index if not exists rhen_strategy_signals_release_time_idx
    on rhen.strategy_signals (strategy_release_id, observed_at desc);

create index if not exists rhen_strategy_signals_expiry_idx
    on rhen.strategy_signals (expires_at);

create or replace function rhen.reject_strategy_signal_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'strategy_signals_are_immutable';
end;
$$;

drop trigger if exists rhen_strategy_signals_immutable
    on rhen.strategy_signals;
create trigger rhen_strategy_signals_immutable
before update or delete on rhen.strategy_signals
for each row execute function rhen.reject_strategy_signal_mutation();

create table if not exists anevum.tenant_execution_runtimes (
    runtime_id text primary key,
    environment text not null default 'PAPER',
    status text not null,
    source_commit text not null,
    deployment_id text,
    capabilities jsonb not null default '{}'::jsonb,
    started_at timestamptz not null,
    heartbeat_at timestamptz not null,
    updated_at timestamptz not null default now(),
    check (environment = 'PAPER'),
    check (status in ('READY','DRAINING','ERROR','OFFLINE'))
);

create index if not exists anevum_tenant_execution_runtime_heartbeat_idx
    on anevum.tenant_execution_runtimes (environment, status, heartbeat_at desc);

create table if not exists anevum.tenant_execution_account_state (
    tenant_id uuid not null,
    broker_account_id uuid not null,
    high_water_equity numeric(38,12),
    last_equity numeric(38,12),
    last_observed_at timestamptz,
    last_signal_id text,
    updated_at timestamptz not null default now(),
    primary key (tenant_id, broker_account_id),
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    check (high_water_equity is null or high_water_equity >= 0),
    check (last_equity is null or last_equity >= 0)
);

alter table rhen.account_order_intents
    add column if not exists intent_kind text not null default 'ENTRY';

alter table rhen.account_order_intents
    drop constraint if exists rhen_account_order_intents_intent_kind_check;
alter table rhen.account_order_intents
    add constraint rhen_account_order_intents_intent_kind_check
    check (intent_kind in ('ENTRY','EXIT','PROTECTIVE_STOP'));

comment on table rhen.strategy_signals is
    'Immutable approved-release signals. A signal is not an order; the tenant executor independently evaluates every assigned account.';

comment on table anevum.tenant_execution_runtimes is
    'Fresh READY heartbeat is required before Platform Core may open the tenant execution runtime gate. v1 is PAPER only.';

comment on table anevum.tenant_execution_account_state is
    'Tenant-scoped executor risk memory including account-equity high-water state.';

commit;
