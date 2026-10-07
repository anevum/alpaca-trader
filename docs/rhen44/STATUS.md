# RHEN 4.4 pre-crossover implementation status

Canonical workload: ANEVUM.RHEN.PACKAGE.2026-10-06.003.V4-4-VISUAL-INTELLIGENCE.
All 34 package checksums verified. The complete package is retained under `package/`.

Production remains RHEN 4.3.2, source `0270563a613c57a959d1c6b93b97a01509a418df`,
Railway deployment `4ec33727-f98b-4ffe-97f8-1ce30a2e95e2` SUCCESS.
Command source baseline is `bd4999cbd98e6c209c451a183e9f965e9682ebdf`; GitHub
audit/deploy/verify checks succeeded. No 4.4 change has been merged or deployed to
production. Branch merging triggers automatic deployment and is **not** a safe
substitute for a separately verified staging release.

`baseline-observation.json` records actual read-only health evidence. It is an
observed rollback candidate, **not** a complete configuration freeze: Railway
OAuth withholds variable values and protected Command/account endpoints require
authorization not available in this execution environment. IREN's configuration
identity is not mislabeled as the trading configuration fingerprint.

## Actual-state discrepancies

- Current 4.3 logs report overnight `execution_authorized: True`. The package
  assumes overnight is observation-only. User instructions require preserving
  4.3 unchanged; no live variable, lane, or broker authority was altered. New 4.4
  observation paths always report `entry_authority: false`.
- Live regular scanner reports 20 configured symbols. The 4.4 observer uses the
  existing extended observation universe independently and validates capacity;
  it does not widen the live execution universe.
- Core effective storage is approaching its 750 MB shedding threshold. New
  checkpoints retain at most 120 bars/symbol, 1440 summary buckets, and bounded
  delivered broker observations. Quotes are never durably written per tick.
- No actual Basic/Plus entitlement or 24-symbol stream coverage has been attested.
  New routing defaults to Basic; paid adapters exist but runtime paid routing
  remains unavailable until entitlement verification is implemented and attested.

## Requirement status

| Slice / package capability | Current status | Remaining integration or evidence |
|---|---|---|
| 0: freeze 4.3 | Partially verified, preserved | Exact trading fingerprints, account/order/position reads, migration head |
| 1: stream/feed primitives | Implemented, tested, gated | Actual entitlement attestation |
| 2: event-driven market observation | Implemented, tested locally, gated | Live Alpaca auth, coverage/reconnect/shadow run |
| 3: broker trade_updates | Durable shadow inbox/projection implemented | Canonical order/fill ledger reconciliation integration; never silently replace 4.3 reconciliation |
| 4: warm start | Checkpoints/history bootstrap implemented; local restart test passes | Runtime restart with real positions/orders and gap/backfill evidence |
| 5: scanner/rejection | Bounded classifications/minute diagnostics implemented | Full risk/cost/asset reason mapping, session/day aggregates, evaluable-symbol-hour exposure, candidate durability |
| 6: Command WebSocket | Protected route, unified router bridge, worker proxy and single client socket implemented | Authorized end-to-end Cloudflare/Railway proof; live latency measurement |
| 7: core visuals | Candles/scanner/sparklines/feed strip/broker tape implemented, gated | Real-data screenshot QA; position/stop/target overlays, volume/time-range controls |
| 8: advanced visuals | Forecast contract/band/expiry and replay PIT primitives implemented | NOSTRA forecast producer wiring, account/performance series, VELUM artifact/control wiring |
| 9: regular stream promotion | Intentionally not performed | Same-input strategy parity + real stream-vs-poll shadow equivalence; risk/engine data-source release |
| 10: four sessions | Existing calendar resolver reused; Basic gaps explicit | Live per-session observation, overnight asset eligibility refresh and halt provenance |
| 11: policy primitives | Library fingerprinting, immutable disabled/shadow snapshots, hysteresis/dwell/vetoes implemented | Profile-release approval registry/ASC-008 trusted activation integration; ACTIVE explicitly rejected |
| 12: NOSTRA wrapper | Reuses ASC-002 classifier; PIT/missing/stale/fingerprint tested | Canonical feature producer and runtime observation persistence |
| 13: Capital Governor | Pure no-margin bounded counterfactual expression implemented | Production sizing binding-cap adapters and governed shadow evidence |
| 14: adaptive shadow | Primitives tested; execution values equal baseline | Durable runtime counterfactual lineage and recorded entry-policy provenance |
| 15: combined Command | Separate gated market surface inside current Operate | Adaptive panels and full canonical telemetry coverage |
| 16: VELUM/GRAEN/ASC | Existing machinery preserved; no protected data accessed | Frozen experiments, no-lookahead replay, forward cohorts, untouched holdout results |
| 17-19: canary/session/assertive promotion | Intentionally disabled | All package promotion gates and lane/profile-specific evidence |

These are engineering artifacts and local tests, not live shadow evidence. No
stream equivalence, trading benefit, broker reconciliation change, active policy
approval, holdout pass, or full visual acceptance is claimed.

## Authority and configuration

Production defaults introduced by this branch:

```
RHEN_MARKET_STREAM_ENABLED=false
RHEN_BROKER_STREAM_SHADOW_ENABLED=false
COMMAND_LIVE_STREAM_ENABLED=false
COMMAND_LIVE_FLUSH_MS=125
RHEN_MARKET_STREAM_CHECKPOINT_PATH=/data/rhen44-shadow-checkpoint.db
```

The package example's enabled stream flags are deliberately changed to disabled
defaults during development. The new observer consumes market events immediately;
the 30-second task is calendar/checkpoint audit only. Command flush is 125 ms,
critical execution events bypass coalescing. Local fixtures never become live data.

There is no configuration switch to promote this branch's market data or adaptive
policy to execution. `AdaptivePolicyController.observe(mode="active")` fails.
Capital/NOSTRA modules have no broker imports. Options, shorting, crypto, margin,
and blanket extended execution have no new authority path.

## Next authorized implementation steps

1. Obtain existing authenticated read paths; finish the exact 4.3 configuration
   and broker baseline without changing it.
2. Deploy this branch to an isolated environment of the existing service only
   after all ordinary/extended execution flags are explicitly disabled before
   startup. Do not clone an armed environment and then disarm it after launch.
3. Confirm Alpaca connection capacity: observation sockets must not displace the
   champion or consume an unverified entitlement. Run initial live shadow with
   canonical durable evidence and no broker-write path.
4. Complete the partial slices in the table, using existing risk, execution,
   ledger, GRAEN, NOSTRA, and VELUM abstractions.
5. Freeze profile/session lineage and collect the package's independent sessions,
   candidates, differentials, coverage, utility, sign-test, and untouched holdout.
6. Perform the full runtime/UI/recovery/kill-switch/rollback gates. Promotion must
   not proceed on local tests alone or under-sampled evidence.

## Rollback

No production rollback is currently necessary: 4.3 is untouched. In isolated
staging, stop the observer or disable its three flags, reconnect/reconcile using
the known 4.3 path, and verify no new broker intents. Before a later crossover,
preserve exact 4.3 trading configuration and deployment identity separately. The
observed deployment is rollback/redeploy capable; actual rollback execution was
not tested on production because that would interrupt the champion.
