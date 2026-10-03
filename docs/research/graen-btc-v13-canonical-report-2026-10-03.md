# GRAEN BTC V13 Canonical Research Report

Date: 2026-10-03  
Campaign: `btc-hypothesis-tournament-v13`  
Methodology: `graen-btc-hypothesis-tournament-v13`  
Canonical result: `V13_NO_DEVELOPMENT_SURVIVOR`  
Decision: `V13_HYPOTHESES_FALSIFIED`  
Next action: `DESIGN_NEXT_BTC_HYPOTHESIS`

## A. What V6-V12 taught us

The pre-V13 evidence map was frozen before V13 DEVELOPMENT.

| Campaign | Mechanisms examined | Canonical lesson |
| --- | --- | --- |
| V6 | Cross-sectional residual downshock reclaim | Development-only evidence did not establish a promotable edge. |
| V7 | Cross-asset lead-lag response; broad-market laggard response | Market-transmission families did not survive the protected promotion pipeline. |
| V8 | Adaptive cross-asset lead-lag; adaptive broad-market laggard response; adaptive calendar-regime drift | Adaptive threshold cycling exhausted those families rather than justifying further tuning. |
| V9 | Activity-confirmed momentum continuation | Falsified under the protected research process. |
| V10 | Activity-confirmed trend-pullback recovery | Mixed development history; a candidate freeze did not yield a valid confirmatory result because required stage corpus was incomplete. |
| V11 | BTC-only activity-confirmed trend-pullback recovery | No DEVELOPMENT survivor under stressed costs. |
| V12 | Compression breakout; volatility-normalized trend; downshock reclaim | No DEVELOPMENT survivor. Large samples still had nonpositive stressed-cost/delayed expectancy, profit factor at or below 1, and inadequate temporal-fold consistency. |

V13 therefore did not retune those same families.

## B. Frozen V13 hypothesis set

Eight candidates across four materially different BTC mechanisms were frozen:

1. `serial_dependence_reversion`
   - Negative short-horizon return autocorrelation plus a normalized downside shock and positive bar confirmation.
2. `fair_value_dislocation_reversion`
   - Large downside displacement from a causal rolling volume-weighted fair-value anchor in a non-persistent return state.
3. `return_acceleration`
   - Positive change in return velocity before the slower move is already extended.
4. `volatility_of_volatility_transition`
   - Transition from ordinary to elevated short-horizon volatility with positive signed return confirmation.

All candidates used causal BTC/USD 5-minute OHLCV-derived inputs only.

Frozen methodology included:
- DEVELOPMENT-only reuse of the previously inspected historical archive;
- forward-only temporal folds;
- high crypto spread/slippage as the selection cost scenario;
- base-versus-high cost sensitivity;
- +5 minute delayed-entry sensitivity;
- minimum trade and independent-day requirements;
- at least 5 of 7 positive temporal folds;
- dependence-adjusted null testing;
- Benjamini-Yekutieli multiplicity control;
- drawdown, win/loss, MFE/MAE, and concentration diagnostics;
- statistical survival distinguished from economic survival.

## C. Exact implementation changes

Merged implementation chain:

- PR #253, merge `914ea33af17008c55e30ff5385fc32af367fdff1`
  - added frozen `graen/crypto/btc_hypotheses_v13.py`;
  - registered native V13 Foundation/PostgreSQL stages;
  - added V12 -> V13 deterministic transition;
  - added exact V13 dispatch in VELUM;
  - added V13 forward-shadow support;
  - separated `VALIDATION` and `HOLDOUT` evidence phases;
  - added fail-closed stage guards;
  - kept all execution authority false.

- PR #254, merge `48f5c79fca42a8be0defd45e2439412510693323`
  - reused each frozen causal signal stream across fixed cost scenarios and temporal folds;
  - added interrupted DEVELOPMENT/VELUM recovery;
  - did not change candidates, gates, costs, or evidence definitions.

- PR #255, merge `859491fa433b54e85f748b88e1a3aa152fa5355b`
  - added read-only V13 claim diagnostics;
  - exposed exact native claim eligibility without changing research behavior.

A stale VELUM Railway source pin to the V12 merge was also removed. VELUM now follows canonical `main` and watches `btc_hypotheses_v13.py`.

## D. Tests and CI

For PRs #253, #254, and #255:

- CI: PASS
- Foundation runtime audit: PASS
- main test job: PASS
- `codex-postgres`: PASS
- `graen-forward-shadow`: PASS
- `velum-graen`: PASS

V13-specific regression coverage verifies:
- frozen candidate/spec determinism;
- deterministic candidate reproduction;
- one frozen signal collection per candidate;
- native Foundation stage registration;
- exact VELUM V13 dispatch;
- forward-shadow V13 compatibility;
- distinct VALIDATION and HOLDOUT evidence phases;
- VALIDATION cannot open before VELUM pass;
- HOLDOUT cannot open before validation pass;
- live/broker execution authority remains false;
- interrupted DEVELOPMENT/VELUM work can be invalidated and requeued without opening sealed evidence.

## E. Deployment state

Post-run Railway verification:

- GRAEN: live / SUCCESS
- GRAEN research executor: live / SUCCESS
- VELUM: live / SUCCESS
- crypto edge discovery: live / SUCCESS
- IREN executor: live / SUCCESS
- research agent: live / SUCCESS
- research scheduler: live / SUCCESS
- alpaca-trader: live / SUCCESS
- Foundation ingest: live / SUCCESS
- PostgreSQL: live / SUCCESS

The live trader remained on its protected production behavior. Crypto scan logs repeatedly reported `execution_enabled: False`, and no order-submit/fill event was observed during V13.

Foundation's `/v1/graen-gateway` path remained healthy after the campaign: 1,148 requests in the checked one-hour window, all 2xx.

Separate existing issue: NOSTRA's evidence query can exhaust PostgreSQL shared-memory space and return `/v1/nostra-gateway` 500. The failing SQL is confined to `nostra.*` evidence tables and was not caused by V13. It was not modified under this task.

## F. V13 DEVELOPMENT results

All expectancy values below are net returns per trade under the frozen high-cost selection scenario.

| Candidate | Mechanism | Trades | Independent days | Expectancy | PF | Max DD | Positive folds | +5m delay expectancy | Base-cost expectancy | Statistical | Economic | Result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| V13-BTC-ACCEL-15-60-A | return acceleration | 3,907 | 637 | -0.1335% | 0.346 | 99.48% | 0/7 | -0.1306% | -0.0639% | Fail | Fail | Reject |
| V13-BTC-ACCEL-30-120-B | return acceleration | 2,035 | 620 | -0.1434% | 0.446 | 94.82% | 0/7 | -0.1335% | -0.0737% | Fail | Fail | Reject |
| V13-BTC-ACREV-15-6H-A | serial-dependence reversion | 193 | 171 | -0.1263% | 0.411 | 24.01% | 1/7 | -0.1039% | -0.0566% | Fail | Fail | Reject |
| V13-BTC-ACREV-30-12H-B | serial-dependence reversion | 55 | 54 | -0.1365% | 0.557 | 11.20% | 1/7 | -0.1467% | -0.0668% | Fail | Fail | Reject |
| V13-BTC-FVREV-360-A | fair-value dislocation reversion | 40 | 38 | -0.0576% | 0.744 | 5.54% | 0/7 | -0.1237% | +0.0122% | Fail | Fail | Reject |
| V13-BTC-FVREV-720-B | fair-value dislocation reversion | 16 | 14 | -0.0954% | 0.683 | 2.44% | 0/7 | -0.1008% | -0.0257% | Fail | Fail | Reject |
| V13-BTC-VOV-60-720-A | volatility-of-volatility transition | 853 | 494 | -0.1241% | 0.580 | 66.53% | 0/7 | -0.1192% | -0.0544% | Fail | Fail | Reject |
| V13-BTC-VOV-120-1440-B | volatility-of-volatility transition | 507 | 395 | -0.0947% | 0.744 | 42.50% | 1/7 | -0.1080% | -0.0250% | Fail | Fail | Reject |

Dependence-adjusted p-value was 1.0 for every candidate. No candidate survived Benjamini-Yekutieli multiplicity control.

The faintest pre-stress signal was V13-BTC-FVREV-360-A at +0.0122% expectancy under the base-cost scenario. It failed decisively at the frozen high-cost scenario, failed the delayed-entry test, had only 40 trades / 38 independent days, passed 0/7 temporal folds, and failed multiplicity. It is not a survivor and must not be promoted or threshold-tuned into one.

## G. VELUM results

Not reached.

No candidate passed DEVELOPMENT, so opening VELUM would have violated the frozen stage order.

## H. VALIDATION results

Not reached.

## I. HOLDOUT result

Not reached.

HOLDOUT remained sealed.

## J. Promotion readiness

No V13 strategy is promotion-ready.

## K. Exact reason no strategy survived

Every V13 candidate failed both statistical and economic survival.

Across the tournament:
- all high-cost expectancies were negative;
- all profit factors were below 1;
- all delayed-entry expectancies were negative;
- temporal consistency was 0/7 or 1/7 positive folds;
- every multiplicity-adjusted statistical test failed;
- two fair-value candidates also failed minimum sample/day-block requirements;
- no candidate met the frozen DEVELOPMENT gate.

The failure is therefore not explained by one unlucky threshold or one missing gate. The tested OHLCV-derived mechanisms did not contain a defensible net BTC edge under the protected methodology.

## L. Highest-value next research action

Do not tune V13 harder.

The highest-value next campaign should move to data/mechanisms V13 did not test: BTC microstructure and execution-state evidence, especially historical bid/ask spread state, quote imbalance, trade intensity, signed trade/volume imbalance, and short-lived liquidity shocks if a sufficiently clean canonical feed is available.

Rationale:
- V9-V13 have now exhausted several bar-derived momentum, pullback, breakout, reversion, acceleration, volatility-transition, and fair-value families.
- V13's only slightly positive base-cost observation disappeared under realistic high costs, pointing directly at execution/liquidity as a first-class part of the mechanism rather than a post-hoc deduction.
- A next campaign should remain small and mechanism-driven, and should be abandoned if clean historical microstructure data cannot be obtained without leakage or material gaps.

No live BTC execution is authorized by this report.
