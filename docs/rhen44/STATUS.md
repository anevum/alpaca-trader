# RHEN 4.4 pre-crossover implementation status

Canonical workload: ANEVUM.RHEN.PACKAGE.2026-10-06.003.V4-4-VISUAL-INTELLIGENCE.
All 34 package checksums verified. The complete package is retained under `package/`.

Initial production baseline: RHEN 4.3.2, source `0270563a613c57a959d1c6b93b97a01509a418df`,
Railway deployment `4ec33727-f98b-4ffe-97f8-1ce30a2e95e2` SUCCESS.
Command source baseline is `bd4999cbd98e6c209c451a183e9f965e9682ebdf`; GitHub
audit/deploy/verify checks succeeded. Backend PR #419 subsequently merged as
`acf324e1ebb3be04b1c87e1d23dd294ca7a500f5`; Command PR #189 merged as
`de2419937edca46fdcf230b2407092348a156f43`, explicitly authorized by the user.
Their automatic deployments contain disabled 4.4 foundations and preserve the
4.3 trading implementation. Deployment completion must be observed separately.
A merged commit is not evidence of 4.4 validation or promotion.

`execution.body.rhen44` in unified health reports configured flags, observer
availability, incomplete implementation and promotion blockers. No flag grants
4.4 broker-write authority or active adaptive policy. The request to make 4.4
fully live is recorded as the target; unmet package gates remain required.
Release metadata remains 4.3.2 until a validated behavioral crossover.

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
| 5: scanner/rejection | Durable bounded decisions/candidates, target-session/day rollups and champion check mapping implemented; quote/reconnect dedup tested | Full risk/cost/asset mapping, evaluable-symbol-hour exposure, canonical research export/completeness |
| 6: Command WebSocket | Protected route, unified router bridge, worker proxy and single client socket implemented | Authorized end-to-end Cloudflare/Railway proof; live latency measurement |
| 7: core visuals | Candles/scanner/source volume/rolling VWAP/broker position and order levels/actual fill markers/window controls implemented, gated | Real-data screenshot QA; full server time-range history and strategy threshold overlays |
| 8: advanced visuals | Forecast contract/band/expiry, replay PIT and event-triggered read-only broker equity/cash series implemented | NOSTRA forecast producer wiring, normalized/drawdown/session performance, VELUM artifact/control wiring |
| 9: regular stream promotion | Intentionally not performed | Same-input strategy parity + real stream-vs-poll shadow equivalence; risk/engine data-source release |
| 10: four sessions | Existing calendar resolver reused; Basic gaps explicit | Live per-session observation, overnight asset eligibility refresh and halt provenance |
| 11: policy primitives | Library fingerprinting, immutable disabled/shadow snapshots, hysteresis/dwell/vetoes implemented | Profile-release approval registry/ASC-008 trusted activation integration; ACTIVE explicitly rejected |
| 12: NOSTRA wrapper | Reuses ASC-002 classifier with completed contiguous-bar cross section; PIT/missing/stale/fingerprint and durable decision lineage tested | Canonical research feature/schema parity and export; all-symbol missing/stale remains UNKNOWN |
| 13: Capital Governor | Counterfactual adapter reuses existing sizing and exposure caps on observed broker snapshots; evidence veto keeps notional zero | Approved evidence/health factors and governed shadow validation |
| 14: adaptive shadow | Runtime NOSTRA/policy snapshot lineage persisted with decisions; immutable execution values equal baseline; missing canonical health vetoes entries | Restart-persistent dwell/controller state, canonical health/ASC approval adapters, research lineage export |
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

## Isolated observer preparation (2026-10-07 UTC)

Created `rhen44-shadow` service `8a587e3d-2464-4bb7-9507-3b3321c8d121` in
the existing Railway project, separately from champion service `f933a669-8591-4233-8510-e0db1548e463`.
It has its own 100 MB volume `8a57da9f-dad9-400c-944d-8335ec0bd295` and a
dedicated `Dockerfile.rhen44-shadow` / `app.market_fabric.staging:create_app` entrypoint.
No source was attached before execution/arming flags were explicitly disabled.
Only whitelisted strategy/risk inputs, Alpaca and Command authentication values
are referenced from `rhen`; no values were exposed or champion variables modified.
The entrypoint rejects armed settings and does not construct/import the main
execution engine, research services or canonical ledger writer. Its broker REST
client has only three allowlisted GET paths. There is no order route or method.

The isolated database has a 32 MiB page ceiling, retains 2000 decision payloads,
1000 candidate payloads, 40000 dedup identities and 14 target-session-day rollups.
Retention is bounded; these aggregates do not replace complete canonical ASC/GRAEN
validation lineage. No independent-session pass is inferred from a calendar bucket
or local fixture. Broker REST refresh is triggered by trade_updates, with a
120-second bootstrap/reconciliation/audit timeout.

Deployment/actual feed evidence must be recorded separately after startup. A
prepared service or healthy HTTP process alone does not prove live feed coverage.

### Deployment observation

Backend #421 merged as `f662d600d9b123b537381643a5a95f1a3c31f0d0` after
hosted test/Postgres/inventory checks passed. Champion deployment
`906f5e4a-c441-440c-afbf-0ee906e06bac` succeeded with the existing gates off.
Isolated observer is pinned to `ef3b26530df3d4b407132114972bb17d3f163d6b`,
deployment `f26cb87a-1576-4869-b1e9-f105b84ddc67` SUCCESS, one running replica,
separate volume, zero active warnings/criticals. This confirms process deployment,
not subscription coverage, Alpaca entitlement or shadow equivalence.

Automatic approval review rejected generating a public Railway domain for the
observer: isolated shadow authorization did not clearly authorize public exposure
of its authenticated observation surface, and private access is safer. No domain
was created and no indirect public proxy was substituted. Public Command-to-shadow
integration remains blocked pending explicit exposure authorization. A bounded
private operational log reports stream/coverage/reconstruction state without
credentials, account dollars, position/order/fill payloads or market prices.
