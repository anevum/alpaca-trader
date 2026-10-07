# Capital Governor

## Purpose

Use more of available capital when the opportunity/regime is strong and less when evidence is weak, while keeping all existing account-level hard limits authoritative.

## Existing safety foundation

`app/sizing.py` already derives notional from:

- risk-per-trade budget;
- stop distance;
- remaining gross exposure;
- per-position equity exposure;
- portfolio stop-risk budget;
- cash;
- hard order/position ceilings.

`app/capital_allocator.py` already adds expected-net-edge/confidence ranking and bounded opportunity multipliers.

4.4 extends those primitives rather than replacing them.

## Proposed module

`app/capital_governor.py`

## Core expression

For an already-qualified opportunity:

```text
base_safe_notional = existing calculate_entry_notional(...)

policy_notional = base_safe_notional
                * profile.allocation_multiplier
                * regime_confidence_factor
                * health_factor
                * drawdown_factor

final_notional = min(
    policy_notional,
    opportunity/risk caps from 4.3,
    available cash cap,
    existing hard order cap,
    existing hard position cap,
    existing hard gross-exposure cap,
    existing hard portfolio stop-risk cap,
    liquidity/correlation caps
)
```

No multiplier may make `final_notional` exceed what the existing hard-risk path would allow.

## Capital utilization

Profiles express a fraction of the existing hard gross-exposure envelope. Example:

- defensive: 40-55% of allowed hard gross exposure;
- normal: 65-80%;
- assertive: up to 100% of the already-approved hard envelope.

This improves utilization without enabling margin/leverage.

## Risk throttle

Define:

```text
risk_throttle = min(regime_factor, health_factor, drawdown_factor, evidence_factor)
```

Each factor is in [0, 1]. Therefore bad evidence can only reduce the amount deployed.

Potential starting functions for shadow evaluation:

- regime factor: confidence-weighted by profile fit;
- health factor: 1.0 healthy, 0.75 watch, 0.4 degraded, 0 blocked;
- drawdown factor: monotonic decrease as intraday drawdown approaches the daily-loss circuit breaker;
- evidence factor: decrease when cost-model sample or forecast calibration is weak.

Exact functions must be frozen before validation.

## Buying power

4.4 optimizes use of actual available capital but does not authorize borrowed buying power. Add an explicit policy field:

`allow_margin=false`

When false, cash remains an independent cap even if Alpaca reports higher buying power.

Margin/leverage is a later protected release and must not be smuggled in as an adaptive multiplier.

## Exit adaptation

Profile-specific exits may include validated values for:

- target multiplier;
- stop multiplier, always bounded by an approved maximum stop;
- max hold duration;
- re-entry cooldown;
- profit-protect activation/retention;
- thesis-failure sensitivity.

A longer hold is not automatically safer or more profitable. Each exit profile must be replayed and shadow-validated with the same no-lookahead lineage as the entry policy.

## Drawdown behavior

Aggressiveness upgrades are forbidden while:

- daily P&L is below a configured recovery threshold;
- the loss-streak cooldown is active;
- portfolio stop-risk utilization is elevated;
- Strategy Health edge/execution/evidence dimensions are degraded;
- account reconciliation is not safe.

A safety downgrade can happen immediately even if the profile dwell timer has not elapsed.
