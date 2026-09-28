# RHEN Adaptive Decision Space v1

Status: RESEARCH-ONLY
Date: 2026-09-28
Source session: RHEN live session, September 28, 2026
Authority: No live strategy, risk, sizing, broker, or promotion authority.

## Objective

Replace one-dimensional threshold tuning with a decomposed adaptive research model.

RHEN currently makes several logically different decisions that should not be collapsed into one score:

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

A bounded research priority may then be formed as:

P(i,t) = clamp(
  (0.20 A + 0.35 Q + 0.30 T + 0.15 X)
  * (0.50 + 0.50 C),
  0,
  1
)

The confidence multiplier deliberately prevents a sparse early sample from being treated as certainty.

## Default feature decomposition

Attention:
- intraday return
- session range
- relative volume
- liquidity
- trend persistence

Qualification:
- momentum
- VWAP edge
- market confirmations
- market regime
- spread quality
- data freshness
- relative volume
- trend persistence

Timing:
- momentum acceleration
- distance from a useful VWAP region
- current bar impulse
- spread quality
- data freshness
- confirmation alignment

Exit health:
- current return
- retained fraction of MFE
- position momentum health
- regime health
- remaining time budget
- adverse excursion state

Confidence:
- evidence coverage
- effective sample strength
- coefficient/feature stability

## September 28 observations

The first live qualified opportunity after the afternoon recovery appeared at 14:04:20 ET in ORCL. ORCL was submitted at 14:05:04 ET.

The post-recovery live session produced 8 completed round-trip trades, 16 filled orders, 4 winners, 4 losers, and approximately +$0.0825 realized net P&L.

The existing quality score alone did not rank outcomes reliably. Examples from the executed set:

- KR quality score 86.34 -> losing trade.
- ORCL quality score 83.96 -> losing trade.
- CMG quality score 83.40 -> strongest winner of the session.
- MSFT quality score 80.82 -> losing trade.
- KGC quality score 85.14 -> small winner.

Therefore the observed evidence does not support simply raising or lowering MIN_QUALITY_SCORE.

The better hypothesis is that qualification quality and entry timing are distinct. A symbol can be a valid setup but still be entered at a poor moment. Future research should estimate these dimensions separately.

## Telemetry discrepancy

The canonical candidate_evaluations table reported zero qualified rows even though live scan events produced qualified buy signals and eight executed entries.

The live scan event stream contains the strategy metadata and quality scores used in actual execution. Until candidate_evaluations linkage is repaired, research using candidate qualification statistics must treat that table as incomplete for this session.

Signals also had null candidate_id values. This prevents direct signal-to-candidate attribution and should be repaired before using candidate-level forward-outcome modeling as a canonical source.

## Research protocol

1. Preserve the current live system as the baseline.
2. Compute D(i,t) offline from canonical decision telemetry.
3. Attach realized forward outcomes at multiple horizons.
4. Estimate whether A, Q, T, and X have independent explanatory value.
5. Compare against:
   - existing quality score
   - current hard qualification gates
   - simple momentum/VWAP baselines
6. Use walk-forward session splits.
7. Keep protected validation sessions untouched until methodology is frozen.
8. Do not promote coefficients from a single session.
9. Any proposed live integration requires a separate experiment, evidence review, and explicit approval.

## Immediate next experiment

ADS-001: Qualification versus timing decomposition.

Question:
Does separating setup qualification from entry timing improve forward-return discrimination relative to the existing quality score?

Primary comparison:
- existing quality_score
- Q only
- T only
- combined Q/T research priority

Evaluation:
- forward returns at 1, 3, 5, 10, and 15 minutes
- MFE / MAE
- realized trade return where an actual trade exists
- calibration by score decile
- rank correlation with forward return
- stability across independent sessions

No production promotion is authorized by this document.
