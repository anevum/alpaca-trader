-- ANEVUM Foundation v2
-- NOSTRA append-only baseline evaluation and calibration evidence.

begin;

create table if not exists nostra.evidence_evaluations (
    evaluation_id text primary key,
    evaluated_at timestamptz not null,
    model_id text not null,
    model_version text not null,
    horizon_minutes integer not null check (horizon_minutes > 0),
    target_kind text not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    sample_count integer not null check (sample_count > 0),
    through_score_id text not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    check (window_end >= window_start),
    check ((payload->>'research_only')::boolean is true),
    check ((payload->>'execution_authority')::boolean is false)
);

drop trigger if exists nostra_evidence_evaluations_append_only
    on nostra.evidence_evaluations;
create trigger nostra_evidence_evaluations_append_only
before update or delete on nostra.evidence_evaluations
for each row execute function nostra.reject_mutation();

create index if not exists nostra_evidence_evaluations_model_idx
    on nostra.evidence_evaluations (
        model_id,
        model_version,
        horizon_minutes,
        evaluated_at desc
    );

create index if not exists nostra_evidence_evaluations_window_idx
    on nostra.evidence_evaluations (window_end desc, sample_count desc);

commit;
