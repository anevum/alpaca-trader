-- ANEVUM Foundation v2
-- NOSTRA/FORWARD append-only research evidence.

begin;

create schema if not exists nostra;

create table if not exists nostra.snapshots (
    snapshot_id text primary key,
    as_of_timestamp timestamptz not null,
    symbol text,
    market_lane text not null,
    feature_set_version text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    check ((payload->>'research_only')::boolean is true),
    check ((payload->>'execution_authority')::boolean is false)
);

create table if not exists nostra.forecasts (
    forecast_id text primary key,
    snapshot_id text not null references nostra.snapshots(snapshot_id),
    generated_at timestamptz not null,
    symbol text,
    market_lane text not null,
    horizon_minutes integer not null check (horizon_minutes > 0),
    target_kind text not null,
    model_id text not null,
    model_version text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    check ((payload->>'research_only')::boolean is true),
    check ((payload->>'execution_authority')::boolean is false)
);

create table if not exists nostra.outcomes (
    outcome_id text primary key,
    forecast_id text not null references nostra.forecasts(forecast_id),
    observed_at timestamptz not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    check ((payload->>'research_only')::boolean is true),
    check ((payload->>'execution_authority')::boolean is false)
);

create table if not exists nostra.scores (
    score_id text primary key,
    forecast_id text not null references nostra.forecasts(forecast_id),
    outcome_id text not null references nostra.outcomes(outcome_id),
    scoring_version text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    check ((payload->>'research_only')::boolean is true),
    check ((payload->>'execution_authority')::boolean is false)
);

create or replace function nostra.reject_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'NOSTRA evidence is append-only';
end;
$$;

drop trigger if exists nostra_snapshots_append_only on nostra.snapshots;
create trigger nostra_snapshots_append_only before update or delete on nostra.snapshots
for each row execute function nostra.reject_mutation();

drop trigger if exists nostra_forecasts_append_only on nostra.forecasts;
create trigger nostra_forecasts_append_only before update or delete on nostra.forecasts
for each row execute function nostra.reject_mutation();

drop trigger if exists nostra_outcomes_append_only on nostra.outcomes;
create trigger nostra_outcomes_append_only before update or delete on nostra.outcomes
for each row execute function nostra.reject_mutation();

drop trigger if exists nostra_scores_append_only on nostra.scores;
create trigger nostra_scores_append_only before update or delete on nostra.scores
for each row execute function nostra.reject_mutation();

create index if not exists nostra_forecasts_snapshot_idx on nostra.forecasts(snapshot_id);
create index if not exists nostra_outcomes_forecast_idx on nostra.outcomes(forecast_id);
create index if not exists nostra_scores_forecast_idx on nostra.scores(forecast_id);

commit;
