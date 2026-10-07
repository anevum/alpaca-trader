# RHEN 4.4 pre-crossover implementation status

Canonical workload: ANEVUM.RHEN.PACKAGE.2026-10-06.003.V4-4-VISUAL-INTELLIGENCE.
All 34 package checksums verified. The complete package is retained under `package/`.

Latest operational continuation (2026-10-07): champion main is `a9385e3`,
still 4.3.2; Command #193 merged as `e4772ac` and deployed Worker
`fcd9a022-5a9c-4dd3-9d02-3f01fac4f710` with hosted verify/deploy/audit and
public desktop/mobile QA passing. The explicitly authorized public observer
domain is `https://rhen44-shadow-production.up.railway.app`. Status/history
reject anonymous requests (401), malformed bearer fails (401), anonymous
WebSocket handshakes fail (403), and broker routes remain absent (404).
Command's authenticated stream/history connection is deployed. Authenticated
browser visual/replay/reconnect/latency acceptance remains unpassed: the cloud
browser sign-in reports a Cloudflare verification error, and the user cannot
complete that handoff on mobile. Connected GitHub/Railway access is being used
for implementation and runtime validation; it does not provide a Command session.

The observations below retain their original chronology. Earlier statements
about a missing domain or disabled Command UI are superseded by this continuation.

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
| 2: event-driven market observation | Implemented; isolated runtime deployed; overnight subscription diagnosis/fix merged | Verify actual accepted channels and source coverage, reconnect and shadow equivalence |
| 3: broker trade_updates | Durable shadow inbox/projection implemented | Canonical order/fill ledger reconciliation integration; never silently replace 4.3 reconciliation |
| 4: warm start | Checkpoints/history bootstrap implemented; local restart test passes | Runtime restart with real positions/orders and gap/backfill evidence |
| 5: scanner/rejection | Durable bounded decisions/candidates, rejection rollups, deadline invalidation and source-valid symbol-hour accounting implemented; distinct-candidate/lineage/restart tests pass | Full risk/cost/asset mapping, canonical research export/completeness and runtime exposure attestation |
| 6: Command WebSocket | Protected route, unified router bridge, worker proxy and single client socket deployed to authenticated shadow | Authenticated end-to-end browser acceptance; live latency measurement |
| 7: core visuals | Candles/scanner/source volume/rolling VWAP/broker position and order levels/actual fill markers/window controls implemented, gated | Real-data screenshot QA; full server time-range history and strategy threshold overlays |
| 8: advanced visuals | Forecast contract/band/expiry, replay PIT and event-triggered read-only broker equity/cash series implemented | NOSTRA forecast producer wiring, normalized/drawdown/session performance, VELUM artifact/control wiring |
| 9: regular stream promotion | Intentionally not performed | Same-input strategy parity + real stream-vs-poll shadow equivalence; risk/engine data-source release |
| 10: four sessions | Existing calendar resolver reused; Basic gaps explicit | Live per-session observation, overnight asset eligibility refresh and halt provenance |
| 11: policy primitives | Library fingerprinting, immutable disabled/shadow snapshots, hysteresis/dwell/vetoes implemented | Profile-release approval registry/ASC-008 trusted activation integration; ACTIVE explicitly rejected |
| 12: NOSTRA wrapper | Reuses ASC-002 classifier with completed contiguous-bar cross section; PIT/missing/stale/fingerprint and durable decision lineage tested | Canonical research feature/schema parity and export; all-symbol missing/stale remains UNKNOWN |
| 13: Capital Governor | Counterfactual adapter reuses existing sizing and exposure caps on observed broker snapshots; evidence veto keeps notional zero | Approved evidence/health factors and governed shadow validation |
| 14: adaptive shadow | Runtime NOSTRA/policy lineage, persistent dwell/controller recovery and read-only champion health deployed; immutable execution values equal baseline | Canonical ASC approval adapters and research lineage export |
| 15: combined Command | Authenticated market/adaptive/account diagnostics/replay surface connected inside current Operate | Authenticated visual acceptance, full canonical telemetry/forecast/replay integration |
| 16: VELUM/GRAEN/ASC | Existing machinery preserved; no protected data accessed | Frozen experiments, no-lookahead replay, forward cohorts, untouched holdout results |
| 17-19: canary/session/assertive promotion | Intentionally disabled | All package promotion gates and lane/profile-specific evidence |

These are engineering artifacts and local tests, not live shadow evidence. No
stream equivalence, trading benefit, broker reconciliation change, active policy
approval, holdout pass, or full visual acceptance is claimed.

Later live observations below separately attest broker socket/account-read health;
they do not establish market coverage, canonical ledger parity or promotion.

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

### Actual runtime reconciliation, 2026-10-07 02:24 UTC

Backend #422 and #423 passed hosted test/Postgres/inventory checks and merged.
Command #190 merged as `dc1e643330927145de81965064c2897b9f7921ac`; its main
audit/verify/deploy checks all succeeded. Command live-stream feature gates remain
disabled, so this deployment is not a public live visual acceptance pass.

At 02:24 UTC, champion health reported source
`75436d3aa7d6c4596cb162d65c6ac4ca91eb6033`, deployment
`d14cfe27-d543-4e45-9dc3-b001c1d03f3a`, RHEN 4.3.2 and strategy
`LIVE-2026-09-25-003`, all three 4.4 flags false, observer absent, reconciliation
safe, no unresolved intents/unknown orders/untracked positions, and no module
failures. This is an observed reconciliation summary, not an exact protected
configuration freeze. Core effective storage was 732.83 MB against a 750 MB
shedding threshold; the observer uses its separate volume.

Private shadow telemetry at 02:21:45 UTC on
`f4894de35a7dfbe6256e5c3ea04ee781f89a80e4` attested authenticated broker
`trade_updates` HEALTHY and account snapshot quality LIVE, with no write authority.
The overnight market socket returned provider error 410, zero subscriptions,
zero bars and zero candidates. Alpaca defines 410 as an unsupported subscription
channel, not an entitlement denial. No 24-symbol market coverage was inferred.

Backend #424 passed hosted checks and merged as
`09452e46b20dac19d427d2790ffe3d09a5ed5281`. The isolated observer is pinned to
its feature commit `401759041b20b8a36cc668cbbc2ac3dd1774220d`, deployment
`b9219ca3-7ba7-438c-9e90-62653e7d8f4d` SUCCESS. It negotiates overnight
quotes/bars/updatedBars separately and preserves verified quotes when optional
bar channels return 410. Accepted and unavailable channels are explicitly
reported. Overnight historical reconstruction remains unavailable rather than
substituting delayed BOATS bars. Process success is not proof that this fix has
received live market events; actual coverage/restart evidence must follow.

At 02:25:30 UTC the pinned fix actually accepted `quotes` and `bars` for all
24 symbols; only `updatedBars` was unavailable. Telemetry contained 21 symbols
with observed quotes and 8 source bars, zero stream errors, zero evaluable
decisions/candidates/intents, and explicit STALE_BAR/STALE_QUOTE rejections.
This is accepted subscription coverage, not fresh/evaluable 24-symbol coverage.
At 02:26:44 UTC after an explicitly authorized isolated restart, market
subscriptions returned to 24, broker stream was HEALTHY and account quality LIVE.
No production restart was requested or performed directly.

That restart exposed an overnight warm-start gap: the branch skipped its local
checkpoint together with the unavailable historical endpoint. The follow-up
restores matching same-session/source completed checkpoint bars, while quotes
and subscriptions remain ephemeral and stale bars remain rejected. Restored bar
count is operational telemetry. Overnight source candles are marked DELAYED and
quote midpoints marked INDICATIVE; receipt time does not make delayed bars fresh.
The dedicated test passes; live checkpoint restoration must be attested after
deployment rather than inferred from unit tests.

The retained rollback branch `rollback/rhen-v4.3.2-0270563-20261007-0145` points
to exact known-good `0270563a613c57a959d1c6b93b97a01509a418df`. Broker-write
promotion, adaptive ACTIVE, options execution, shorts, additional leverage,
crypto and expanded 24/5 authority remain unavailable in the new implementation.


## Slice 20: policy recovery, bounded replay and release review (2026-10-07)

Implementation added in isolation: checksummed same-session/configuration policy
confirmation/dwell restoration; bounded source-availability SQLite history;
protected history reads; persisted broker-account sample baselines, sampled-peak
drawdown and actual-position exposure diagnostics; immediate operational socket
state publication; and private read-only champion health/configuration identity.
The health identity exposes only the existing scheduler protected-contract hash,
not a complete settings/configuration freeze. Stale, unhealthy or mismatched
champion reads cannot satisfy canonical health lineage. Baseline execution values
remain unchanged, evidence approval is still unavailable and ACTIVE remains blocked.

ASC-008 release review checks exact artifact/profile/version/configuration lineage,
independent forward-session floors, untouched non-overlapping holdout floors,
VELUM point-in-time and GRAEN frozen validation, every runtime gate, rollback and
authorization references. It is a pure review contract; authenticated canonical
artifact resolver/registry integration remains unfinished. Unit fixtures never
become validation evidence and eligibility never automatically activates execution.

NOSTRA return-forecast projection requires the canonical observed snapshot reference,
model/methodology versions, horizon/expiry and a supported authority state. Only
observed-reference and expected-terminal endpoints are projected; their connection
is labelled visual interpolation. No uncertainty bounds are invented. The adapter
is implemented but the canonical live forecast producer is not yet wired.

Command adds adaptive/capital observability, account diagnostics and bounded
source-availability replay with authenticated, gated reads. Replay shows actual
archived observations and explicitly does not establish a VELUM pass. These
visuals remain feature gated pending an authorized protected deployment path to
the isolated observer and browser/runtime validation. Cash-flow-adjusted strategy
returns remain unavailable. No polling replaces streaming as the primary live path.

Actual pre-deployment observation at 12:09 UTC: existing isolated b0c622 observer
accepted all 24 PREMARKET/IEX subscriptions (bars, quotes, updatedBars), broker
stream HEALTHY and account LIVE, with four source bars/eight quotes and zero
evaluable decisions, candidates or intents. Accepted subscriptions do not establish
fresh/evaluable coverage. Slice 20 clears stale overnight bootstrap error/count
when beginning a new-session bootstrap; real reconnect/restart attestations follow
deployment. All independent-session and untouched-holdout requirements remain
unpassed. Original 4.3 rollback commit/configuration boundaries remain preserved;
4.4 writes and future asset authorities remain disabled.


Slice 20 deployment attestation: #426 merged as fd6d917; isolated observer
116317279a4033c79d21d7c0f5fbb14b8919e855 is SUCCESS on Railway deployment
848f0d94-56e8-48a9-86b5-a5f3f1a279dc. At 12:39:25 UTC after an authorized
isolated restart, policy recovery was RESTORED_SHADOW_ONLY, retained archive
21 observations, all 24 IEX subscriptions returned, broker HEALTHY, account and
account diagnostics LIVE, no stream errors, and private champion health identity
matched the existing production protected-contract hash. Market remained WARMING
and candidate/evaluable/intent counts zero; this is recovery evidence, not a
coverage or promotion pass. Production remained healthy 4.3.2 with all three
4.4 gates false, no observer and reconciliation safe. Retained rollback ref
0270563a613c57a959d1c6b93b97a01509a418df independently reverified.

Authority hardening requires explicit false options-write, expanded-session and
expanded-leverage envelope fields, rejects unknown policy-value fields, rejects
non-object checkpoint corruption without mutating state, and treats malformed or
stale private champion lineage as unavailable. No new execution authority or
protected activation path is introduced. Canonical integration/promotion remains
incomplete. Command #191 uploaded successfully but production desktop scroll QA
failed its precondition; #192 corrects setup while preserving the outcome assertion.

## Closure realignment: broad discovery versus narrow stream (2026-10-07)

Current 4.3.2 production discovery scans a 100-symbol active universe while the
Basic-plan 4.4 market WebSocket intentionally observes a bounded 24-symbol set.
These are separate layers, not competing universe definitions. The release truth
now reports `BROAD_DISCOVERY_NARROW_STREAM` and fails promotion closed with
`discovery_stream_hotset_rotation_incomplete` whenever broad discovery exceeds
the stream capacity. A future crossover must preserve the broad discovery pool
and rotate a bounded high-priority stream hot set; promoting the current static
24-symbol observer as the entire live opportunity universe is forbidden.

The Command Cloudflare Access verifier also now caches validated JWKS for five
minutes, permits bounded stale-key use for transient fetch failures, refreshes on
unknown key IDs, and converts network/JSON failures into controlled Command 503
errors. This addresses the observed single production `httpx.ReadTimeout`
without weakening token signature, issuer, audience, expiry or email checks.

