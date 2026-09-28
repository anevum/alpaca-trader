# MATH-001-C

## Multiplicity control for adaptive RHEN research

**Project:** MATH-001 — Discovery Reliability  
**Workstream:** MATH-001-C  
**Status:** IMPLEMENTED FOR STUDY  
**Claim class:** known mathematics + ANEVUM application  
**Production authority:** NONE

### 1. Problem

Once RHEN searches more than one hypothesis, a per-hypothesis error threshold is not enough.

MATH-001-A1 now records the search denominator. MATH-001-B1 provides anytime-valid evidence for an individual candidate. MATH-001-C specifies how evidence is interpreted when multiple hypotheses are considered.

The first implementation deliberately separates two settings:

1. fixed confirmatory families known before results are inspected;
2. an open-ended stream of confirmatory hypotheses whose eventual total count is unknown.

### 2. Fixed-family p-value controls

The study engine implements:

- Bonferroni — family-wise error rate (FWER), arbitrary dependence;
- Holm — step-down FWER, arbitrary dependence;
- Benjamini-Hochberg (BH) — false discovery rate (FDR), requiring independence or PRDS;
- Benjamini-Yekutieli (BY) — FDR with the harmonic correction for arbitrary dependence.

BH is never treated as an arbitrary-dependence procedure. A proposal using BH must freeze an explicit independence/PRDS assumption.

### 3. Fixed-family e-value control

For a fixed family of K e-values ordered

E_[1] >= ... >= E_[K],

base e-BH at level alpha selects the largest k such that

E_[k] >= K / (alpha k),

and rejects the hypotheses associated with the largest k e-values.

For raw e-values, base e-BH controls FDR under arbitrary dependence.

RHEN therefore treats e-BH as the main fixed-family candidate when its evidence objects are valid e-values from the frozen MATH-001-B construction.

A proposal using e-BH must freeze the exact source of the e-values. A string label is not sufficient.

### 4. Online e-LOND

For an unbounded sequence of hypotheses, let gamma_t >= 0 with

sum_t gamma_t <= 1.

With R_{t-1} the prior discovery set, e-LOND assigns

alpha_t = alpha gamma_t (|R_{t-1}| + 1)

and rejects H_t when

E_t >= 1 / alpha_t.

The implemented deterministic study discount is

gamma_t = 1 / (t(t+1)),

whose infinite sum is one.

This is a transparent baseline rather than an optimized allocation.

### 5. Asynchronous e-LOND

RHEN experiments may overlap in calendar time. A later hypothesis may be launched before an earlier experiment finishes.

The implemented asynchronous form therefore computes the launch-time alpha using only prior experiments whose decisions are already complete:

alpha_t = alpha gamma_t (|R_completed,t-1| + 1).

Incomplete experiments are pessimistically treated as non-discoveries for the new launch.

### 6. Proposal freeze contract

Every proposal with more than one candidate configuration must now have a deterministic multiplicity plan before it can be freeze-eligible.

Required minimums:

- supported method;
- explicit alpha;
- fixed family scope;
- exact family size from the immutable configuration list;
- method-specific assumptions.

Additional requirements:

- BH requires a frozen independence/PRDS assumption;
- e-BH requires a frozen e-value source;
- online e-LOND is not accepted as a substitute for a fixed-family within-proposal procedure.

The resulting plan is deterministically hashed and assigned a UUIDv5 identity. It is bound to the immutable proposal hash.

### 7. Search-ledger integration

The multiplicity plan is embedded into the same MATH-001-A1 search-ledger artifact as the proposal.

This prevents a procedure from being selected after results are seen.

The plan records:

- method;
- alpha;
- family scope;
- family size;
- input type;
- error metric;
- dependence scope;
- method configuration;
- search generation;
- plan hash;
- proposal hash.

### 8. Benchmark laboratory

The MATH-001-C benchmark compares:

- Bonferroni;
- Holm;
- BH;
- BY;
- e-BH;
- e-LOND;
- async-e-LOND.

Known-truth scenarios currently include:

- independent global null;
- signed common-factor dependent global null;
- independent sparse alternatives;
- signed common-factor dependent sparse alternatives.

Metrics are:

- empirical FDR;
- family-wise false-discovery probability;
- empirical power;
- mean discovery count.

The simulated one-sided p-values are exact under standard normal null statistics. The e-values are normal likelihood-ratio e-values

E = exp(theta Z - theta^2 / 2),

which have expectation one under Z ~ N(0,1).

These simulations are diagnostic. They do not replace the theorems establishing the procedures' stated guarantees.

### 9. Separation from A1 search exposure

A1 search exposure and C multiplicity correction are related but are not the same object.

Development variants that were generated after looking at a dataset are permanently recorded by A1. MATH-001-C does not invent a numerical penalty such as "multiply alpha by all historical variants."

Instead:

- contaminated observations cannot become untouched confirmation;
- confirmatory families/streams are frozen and counted explicitly;
- the chosen valid multiple-testing procedure is applied to the corresponding confirmatory evidence objects;
- historical search exposure remains visible for selection-bias and corpus-reuse analysis.

### 10. Boundaries

MATH-001-C does not:

- declare any RHEN edge real;
- choose the final organization-wide alpha or FDR target;
- convert exploratory Development results into confirmatory evidence;
- open DEVELOPMENT, VALIDATION, HOLDOUT, or quarantine;
- alter live strategy, risk, sizing, execution, broker behavior, or universe;
- promote a challenger;
- claim novelty for Bonferroni, Holm, BH, BY, e-BH, e-LOND, or async-e-LOND.

The organization-wide online multiplicity policy remains STUDY_ONLY_UNFROZEN until MATH-001-H selects a reliability policy after the complete simulation program.


### References

- Holm, S. (1979). A Simple Sequentially Rejective Multiple Test Procedure. Scandinavian Journal of Statistics, 6(2), 65–70.
- Benjamini, Y. & Hochberg, Y. (1995). Controlling the False Discovery Rate: A Practical and Powerful Approach to Multiple Testing. Journal of the Royal Statistical Society, Series B, 57(1), 289–300.
- Benjamini, Y. & Yekutieli, D. (2001). The Control of the False Discovery Rate in Multiple Testing under Dependency. Annals of Statistics, 29(4), 1165–1188.
- Wang, R. & Ramdas, A. (2022). False Discovery Rate Control with E-values. Journal of the Royal Statistical Society Series B, 84(3), 822–852.
- Xu, Z. & Ramdas, A. (2024). Online Multiple Testing with E-values. Proceedings of AISTATS 2024, PMLR 238, 3997–4005.
