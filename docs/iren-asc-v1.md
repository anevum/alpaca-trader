# IREN Adaptive Strategy Control v1

Status: DRAFT / SHADOW / NO PRODUCTION AUTHORITY

IREN Adaptive Strategy Control (ASC) is the supervisory research system around
RHEN, NOSTRA, and GRAEN.

Its purpose is not to create a strategy that can never fail. Its purpose is to
make strategy deterioration observable, measurable, contained, and researchable
before a production change is considered.

## Components

### ASC-001 Strategy Health

Maintains separate health dimensions for edge, regime fit, forecast
calibration, execution, distribution drift, parameter pressure, challenger
pressure, and evidence integrity.

### ASC-002 NOSTRA Regime State

Builds transparent point-in-time market-state vectors and regime probability
distributions with UNKNOWN probability and market familiarity.

### ASC-003 Control State

Applies deterministic hysteresis across NORMAL, ADAPT, RESEARCH, and DEFENSIVE.
Escalation is fast; recovery requires repeated evidence.

### ASC-004 Adaptation Proposals

Turns validated counterfactual evidence into small bounded parameter proposals.
Every proposal includes provenance and rollback values and requires
authorization.

### ASC-005 Counterfactual Parameter Lab

Tests one strategy gate at a time against completed candidate forward outcomes.
Uses frozen search grids, session-level dependence handling, explicit
transaction costs, and multiplicity control.

### ASC-006 NOSTRA Transition Research

Builds a transparent empirical next-regime model from ordered observations
without crossing session boundaries. Confidence remains capped until sufficient
independent evidence accumulates.

### ASC-007 Fixed vs Adaptive Shadow

Freezes next-session adaptive research plans and compares them against the fixed
champion on later sessions. Same-session evaluation is forbidden.

### ASC-008 Promotion Gate

Requires evidence quality, ASC-007 validation, GRAEN controls, frozen
validation, and proposal-specific human authorization before an activation
contract can even be created. The gate itself still cannot deploy.

### ASC-009 Strategy-Family Routing

Provides the future interface for regime-conditioned strategy-family ranking.
NO_TRADE is selected when no independently validated positive-utility family is
available.

## Supporting controls

### Parameter pressure

Tracks whether validity-passed adaptive preferences repeatedly push parameters
toward approved boundaries. Persistent boundary pressure escalates research; it
never expands limits automatically.

### GRAEN validation

Checks selection-bias controls, temporal dependence, multiple testing,
walk-forward validation, and frozen holdout state. Missing holdout evidence is
reported as missing rather than inferred.

## Canonical daily flow

1. Freeze and reconcile RHEN evidence.
2. Complete forward candidate outcomes.
3. Refresh ADS-002 shadow evidence.
4. Run ASC-005 session searches.
5. Aggregate compatible searches across sessions.
6. Build NOSTRA session state and transition research.
7. Compute parameter pressure and Strategy Health.
8. Apply ASC-003 state hysteresis.
9. Produce bounded ASC-004 proposals only when evidence warrants them.
10. Evaluate the prior session's ASC-007 shadow plan.
11. Aggregate fixed-vs-adaptive validation.
12. Run GRAEN validation.
13. Preview ASC-008 promotion gates without authorization.
14. Persist the complete artifact in the canonical daily report.

## Absolute v1 authority boundary

This branch does not:

- call the broker from ASC;
- mutate RHEN execution logic;
- mutate risk or sizing;
- modify Railway variables;
- apply a proposed parameter;
- promote a challenger automatically;
- create production strategy-family routing;
- treat research counterfactuals as realized trades.

All ASC outputs are evidence and control-state artifacts until a separately
authorized production change is built, deployed, verified, and made
rollback-capable.
