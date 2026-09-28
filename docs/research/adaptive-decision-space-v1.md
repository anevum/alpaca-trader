# RHEN Adaptive Decision Space v1

Status: RESEARCH-ONLY / ADS-001 COMPLETE
Date: 2026-09-28
Authority: No live strategy, risk, sizing, broker, or promotion authority.

## Objective

Replace one-dimensional threshold tuning with a decomposed adaptive research model.

RHEN makes several logically different decisions that should not be collapsed into one score:

1. Attention: which symbols deserve computational attention now?
2. Qualification: does the symbol satisfy the current strategy thesis?
3. Timing: is this a good moment to enter?
4. Exit health: should an existing position continue to be held?
5. Confidence: how much evidence supports the current learned weighting?

The research model returns these dimensions separately. It does not emit orders.

## Decision vector

For symbol i at time t:

D(i,t) = [A(i,t), Q(i,t), T(i,t), X(i,t), C(i,t)]

where each component lies in [0,1].

A = attention score
Q = qualification score
T = entry timing score
X = exit-health score
C = evidence confidence

The architecture remains valid as a research representation. No coefficients in this document are approved for production execution.

## ADS-001 question

Does separating setup qualification from entry timing improve forward-return discrimination relative to the existing quality score?

## Data used

Development / comparison evidence:
- 2026-09-25 canonical qualified-candidate corpus.
- 36 qualified candidate rows.
- Complete forward outcomes available for 34 candidates at 5 minutes, 35 at 15 minutes, 33 at 30 minutes, and 33 at 60 minutes.
- Existing quality score and contemporaneous strategy features recovered from canonical candidate telemetry.

Independent live consistency evidence:
- 2026-09-28 live session after the afternoon recovery.
- 8 completed round-trip trades.
- 16 submitted orders filled.
- 4 winners and 4 losers.
- Approximately +$0.0825 realized net P&L.
- 5 executed trades had complete attributable live scan feature records suitable for direct score comparison.

Protected validation / holdout datasets were not opened.

## Existing quality-score result

The existing quality score showed weak or negative rank association with subsequent return in the September 25 candidate corpus:

- 5 min Spearman: -0.017
- 15 min Spearman: -0.154
- 30 min Spearman: -0.159
- 60 min Spearman: -0.108

This is not evidence that the current quality score provides useful ordinal forward-return discrimination on that session.

September 28 produced the same qualitative concern:
- KR quality 86.34 -> losing realized trade.
- ORCL quality 83.96 -> losing realized trade.
- CMG quality 83.40 -> strongest realized winner among the fully attributable set.
- MSFT quality 80.82 -> losing realized trade.
- KGC quality 85.14 -> small realized winner.

Therefore ADS-001 rejects the idea that simply raising or lowering MIN_QUALITY_SCORE is a justified improvement.

## Qualification/timing decomposition result

A first decomposition was evaluated using structural setup features separately from entry-timing / extension features.

On September 25, the combined decomposed score materially improved rank discrimination relative to the existing quality score:

- 5 min combined Q/T Spearman: +0.040
- 15 min: +0.210
- 30 min: +0.334
- 60 min: +0.233

The 30-minute result was the strongest observed relationship.

At 15 minutes:
- top timing quartile average forward return: approximately +0.006%
- bottom timing quartile: approximately -0.742%

At 30 minutes:
- top combined Q/T quartile average forward return: approximately +0.022%
- bottom combined Q/T quartile: approximately -0.863%

At 60 minutes:
- top combined Q/T quartile average forward return: approximately +0.009%
- bottom combined Q/T quartile: approximately -0.940%

These are development-session observations, not validated production effect estimates.

## Important timing finding

The initial assumption that stronger raw momentum should always increase entry-timing quality was falsified on the September 25 session.

Feature rank correlations with forward return included:

5 minutes:
- momentum: -0.363
- trend gap: -0.461
- spread quality: +0.256

15 minutes:
- momentum: -0.356
- bar impulse: -0.243

30 minutes:
- confirmations: +0.422
- bar impulse: -0.276
- VWAP edge: -0.204

60 minutes:
- confirmations: +0.259
- bar impulse: -0.375
- VWAP edge: -0.252

Interpretation: on that session, stronger already-realized impulse frequently behaved more like entry extension / chase risk than additional edge.

This supports separating:
- "is this a valid setup?"
from
- "is now an efficient entry point?"

It does not establish one universal anti-momentum rule.

## Cross-session stability check

The September 28 fully attributable executed subset contained only five trades, so it is too small for reliable coefficient estimation.

Within that subset:
- existing quality-score Spearman versus realized return: +0.300
- structural qualification proxy: +0.900
- first timing proxy: -0.400
- combined proxy: approximately +0.728

Because n=5, these values are descriptive only.

Most importantly, the exact September 25 timing relationship did not reproduce cleanly. That means the decomposition concept appears useful, but the current timing coefficients are not stable enough for promotion.

## Telemetry limitation

September 28 candidate_evaluations reported zero qualified rows even though live scan events produced qualified buy signals and eight executed entries.

Signals also had incomplete candidate linkage for that session.

Therefore:
- September 28 live scan events are usable for execution-attribution research.
- September 28 candidate-level forward-outcome modeling is not yet canonical.
- candidate -> signal -> order -> position linkage should be repaired before the next formal adaptive-model experiment.

## ADS-001 determination

ADS-001 is COMPLETE.

Decision:

1. ACCEPT the decomposed Adaptive Decision Space architecture as the canonical research direction.
2. REJECT the current single quality score as sufficient evidence for entry ranking.
3. REJECT simple threshold tuning of MIN_QUALITY_SCORE as the next improvement path.
4. REJECT the current timing coefficients for live production use.
5. DO NOT change live strategy, risk controls, sizing, capital allocation, broker behavior, or execution gates from ADS-001.
6. Continue collecting live sessions and compute A/Q/T/X components in shadow/research mode only.
7. Repair canonical candidate-signal-order-position attribution before any promotion study.
8. Require multi-session stability and a separately frozen promotion experiment before live integration.

## Canonical interpretation

The strongest supported conclusion is architectural, not parametric:

RHEN should learn separate functions for:
- what to watch,
- what qualifies,
- when to enter,
- when to exit,

rather than trying to make one scalar quality score answer all four questions.

The next evidence phase should learn these functions from accumulated session outcomes while preserving the current live system as the production baseline.

No production promotion is authorized by ADS-001.
