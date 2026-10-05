-- ANEVUM Command Platform Core v1
-- Customer-controlled trading enablement. Does not grant live-customer authority.

begin;

create table if not exists anevum.tenant_trading_controls (
    tenant_id uuid not null,
    broker_account_id uuid not null,
    bot_enabled boolean not null default false,
    customer_consent_version text,
    customer_consented_at timestamptz,
    updated_by text not null,
    updated_at timestamptz not null default now(),
    primary key (tenant_id, broker_account_id),
    foreign key (tenant_id, broker_account_id)
        references anevum.broker_accounts(tenant_id, broker_account_id),
    check (
        not bot_enabled
        or (
            customer_consent_version is not null
            and customer_consented_at is not null
        )
    )
);

comment on table anevum.tenant_trading_controls is
    'Customer paper/live bot preference and trading consent evidence. This table never grants protected live-customer authority by itself.';

commit;
