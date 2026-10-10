# ANEVUM V5 F3f — bounded paper-data source eligibility probe

Program: ANEVUM.V5.FOUNDATION.2026-10-09.001
Parent: RHEN F3e draft #485 and all prior unmerged F1–F3d drafts.
Branch: build/anevum-v5-foundation-f3f-market-data-stage-probe-20261010
State: DRAFT / REAL IEX EXECUTION NOT AUTHORIZED OR RUN.

## Rationale

F3e's fair latest quote algorithm, response-sha archive and detached
mock-recovery integration passed full CI. This does not prove that the actual
API credentials to be used by RHEN have IEX historical bar/quote access,
sufficient timestamps, correct per-symbol quote response or provider limits.

This F3f slice is a **separate read-only staging eligibility check**, NOT the
later genuine market-session acceptance. The earlier read-only connected
Alpaca tool check demonstrated returned IEX bar/quote records, but did not
exercise F3e's new code, its private staging secrets or source-specific R2.

## Current execution modes

1. Offline default, **zero secrets and network**:

       python -m scripts.v5_f3f_market_stage_probe

   Uses a deterministic small in-memory fake IEX provider. Exercises F3e
   snapshot build, separate preplanned F3b schedule, scanner-first F3a
   record, F1 journal, local immutable market objects and local replay.
   Normal result: \`OFFLINE_INJECTED_FAKE\`, \`AWAITING_EVIDENCE\`, three GET
   *mock calls* for two symbols. No raw market values in stdout.

2. Real explicitly approved option (DO NOT RUN without new credentials and
   separate owner authorization):

       python -m scripts.v5_f3f_market_stage_probe \
         --execute \
         --scan-at 2026-10-09T14:33:00+00:00 \
         --symbols AAPL,MSFT

   Requires ALL of:
   - \`ANEVUM_F3F_PAPER_DATA_APPROVED=READONLY_IEX_PAPER_DATA_ONLY\`
   - \`ANEVUM_F3F_PAPER_DATA_KEY_ID\` — newly approved dedicated isolated
     paper-market-data key identifier
   - \`ANEVUM_F3F_PAPER_DATA_SECRET_KEY\` — its matching secret
   - an explicit human-authorized isolated staging executor,
     never GitHub public PR CI, browser/client JS, production web Worker
     or the retired live RHEN deployment.

   The script has NO import path that calls market orders, accounts or
   brokerage execution. It uses the fixed GET-only Alpaca historical bars
   and quotes from the F3c/F3e module and passes no OAuth application keys.
   A provider-level paper API key may nevertheless have more privileges than
   this script uses, so grant only the intended isolated staging access,
   rotate and monitor credentials, and do not publish them.

## Bounded acceptance and status

- Up to two US equity symbols; IEX only; no SIP subscription or automated
  paid data activation.
- Explicit minute-aligned UTC scan within previous 14 days, at least 16
  minutes old. Only weekday regular session times 09:45–16:00 ET;
  exchange holidays are not inferred from weekday alone.
- Four maximum historical bar GET pages, one latest quote GET per symbol,
  at most **six** HTTP requests (8-second timeout each) per two-symbol run;
  byte/pagination constraints inherited from F3e.
- Session expected scan is WAL-declared BEFORE any HTTP request. No clock
  guesses, fallback historical values, market-on-close order or journal
  backfill if the scan is absent.
- Real data must have at least one completed bar and an in-window sampled
  quote for each requested symbol. Missing quote/bar or broken journal
  local replay results in \`BLOCKED\`, not fabricated availability.
- stdout is only sanitized JSON: request counts, symbol coverage, issue
  codes and explicit NO assertions for provider signing, off-host restore,
  complete market session, trading or research readiness.
- All source WALs and raw 1-minute/quote evidence are TEMPORARY. They
  are deleted on exit. **No off-host retention or legally durable market
  archive is made by this probe.**
- Even the successful real-GET probe can establish only that these
  narrowly scoped IEX historical responses were returned, and the
  paper snapshot was internally replayable. Evidence state stays
  \`AWAITING_EVIDENCE\` instead of \`ARCHIVED_VERIFIED\`.

## Security

Never place \`ANEVUM_F3F_PAPER_DATA_KEY_ID\` or its secret in
\`anevum-web-nextgen\`, shared issue comments, PRs or screenshots.
Do not reuse Alpaca Connect OAuth client ID/secret or the founder's
live brokerage credentials. Never echo command environment variables.
The real-execution gate is absent from the normal GitHub CI command.
There is **no workflow_dispatch** for this real mode yet.

## Next evidence gates

1. After explicit owner authorization, establish the isolated F3f
   staging environment with dedicated paper-market credentials, review
   the effective permission/entitlement and execute a single bounded
   real GET-only probe. Treat missing data as a BLOCKED result.
2. Separately authorize a private R2 test of the F3e raw provider
   page format and a distinct source-witness store/custody identity.
3. Independently anchor scheduler-origin counts, source receipts,
   and raw universe/one-minute bar/quote provenance.
4. Capture one *prospective* full paper session and restore from
   outside all local WAL, then five consecutive full sessions of
   integrity. Include 5m/15m/60m reason-coded candidate outcomes.
5. Security, provider-data licensing and compliance review before
   any member product deployment or live brokerage connection.

F3f code is an isolated draft; nothing has been merged to production
or used to initiate live orders.
