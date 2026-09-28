# ANEVUM Theory Problem 001

## Adaptive Inference Under Nonstationarity

**Problem ID:** ATP-001  
**Program:** ANEVUM Mathematics & Theory  
**Status:** ACTIVE  
**Started:** 2026-09-27  
**Applied laboratory:** RHEN  
**Novelty state:** UNASSESSED  
**Production authority:** NONE

### Abstract

ATP-001 studies a general decision problem: an adaptive system acts in an environment whose governing structure can change, but the structure is only partially observable and observations are noisy, delayed, and costly to act upon. The system must decide not only what action to take but when accumulated evidence justifies changing the policy that selects actions.

RHEN is the first applied laboratory because short-horizon markets make the costs of incorrect adaptation explicit: spreads, slippage, latency, false regime changes, finite capital, and opportunity cost. The mathematical program is broader than trading. The same structure appears in control, sequential decision-making, online learning, fault detection, robotics, adaptive experimentation, and resource allocation.

This note establishes the research object and vocabulary. It does **not** claim a new theorem, a proven market edge, or mathematical originality.

## 1. Research standard

ANEVUM separates five claim classes:

1. **KNOWN** — established mathematics from the literature.
2. **APPLICATION** — known mathematics applied to an ANEVUM/RHEN problem.
3. **POTENTIALLY_NOVEL** — a result that appears nontrivial after literature review but is not yet independently verified.
4. **CONJECTURE** — a falsifiable mathematical statement that has not been proved.
5. **ORIGINAL_VERIFIED** — a result for which novelty and correctness have survived explicit independent review.

No result may be presented as original merely because an internal agent generated it. Failed proofs, counterexamples, null results, and rejected conjectures remain in the permanent record.

## 2. Core model

Let the latent environment state at time (t) be

[
z_t in mathcal{Z}.
]

The system does not observe (z_t) directly. It receives an observation

[
x_t sim P_{	heta_t}(cdot mid z_t),
]

where (	heta_t) may itself change over time. RHEN chooses an action

[
a_t in mathcal{A}
]

using a policy

[
a_t sim pi_t(cdot mid mathcal{H}_t),
]

where (mathcal{H}_t) is the information available at decision time. Future information is excluded.

A first objective is

[
max_{pi}
mathbb{E}
left[
sum_{t=1}^{T}
r(a_t,z_t)
-
c(a_t,a_{t-1})
-
lambda R_t
ight].
]

Here (r) is decision reward, (c) is the cost of changing or executing actions, and (R_t) is a risk term. In a trading application, (c) must include applicable spread, slippage, latency, turnover, and other observable frictions rather than treating adaptation as free.

This objective is a modeling framework, not a theorem.

## 3. Nonstationarity

A useful abstract measure of environmental movement is a path variation budget

[
V_T
=
sum_{t=2}^{T}
lVert 	heta_t-	heta_{t-1}Vert.
]

The system generally does not know (V_T) in advance.

The important cases are therefore not simply stationary versus nonstationary. They include:

- gradual drift;
- abrupt change points;
- temporary shocks;
- recurring regimes;
- heteroskedastic noise;
- structural change in the observation process;
- structural change in the reward process;
- changes that are too small or too short-lived to justify adaptation.

ATP-001 is specifically concerned with distinguishing **actionable structural change** from noise.

## 4. Dynamic comparator

A fixed-strategy benchmark can be misleading when the environment changes. Let (a_t^*) denote a time-varying comparator. Dynamic regret can be written as

[
mathcal{R}_T
=
sum_{t=1}^{T}L_t(a_t)
-
sum_{t=1}^{T}L_t(a_t^*).
]

The objective is not to assume that (mathcal{R}_T) can always be made small. Some environments make reliable adaptation impossible. An impossibility result is therefore a valid and useful outcome.

The target is to identify model classes under which useful bounds are possible and make their dependence explicit:

[
mathcal{R}_T
le
f(
V_T,
sigma,
C_{mathrm{switch}},
C_{mathrm{trade}},
	au,
mathcal{I}_T
),
]

where (sigma) summarizes observation noise, (C_{mathrm{switch}}) policy-switching cost, (C_{mathrm{trade}}) execution friction, (	au) delay, and (mathcal{I}_T) available information.

## 5. The central decision

Suppose the system is currently operating under policy (pi_0) and evidence accumulates for a candidate policy (pi_1).

The practical decision is not:

> Has the data changed?

It is:

> Is the posterior or sequential evidence for a persistent and economically meaningful change strong enough that the expected value of switching exceeds the total cost of switching incorrectly or too early?

A schematic switching rule is

[
	ext{switch if }
mathbb{E}[Delta U mid mathcal{H}_t]
>
C_{mathrm{switch}}
+
C_{mathrm{false}}
+
C_{mathrm{estimation}}.
]

Whether an optimal or near-optimal rule admits a useful threshold form is itself part of ATP-001.

## 6. Initial conjectures

### ATP-001-C1 — Evidence-adjusted switching threshold

**Status:** OPEN  
**Novelty:** UNASSESSED

For a bounded family of nonstationary latent-state models with positive switching cost, an optimal or near-optimal adaptation rule can be expressed as an evidence threshold that increases with switching cost and false-alarm loss and decreases with expected persistence and value of the candidate regime.

**Falsification route:** exhibit an admissible model class in which threshold policies are materially dominated by valid non-threshold policies, or prove that no useful approximation bound is possible.

### ATP-001-C2 — Persistence-adjusted regime evidence

**Status:** OPEN  
**Novelty:** UNASSESSED

When change-point evidence is discounted or weighted by estimated regime persistence, adaptation can reduce false switches relative to evidence-only detection without materially increasing detection delay across a frozen model class.

**Falsification route:** establish that persistence adjustment cannot improve the false-switch versus detection-delay frontier under the frozen loss function.

### ATP-001-C3 — Value-of-information gate

**Status:** OPEN  
**Novelty:** UNASSESSED

Additional features, models, or strategy complexity should be admitted only when the expected decision value of the additional information exceeds its estimation, execution, and model-selection costs.

One generic quantity is

[
operatorname{VOI}(Y)
=
mathbb{E}
left[
max_a mathbb{E}[U(a,z)mid mathcal{H},Y]
ight]
-
max_a mathbb{E}[U(a,z)mid mathcal{H}].
]

An information source (Y) is operationally useful only if its attainable value survives the costs required to estimate and use it.

**Falsification route:** find a valid frozen setting in which the gate systematically rejects information that improves net expected utility or accepts information that cannot improve it.

## 7. Workstreams

**W1 — Latent-state representation.** Determine what a regime is allowed to mean mathematically. Avoid retrospective state labels that encode future information.

**W2 — Sequential change evidence.** Compare Bayesian, likelihood-ratio, change-point, martingale/e-value, and related sequential evidence methods under dependence and heteroskedasticity.

**W3 — Switching-cost control.** Treat adaptation itself as a decision with cost. Derive conditions under which switching dominates remaining with the current policy.

**W4 — Dynamic regret.** Study changing comparators, unknown variation budgets, delayed feedback, and lower bounds. Identify where adaptation is provably impossible or necessarily slow.

**W5 — Information value.** Measure whether a feature or data source reduces decision uncertainty enough to justify its complexity.

**W6 — Market frictions.** Translate abstract guarantees into realistic RHEN constraints: spread, slippage, liquidity, latency, finite capital, execution uncertainty, and position/risk limits.

## 8. Applied RHEN protocol

ATP-001 does not bypass RHEN's existing research controls.

The sequence is:

[
	ext{observation}
ightarrow
	ext{formal model}
ightarrow
	ext{conjecture}
ightarrow
	ext{derivation / proof attempt}
ightarrow
	ext{frozen experiment proposal}
ightarrow
	ext{development}
ightarrow
	ext{validation}
ightarrow
	ext{holdout}
ightarrow
	ext{shadow evidence}
ightarrow
	ext{separate promotion decision}.
]

A theoretical result can justify an experiment. It cannot authorize a trade.

Any applied experiment linked to ATP-001 must preserve:

- decision-time information boundaries;
- predefined endpoints and falsification criteria;
- explicit friction assumptions;
- development/validation/holdout separation;
- untouched quarantine where required;
- durable negative results;
- exact source and methodology provenance.

## 9. First research agenda

The first serious mathematical work should proceed in this order:

1. Specify the smallest latent-state model in which switching cost matters.
2. Solve or numerically characterize the optimal policy for that controlled model.
3. Determine whether the policy has a threshold structure.
4. Vary noise, persistence, delay, and switching cost to identify invariant structure.
5. Search the literature for equivalent or stronger results before asserting novelty.
6. Attempt a proof for the smallest defensible proposition.
7. Search deliberately for counterexamples.
8. Only after the mathematical object is stable, map observables from RHEN telemetry into the model.
9. Freeze an experiment before examining protected-stage outcomes.
10. Record the result whether positive, null, or contradictory.

## 10. Success and failure

ATP-001 succeeds if it produces any of the following with defensible provenance:

- a correct theorem useful to adaptive decision-making;
- a useful impossibility or lower-bound result;
- a quantitative bound that improves RHEN experiment design;
- a principled adaptation algorithm that survives frozen empirical testing;
- a clear negative result that eliminates an unproductive family of adaptive methods.

It fails scientifically only if we obscure assumptions, tune after seeing protected evidence, discard negative results, or claim novelty/performance unsupported by the record.

That standard is intentional. The research record is the product as much as any eventual algorithm.
