# ASC-006 NOSTRA Transition Forecasting

Status: SHADOW / RESEARCH ONLY

ASC-006 adds a transparent next-regime baseline on top of ASC-002.

The first model is deliberately simple: a smoothed empirical transition matrix.
Transitions are formed only between consecutive observations within the same
trading session. Session boundaries are never bridged.

## Why this baseline exists

Before fitting more complex forecasting models, NOSTRA needs a frozen reference
that answers:

Given the current regime, what regime has historically followed next?

A future learned model must beat this baseline out of sample.

## Method

- regime vocabulary is inherited from ASC-002;
- transition counts are grouped by source regime;
- a symmetric Dirichlet prior with alpha 0.5 prevents zero-probability states;
- uncertainty is reflected by entropy, transition count, and independent-session
  count;
- confidence remains hard-capped below 0.50 until at least 10 independent
  sessions and 50 transitions exist for the source regime.

## Calibration

Frozen probability forecasts can later be scored with:

- multiclass Brier score;
- log loss;
- top-1 accuracy.

Calibration is not considered mature below 100 realized forecasts.

## Safety

ASC-006 has no execution, strategy-selection, sizing, risk, or promotion
authority. It is a research baseline only.
