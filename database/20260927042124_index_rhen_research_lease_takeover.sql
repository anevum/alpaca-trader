-- Cover the optional lease-takeover audit foreign key identified by the
-- post-migration Supabase performance advisor.
create index trading_research_leases_takeover_idx
  on private.trading_research_leases(takeover_from_run_id)
  where takeover_from_run_id is not null;
