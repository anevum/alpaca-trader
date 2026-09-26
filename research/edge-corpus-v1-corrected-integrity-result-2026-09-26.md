# Edge Corpus v1 — corrected-integrity development verdict

Date: 2026-09-26  
Status: **ALL FIVE FAMILIES REJECTED IN DEVELOPMENT**

This report supersedes `research/edge-corpus-v1-result-2026-09-25.md`.

## Why the rerun was required

The original corpus runner defined symbol completeness as raw one-minute bar
count divided by the densest symbol's raw bar count. Diagnostics proved that
metric was measuring IEX trade-print density rather than historical transport
completeness.

The replacement rule was adopted only after a direct false negative was
observed: symbols with all expected sessions present were being rejected for
having fewer IEX one-minute bars.

## Corrected corpus integrity

The corrected rule requires:

1. complete pagination with no page token remaining; and
2. regular-session data on every expected Alpaca trading-calendar session.

The old raw-bar ratio remains diagnostic only as `iex_bar_density_ratio`.

Results across all six development windows:

| Window | Pagination | Candidates complete | Missing confirmations | Missing sessions | Minimum IEX density ratio |
| --- | --- | ---: | --- | --- | ---: |
| dev-01 | complete | 36 / 36 | none | none | 0.636987 |
| dev-02 | complete | 36 / 36 | none | none | 0.616968 |
| dev-03 | complete | 36 / 36 | none | none | 0.574919 |
| dev-04 | complete | 36 / 36 | none | none | 0.498703 |
| dev-05 | complete | 36 / 36 | none | none | 0.557012 |
| dev-06 | complete | 36 / 36 | none | none | 0.460010 |

Shared development panel: **36 / 36 = 100%**.

The independent shared-panel requirement remains 80%, so this is a genuine pass
of corpus integrity, not a relaxed threshold.

The density diagnostic dropping to 46.0% while session integrity remains 100%
is further evidence that raw IEX bar density cannot serve as a completeness
metric.

## Frozen research settings

The rerun explicitly pinned the research settings instead of inheriting
mutable Railway runtime values:

- horizon: 15 minutes;
- event cooldown: 15 minutes;
- stop: 0.35%;
- target: 0.50%;
- entry start: 09:31 ET;
- entry cutoff: 15:30 ET;
- stress friction: 12 bps spread + 5 bps slippage per side.

Execution was hard-disabled:

- `EXECUTION_ENABLED=false`;
- `BOT_ARMED=false`;
- `LIVE_TRADING=false`;
- `SCAN_ONLY=true`;
- `TRADING_MODE=paper`.

## Decisive stress result — dev-01

The development profile requires every cost scenario to satisfy a worst-period
expectancy floor of **-0.15%**.

Under the frozen stress scenario, every family breached that floor in the very
first development window:

| Family | Events | dev-01 stress expectancy | Profit factor | Development floor |
| --- | ---: | ---: | ---: | ---: |
| controlled_continuation | 3,395 | -0.21056% | 0.0882 | -0.15% |
| pullback_reclaim | 675 | -0.19109% | 0.2238 | -0.15% |
| compression_breakout | 1,853 | -0.20800% | 0.0847 | -0.15% |
| relative_strength_impulse | 343 | -0.21456% | 0.1611 | -0.15% |
| opening_breakout_retest | 216 | -0.21105% | 0.1680 | -0.15% |

A later development window cannot repair a worst-period minimum. Therefore all
five families were irreversibly rejected after `dev-01` stress.

This early stop is exact, not heuristic: a development survivor must pass every
cost scenario, and each family already failed the frozen stress scenario.

## Terminal verdict

Survivors: **none**

Rejected:

1. controlled_continuation — stress worst-period floor;
2. pullback_reclaim — stress worst-period floor;
3. compression_breakout — stress worst-period floor;
4. relative_strength_impulse — stress worst-period floor;
5. opening_breakout_retest — stress worst-period floor.

Validation was not opened.

The July holdout remains unopened.

No family is eligible for shadow promotion, live promotion, added capital,
increased concurrency, or scaling.

## Reconciliation with the prior result

The prior result also rejected all five families, but its reported event counts
came from the older execution path and are now superseded. The corrected
session-integrity rerun is canonical because it uses the full verified 36-symbol
panel, explicit pagination/session checks, frozen manifest research settings,
and a reproducible decisive stress gate.

## Next phase

Edge Discovery v1 is closed.

Do not retune these five families against the same corpus. The next research
cycle must use genuinely different information families and a new versioned
hypothesis/corpus path.
