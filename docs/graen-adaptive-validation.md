# GRAEN Adaptive Validation v1

Status: RESEARCH VALIDATION / NO PROMOTION AUTHORITY

GRAEN Adaptive Validation separates three different claims that must not be
collapsed:

1. the counterfactual search was statistically controlled;
2. the adaptive shadow beat a fixed baseline walk-forward;
3. the frozen system survived untouched holdout evaluation.

ASC-005 can establish controlled search evidence.

ASC-007 can establish no-lookahead walk-forward evidence.

Neither one can automatically satisfy the frozen holdout requirement.

## Frozen holdout

A holdout is accepted only when an external holdout artifact explicitly states:

- status PASSED;
- the method was frozen before holdout access;
- quarantine data was not accessed during development;
- at least 5 independent holdout sessions;
- at least 50 complete holdout candidates;
- at least 95 percent forward-outcome coverage.

This module never opens holdout data itself.

## Promotion boundary

Even when all GRAEN validation fields pass, promotion_authorized remains false.
The result can be consumed by ASC-008, which still requires exact human
authorization and a separate operator activation step.
