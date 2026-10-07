# Local verification, 2026-10-07 UTC

Continuation: freshness/coverage slice

- Full guarded local suite: **1050 passed, 8 skipped**. Existing deprecation
  warnings only. Tests ran with all execution/arming/4.4 stream flags disabled.
- Required `scripts.staging_check` after scheduling hardening: **1050 passed, 10 skipped**; two additional
  retired/fixture-specific cases are skipped by that staging guard.
- Nine new tests exercise exact quote/bar deadline clipping; quiet-symbol
  invalidation without any market event; clock reversal; disconnect/session
  separation; restart downtime exclusion; atomic distinct-candidate counting
  with configuration lineage; and no synthetic market/decision emission.
- A buffered market burst test verifies cooperative scheduling preserves all
  40 ordered events while giving other tasks a turn before the burst ends.
  Completed-bar gap checks cache exact source timestamp tuples, including
  intermediate corrections; freshness thresholds and formulas are unchanged.
- Exposure retains 14 session days, at most 5000 lineage rows/table and current
  lineage in memory. SQLite retains the existing 32 MiB ceiling. Exposure is
  derived prerequisite availability, not an independently validated session.
- Frontend 53 tests and TypeScript check passed. A local fixture timer threshold
  is not used in runtime; real source limits remain 45s quotes / 120s bars.
- Deployment and actual runtime observations must be recorded separately. The
  champion must not auto-redeploy for this slice during its live trading session;
  backend work is committed to an isolated branch/PR and only the shadow service
  may deploy it. Command can deploy independently without changing trading.

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

Authority/recovery hardening follow-up: guarded suite 1,041 passed, 10 skipped.
Added explicit future-authority/unknown-policy-field rejection, non-object corrupt
checkpoint rejection without controller mutation, and stale/malformed/mismatched
private champion-read rejection. Actual slice 20 restart observation is recorded
in STATUS.md and #426; these checks do not claim canonical promotion eligibility.


### 2026-10-07 isolated asset eligibility and replay revision slice

Canonical package: all 34 checksums verified and all package files read before
implementation. Baseline repository `edd089b`; no production flags or source
changed. Guarded staging suite: **1,068 passed, 10 skipped** (5 existing
warnings). Unchanged Command unit suite: **53 passed**. New cases exercise fresh,
stale and future-dated persisted asset evidence, missing capabilities, equity-only
identity, overnight tradability/halt rejection, malformed refresh retention,
bounded GET-only broker reads, scanner vetoes, corrected-bar limit semantics,
point-in-time availability and simultaneous distinct fills.

Actual production health: 4.3.2 armed/live, reconciliation safe, all 4.4 production
stream/Command gates false. Exact trading fingerprint recorded in STATUS.md;
full variable-value export remains withheld by Railway OAuth. Authenticated
Command 4.3 panels were observed. The 4.4 section had no snapshot and reported
disconnected/stale transport; direct history navigation was browser-client blocked.
This is an unpassed browser validation, not proof of a server or authentication
failure. No token/cookie was extracted or authentication weakened.

Asset support remains observation-only and does not authorize overnight execution,
options, crypto, short selling or leverage. Separate shadow asset facts and the
new configuration lineage need actual deployment/restart observations. Replay
history is bounded source evidence, not VELUM validation. Independent forward,
holdout, trusted promotion adapters, complete research outcomes, >=95% coverage,
live browser latency, kill-switch/rollback drills and canonical ledger integration
remain unpassed. The champion and original rollback commit remain preserved.


Follow-up shadow observation: hosted CI/run 37655113671 and runtime audit/run
37655113668 passed. Observer-only pinned deployment `10e4de23-d25e-4880-8637-f638da558dbb`
on `d308fee` succeeded. Same-image restart recovered the prior asset source timestamp
`2026-10-07T16:50:43.017132Z` before the new GET audit completed, then re-attested all
24 assets; restored 2,831 bars; recovered policy state as RESTORED_SHADOW_ONLY;
and reconnected the broker/account observers. The initial new methodology correctly
rejected prior policy lineage. Coverage remains ~69%, below 95%; zero order intents.

Live max callback 645 ms / publisher lag 614 ms prompted a separate retention
optimization. Indexed oldest-first excess deletion preserves hard capacity and
atomic candidate accounting. The same local capacity-filled SQLite fixture's median
insert decreased from 56.77 ms to 0.075 ms (max 57.91 ms to 0.518 ms). The fixture
is synthetic engineering data; these timings do not attest production latency.
A new capacity/restart dedup test is included. Guarded suite now 1,069 passed,
10 skipped. Follow-up hosted CI and pinned runtime telemetry must still be observed.

Current known-good production commit additionally preserved at
`rollback/rhen-v4.3.2-edd089b-20261007-1655`; original `0270563` rollback ref is unchanged.
No production service restart, source, config, trading path or release change occurred.


Retention follow-up runtime: `3ce688c` / Railway `b6fbfee1` SUCCESS. At
2026-10-07T16:58:01.585396Z, 32,174 market events processed, max callback 63 ms,
max publisher lag 68 ms, 24 assets/subscriptions, broker HEALTHY/account LIVE,
policy RESTORED_SHADOW_ONLY, 2,831 restored bars, coverage ~69.5% and zero order
intents. This short observation window is not end-to-end browser latency evidence.
Production still has the original runtime instance `27c3f39c-a2c7-44e9-973e-3449a471c13b`,
start timestamp 16:21:13Z, edd089b / b7676403 and the same protected fingerprint.

Latest-code hosted CI remains UNRUN: GitHub connector branch-ref/PR mutations
returned repeated GraphQL/internal errors, although commit/tree/branch creation
and reads worked. Both code commits are branch-preserved; #429 is still draft
on d308fee. Main is untouched. The JSON continuation records this external blocker
without conflating earlier green hosted checks with the latest head.


### 13:14 ET bootstrap and observed execution recovery continuation

GitHub mutation blocker resolved: #430 open; hosted CI 37656326159 and audit
37656326175 passed on full continuation 898738a. #429 superseded and closed.
New guarded backend checks: 1,070 passed / 10 skipped. Command: 54 passed;
TypeScript and complete Vite/Worker build pass. Replay source test changed to
assert preserved original source plus separate replay_source metadata rather
than conflating observed Alpaca evidence with VELUM transport. Recovery tests
cover simultaneous distinct fills, unavailable/future records and universe scope.
The authenticated bootstrap route rejects anonymous reads; publisher bootstrap
is read-only and cannot mutate delivery sequence or subscribe a client. Client
bootstrap stays stale and cannot overwrite an existing stream generation.

No unit/build result passes live stream or production crossover requirements.


### 13:24 ET missed-expiry fix and isolated deployment

Current verified continuation (2026-10-07, 13:24 ET): isolated backend commit
`b23f9d09c81e360ff9898f168534f4b538f65acc` passed hosted CI 37658279361 and
runtime audit 37658279678. Railway shadow deployment
`83b95140-d2c6-42ab-8c6d-221e08132aff` succeeded. It recovered 2,840 bars,
restored shadow-only policy, subscribed 24 symbols and reconnected broker
updates. Anonymous bootstrap was HTTP 401, counted in private transport
telemetry; shadow health was HTTP 200. Broker-write authority remains false.

Hosted CI caught a missed source-expiry scheduling race on the preceding
35ea9cb revision. Fixed by publishing already elapsed quote/bar/asset expiry
before selecting future deadlines, without market polling or invented events.
Deterministic delayed-start regression and original deadline test pass.
Current local staging suite: 1,071 passed / 10 skipped; dedicated 4.4: 107 passed.
No source freshness threshold was relaxed.

Command bootstrap commit `932e9d4` is in PR #197. Its local 54 tests, TypeScript
and complete production build pass. Hosted verification is still running browser
capture; it has not been merged or deployed at this observation. Live authenticated
Command still has frozen/unavailable 4.4 data. UI acceptance remains unpassed.

At 17:22-17:23 UTC, champion health reads returned HTTP 503 and production
logs showed upstream ReadTimeout plus LEDGER_RECONCILE_ERROR. Railway still
reported original 4.3.2 commit/deployment SUCCESS. Current reconciliation safety
cannot be inferred from the earlier healthy snapshot. No champion code/config
change or restart was performed. The shadow also reports champion health lineage
UNAVAILABLE; this is an additional crossover blocker.

Observed shadow source coverage is approximately 69%, below the required 95%.
Signal counters are not complete risk-validated research candidates. No independent
forward/holdout gate has passed. Hotset rotation, canonical ledger integration,
trusted approvals, forecast producer and full runtime acceptance remain incomplete.
Options writes, short equities, expanded leverage, crypto and blanket 24/5 4.4
execution remain disabled. This continuation is not a complete 4.4 release.


Actual restart observation at 17:24:31 UTC: same isolated deployment recovered
2,842 bars, policy RESTORED_SHADOW_ONLY, broker HEALTHY and 24 subscriptions.
Broker-write authority false; transport counters reset after container restart.
No full promotion gate is inferred from this recovery exercise.

### 13:30 ET degraded champion reconciliation

The aggregate production health HTTP 503 contains an execution body. That body
confirms the exact preserved source, runtime instance and configuration identity,
armed 4.3 execution and **reconciliation_safe=false**. Aggregate module failures
are graen/nostra and core error ReadTimeout. This supersedes the earlier
UNATTESTED reconciliation reading; current production entry safety is blocked.

The isolated health adapter now retains a valid execution observation inside an
aggregate 503, labels the read DEGRADED_READ and preserves source identity while
keeping runtime_ok/reconciliation_safe false. It rejects an absent/failed execution
body. This does not change production health, reconciliation or authority.
Local guarded checks: 1,075 passed / 10 skipped; dedicated 4.4: 111 passed.
Deployment of this follow-up is separate from the b23f9d0 restart evidence above.

### 13:34 ET deployment and hosted Command capture fix

Backend 980c325 passed hosted CI 37659677774 and audit 37659677647, and
shadow deployment 613a9fc0-4c78-4657-9b05-eea45d1f516b is SUCCESS.
The earlier b23f9d0 isolated restart validation remains separately identified.
No champion deployment, configuration or process restart was performed.

Command hosted run 37657553629 was cancelled at job timeout: the duplicate
Chrome CLI process hung before the first release screenshot. Tests, build,
privacy/route probes and bounded browser runtime/visual checks had passed.
Companion b67ee12 now captures identical release screenshots and rendered
marker checks through the existing bounded hydrated CDP visual runner, retaining
14 desktop/mobile images and seven rendered DOM artifacts. New hosted run
37659961655 is in progress. PR #197 remains unmerged, production UI unchanged.
The capture fix does not bypass the upstream healthy-runtime or visual gates.

### 13:35 ET final observed checkpoint

Shadow 980c325 / deployment 613a9fc0 was observed at 17:33:54 UTC with
HEALTHY broker, 24 subscriptions, 2,843 recovered bars, RESTORED_SHADOW_ONLY
policy, DEGRADED_READ champion lineage and broker-write authority false.
Observed prerequisite coverage 0.711571380577331 remains below 0.95.

Command retry 37659961655 FAILED: tests/build passed, then the live public-feed
probe returned HTTP 502 at /api/public/trading/live. Browser capture changes
were not reached in this retry, so their hosted acceptance remains unverified.
The prior browser CLI hang was addressed in source; no check was removed or
relaxed. PR #197 remains unmerged and production Command is unchanged.
This is an observed upstream resource/runtime blocker, not proof of a client
bootstrap defect. No new market fixtures or authority were enabled.

Backend code and documentation remain isolated in draft #430. Production
4.3.2 still has the same source, configuration fingerprint and runtime instance.
Its current reconciliation flag is false; no crossover was attempted. This is a
responsible partial 4.4 implementation checkpoint, not definition-of-done or
promotion completion. Canonical ledger bridge, discovery hotset rotation, trusted
adaptive approvals, forecast producers, VELUM replay and independent forward/
holdout/visual runtime acceptance remain explicit unfinished work.
