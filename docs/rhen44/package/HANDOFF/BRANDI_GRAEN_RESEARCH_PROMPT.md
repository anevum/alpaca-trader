# Optional independent GRAEN research workload

Canonical workload: `ANEVUM.GRAEN.RESEARCH.2026-10-06.001.ADAPTIVE-POLICY-EVIDENCE`

Act as an independent research/audit worker for RHEN 4.4 Adaptive Policy Control. Do not deploy production code, change Railway variables, modify broker authority, or authorize live risk.

Use the latest actual `anevum/rhen` evidence and the RHEN 4.4 development package.

Goal:
Determine whether the proposed policy-profile approach can improve net expectancy/capital use over fixed RHEN 4.3 without increasing unacceptable drawdown, execution cost, instability, or overfitting.

Work to perform:
- inspect current RHEN 4.3 strategy, opportunity engine, cost model, sizing and realized evidence;
- inspect existing ASC-002/005/007/008, NOSTRA regime, parameter-pressure and GRAEN validation artifacts;
- research current external literature/documentation relevant to intraday regime detection, adaptive position sizing, execution costs, volatility-conditioned exits, contextual policy selection, and small-account risk;
- distinguish general research claims from evidence actually supported by RHEN's data;
- critique the proposed DEFENSIVE / FAST_SCALP / NORMAL / TREND_EXTEND / ASSERTIVE_TREND profiles;
- identify parameters that should stay fixed because the data is too weak;
- identify candidate profile values/ranges worth freezing for VELUM/ASC testing;
- design falsification criteria and expected failure modes;
- inspect whether NOSTRA regime states are sufficiently separable/calibrated to be used for routing;
- inspect whether transaction costs erase apparent benefits;
- look for selection bias, regime leakage, multiple testing, small-sample errors and false confidence.

Deliver a self-contained research package with:
- executive conclusion;
- source/citation ledger with retrieval dates;
- RHEN evidence used;
- hypotheses;
- proposed frozen profile revisions;
- VELUM experiment definitions;
- statistical gates;
- holdout/quarantine plan;
- recommendations categorized as KEEP / TEST / REJECT / INSUFFICIENT EVIDENCE;
- explicit statement that no production promotion has been authorized.

Do not optimize toward the desired answer. A conclusion that an assertive profile should not be promoted is a valid successful result.
