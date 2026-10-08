# RHEN 4.4 Shadow Archive and Retirement — 2026-10-08

Session: `ANEVUM.OPS.PACKAGE.2026-10-08.001.LEAN-SYSTEMS-OVERHAUL`

## Decision

The permanent `rhen44-shadow` Railway compute service is retired from the lean production topology.

This is not a promotion or rejection of every 4.4 idea. It is a resource/topology decision: the deployed shadow explicitly reports itself incomplete and not promotion-eligible, while its source and bounded evidence are preserved for later selective research.

## Preserved source

Exact deployed shadow commit:

`acbdaa86b5994712de10f00cb50eadac75449fef`

Archive branch:

`archive/rhen44-shadow-deployed-20261008`

Latest shadow work branch head observed at retirement:

`ea32f64ac8413860c663fe004bfb7875d95e3bb1`

Archive branch:

`archive/rhen44-shadow-head-20261008`

The original work branch `work/rhen44-research-pipeline-20261007` is also retained.

## Preserved Railway evidence volume

Volume:

- id: `8a57da9f-dad9-400c-944d-8335ec0bd295`
- name: `rhen44-shadow-data`
- allocated size: 100 MB
- mount at retirement: `/data`

The volume is intentionally detached and kept when the shadow compute service is deleted. Do not delete the volume until any unique bounded observations needed for later 4.4 research have been exported or declared obsolete.

## Retirement evidence

Final observed shadow telemetry included:

- `execution_authority=false`
- `broker_orders_possible=false`
- `implementation_complete=false`
- `champion_behavior=4.3`
- `state=RESEARCH_OBSERVATION`
- `promotion_eligible=false`
- `adaptive_active_available=false`
- `archived_observation_count=12000`
- `broker_stream_state=HEALTHY`
- `canonical_ledger_parity.parity_complete=true`
- `scanner_coverage.evaluable_time_fraction=0.0` in the sampled retirement window

Promotion blockers reported by the shadow:

1. exact configuration and broker baseline unattested
2. live entitlement and stream coverage unattested
3. Discover/Review validation incomplete
4. runtime recovery and reconciliation validation incomplete
5. Command live visual acceptance incomplete
6. research and holdout gates incomplete

## Unique branch work retained for selective reuse

The archived branch contains unmerged work including:

- asset eligibility
- hotset rotation
- bounded retention
- profile release registry
- market-fabric runtime/staging changes
- command visual evidence/archive work
- 4.4 verification/status records
- dedicated 4.4 tests

None of this is silently promoted into the live execution path by the retirement.

## Reuse rule

If a 4.4 component is revisited, port it as a bounded experiment onto current `main`, test it against the current lean architecture, and promote only after current replay/forward evidence. Do not restart the archived shadow wholesale.
