# GRAEN Crypto-Native Research v5 — Result

Frozen methodology: `graen-crypto-native-v5`  
Completed: 2026-09-30 02:54:53 UTC  
Execution authority: none  
Required RHEN crypto execution state: disabled

## Research decision

The first crypto-native cycle did not produce a validation candidate.

The correct interpretation is not that spot crypto is proven unexploitable. It is that the tested cross-sectional continuation, delayed continuation, pullback/reclaim, controlled residual mean-reversion, and volatility-normalized trend formulations did not establish a cost-aware, dependence-aware edge under the frozen v5 methodology.

The untouched May 2026 holdout was not opened.

## Corpus integrity

The frozen six-symbol universe was BTC/USD, ETH/USD, SOL/USD, XRP/USD, AVAX/USD, and LINK/USD.

Five-minute bar coverage across the complete requested archive was:

| Symbol | Coverage |
|---|---:|
| BTC/USD | 99.93% |
| ETH/USD | 97.18% |
| SOL/USD | 97.74% |
| XRP/USD | 95.72% |
| AVAX/USD | 96.53% |
| LINK/USD | 97.18% |

Development: 2026-02-01 through 2026-04-02 UTC.  
Validation: 2026-04-02 through 2026-05-02 UTC.  
Untouched holdout: 2026-05-02 through 2026-06-01 UTC.

No v5 inference reused the v1-v4 evaluation periods.

## Feature research

Nine frozen feature definitions were tested at four forward horizons, for 36 confirmatory tests. Dependence was reduced to UTC-day blocks before the moving-block null test. Benjamini-Yekutieli controlled FDR at alpha 0.05 across the complete feature family.

Result: 0/36 feature tests survived multiplicity correction.

The strongest unadjusted result was the long-only reversal hypothesis:

[
x_i = -\left(r_i(15m)-\bar r_{-i}(15m)\right)
]

against 120-minute future return.

Observed validation evidence:

- mean daily cross-sectional Spearman IC: 0.12553
- median daily IC: 0.08087
- positive-day fraction: 62.5%
- independent UTC-day blocks: 24
- raw dependence-adjusted p-value: 0.001499
- first BY decision threshold: 0.000333
- adjusted rejection: no

This is therefore hypothesis-generating evidence only. It cannot be called a validated edge.

Most continuation-style signals weakened or reversed at 60-240 minute horizons. For example, 60-minute absolute return and 60-minute market residual both had mean validation IC -0.04173 at the 60-minute forward horizon and -0.03775 at 240 minutes.

## Candidate experiments

Seventeen frozen executable configurations were evaluated under the stressed cost model. None passed its source-feature gate, none passed candidate multiplicity, and none qualified for holdout.

Best stressed-cost validation configurations by expectancy:

| Configuration | Trades | Expectancy/trade | Win rate | Profit factor | Decision |
|---|---:|---:|---:|---:|---|
| RS_PULLBACK_120 | 87 | -0.00763% | 48.28% | 0.970 | reject |
| MR_BTC_NONBEAR_120 | 40 | -0.04390% | 45.00% | 0.869 | reject |
| RS_DELAY15_240 | 81 | -0.05836% | 39.51% | 0.838 | reject |
| RS_PULLBACK_60 | 91 | -0.10839% | 40.66% | 0.515 | reject |
| RS_CONT_60 | 255 | -0.11260% | 36.08% | 0.494 | reject |

The pullback/reclaim timing model materially reduced the loss relative to immediate relative-strength continuation, but it did not cross zero after stressed costs and its source feature did not validate. It therefore remains rejected, not “almost promoted.”

Development results were also negative. The best development configuration was RS_PULLBACK_120 at -0.09378% expectancy per trade, so the validation near-break-even result was not a stable positive continuation of a development edge.

## Benchmarks

- No-trade: 0 return by construction.
- Existing RHEN rolling momentum/VWAP benchmark: 79 validation trades, -0.11684% expectancy/trade, profit factor 0.558.
- Random-entry 120-minute control: 439 trades, -0.17515% expectancy/trade, profit factor 0.449.
- Compression-breakout benchmark: +1.2601% expectancy on only 3 trades. This sample is too small for inference and had no promotional authority.
- Validation-period buy-and-hold gross returns were positive for BTC (+14.84%), ETH (+7.30%), SOL (+3.23%), XRP (+2.81%), and LINK (+1.76%) where boundary prices were available. This matters because the tested active long-only strategies lost money despite a generally positive passive backdrop.

Complexity did not earn its keep. The more structured candidates improved some loss profiles but did not establish positive stressed-cost expectancy.

## Cost model

The execution model was frozen before archive inspection using live RHEN quote telemetry. The stressed round-trip hurdle combined full observed spread plus two-sided slippage.

This was particularly punitive for wider-spread assets by design. The strategy was required to survive plausible execution friction rather than rely on midpoint returns that RHEN could not capture.

## Walk-forward analysis

The development and validation periods were chronologically separated. The methodology, feature family, candidate family, cost rules, dependence treatment, multiplicity treatment, and holdout gate were frozen before v5 archive inspection.

Validation survivors: 0.

## Holdout evaluation

Not opened.

Reason: no validation candidate satisfied both the frozen feature gate and the frozen candidate gate.

No May 2026 trade outcomes were used to rescue, rank, or retune a failed candidate.

## Robustness report

Not performed on a promoted survivor because no candidate reached holdout. This is fail-closed behavior, not missing evidence.

The relevant robustness conclusion is already visible earlier in the pipeline: the apparent opportunity signal did not survive family-wise search pressure, and all executable validation candidates had non-positive stressed-cost expectancy.

## Strategy specification

None issued.

RHEN has no crypto strategy authorized by GRAEN from v5. `CRYPTO-2026-09-29-001` remains an unvalidated/shadow lineage and must not be promoted on the basis of this work.

## Research implications

The strongest new clue is controlled short-horizon residual reversal, not continuation. That clue is insufficient for validation but is specific enough to define a future independent research generation without reopening v5.

A future v6 should treat v5 as hypothesis-generation history, freeze a much smaller reversal-focused family before reading a new untouched corpus, and explicitly compare:
- residual overreaction magnitude,
- pullback/reclaim versus immediate reversal entry,
- liquidity-tier interaction,
- BTC directional state,
- 90-180 minute thesis decay,
- and abstention when the stressed cost hurdle consumes the predicted reversal.

The v5 May holdout should remain untouched for v5 permanently; it is not a reserve dataset for retuning this generation.

## Safety / production state

The GRAEN worker completed successfully and persisted the research result. RHEN production logs continued to report `execution_enabled: False` for crypto through the completion window. The equity production lane remained deployed separately; no equity strategy or risk parameter was changed by v5.

DEVELOPMENT CONTINUES
