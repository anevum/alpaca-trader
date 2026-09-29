# ASC-005 Counterfactual Parameter Lab

Status: SHADOW / RESEARCH ONLY

ASC-005 evaluates small, frozen one-parameter changes against RHEN's canonical
candidate forward outcomes.

It deliberately does not claim that rejected candidates were executable trades.

## Question answered

For one gate at a time:

If this gate alone had been slightly different, while every other observed
gate remained fixed, did the candidates added or removed by that change have
better forward outcomes after explicit transaction costs?

That is a screening question, not a portfolio backtest.

## Initial parameters

- min_momentum_pct
- min_vwap_edge_pct
- min_confirmations
- max_vwap_extension_pct

Risk, sizing, stops, portfolio exposure, live authorization and broker settings
are excluded.

## Gate isolation

Every candidate must provide an explicit other_gates_passed value representing
all non-target gates.

If this cannot be reconstructed, that candidate is excluded.

ASC-005 never assumes that a rejected candidate failed only the gate under test.

## Frozen search

The search grid is generated before outcomes are evaluated from the declared
adaptive bounds and daily or weekly step budgets.

The grid is recorded in a Search Ledger with the hypothesis family, target
parameter, current value, candidate values, horizon, transaction cost
assumption, comparison count, multiplicity method, dependence method,
selection rule, sessions observed, and selected result if any.

## Dependence handling

Candidate rows inside the same session are not treated as independent
experiments.

Affected-candidate effects are first aggregated by session. Cross-session sign
consistency is then evaluated using an exact two-sided sign test.

## Multiple testing

All alternatives in the frozen parameter grid are counted as comparisons.

The session-level sign-test probability is Bonferroni adjusted by the number of
alternatives tested. This is intentionally conservative for v1.

## Screening effect

For a loosened gate, a newly admitted candidate contributes its forward return
minus round-trip cost.

For a tightened gate, removing a candidate contributes the negative of its
net forward return.

Expected improvement therefore means estimated improvement in the gate's
screening quality. It does not mean realized portfolio expectancy.

## Minimum research-validity gate

A counterfactual cannot pass the ASC-005 research-validity screen unless all of
the following hold:

- at least 5 independent sessions;
- at least 30 affected candidates;
- at least 95 percent forward-outcome coverage among affected candidates;
- at least 60 percent positive non-zero session effects;
- Bonferroni-adjusted exact sign-test p <= 0.10;
- average net screening effect >= 1 basis point;
- an explicit non-negative round-trip cost assumption is supplied.

Failure does not reject a parameter permanently. It means the evidence is not
mature enough for ASC-004 proposal generation.

This is not production promotion. Any resulting ASC-004 proposal still
requires separate authorization.

## Safety boundary

ASC-005 has no broker access, execution authority, risk or sizing authority,
production configuration mutation, or automatic promotion authority.

It consumes completed post-event evidence only.
