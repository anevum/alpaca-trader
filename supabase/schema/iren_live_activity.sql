-- IREN native Live Activity durability layer.
-- Applied to the ANEVUM Supabase project on 2026-09-29.
-- Server-only: private schema, RLS enabled, no anon/authenticated grants.

create table if not exists private.iren_live_activity_tokens (
  activity_id text primary key,
  push_token text not null unique,
  apns_environment text not null default 'production'
    check (apns_environment in ('sandbox','production')),
  bundle_id text not null default 'com.anevum.iren',
  platform text not null default 'ios'
    check (platform = 'ios'),
  surface text not null default 'rhen_live_activity'
    check (surface = 'rhen_live_activity'),
  active boolean not null default true,
  registered_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_push_at timestamptz,
  last_apns_status integer,
  last_apns_id text,
  last_error text,
  constraint iren_live_activity_id_length check (length(activity_id) between 1 and 160),
  constraint iren_live_activity_token_hex check (
    length(push_token) between 32 and 256
    and push_token ~ '^[0-9a-f]+$'
  )
);

alter table private.iren_live_activity_tokens enable row level security;
revoke all on table private.iren_live_activity_tokens from anon, authenticated;

create index if not exists iren_live_activity_tokens_active_idx
  on private.iren_live_activity_tokens (active, updated_at desc);

create table if not exists private.iren_live_activity_deliveries (
  delivery_id bigint generated always as identity primary key,
  activity_id text references private.iren_live_activity_tokens(activity_id) on delete set null,
  requested_at timestamptz not null default now(),
  completed_at timestamptz,
  event text not null check (event in ('update','end')),
  payload_fingerprint text,
  apns_environment text,
  apns_status integer,
  apns_id text,
  response_reason text,
  delivered boolean not null default false,
  duration_ms integer
);

alter table private.iren_live_activity_deliveries enable row level security;
revoke all on table private.iren_live_activity_deliveries from anon, authenticated;

create index if not exists iren_live_activity_deliveries_activity_idx
  on private.iren_live_activity_deliveries (activity_id, requested_at desc);

create index if not exists iren_live_activity_deliveries_recent_idx
  on private.iren_live_activity_deliveries (requested_at desc);
