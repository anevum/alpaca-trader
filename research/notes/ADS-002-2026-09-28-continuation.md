# ADS-002 continuation — 2026-09-28

Status: **RESEARCH_ONLY**  
Methodology: `ADS-002-v1` / `ads-shadow-v1`  
Primary forward horizon: **15 minutes**  
Live strategy/execution changes from this work: **none**

## Purpose

Continue ADS-002 after the September 28 attribution incident without manufacturing historical identity links or promoting selected-trade diagnostics into the canonical predictive corpus.

## Canonical session state

A fresh call to `private.rhen_ads002_refresh_session('2026-09-28')` still returns:

- readiness: `RESEARCH_ONLY`
- `data_valid=false`
- `stable=false`
- executable signals: 8
- direct signals: 0
- unlinked signals: 8
- closed direct trades: 0
- official ADS shadow-score rows: 0
- research-eligible candidates: 0
- complete eligible 15-minute forward outcomes: 0
- confidence: 0

Active reason codes:

- `UNLINKED_EXECUTABLE_SIGNAL`
- `DIRECT_ATTRIBUTION_COVERAGE`
- `FORWARD_15M_COVERAGE`
- `STABILITY_GATES_NOT_MET`
- `REGIME_CLASSIFIER_UNFROZEN`

The post-fix instrumentation was not used to rewrite the earlier market session.

## Attribution incident confirmed

All 8 September 28 executable signals have complete downstream execution chains:

`signal -> intent -> order -> fill -> position -> exit`

But the upstream candidate identity is not recoverable by the frozen direct-link method:

- signal `candidate_key` present: 0/8
- signal `candidate_id` present: 0/8
- signal `cycle_key` resolving to a persisted candidate scan cycle: 0/8

Therefore the session remains excluded from candidate-score predictive evidence. No nearest-time, same-symbol, or other fuzzy historical link is allowed.

## Execution/exit diagnostic

All eight positions closed. The realized-return sample is diagnostic only:

- wins: 4
- losses: 4
- mean realized return: +0.0512%
- median realized return: -0.0263%
- minimum: -0.2078%
- maximum: +0.5023%
- mean holding time: about 7.9 minutes
- usable MFE/MAE for exit-health score: 6/8
- mean X among those six: 56.74
- median X among those six: 70.81

This can inform exit/execution research, but it does not validate ADS-002 candidate selection.

## Selected-trades-only A/Q/T reconstruction

The eight signal payloads preserved enough pre-entry metadata to reconstruct the frozen A/Q/T equations for exploratory analysis. Exact runtime scales were independently confirmed from canonical decision-cycle configuration:

- `MIN_MOMENTUM_PCT=0.0005`
- `TARGET_PCT=0.005`
- `MAX_SPREAD_PCT=0.002`
- `MAX_BAR_AGE_SECONDS=90`
- `MAX_VWAP_EXTENSION_PCT=0.008`

| Symbol | A | Q | T | S_pre | Realized return |
| --- | ---: | ---: | ---: | ---: | ---: |
| ORCL | 52.5950 | 97.3346 | 72.9272 | 81.0645 | -0.1378% |
| KR | 76.2544 | 91.2544 | 69.3886 | 81.6947 | -0.0966% |
| CMG | 80.8650 | 83.3333 | 72.5762 | 79.6125 | +0.5023% |
| MSFT | 62.6618 | 82.8234 | 63.7909 | 73.0813 | -0.2078% |
| TQQQ | 49.7600 | 100.0000 | 74.5273 | 82.3102 | +0.0439% |
| KGC | 75.2890 | 85.2890 | 59.1292 | 75.4411 | +0.0578% |
| SMH | 73.0406 | 81.8359 | 79.6155 | 79.4107 | -0.1206% |
| BMY | 74.0708 | 81.9649 | 60.4524 | 73.9323 | +0.3681% |

Exploratory rank relationships versus realized return:

- `S_pre`: Spearman ≈ +0.071
- Attention A: Spearman ≈ +0.595
- Qualification Q: Spearman ≈ -0.048
- Timing T: Spearman ≈ -0.286
- legacy quality score: Spearman ≈ -0.048

These values are **not canonical ADS effect estimates**. The sample contains only eight already-selected trades, lacks the full candidate counterfactual set, and is subject to selection bias. The Attention result is hypothesis-generating only.

## Canonical follow-up questions registered

Two questions were added to `private.trading_research_questions`:

1. `RQ-ADS002-ATTRIBUTION-INTEGRITY` — priority 100, DATA_QUALITY
   - Does post-fix direct candidate-to-signal attribution reach and sustain the frozen >=99.5% coverage gate?

2. `RQ-ADS002-PRETRADE-PREDICTION` — priority 95, STRATEGY_HYPOTHESIS
   - Across clean post-fix sessions, does `S_pre = 0.20A + 0.50Q + 0.30T` positively rank 15-minute forward candidate returns and remain stable across sessions?

Both remain `MONITOR`; official sample size is 0.

## Next valid evidence

The next regular market session should be treated as the first clean post-fix ADS-002 observation window. Post-close verification should require:

1. direct candidate identity on selected candidate, signal, and intent;
2. direct attribution coverage >= 99.5%;
3. ADS shadow-score coverage >= 99%;
4. eligible 15-minute forward-outcome coverage >= 95%;
5. no identity conflicts;
6. no fuzzy reconstruction;
7. multiple independent sessions before stability claims.

Frozen sample minimums remain:

- >=10 independent sessions
- >=100 research-eligible candidates
- >=30 closed direct trades

Until those gates are satisfied, ADS-002 remains research-only and cannot authorize live parameter changes or promotion.
