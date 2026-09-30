# GRAEN Crypto Research State v1

Date frozen: 2026-09-30 UTC  
Authority: GRAEN research only  
Execution authority: none  
Required production state: `CRYPTO_EXECUTION_ENABLED=false`

## 1. Valid and reusable

The 24/7 crypto data lane, Alpaca historical crypto-bar reader, dynamic asset discovery, execution/risk isolation, telemetry, candidate evidence, forward-outcome enrichment, ADS capture, NOSTRA state capture, VELUM replay infrastructure, dependence diagnostics, and Benjamini-Yekutieli multiplicity implementation are reusable.

The v2.1 corpus-boundary correction is retained as a hard rule: provider-inclusive end bars are trimmed and any overlap with a previously inspected confirmatory corpus invalidates a study.

Research and execution are separated. New strategy discovery lives under `graen/crypto/`; RHEN production execution remains under `app/` and may consume only a formally promoted strategy specification.

## 2. Assumptions retired

The following are not valid production assumptions:

- `CRYPTO-2026-09-29-001` rolling momentum/VWAP as a crypto-native edge.
- Immediate entry after a short-horizon momentum impulse.
- BTC/ETH confirmation as a mandatory universal entry condition.
- A single fixed stop/target pair as the default exit thesis.
- Generation-local multiple-testing correction as sufficient protection for a sequential research program.
- Selecting a symbol because an earlier exploratory diagnostic happened to look better.

Crypto Edge Discovery v1, v2.1, v3, and v4 are retained as exploratory/search-history evidence. v2 is invalid for inference because its corpus overlapped v1. v4 is non-promotional because it selected ETH from an earlier symbol diagnostic and, independently, did not satisfy its own 30-day corpus requirement.

## 3. Evidence currently supported

Historical stressed-cost validation produced no eligible holdout candidate in v1, v2.1, or v3. The best observed validation expectancy improved across generations but remained negative and multiplicity-adjusted evidence never rejected the null.

The live/shadow rolling-momentum candidate population is small after qualification. Its observed 3–15 minute forward returns have been negative on average while 30–60 minute outcomes have sometimes been positive. These observations are motivation only; they are selection-conditioned and are not used to choose symbols, tune parameters, or open the new holdout.

Therefore the defensible conclusion is narrower than “crypto has no edge”: the tested RHEN-derived formulations have not demonstrated a cost-aware out-of-sample edge.

## 4. New hypothesis tree

Primary question: does cross-sectional information identify an opportunity independently from the instant of entry?

1. Market quality
   - sufficient bar/activity coverage
   - symbol-specific frozen transaction-cost scenarios
   - abstain when plausible cost overwhelms the gross move
2. Opportunity information
   - absolute return
   - multi-asset market residual return
   - volatility-normalized return
   - persistence
   - acceleration
   - activity change
3. Market state
   - BTC directional state
   - cross-sectional breadth
   - cross-sectional dispersion
   - weekday/weekend and UTC hour diagnostics
4. Entry timing
   - immediate
   - delayed confirmation
   - pullback/reclaim
   - controlled mean reversion
   - abstention
5. Exit
   - fixed 60/120/240 minute benchmarks first
   - adaptive exits are investigated only after an entry mechanism survives validation

ADS and NOSTRA remain recorded features/challengers. They are not promotion gates in this cycle.

## 5. Frozen methodology: `graen-crypto-native-v5`

Confirmatory universe: BTC/USD, ETH/USD, SOL/USD, XRP/USD, AVAX/USD, LINK/USD. This is the pre-existing v1 universe, reused to avoid outcome-based symbol selection.

Untouched archive:
- development: 2026-02-01 through 2026-04-02 UTC
- validation: 2026-04-02 through 2026-05-02 UTC
- holdout: 2026-05-02 through 2026-06-01 UTC

The archive does not overlap v4's inspected June/July corpus.

Bars are causally resampled to five-minute buckets. Primary cross-sectional observations occur hourly. Feature/target inference is aggregated by UTC day before bootstrap testing so overlapping intraday horizons are not treated as independent observations.

Primary feature family and forward horizons are frozen before reading the archive. Benjamini-Yekutieli controls FDR across the complete v5 primary feature family. Candidate tests are a second separately frozen family with their own BY correction; a strategy cannot reach holdout unless its source feature is validated and the candidate itself passes the candidate-family gate.

Selection is performed under the stressed/high cost scenario only. Low/base costs are descriptive sensitivity checks. A holdout is opened only for one validation winner. If no candidate qualifies, holdout trade outcomes are not evaluated.

## 6. Exact first batch

Feature tests:
- absolute return 15m and 60m
- market-residual return 15m and 60m
- negative market-residual 15m (long-only mean-reversion hypothesis)
- volatility-normalized 60m return
- 60m directional persistence
- 15m-vs-60m acceleration
- 60m-vs-360m activity ratio

Each is tested against 30m, 60m, 120m, and 240m forward returns.

Controlled candidate family:
- simple momentum continuation
- residual relative-strength continuation
- residual continuation + 15m delayed confirmation
- residual continuation + pullback/reclaim timing
- controlled residual mean reversion in a non-bearish BTC regime
- volatility-normalized trend
- no-signal random-entry control with equivalent hold time

Holding-time variants are frozen at 60m, 120m, and 240m where applicable. No candidate may be promoted from this batch unless the full validation, untouched holdout, robustness, VELUM, shadow, paper, and production-readiness path subsequently passes.

## State before v5 execution

**DEVELOPMENT CONTINUES**
