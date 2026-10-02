-- ANEVUM Foundation v2
-- Migration 0007: accelerate candidate forward-outcome evidence reads.
-- This changes only the PostgreSQL access path; evidence rows and semantics are unchanged.

create index if not exists rhen_events_forward_candidate_identity_idx
    on rhen.events (
        (coalesce(
            nullif(payload->>'candidate_id', ''),
            nullif(payload->>'candidate_key', '')
        )),
        occurred_at
    )
    where event_type = 'candidate_forward_outcome';
