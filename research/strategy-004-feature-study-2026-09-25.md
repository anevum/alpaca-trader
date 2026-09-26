# Strategy 004 feature study — 2026-09-25

Status: **OFFLINE RESEARCH ONLY.**

## Why this build exists

Candidate A (stronger VWAP edge) and Candidate B (two-bar persistence) both
reduced activity but remained negative on untouched pre-September-25 periods.
The next experiment therefore must not be another small threshold adjustment to
the same momentum/VWAP rules.

The feature study separates two questions that were previously confounded:

1. Was an entry opportunity structurally good at the decision point?
2. Did portfolio capacity, ranking, correlation, cooldowns, or exit management
   later determine whether and how that opportunity was traded?

For every raw production-strategy BUY opportunity that passes historical market
quality, the study records only information available at that moment and labels
what price does over the next 1, 3, 5, 10, and 15 minutes.

## Decision-time features

The initial feature set includes:

- production quality score;
- short-horizon momentum;
- VWAP edge;
- relative volume;
- confirmation and regime counts;
- completed-bar age;
- latest and prior one-minute returns;
- fast/slow average gap;
- recent range;
- fraction of recent bars that are positive;
- path efficiency = net directional move / gross absolute path;
- fraction of the recent net move contributed by the latest bar;
- current-bar close location inside its high/low range;
- current volume relative to the preceding three bars.

These features are intentionally descriptive. No new live threshold is selected
by this build.

## Forward labels

For each candidate and horizon the study records:

- maximum favorable excursion (MFE);
- maximum adverse excursion (MAE);
- horizon close return;
- whether the configured target was reached before the effective stop;
- whether the effective stop was reached before the target.

When a single one-minute bar touches both stop and target, stop is labeled first
because intrabar ordering is unknown. This is deliberately conservative.

Production exits, position caps, correlation blocks, cooldowns, and portfolio
allocation are ignored by the labeler. The goal is to isolate entry quality.

## Research protocol

Use earlier periods for feature discovery. Look for features whose quartiles show
a monotonic improvement in MFE, target-before-stop rate, and/or reduction in MAE.
Do not choose thresholds from September 25 and call them validated.

A Candidate C entry rule may be proposed only after:

1. the same structural relationship appears across multiple development periods;
2. the rule is frozen before opening the untouched holdout;
3. the holdout produces positive expectancy after spread/slippage in the full
   replay engine;
4. the ordinary Strategy 004 promotion gate still passes;
5. a separate forward shadow sample passes.

PR #28 remains gated regardless of feature-study findings until that sequence is
complete.


## Development-period feature study result

The first feature study was run against the two development windows already
used for Candidate B:

- 2026-08-17 through 2026-08-21: 99 quality-eligible BUY opportunities;
- 2026-09-08 through 2026-09-11: 84 quality-eligible BUY opportunities.

Using the configured 0.50% target and 0.35% stop with conservative stop-first
same-bar ordering, the unfiltered 15-minute opportunity labels were:

| Period | Target before stop | Stop before target | Mean MFE | Mean MAE |
| --- | ---: | ---: | ---: | ---: |
| Aug 17–21 | 14.1% | 49.5% | +0.279% | -0.548% |
| Sep 8–11 | 22.6% | 42.9% | +0.398% | -0.371% |

No single feature showed a clean monotonic relationship in both periods.
Notably:

- higher momentum increased favorable excursion but also increased stop-first
  behavior;
- wider recent ranges behaved similarly;
- higher VWAP edge was not consistently better;
- the existing 0–100 quality score remained non-monotonic.

A constrained two-condition scan was then used only on these development
periods. Rules had to retain at least 15 observations in each period and improve
both target-before-stop and stop-before-target behavior versus each period's
baseline.

The strongest provisional structures again separated into two families:

1. **quiet/controlled continuation** — limited prior-bar expansion combined with
   non-accelerating current volume;
2. **participation impulse** — above-median quality combined with a sharp
   current-volume acceleration.

Other stable development-period combinations included moderate trend-gap plus
broad market confirmation and adequate relative volume after a less efficient
recent price path.

These are discovery results, not Candidate C. The scan is now implemented in
the offline tooling so future rule discovery is reproducible. Candidate C must
be frozen from development data, run through the full replay engine, and then
tested on a genuinely reserved holdout before any shadow promotion.


## Candidate C frozen before holdout

Candidate C is now frozen as a deliberately simple controlled-continuation
filter:

- retain all ordinary production BUY requirements;
- retain the production minimum 3-bar momentum floor;
- reject when 3-bar momentum exceeds **0.29%**;
- reject when nine-bar trend persistence exceeds **0.625**, equivalent to more
  than five rising close-to-close transitions across the last eight
  transitions.

The rule was chosen because the development-period stability scan showed the
same basic effect in both windows: moderate rather than extreme momentum, with
some recent interruption/pullback, improved target-before-stop balance.

No other production parameter changes in Candidate C. Stop, target, quality
floor, confirmation logic, regime logic, sizing, correlation, cooldowns and
exit behavior remain the production control.

The thresholds are now frozen. Reserved holdout results must not be used to
retune Candidate C. A failure means reject Candidate C and return to research,
not optimize against the holdout.


## Reserved holdout lock

Before Candidate C holdout evaluation, the reserved test window is locked as:

**2026-08-31 through 2026-09-04**

Candidate C thresholds were frozen before viewing this window's Candidate C
results. The holdout must be evaluated once. If it fails the historical
promotion criteria, Candidate C is rejected; its thresholds must not be tuned
against this window.
