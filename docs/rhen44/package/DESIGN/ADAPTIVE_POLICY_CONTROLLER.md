# Adaptive Policy Controller

## Role

The controller converts current NOSTRA regime state and RHEN health/economic evidence into one immutable `EffectivePolicySnapshot` used by a trading decision cycle.

It never calls Alpaca and never mutates environment variables.

## Proposed new module

`app/adaptive_policy.py`

Primary objects:

- `PolicyProfile`
- `PolicyLibrary`
- `PolicyContext`
- `EffectivePolicySnapshot`
- `PolicyDecision`
- `AdaptivePolicyController`

## Required inputs

- timestamp and session segment;
- current NOSTRA regime distribution, confidence, UNKNOWN probability, familiarity;
- Strategy Health dimensions;
- execution-cost/opportunity-engine health;
- account equity/cash/risk reference equity;
- current day P&L/drawdown;
- loss-streak/cooldown state;
- existing exposure and portfolio stop-risk utilization;
- selected opportunity quality/net-edge distribution;
- current policy and time since transition;
- approved policy library fingerprint;
- protected configuration fingerprint.

## Controller states

- `DISABLED` — feature flag off; exact 4.3 behavior.
- `SHADOW` — compute and persist decisions but execution uses 4.3 baseline.
- `ACTIVE` — approved profiles may affect effective policy.
- `FALLBACK` — adaptive inputs invalid/stale; execution uses baseline or tighter safety policy.
- `DEFENSIVE_LOCK` — health/drawdown/evidence event forbids upgrades until recovery criteria pass.

## Profile order

Aggressiveness ordering used for hysteresis and vetoes:

```text
NO_TRADE < DEFENSIVE < FAST_SCALP < NORMAL < TREND_EXTEND < ASSERTIVE_TREND
```

This ordering is supervisory only; a profile may still have stricter settings on a particular dimension.

## Selection sequence

1. Validate feature flag, library fingerprint, and point-in-time inputs.
2. Build NOSTRA candidate profile from regime mapping.
3. Apply evidence-integrity veto.
4. Apply Strategy Health veto.
5. Apply account/drawdown/loss-streak throttle.
6. Apply session and execution-cost veto.
7. Confirm candidate profile is approved for the current strategy version and session.
8. Apply hysteresis/minimum dwell.
9. Safety downgrades may bypass dwell and occur immediately.
10. Upgrades require consecutive qualifying observations.
11. Resolve profile values against 4.3 baseline and hard limits.
12. Validate invariant `effective risk <= hard risk`.
13. Freeze one `EffectivePolicySnapshot` for the decision cycle.
14. Persist policy decision and attach snapshot ID to candidate/order events.

## Timing

Recommended starting values:

- controller refresh: 300 seconds;
- minimum profile dwell: 900 seconds;
- upgrade confirmation: 2 consecutive observations;
- downgrade confirmation: 1 observation for safety/health, 2 for normal regime changes;
- max NOSTRA state age: 600 seconds.

The system still evaluates market opportunities on its normal faster loop. The adaptive profile does not need to change on every scan.

## Initial deterministic regime mapping

This is a starting shadow mapping, not a profitability claim.

- `TREND_EXPANSION` or strong `BROAD_ADVANCE`, confidence >= 0.75, healthy evidence -> `ASSERTIVE_TREND` candidate.
- `LATE_SESSION_EXPANSION`, adequate time before cutoff, healthy trend persistence -> `TREND_EXTEND` candidate.
- `TREND_DECAY` or `ROTATION` -> `NORMAL` or `FAST_SCALP` depending volatility/cost.
- `MIDDAY_COMPRESSION` -> `DEFENSIVE` unless a separately validated compression policy exists.
- `HIGH_VOLATILITY` -> `DEFENSIVE`; `FAST_SCALP` only if spread/slippage and quality gates explicitly pass.
- `CHOP` -> `DEFENSIVE` or `NO_TRADE`.
- `BROAD_DECLINE` in the long-only system -> `DEFENSIVE` or `NO_TRADE`.
- `UNKNOWN`, low familiarity, high UNKNOWN probability, or stale data -> no upgrade; `BASELINE_LOCKED`/`DEFENSIVE`.

## Anti-thrashing

- never change profile twice inside minimum dwell unless the second move reduces risk;
- store the regime probability vector, not only the winning label;
- require a minimum probability margin over the current profile's supporting regime before upgrade;
- freeze policy snapshot per decision cycle so sizing and execution cannot see different policy values for the same candidate;
- position exits continue under the position's recorded entry policy plus current safety overlays; do not retroactively rewrite the thesis without an explicit exit rule.

## Baseline fallback

`BASELINE_LOCKED` is not a tunable profile. It means use the exact frozen 4.3 values from the post-4.3 baseline manifest.

This guarantees that controller failure cannot create a new trading behavior.
