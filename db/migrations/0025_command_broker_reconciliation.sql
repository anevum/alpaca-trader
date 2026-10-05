-- ANEVUM Command Platform Core v1
-- Per-tenant broker reconciliation evidence.
-- Read-only broker observation; grants no order or transfer authority.

begin;

create table if not exists anevum.broker_reconciliations (
    reconciliation_id uuid primary key default gen_random_uuid(),
    tenant_id uuid not null,
    broker_account_id uuid not null,
    status text not null,
    environment text not null,
    provider text not null default 'ALPACA',
    provider_account_id_expected text not null,
    provider_account_id_observed text,
    observed_at timestamptz not null,
    completed_at timestamptz not null default now(),
    account_snapshot jsonb,
    positions_snapshot jsonb,
    open_orders_snapshot jsonb,
    recent_orders_snapshot jsonb,
    snapshot_hash text,
    error_code text,
    error_detail text,
    created_at timestamptz not null default now(),
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    check (status in ('SUCCESS','FAILED','IDENTITY_MISMATCH')),
    check (environment in ('PAPER','LIVE')),
    check (provider = 'ALPACA'),
    check (
        (status = 'SUCCESS'
         and provider_account_id_observed is not null
         and provider_account_id_observed = provider_account_id_expected
         and account_snapshot is not null
         and positions_snapshot is not null
         and open_orders_snapshot is not null
         and recent_orders_snapshot is not null
         and snapshot_hash is not null
         and error_code is null)
        or
        (status <> 'SUCCESS' and error_code is not null)
    )
);

create unique index if not exists anevum_broker_reconciliations_success_hash_uidx
    on anevum.broker_reconciliations (
        tenant_id,
        broker_account_id,
        snapshot_hash
    )
    where status = 'SUCCESS';

create index if not exists anevum_broker_reconciliations_account_time_idx
    on anevum.broker_reconciliations (
        tenant_id,
        broker_account_id,
        observed_at desc
    );

create index if not exists anevum_broker_reconciliations_status_time_idx
    on anevum.broker_reconciliations (status, observed_at desc);

comment on table anevum.broker_reconciliations is
    'Read-only Alpaca reconciliation evidence. Must never contain authorization headers or raw secrets.';

commit;
