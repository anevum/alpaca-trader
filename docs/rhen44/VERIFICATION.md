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
