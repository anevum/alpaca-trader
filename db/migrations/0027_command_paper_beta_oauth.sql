-- ANEVUM Command Paper Beta v1
-- Invite-only customer identity, paper OAuth state, and encrypted token envelopes.
-- Encrypted database envelopes are PAPER-BETA only; managed KMS remains a live-customer launch gate.

begin;

create unique index if not exists anevum_principals_email_uidx
    on anevum.principals (lower(email))
    where email is not null;

create table if not exists anevum.oauth_states (
    state_hash text primary key,
    tenant_id uuid not null references anevum.tenants(tenant_id),
    principal_id uuid not null references anevum.principals(principal_id),
    provider text not null default 'ALPACA',
    environment text not null default 'PAPER',
    redirect_uri text not null,
    expires_at timestamptz not null,
    consumed_at timestamptz,
    created_at timestamptz not null default now(),
    check (provider = 'ALPACA'),
    check (environment = 'PAPER'),
    check (expires_at > created_at),
    check (consumed_at is null or consumed_at >= created_at)
);

create index if not exists anevum_oauth_states_expiry_idx
    on anevum.oauth_states (expires_at)
    where consumed_at is null;

create table if not exists anevum.secret_envelopes (
    secret_reference text primary key,
    tenant_id uuid not null references anevum.tenants(tenant_id),
    purpose text not null,
    key_version text not null,
    nonce bytea not null,
    ciphertext bytea not null,
    created_at timestamptz not null default now(),
    revoked_at timestamptz,
    check (purpose = 'ALPACA_PAPER_OAUTH'),
    check (length(nonce) = 12),
    check (length(ciphertext) > 16)
);

create index if not exists anevum_secret_envelopes_tenant_idx
    on anevum.secret_envelopes (tenant_id, created_at desc);

comment on table anevum.secret_envelopes is
    'Paper-beta encrypted OAuth token envelopes. Never store plaintext tokens. Replace with managed KMS/secret-store backing before customer live-money launch.';

commit;
