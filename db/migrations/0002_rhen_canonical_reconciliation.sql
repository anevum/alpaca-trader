-- ANEVUM Foundation v2
-- Migration 0002: RHEN canonical intent/reconciliation projections.
-- Additive only. No strategy/risk/execution configuration changes.

begin;

create table if not exists rhen.order_intents (
    intent_id text primary key,
    run_id text not null references rhen.strategy_runs(run_id),
    strategy_version_id text,
    client_order_id text not null,
    symbol text,
    side text,
    intended_at timestamptz not null,
    state text not null default 'pending',
    correlation_id text,
    payload jsonb not null default '{}'::jsonb,
    reconciliation_checked_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (run_id, client_order_id)
);

create index if not exists rhen_order_intents_state_idx
    on rhen.order_intents (run_id, state, intended_at);

create index if not exists rhen_orders_client_order_idx
    on rhen.orders (run_id, client_order_id)
    where client_order_id is not null;

create table if not exists rhen.reconciliations (
    reconciliation_id bigint generated always as identity primary key,
    run_id text not null references rhen.strategy_runs(run_id),
    strategy_version_id text,
    observed_at timestamptz not null,
    managed_symbols text[] not null default '{}'::text[],
    broker_positions jsonb not null default '[]'::jsonb,
    open_orders jsonb not null default '[]'::jsonb,
    unresolved_intents jsonb not null default '[]'::jsonb,
    unknown_open_orders jsonb not null default '[]'::jsonb,
    untracked_positions jsonb not null default '[]'::jsonb,
    safe_to_enter boolean not null,
    reason text,
    created_at timestamptz not null default now()
);

create index if not exists rhen_reconciliations_run_time_idx
    on rhen.reconciliations (run_id, observed_at desc);

commit;
