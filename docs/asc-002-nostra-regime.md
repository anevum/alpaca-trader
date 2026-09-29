# ASC-002 NOSTRA Regime State

Status: SHADOW / READ-ONLY

ASC-002 adds the first deterministic NOSTRA market-state and regime layer for
IREN Adaptive Strategy Control.

The module performs no market-data reads. Callers must provide point-in-time
inputs that were available at the observation timestamp.

## Inputs

Initial state features include:

- 5-minute and 15-minute SPY / QQQ / IWM returns;
- fraction of candidates above VWAP;
- fraction of candidates with positive 5-minute return;
- median fast/slow trend spread;
- median VWAP edge;
- median absolute 5-minute movement;
- cross-sectional 5-minute dispersion;
- median relative volume;
- eligible-candidate ratio;
- realized short-horizon volatility;
- average spread;
- session segment.

## Output

The module returns:

- a point-in-time market-state vector;
- probabilities across transparent regime families;
- primary regime;
- confidence;
- UNKNOWN probability;
- market familiarity;
- diagnostics explaining the classification.

The initial regime vocabulary is:

- TREND_EXPANSION
- TREND_DECAY
- BROAD_ADVANCE
- BROAD_DECLINE
- ROTATION
- HIGH_VOLATILITY
- LOW_VOLATILITY
- CHOP
- OPENING_DISCOVERY
- MIDDAY_COMPRESSION
- LATE_SESSION_EXPANSION
- UNKNOWN

## Safety boundary

ASC-002 is classification research only.

It has no authority to:

- place orders;
- select a live strategy;
- mutate live parameters;
- modify risk or sizing;
- promote a model;
- deploy itself into the execution path.

## Design philosophy

v1 intentionally uses transparent deterministic transforms rather than a
learned black-box classifier. This gives GRAEN a clearly inspectable baseline
and lets us collect labeled state/outcome history before fitting transition or
outcome models.

Future NOSTRA work should compare learned models against this frozen baseline
rather than silently replacing it.
