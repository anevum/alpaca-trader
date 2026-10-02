-- ANEVUM Foundation v2
-- NOSTRA/FORWARD append-only research evidence.

begin;

create schema if not exists nostra;

create table if not exists nostra.evidence_snapshots (
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

create table if not exists nostra.evidence_forecasts (
    forecast_id text primary key,
    snapshot_id text not null references nostra.evidence_snapshots(snapshot_id),
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

create table if not exists nostra.evidence_outcomes (
    outcome_id text primary key,
    forecast_id text not null references nostra.evidence_forecasts(forecast_id),
    observed_at timestamptz not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    check ((payload->>'research_only')::boolean is true),
    check ((payload->>'execution_authority')::boolean is false)
);

create table if not exists nostra.evidence_scores (
    score_id text primary key,
    forecast_id text not null references nostra.evidence_forecasts(forecast_id),
    outcome_id text not null references nostra.evidence_outcomes(outcome_id),
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

drop trigger if exists nostra_evidence_snapshots_append_only on nostra.evidence_snapshots;
create trigger nostra_evidence_snapshots_append_only before update or delete on nostra.evidence_snapshots
for each row execute function nostra.reject_mutation();

drop trigger if exists nostra_evidence_forecasts_append_only on nostra.evidence_forecasts;
create trigger nostra_evidence_forecasts_append_only before update or delete on nostra.evidence_forecasts
for each row execute function nostra.reject_mutation();

drop trigger if exists nostra_evidence_outcomes_append_only on nostra.evidence_outcomes;
create trigger nostra_evidence_outcomes_append_only before update or delete on nostra.evidence_outcomes
for each row execute function nostra.reject_mutation();

drop trigger if exists nostra_evidence_scores_append_only on nostra.evidence_scores;
create trigger nostra_evidence_scores_append_only before update or delete on nostra.evidence_scores
for each row execute function nostra.reject_mutation();

create index if not exists nostra_evidence_forecasts_snapshot_idx on nostra.evidence_forecasts(snapshot_id);
create index if not exists nostra_evidence_outcomes_forecast_idx on nostra.evidence_outcomes(forecast_id);
create index if not exists nostra_evidence_scores_forecast_idx on nostra.evidence_scores(forecast_id);

commit;
