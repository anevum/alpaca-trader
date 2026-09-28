# MATH-001-D

## Dependence, conditional validity, and adversarial stress testing

**Project:** MATH-001 — Discovery Reliability  
**Workstream:** MATH-001-D  
**Status:** IMPLEMENTED FOR STUDY  
**Claim class:** known martingale/sequential mathematics + ANEVUM application  
**Production authority:** NONE

### 1. Core distinction

MATH-001-B1 does not require IID observations.

Its primary null is conditional:

E[Y_i | F_{i-1}] <= 0,

with Y_i bounded in [-1,1] and the betting rule predictable.

Therefore the question is not simply:

> Are the outcomes dependent?

The relevant question is:

> Does the dependence preserve the conditional-mean null under the declared global filtration?

Some dependent processes preserve it. Others have unconditional mean zero while violating it.

This distinction is now a first-class RHEN research invariant.

### 2. Dependence that can remain compatible with B1

The D benchmark contains known-truth null processes with:

- predictable volatility states;
- volatility clustering;
- rare asymmetric bounded extremes;
- contemporaneous common-factor shocks across candidate strategies;
- common-factor shocks combined with predictable volatility clustering.

These can be dependent and heteroskedastic while preserving

E[Y_i | F_{i-1}] = 0.

For those scenarios the B1 validity claim applies.

Cross-candidate dependence is a separate dimension from temporal validity. When each candidate's terminal evidence object is a valid e-value, base e-BH can control FDR under arbitrary dependence between those e-values.

### 3. Dependence that breaks the B1 null

The benchmark deliberately includes two processes with unconditional mean zero but known conditional-null violations.

#### Overlapping MA(1)-style outcomes

Y_t = (epsilon_t + epsilon_{t-1}) / 2

with independent symmetric epsilon_t.

Unconditionally E[Y_t] = 0.

But after epsilon_{t-1} is known,

E[Y_t | F_{t-1}] = epsilon_{t-1}/2,

which is positive whenever the prior innovation is positive.

Therefore it is not a valid instance of the B1 null.

This represents the failure mode created when overlapping trades or overlapping return windows double-count information.

#### Persistent AR(1)-style outcomes

Y_t = phi Y_{t-1} + (1-phi) epsilon_t,

with 0 < phi < 1.

Again the unconditional mean is zero, but

E[Y_t | F_{t-1}] = phi Y_{t-1}.

The conditional null fails after positive states.

D labels these worlds as KNOWN_CONDITIONAL_NULL_VIOLATION. If the B1 process rejects too often there, that is evidence of assumption failure, not evidence that the martingale theorem failed.

### 4. Frozen proposal dependence contract

Every future RHEN research proposal must now predeclare these robustness checks:

- conditional_mean_residual_check;
- serial_autocorrelation_diagnostic;
- volatility_clustering_stress;
- rare_extreme_stress;
- overlap_double_counting_check.

If a proposal contains multiple candidate configurations it must additionally declare:

- cross_candidate_dependence_stress.

These checks must be present before methodology can be freeze-eligible.

The resulting dependence plan is deterministically hashed, receives a UUIDv5 identity, and is bound to the immutable proposal hash.

### 5. Raw heavy tails

The current B1 implementation accepts only normalized outcomes in [-1,1].

Therefore raw unbounded returns are outside the B1 theorem and are rejected by the implementation.

MATH-001-D does not solve this by silently clipping after looking at the data.

Instead:

- the score transformation must be defined before confirmation;
- the estimand represented by that score must be stated;
- bounded rare-extreme simulations stress skew and tail concentration inside the current valid score domain;
- a future unbounded-outcome e-process requires its own explicit assumptions and derivation.

### 6. Diagnostics

The executable D module records path-level diagnostics:

- sample mean and variance;
- lag-1 level autocorrelation;
- lag-1 squared-outcome autocorrelation as a volatility-clustering diagnostic;
- maximum absolute normalized outcome;
- longest positive run;
- longest negative run.

For candidate families it also records:

- mean pairwise correlation;
- mean absolute pairwise correlation;
- maximum absolute pairwise correlation.

These diagnostics are descriptive. They do not themselves prove or disprove the conditional null.

### 7. Benchmark laboratory

The known-truth benchmark evaluates:

#### Valid conditional-null worlds

- iid_null;
- predictable_volatility_null;
- rare_extreme_null;
- common_factor_null;
- common_factor_volatility_null.

#### Known assumption violations

- overlap_ma1_unconditional_zero;
- ar1_unconditional_zero.

For every scenario it measures:

- single-candidate anytime e-process crossing rate;
- repeated-look naive hit rate;
- confidence-sequence false-positive rate;
- familywise e-process crossing rate;
- terminal e-BH any-rejection rate;
- level autocorrelation;
- cross-candidate correlation;
- terminal and maximum e-values.

The benchmark always reports whether the B1 calibration claim applies to the scenario.

### 8. Bootstrap policy

Block/stationary bootstrap methods may be useful diagnostics for weakly dependent stationary data.

They are not used as a blanket replacement for the conditional martingale validity argument, particularly under structural change, adaptive sampling, overlapping outcomes, or unknown nonstationarity.

### 9. Interaction with MATH-001-C

MATH-001-C and D address different dependencies.

C asks how to control false discoveries across hypotheses.

D asks whether each hypothesis-specific evidence stream is itself valid under its temporal information structure.

Arbitrary dependence guarantees for e-BH or e-LOND do not rescue invalid constituent e-values. Every candidate evidence object must first satisfy its own null/e-value contract.

### 10. Boundaries

MATH-001-D does not:

- assume IID returns;
- declare autocorrelation itself to be an edge;
- interpret unconditional zero mean as sufficient for B1 validity;
- allow raw unbounded returns into the current B1 process;
- authorize automatic clipping or winsorization;
- select the final RHEN evidence policy;
- open DEVELOPMENT, VALIDATION, HOLDOUT, or quarantine;
- alter live strategy, universe, execution, risk, sizing, broker behavior, or capital allocation;
- promote a challenger.

### References

- Howard, S. R., Ramdas, A., McAuliffe, J., & Sekhon, J. (2021). Time-uniform, nonparametric, nonasymptotic confidence sequences. Annals of Statistics, 49(2), 1055–1080.
- Wang, R. & Ramdas, A. (2022). False Discovery Rate Control with E-values. Journal of the Royal Statistical Society Series B, 84(3), 822–852.
- Xu, Z. & Ramdas, A. (2024). Online Multiple Testing with E-values. AISTATS 2024, PMLR 238, 3997–4005.
- Politis, D. N. & Romano, J. P. (1994). The Stationary Bootstrap. Journal of the American Statistical Association, 89(428), 1303–1313.
