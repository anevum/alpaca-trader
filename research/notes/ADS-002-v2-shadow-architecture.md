# ADS-002 v2 Shadow Architecture

Status: **ACTIVE_SHADOW / SPEC_ONLY MIX**  
Program: **ADS-002**  
Methodology: `ads-shadow-v2`  
Feature schema: `ads-features-v2`  
Execution authority: **NONE**  
Automatic promotion authority: **NONE**

## Objective

ADS-002 v2 turns RHEN's mathematical research into a versioned champion/challenger system without changing the live trading champion.

The design preserves separability:

- **A — Attention:** which assets deserve attention?
- **Q — Qualification:** is the setup structurally attractive?
- **T — Timing:** is this a favorable instant to enter?
- **X — Exit continuation value:** is holding still preferable to exiting?
- **C — Confidence:** how strongly should RHEN trust a research score?

No v2 score is consumed by execution, sizing, risk, order routing, or protective logic.

## Frozen live baseline

ADS-002 v1 remains unchanged:

```
S_pre_v1 = 0.20 A_v1 + 0.50 Q_v1 + 0.30 T_v1
```

v1 remains the research benchmark. The live production strategy remains the champion.

## Decision-time feature capture

`ads-features-v2` captures only information available at the time RHEN evaluates a candidate:

- 1m / 3m / 5m returns
- absolute 5m movement
- 1m acceleration
- short-horizon realized volatility
- volatility expansion ratio
- bar-range expansion ratio
- relative-volume ratio
- 5m dollar volume
- trend persistence
- fast/slow trend spread
- momentum
- VWAP edge
- independent-confirmation ratio
- regime-confirmation ratio
- bid/ask spread when market-quality telemetry exists
- quote age
- bar age
- feature timestamp and bar count

Historical same-minute z-scores are nullable until a clean historical baseline exists. Missing history is never fabricated.

## A_v2 — Attention

A_v2 combines five mathematically distinct families:

```
A = 0.25 R_vol
  + 0.25 R_move
  + 0.20 R_volatility_expansion
  + 0.15 R_range_expansion
  + 0.15 R_dollar_volume
```

Each R term is normalized to [0,1].

Preferred normalization once enough history exists:

```
R_j = 0.5 * sigmoid(z_time_of_day,j)
    + 0.5 * cross_sectional_percentile_j
```

Until the time-of-day history is mature, the implementation falls back explicitly to cross-sectional ranking. A score is not emitted if less than 75% of the configured Attention weight is observable.

## Q_v2 — Qualification

Q_v2 avoids naïvely double-counting correlated technical indicators by grouping them into three evidence families.

```
Q = 0.50 F_trend
  + 0.30 F_confirmation
  + 0.20 F_relative
```

Trend family:
- 3m return
- fast/slow trend spread
- VWAP edge
- trend persistence

Confirmation family:
- independent market-confirmation pass ratio
- regime-confirmation ratio

Relative family:
- cross-sectional 5m return rank
- cross-sectional relative-volume rank

Features are averaged within a family before families are combined. This reduces repeated reward for multiple indicators measuring the same underlying trend.

## T_v2 — Timing

T_v2 deliberately uses a non-linear architecture.

Microstructure quality:

```
M = geometric_mean(
  exp(-(spread/tau_spread)^2),
  exp(-quote_age/tau_quote),
  exp(-bar_age/tau_bar)
)
```

VWAP extension uses a hump-shaped preference instead of "more is always better":

```
E = exp(-((extension_ratio - 0.50)^2)/(2 * 0.35^2))
```

Short-term impulse:

```
I = sigmoid(
  0.67 * return_1m / momentum_scale
  + 0.33 * acceleration_1m / momentum_scale
)
```

Final Timing:

```
T = M^0.40 * E^0.35 * I^0.25
```

The geometric structure makes severe weakness in one timing dimension harder for unrelated strength to hide.

## Active v2 pretrade challengers

### 1. ads002-add-v2

```
S_add = 0.20 A + 0.50 Q + 0.30 T
```

Purpose: isolate whether better component definitions and normalization outperform v1 while preserving the familiar additive structure.

### 2. ads002-geo-v2

```
S_geo = A^0.20 * Q^0.50 * T^0.30
```

Purpose: test whether penalizing weak dimensions improves forward ranking.

### 3. ads002-rank-v2

```
S_rank = 0.20 rank(A)
       + 0.50 rank(Q)
       + 0.30 rank(T)
```

Purpose: test whether relative opportunity ranking is more stable than absolute scoring in a changing intraday environment.

## C_v2 — Confidence

Confidence is not another opportunity score.

After sufficient clean evidence:

```
S_effective = 0.50 + C * (S_raw - 0.50)
```

This means uncertainty moves the research conclusion toward neutral, not toward zero.

C uses:

- direct attribution coverage
- forward-outcome coverage
- independent sessions
- eligible candidate count
- directly attributed closed trades
- sign consistency of session-level Spearman effects
- quartile-spread consistency
- median session Spearman magnitude

The implementation hard-caps C below 0.50 until all minimum evidence gates are satisfied.

## Spec-only innovation models

These formulas are implemented as research primitives but remain intentionally unfitted.

### ads002-netev-v2

```
E[R_net] =
  P(up) * mu_up
  + (1 - P(up)) * mu_down
  - expected_round_trip_cost
```

It may not activate until the probability, return-magnitude, and cost models are independently calibrated.

### ads002-horizon-v2

For horizons h in {1,3,5,10,15,30,60}:

```
h* = argmax_h (
  E[R_net,h] - uncertainty_penalty_h
)
```

No dynamic horizon can be trusted until the multi-horizon forecasts pass purged temporal validation.

### ads002-exitcv-v2

```
EV_hold =
  P(continuation) * mu_continuation
  + (1-P(continuation)) * mu_reversal
  - lambda * expected_shortfall
  - opportunity_cost

EV_exit = -exit_cost

Delta_X = EV_hold - EV_exit
```

The implementation exposes the formula but does not fit its probabilities or return terms.

## X training telemetry

Position telemetry now also records:

- current return
- entry price
- current price
- maximum favorable excursion
- maximum adverse excursion
- risk stop
- protected-profit floor
- profit-protection state
- thesis-failure count
- holding time
- target
- excursion timestamps

This is observational training data only.

## Validation endpoints

Primary v2 endpoint:

- session-level Spearman correlation between challenger score and 15-minute candidate forward return

Secondary endpoints:

- upper-vs-lower score quantile forward-return spread
- sign consistency across independent sessions
- model coverage
- direct attribution coverage
- calibration/error metrics for later probabilistic models

Rows within a session are not treated as independent sessions.

## Frozen minimum evidence gates

No challenger may be considered for promotion before at least:

- 10 independent trading sessions
- 100 eligible forward-scored candidates
- 30 directly attributed closed trades
- >=99.5% direct attribution coverage
- >=95% complete 15-minute forward-outcome coverage
- stable positive cross-session evidence
- frozen-validation pass
- explicit human review

The database permanently marks the current v2 models as having no execution or automatic-promotion authority.

## Champion/challenger rule

```
LIVE CHAMPION
  -> unchanged production trading strategy

SHADOW
  -> ADS-002 v1
  -> ads002-add-v2
  -> ads002-geo-v2
  -> ads002-rank-v2

SPEC / UNFITTED
  -> ads002-netev-v2
  -> ads002-horizon-v2
  -> ads002-exitcv-v2
```

Research may reject, retain, freeze, or propose a challenger. Research alone cannot deploy it into live trading.

## First clean experiment

The next regular market session after deployment is the first intended v2 observation window.

The initial question is deliberately simple:

> Do any of the frozen v2 challenger scores rank 15-minute forward candidate outcomes more consistently across sessions than the frozen ADS-002 v1 baseline?

No weights should be retuned from a single session.

## Current limitations

- Historical time-of-day normalization is not considered mature yet.
- Full Timing requires market-quality telemetry; incomplete candidates remain explicitly incomplete rather than receiving synthetic values.
- Expected-value, horizon, and exit-continuation probability models are unfitted.
- The confidence model begins at zero and remains hard-capped until evidence gates are satisfied.

These are safeguards, not missing-data excuses.
