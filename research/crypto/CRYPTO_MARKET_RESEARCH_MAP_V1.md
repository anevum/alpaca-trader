# Crypto Market Research Map v1

## Scope

GRAEN is testing executable long/flat spot-crypto mechanisms, not assuming a momentum or mean-reversion answer. RHEN execution remains disabled and consumes no experimental strategy directly.

| Mechanism | Theoretical rationale | Observable features | Main confounders / failure modes | Data required | Falsification criterion |
|---|---|---|---|---|---|
| Cross-sectional residual continuation | Asset-specific demand can persist after removing common crypto movement | leave-one-out market residual returns, rank, persistence, acceleration | common-factor contamination, winner concentration, costs, delayed reaction already exhausted | synchronized multi-asset bars, activity, costs | validation residual IC is non-positive after multiplicity control or candidate expectancy is non-positive under stressed costs |
| Controlled residual reversal | Short-lived overreaction can reverse, especially in less efficient names | negative residual return, pullback magnitude, BTC state, activity | illiquidity masquerading as reversal, midpoint-only bars, falling-knife behavior | synchronized bars, quote/activity quality, costs | no positive validation IC/candidate edge or effect disappears under liquid-only and cost stress |
| Volatility-normalized trend | Similar directional moves may have different information content when scaled by local noise | return divided by realized volatility, persistence | volatility clustering, jump risk, unstable normalization | intraday bars | adjusted validation evidence fails or nearby horizons reverse sign |
| Compression → expansion | volatility contraction can precede directional range expansion | short/long realized-vol ratio, range break, activity change | false breaks, spread widening, gap/jump fills | OHLCV/trade count and costs | stressed-cost breakout benchmark has non-positive expectancy |
| Opportunity then delayed timing | selection and entry may be distinct problems; waiting can avoid immediate post-impulse reversal | residual rank at opportunity, subsequent confirmation, pullback/reclaim | missed moves, extra latency, selection leakage | causal intraday path after opportunity | delayed variants do not improve validation expectancy/stability versus same opportunity with instant entry |
| Regime conditioning | crypto return mechanisms may vary with market direction, breadth, dispersion and liquidity | BTC/ETH returns, breadth, dispersion, activity state, UTC time | regime labels fitted to outcomes, sparse state cells | synchronized market panel | conditioning does not improve out-of-sample edge after complexity/multiplicity penalty |
| Time structure | 24/7 liquidity and behavior are not uniform | UTC hour, weekday/weekend, session overlap | data-mined time filters, daylight/session definitions | timestamped bars/quotes | no stable validation differences; no production time restriction |
| ADS/NOSTRA incremental value | existing ANEVUM forecasts may add information beyond raw market features | ADS prediction, NOSTRA state plus baseline features | circularity, hidden promotion shortcut, calibration drift | pre-event stored predictions and later outcomes | no incremental OOS information; remain optional evidence only |

## Market-quality contract

Research distinguishes price availability from trade activity. Alpaca crypto bars can contain quote-midpoint-derived prices even when volume is zero, so zero-volume and trade-count persistence are measured explicitly rather than treating every priced bar as equally tradable.

A candidate entry must pass a causal activity gate and a symbol-specific stressed-cost hurdle. The cost model is frozen before archive inspection in `cost_snapshot_v5.json`.

## Confounders carried into every experiment

Common BTC-driven movement, cross-asset correlation, repeated observations from the same move, overlapping holding horizons, symbol concentration, time-of-week concentration, spread/slippage uncertainty, bar sparsity, midpoint-derived zero-trade bars, survivorship of the current broker universe, and adaptive research history are all treated as explicit threats rather than ignored residual noise.

## Promotion falsification

Any of the following is sufficient to stop promotion: no multiplicity-adjusted validation signal, non-positive stressed-cost expectancy, insufficient independent daily evidence, holdout failure, edge dominated by one symbol/time state, abrupt failure under modest parameter/cost/delay stress, VELUM mismatch, shadow mismatch, paper mismatch, or any loss of causal/data-integrity guarantees.
