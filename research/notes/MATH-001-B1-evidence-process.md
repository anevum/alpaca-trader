# MATH-001-B1

## Canonical mixture e-process and confidence-sequence companion

**Project:** MATH-001 — Discovery Reliability  
**Artifact:** MATH-001-B1  
**Claim classes:** KNOWN mathematics + ANEVUM application  
**Status:** IMPLEMENTED FOR STUDY  
**Production authority:** NONE

### 1. Statistical object

For a frozen candidate hypothesis h, let Y_{h,i} be its normalized net outcome at the i-th eligible opportunity, with

-1 <= Y_{h,i} <= 1.

The filtration F_{i-1} contains exactly the information legitimately available before the i-th outcome.

The MATH-001 v1 null is

H_{0,h}: E[Y_{h,i} | F_{i-1}] <= 0 for every i.

This is a conditional no-positive-edge null. It allows temporal dependence and heteroskedasticity; it does not require IID observations.

### 2. Fixed-lambda e-process

Choose a predictable betting fraction lambda satisfying

0 <= lambda < 1.

Define

E_n^(lambda) = product_{i=1}^n (1 + lambda Y_{h,i}).

Because Y_{h,i} >= -1, every factor is nonnegative. Under H0,

E[E_n^(lambda) | F_{n-1}]
= E_{n-1}^(lambda) * (1 + lambda E[Y_{h,n} | F_{n-1}])
<= E_{n-1}^(lambda).

Therefore E_n^(lambda) is a nonnegative supermartingale with initial value 1.

Ville's inequality then gives

P_H0(sup_n E_n^(lambda) >= 1/alpha) <= alpha.

Repeated inspection of this process does not create the usual optional-stopping inflation associated with repeatedly checking a fixed-sample p-value.

### 3. Mixture construction

No single lambda is appropriate for every effect size. To avoid selecting lambda after observing the result, freeze a collection lambda_1,...,lambda_J and nonnegative weights w_j summing to one.

Define

E_n = sum_{j=1}^J w_j E_n^(lambda_j).

A nonnegative weighted sum of nonnegative supermartingales is a nonnegative supermartingale, so E_n is also an e-process.

The implemented study grid is

(0.01, 0.025, 0.05, 0.1, 0.2, 0.4, 0.7)

with equal weights unless a frozen experiment explicitly supplies another predeclared mixture.

This grid is a study configuration, not the final RHEN evidence policy. MATH-001 simulations must evaluate whether another frozen mixture is preferable before any confirmation standard is locked.

### 4. Anytime p-value companion

Given the e-process path, define

p_n^any = min(1, 1 / max_{k<=n} E_k).

This is a conservative anytime-valid evidence summary under the same null.

It is not a posterior probability and must not be presented as the probability that an edge is real.

### 5. Confidence-sequence companion

The e-process answers whether the strong conditional null can be rejected. A separate quantity is useful for effect magnitude.

Let

mu_i = E[Y_i | F_{i-1}].

For fixed n, Hoeffding-Azuma for bounded adapted observations implies

P(sum_{i=1}^n (Y_i - mu_i) >= t) <= exp(-t^2/(2n)).

Allocate error over all positive integers using

alpha_n = 6 alpha / (pi^2 n^2),

so that sum_n alpha_n = alpha.

Setting the fixed-time bound equal to alpha_n gives

r_n = sqrt((2/n) log(1/alpha_n)).

Therefore, with probability at least 1-alpha simultaneously for all n,

(1/n) sum_{i=1}^n mu_i
>=
bar Y_n - r_n.

The implemented lower confidence sequence is

L_n = max(-1, bar Y_n - sqrt((2/n) log(pi^2 n^2/(6 alpha)))).

This lower-bounds the running average conditional expectancy. It does not assume stationarity.

The bound is intentionally conservative. Later MATH-001 work may replace it with a sharper confidence sequence only if the replacement's assumptions and coverage are explicitly verified.

### 6. G1 null-search experiment

The implementation also includes the first known-truth simulation.

Every candidate receives IID Rademacher outcomes:

P(Y=1)=P(Y=-1)=1/2.

Hence every candidate has exactly zero expectancy.

The naive comparator repeatedly applies a single-test fixed-time Hoeffding threshold to every candidate at every checkpoint while ignoring repeated looks and the number of candidates.

The anytime comparator gives each candidate the mixture e-process and uses the temporary familywise threshold

K/alpha

for K candidates. By Ville's inequality plus the union bound,

P(any null candidate ever crosses K/alpha) <= alpha.

This conservative familywise construction is only the G1 safety comparator. It is not the final MATH-001 multiplicity policy; e-BH/e-LOND and other procedures remain part of the later multiplicity workstream.

### 7. Implementation

Canonical study implementation:

app/research_agent/math001.py

Tests:

tests/test_math001.py

Reproducible G1 runner:

scripts/math001_g1.py

### 8. Boundaries

MATH-001-B1 does not:

- declare an edge confirmed;
- open DEVELOPMENT, VALIDATION, HOLDOUT, or quarantine;
- change research-stage transition rules;
- change RHEN strategy, universe, execution, risk, sizing, broker behavior, or capital allocation;
- set the final alpha, FDR target, effect-size threshold, or betting mixture;
- claim mathematical novelty.

It establishes the first valid evidence primitive that later MATH-001 work can test and refine.
