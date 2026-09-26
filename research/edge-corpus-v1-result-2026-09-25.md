# Edge Corpus v1 — development elimination result

Date: 2026-09-25  
Status: **ALL FIVE FAMILIES REJECTED IN DEVELOPMENT**

## Execution integrity

The production trading service was not modified.

The intended isolated Railway research service could not be provisioned because
the workspace is at the free-plan resource limit. The existing shadow service
is pinned to an old source snapshot and could not be repointed through the
available Railway source API.

Historical data was therefore evaluated directly through Alpaca IEX in bounded
batches that stayed below the connector's 10,000-row cap. A five-symbol
full-week preflight returned 9,729 records with all symbols represented.

The same frozen Edge Discovery v1 family definitions, 15-minute horizon,
0.35% stop, 0.50% target, 15-minute same-family/symbol cooldown, and
base/moderate/stress friction assumptions were applied.

## Development window 1 — 2026-01-05 through 2026-01-23

Full frozen 36-symbol candidate panel plus SPY/QQQ/SMH context.

| Family | Events | Base expectancy | Base PF | Stress expectancy | Stress PF |
| --- | ---: | ---: | ---: | ---: | ---: |
| controlled_continuation | 567 | -0.0596% | 0.612 | -0.1860% | 0.221 |
| pullback_reclaim | 252 | -0.0549% | 0.686 | -0.1860% | 0.275 |
| compression_breakout | 258 | -0.0749% | 0.501 | -0.2043% | 0.156 |
| relative_strength_impulse | 107 | -0.0289% | 0.812 | -0.1649% | 0.305 |
| opening_breakout_retest | 0 | n/a | n/a | n/a | n/a |

The development gate requires worst-period expectancy >= -0.15% in every cost
scenario. The stress result in dev-01 alone permanently rejects:

- controlled_continuation;
- pullback_reclaim;
- compression_breakout;
- relative_strength_impulse.

Later windows cannot repair a minimum/worst-period failure.

## Opening-breakout-retest continuation

The only family not already killed by dev-01 was opening_breakout_retest.

Observed qualifying events:

- dev-01: 0
- dev-02 (2026-01-26 through 2026-02-13): 0
- dev-03 (2026-02-17 through 2026-03-06): 0
- dev-04 (2026-03-09 through 2026-03-27): 0

The development gate requires at least four positive development periods.
Only dev-05 and dev-06 remain. Therefore the maximum attainable positive-period
count is two. The family is mathematically unable to pass and is rejected
without opening more data.

## Terminal development verdict

Survivors: **none**

Rejected:

1. controlled_continuation — failed stress worst-period gate;
2. pullback_reclaim — failed stress worst-period gate;
3. compression_breakout — failed stress worst-period gate;
4. relative_strength_impulse — failed stress worst-period gate;
5. opening_breakout_retest — insufficient possible positive development
   periods after zero events in four consecutive windows.

Validation windows were not used.

The July holdout remains unopened.

No family is eligible for shadow promotion, live promotion, additional
concurrency, additional capital, or PR #28 scaling.

## Required next phase

The existing five-family slate is closed. Do not optimize or retune these
thresholds against the same corpus.

Proceed to genuinely new information families:

1. residual relative strength;
2. liquidity-shock reversion;
3. opening-gap structure;
4. sector leader-laggard;
5. multi-timescale breakout.

These must enter a new corpus/versioned hypothesis cycle rather than inheriting
a promotion path from Edge Discovery v1.
