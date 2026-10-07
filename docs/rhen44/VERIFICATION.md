# Local verification, 2026-10-07 UTC

- Unmodified 4.3 backend baseline: 963 passed, 8 skipped.
- 4.4 branch full backend suite: 1006 passed, 8 skipped, 5 deprecation warnings.
- New 4.4 tests: 43, including real loopback WebSocket transport, Command auth
  rejection and expiry, snapshot ordering, critical delivery, state recovery,
  duplicate fills, calendar/feed boundaries, forecast expiry, replay PIT,
  policy disabled/shadow identity, and 1000 randomized capital-envelope cases.
- After final reconnect quote invalidation change: 27 market tests passed again.
- New disabled settings preserve all existing strategy/risk/execution tests.
- Command tests: 51 passed, including 7 new reducer/provenance/forecast tests.
- Command TypeScript check passed.
- Command complete build passed with default disabled feature flag.
- Command complete build passed with `VITE_COMMAND_LIVE_STREAM_ENABLED=true`.
- Baseline GitHub backend checks: test, codex-postgres, inventory SUCCESS.
- Baseline GitHub frontend checks: audit, deploy, verify SUCCESS.

The unrestricted full suite has the same 8 environment-dependent skips as baseline.
The network-denied staging suite additionally skips the 2 real loopback transport
tests (1004 passed, 10 skipped); the separate `tests/test_rhen44_*.py` CI step runs
all 43 new tests, including those two sockets, without weakening the staging guard.
Postgres integration is not represented as locally executed; baseline hosted
Postgres CI passed. New branch hosted CI status is separate from these local results.

Local WebSocket fixtures are deterministic test inputs, never live evidence or
visual market data. The local publication exercise verified a 125 ms scheduler
with receipt under 500 ms and critical receipt under 100 ms. This is not an
end-to-end Alpaca-to-Command production latency measurement.

Not executed: live Alpaca entitlement/coverage/shadow equivalence, direct broker
account/order/position read, exact protected configuration freeze, staging
Railway deploy/restart, authorized live Command screenshot/visual QA, canonical
ledger trade_updates integration, VELUM profile replay, ASC/GRAEN forward and
holdout gates, kill-switch runtime exercise, production rollback/crossover.

No live promotion or trading profitability result is inferred from unit tests.

## Subsequent integration verification, 2026-10-07 UTC

- Dedicated RHEN 4.4 suite: **57 passed**, including real non-production loopback
  WebSockets, durable quote/reconnect candidate deduplication, read-only broker
  projections, isolated startup authority checks, wide-spread veto and overnight
  optional-channel rejection and overnight checkpoint restoration cases.
- Full repository staging command: `/tmp/rhen44-venv/bin/python scripts/staging_check.py`:
  **1018 passed, 10 skipped**, with 5 existing deprecation warnings. This command
  loads the repository network guard; setting `RHEN_STAGING=1` alone does not.
  An initial invocation without that plugin failed the guard-presence test; the
  correct entrypoint passed without any source/test relaxation.
- Backend #421–#424 test/Postgres/inventory hosted checks passed before merge.
- Command #190 hosted verify (including browser QA), audit and deployment passed.
- Private Railway observer deployed on a separate volume with broker writes
  disabled. Actual broker socket HEALTHY and account snapshot LIVE observed.
- Actual overnight combined market subscription failed with provider code 410.
  The separate-channel fix subsequently accepted bars/quotes for all 24 symbols,
  rejected only updatedBars, and received real quotes/bars. Fresh/evaluable
  full-universe coverage remains unverified. The isolated restart recovered
  subscriptions, broker and account reads; local checkpoint skip was found and
  corrected with a new test. Actual restored-bar evidence remains separate.
  Missing candles/volume/candidates are not filled with fixtures.
- Public champion health confirms 4.3.2, existing strategy, 4.4 gates false,
  reconciled safe and no module failures on the observed deployment.

Still not completed: exact trading configuration freeze, live full-universe
coverage and market reconnect/backfill/warm-start, canonical order/fill ledger
integration, trusted adaptive approval/health adapters, forecast/replay producers,
VELUM experiment replay, independent forward/holdout evidence, authorized live
Command real-data visual QA and latency, runtime kill switches and rollback drill.
The public shadow domain was rejected by automatic approval review and remains
absent. Private operational inspection continues without exposing the service.

The first #425 hosted test run caught a test-only settings alias mistake: the
temporary path was ignored and the runner attempted `/data`. The test now uses
the explicit `RHEN_MARKET_STREAM_CHECKPOINT_PATH` alias and asserts that path.
Live inspection additionally identified the initial supervisor audit overwriting
an uninitialized checkpoint. Saving a state without session context is now a
no-op, covered by the same recovery test. These failures are fixed rather than
waived; the corrected head must pass hosted checks before merge.


### Slice 20 validation boundary

Local guarded backend checks: 1,033 passed, 10 environment-specific skips;
Command: 52 tests passed, TypeScript checks and full gated production build passed.
New tests exercise policy restart/configuration/session rejection, source correction
availability and pruning, actual-account baseline recovery, canonical release
lineage and under-sampled/mismatched evidence rejection, forecast expiry/reference
and degraded states, immediate disconnect telemetry and private GET-only champion
reconciliation. Local tests are engineering checks, not independent forward,
holdout, runtime latency or promotion evidence. Hosted CI and pinned deployment
results must be recorded separately after they actually occur.
