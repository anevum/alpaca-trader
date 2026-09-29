# ASC-007 Fixed vs Adaptive Shadow Validation

Status: SHADOW / RESEARCH ONLY

ASC-007 tests the proposition that adaptation itself adds value.

The experiment compares:

- FIXED CHAMPION: a frozen baseline parameter configuration;
- ADAPTIVE SHADOW: the same baseline plus bounded ASC-004 proposals generated
  only from evidence available before the evaluation session.

## No-lookahead rule

A plan generated from session D may first be evaluated on D+1.

Same-session evaluation is forbidden.

Every result stores the source session, evaluation session, baseline
fingerprint, adaptive fingerprint, and shadow-plan ID.

## Candidate utility

Both configurations see the same canonical candidate population.

For each candidate with a complete forward outcome:

- selected candidate utility = forward return - frozen round-trip cost;
- unselected candidate utility = 0.

This makes NO TRADE explicit.

The session endpoint is:

adaptive mean candidate utility - fixed mean candidate utility.

This remains a strategy-screening experiment. It is not a fill simulation,
capital allocator, or portfolio backtest.

## Frozen validation floor

ASC-007 does not pass until there are at least:

- 10 independent evaluation sessions;
- 100 complete candidate outcomes;
- 30 decisions where adaptive and fixed selection differ;
- 95 percent forward-outcome coverage;
- 60 percent positive non-zero session deltas;
- exact two-sided session sign-test p <= 0.10;
- mean adaptive-minus-fixed candidate utility >= 0.5 basis point after costs.

All session results must share the same baseline fingerprint. A live champion
change therefore starts a new validation lineage rather than mixing regimes.

## Safety

A passing ASC-007 result is evidence that bounded adaptation deserves a
promotion review. It is not authorization to alter production.

ASC-007 has no broker, execution, risk, sizing, deployment, or automatic
promotion authority.
