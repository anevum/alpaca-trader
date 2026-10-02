-- ANEVUM Foundation v2
-- NOSTRA live baseline query support.

begin;

create index if not exists nostra_evidence_forecasts_candidate_identity_idx
    on nostra.evidence_forecasts (
        (payload->'provenance'->>'candidate_identity'),
        generated_at desc
    )
    where target_kind = 'return' and horizon_minutes = 10;

create index if not exists rhen_candidate_forward_identity_horizon_idx
    on rhen.events (
        (coalesce(
            nullif(payload->>'candidate_id',''),
            nullif(payload->>'candidate_key','')
        )),
        (payload->>'horizon_minutes'),
        occurred_at desc
    )
    where event_type = 'candidate_forward_outcome'
      and payload->>'status' = 'complete';

commit;
