# ATP-001 Proposition P1

## Exact myopic switching thresholds in the symmetric two-state model

**Artifact:** ATP-001-P1  
**Claim class:** APPLICATION  
**Status:** DERIVED  
**Novelty claim:** NONE  
**Supports:** ATP-001-C1 only in a deliberately restricted one-period model

### Setup

Let the latent state be \(z\in\{0,1\}\), the action be \(a\in\{0,1\}\), and

\[
p=P(z=1\mid\mathcal H).
\]

A correct action receives reward \(r_c\), an incorrect action receives \(r_w\), with

\[
r_c>r_w.
\]

Changing from the previous action costs \(\kappa\ge 0\).

For action \(1\),

\[
u_1(p)=p r_c+(1-p)r_w.
\]

For action \(0\),

\[
u_0(p)=(1-p)r_c+p r_w.
\]

Therefore

\[
u_1(p)-u_0(p)
=(2p-1)(r_c-r_w).
\]

### Proposition

If the previous action is \(0\), switching to action \(1\) is myopically optimal exactly when

\[
p>
\frac12+
\frac{\kappa}{2(r_c-r_w)}.
\]

If the previous action is \(1\), switching to action \(0\) is myopically optimal exactly when

\[
p<
\frac12-
\frac{\kappa}{2(r_c-r_w)}.
\]

Thus a positive switching cost creates a no-switch or hysteresis interval of width

\[
\frac{\kappa}{r_c-r_w}.
\]

### Proof

When the previous action is \(0\), switching to \(1\) pays the switching cost. Switching is preferred exactly when

\[
u_1(p)-\kappa>u_0(p).
\]

Substituting the reward difference gives

\[
(2p-1)(r_c-r_w)>\kappa.
\]

Since \(r_c-r_w>0\),

\[
p>
\frac12+
\frac{\kappa}{2(r_c-r_w)}.
\]

The reverse case is symmetric. With previous action \(1\), switching to \(0\) is preferred when

\[
u_0(p)-\kappa>u_1(p),
\]

so

\[
-(2p-1)(r_c-r_w)>\kappa,
\]

which yields

\[
p<
\frac12-
\frac{\kappa}{2(r_c-r_w)}.
\]

Subtracting the lower threshold from the upper threshold gives the hysteresis width.

\[
\square
\]

### Interpretation

This establishes an exact threshold structure only for the one-period symmetric model. It does **not** prove ATP-001-C1 for general nonstationary partially observed environments.

It does establish a useful baseline: once changing policy has positive cost, rational adaptation should require stronger evidence than rational persistence. A system that uses the same threshold for changing and staying ignores the switching cost by construction.

For the normalized case

\[
r_c=1,\qquad r_w=-1,
\]

the thresholds become

\[
p_{1\rightarrow0}<\frac12-\frac{\kappa}{4},
\qquad
p_{0\rightarrow1}>\frac12+\frac{\kappa}{4}.
\]

At \(\kappa=0.4\), that gives \(0.4\) and \(0.6\).

### Computational extension

The companion module app/research_agent/atp001.py solves a finite-horizon two-state hidden Markov version numerically. It adds:

- persistent latent-state transitions;
- noisy binary observations;
- Bayesian belief updates;
- future value;
- explicit switching cost.

The numerical solver is not treated as a proof. Its purpose is to identify whether threshold/hysteresis structure survives beyond the analytically solved myopic case, find counterexamples, and generate sharper propositions for later proof attempts.
