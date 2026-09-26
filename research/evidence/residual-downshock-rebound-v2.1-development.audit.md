# Residual Downshock Rebound v2.1 — Development Recovery Audit

Audit date: 2026-09-26

## DEVELOPMENT DECISION

**INVALID / NONCANONICAL RESEARCH ARTIFACT.** The recovered run was not accepted, no canonical development decision was recorded, and canonical DEVELOPMENT remains **NOT RUN**.

The exact Railway payload reconstructed successfully, but the evaluator violated the frozen corpus-gate ordering. It calculated forward outcomes before enforcing a corpus-integrity gate that ultimately failed. The frozen manifest requires the gate to run first and a failed corpus to stop before performance evaluation.

No rerun was performed. Validation was not run, holdout was not opened, and August–September quarantine data was not accessed.

## FACTS

- Experiment: `edge-discovery-v2-residual-downshock-rebound-v2.1`
- Experiment UUID: `1e0e2f4a-6fef-4b2b-923f-630fa830c458`
- Execution code commit: `46c2de0d84c736b12f8a3bbeb64ef4ad1d5305cc`
- Railway deployment: `ded64342-ee58-47db-8378-8bc8dfa8b6ba`
- Recovered report bytes: `139731`
- Recovered report SHA-256: `173cdf9d86a129ddb02bde6c1e60f896d1a4db5c6a88e898a25c63a8162bdf7f`
- Frozen manifest checksum: `92f8bad6362144805cc262c9188d199111a7f9c088caec56c0b0e6d579ffef7a`
- Provider/feed: Alpaca IEX, raw one-minute bars
- Fetched history: 2025-12-04 through 2026-05-08; the pre-development portion was limited to permitted residual-model training history.
- Reported access state: development opened; validation unopened; holdout unopened; quarantine untouched.

The report itself carries only the universe/configuration counts rather than enumerating the exact universe, horizons, and grid. Those exact identities were independently verified from the frozen manifest and the execution code bound to the report's commit.

## GATE RESULTS

The artifact's corpus gate was **FAIL**:

| Window | Symbol rows passing | Minimum synchronization | Minimum model availability | Result |
|---|---:|---:|---:|---|
| dev-01 | 23 / 23 | 95.7143% | 95.7143% | PASS |
| dev-02 | 23 / 23 | 95.1795% | 95.1795% | PASS |
| dev-03 | 23 / 23 | 91.3187% | 91.3187% | PASS |
| dev-04 | 22 / 23 | 87.8974% | 87.8974% | FAIL — COST |
| dev-05 | 23 / 23 | 93.1868% | 93.1868% | PASS |
| dev-06 | 22 / 23 | 84.0000% | 84.0000% | FAIL — COST |

All 2,001 expected symbol-sessions were represented, no sessions were missing, and pagination was complete for all 138 symbol/window rows. The report contains 726,277 raw one-minute bars, 155,604 derived five-minute observations, and 163 partial symbol-session diagnostics. The two failing rows were:

- COST / dev-04: synchronization and model availability `0.8789743589743589`, below `0.90`.
- COST / dev-06: synchronization and model availability `0.84`, below `0.90`.

The corrected expected-session/synchronization principle was used; raw IEX density relative to a densest symbol was not used as the primary gate.

## METHODOLOGY FIDELITY

| Item | Classification |
|---|---|
| Universe, SPY, sector mapping | MATCH |
| Alpaca IEX raw one-minute source | MATCH |
| Fixed ET five-minute bins; no filling/interpolation | MATCH |
| Residual formula, intercept, training window/minima | MATCH |
| Time-of-day robust standardization | MATCH |
| Six thresholds/floors/configuration IDs | MATCH |
| Entry convention and 30-minute cooldown | MATCH |
| 5/15/30/60-minute horizons | MATCH |
| BASE/NORMAL/STRESS costs, including 22 bps STRESS | MATCH |
| Bootstrap, sign-flip, control seeds/resamples | MATCH |
| Bonferroni and development gates | MATCH |
| Survivor ordering | MATCH |
| **Corpus gate before performance** | **MISMATCH — MATERIAL** |

The material mismatch is explicit in `run_development`: `build_observations(...)` executes before `corpus_diagnostics(...)`. Inside `build_observations`, `_observation_outcomes(...)` computes forward stock returns, forward residual returns, and MFE/MAE. The recovered report records 128,943 observations and later reports corpus FAIL. Thus the run did not stop before forward performance calculations as required by `data_quality_gate.must_run_before_performance` and `failure_action`.

## ALL SIX CONFIGURATIONS

No configuration-level result exists in the recovered report. Values must not be inferred.

| Configuration | z threshold | Residual floor | Events | STRESS expectancy | STRESS PF | Bootstrap | Controls/robustness | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|---|
| RDR21-01 | 2.0 | 0.30% | n/a | n/a | n/a | n/a | not run | NOT EVALUATED |
| RDR21-02 | 2.0 | 0.50% | n/a | n/a | n/a | n/a | not run | NOT EVALUATED |
| RDR21-03 | 2.5 | 0.30% | n/a | n/a | n/a | n/a | not run | NOT EVALUATED |
| RDR21-04 | 2.5 | 0.50% | n/a | n/a | n/a | n/a | not run | NOT EVALUATED |
| RDR21-05 | 3.0 | 0.30% | n/a | n/a | n/a | n/a | not run | NOT EVALUATED |
| RDR21-06 | 3.0 | 0.50% | n/a | n/a | n/a | n/a | not run | NOT EVALUATED |

Accordingly, event totals, STRESS expectancy, profit factor, worst-window expectancy, bootstrap bounds, sign-flip p-values, concentrations, negative-control uplifts, tail statistics, and leave-best-symbol/sector results are unavailable. There is no survivor.

## ROBUSTNESS RESULTS

Not run. The corpus-fail branch returned an empty `configurations` array.

## NEGATIVE CONTROLS

Not run. Matched non-shock and sign-flipped positive-shock results do not exist in the recovered artifact.

## LIMITATIONS

- The artifact's corpus diagnostics are internally consistent, but the overall run is invalid because forward outcome calculations occurred before the failed corpus gate.
- The recovered report is not self-contained for exact universe/grid/horizon enumeration; identity was verified against the frozen manifest and exact execution commit.
- The artifact's text says performance was not run. That is true for configuration aggregation, but it is not true for forward-outcome calculation, which occurred inside observation construction.

## CANONICAL STATE

No recovered evidence was written to `private.trading_experiments`, `private.trading_experiment_windows`, `private.trading_experiment_results`, or `private.trading_research_decisions`.

Canonical state remains planned / methodology_frozen / not_run with zero v2.1 result rows. Validation remains ineligible and unopened; holdout and quarantine remain unopened.
