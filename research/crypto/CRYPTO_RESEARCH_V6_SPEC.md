# GRAEN Crypto-Native Research v6

Status: IMPLEMENTED / NOT YET EXECUTED ON A NEW CONFIRMATORY CORPUS  
Methodology: `graen-crypto-native-v6`  
Primary strategy: `CRYPTO-RESIDUAL-RECLAIM-001`  
Execution authority: none  
Required production state: crypto execution disabled

## Purpose

v6 converts the strongest hypothesis-generating result from v5 into one deliberately narrow, falsifiable research program.

v5 did not validate a crypto strategy. Its strongest unadjusted clue was 15-minute cross-sectional residual reversal at approximately a 120-minute forward horizon. v6 therefore tests that mechanism directly rather than reopening broad momentum, trend, breakout, or indicator searches.

## Frozen hypothesis

For asset (i):

[
\epsilon_i(t) = r_{i,15}(t) - \bar r_{-i,15}(t)
]

A candidate opportunity exists when the asset has materially underperformed the rest of the crypto panel:

[
\epsilon_i(t) < 0
]

and

[
|\epsilon_i(t)| >
\max\left(
1.5\sigma_{\epsilon,i,360m},
2 C^{stress}_i
\right).
]

The transaction-cost term is intentionally doubled. A price anomaly that is only marginally larger than modeled round-trip friction is not considered a tradable research opportunity.

## Universe

Context panel:

- BTC/USD
- ETH/USD
- SOL/USD
- XRP/USD
- AVAX/USD
- LINK/USD

Initial executable research targets:

- BTC/USD
- ETH/USD
- SOL/USD

The wider panel remains useful for estimating common crypto movement and dispersion. XRP, AVAX, and LINK are not initial execution targets because the frozen v5 live-quote cost snapshot makes their stressed short-horizon hurdles substantially larger.

## Opportunity and entry

The system scans completed 5-minute bars.

A residual downshock creates an opportunity, not an order.

The primary candidate waits up to 15 minutes for the first completed 5-minute bar satisfying both:

1. the latest 5-minute price return is positive;
2. the 15-minute residual is less negative than it was at the opportunity timestamp.

Entry occurs on the next bar open after that reclaim confirmation.

If no reclaim appears within 15 minutes, the opportunity expires.

The timing control enters immediately on the next bar open from the same opportunity stream. It is descriptive only and cannot be promoted.

## Exit

Primary exit is fixed at 120 minutes from entry.

No optimized profit target, trailing stop, adaptive exit, or NOSTRA-directed exit is part of v6. The purpose is to test the entry mechanism before adding another search dimension.

## Cost model

v6 reuses the frozen `graen-crypto-cost-snapshot-v5` execution assumptions for the first implementation. Candidate selection is performed under the high/stressed scenario.

The research runner also evaluates low/base cost scenarios only if the validation gate permits the holdout to open.

## NOSTRA integration

The current implementation emits an immutable NOSTRA-ready snapshot for every unique residual-shock opportunity.

The snapshot contains:

- 5m, 15m, and 60m returns;
- 15m leave-one-out market residual;
- trailing residual sigma;
- cross-sectional dispersion;
- BTC 15m and 60m state;
- realized volatility;
- activity change;
- stressed transaction-cost hurdle;
- shock threshold;
- residual z-score and shock/cost ratios;
- point-in-time data-quality fields.

Every snapshot is explicitly:

- `research_only=true`;
- `execution_authority=false`.

The current branch does not depend on the still-separate canonical NOSTRA ledger implementation. Once that foundation is merged, these shadow snapshots can be persisted through the canonical snapshot -> forecast -> outcome -> score chain without changing the CRR opportunity definition.

NOSTRA is not yet an entry gate. The future challenger will compare:

- CRR base;
- CRR + validated 10-minute NOSTRA forecast filter.

Until forecast skill is demonstrated out of sample, NOSTRA remains observational.

## Confirmatory feature family

v6 tests one source feature:

- negative 15-minute market residual.

Forward horizons:

- 90 minutes;
- 120 minutes;
- 180 minutes.

The three tests use hourly cross-sections, UTC-day aggregation, a moving-block null, and Benjamini-Yekutieli correction.

This is intentionally smaller than the v5 36-test feature family.

## Candidate family

Only one strategy is confirmatory:

- `CRYPTO-RESIDUAL-RECLAIM-001`.

One immediate-entry version is retained as a non-promotional timing control.

No additional parameter variants are selected from the same validation corpus.

## Corpus boundary

v6 must run on a genuinely new corpus.

The runner fails closed unless corpus provenance is explicitly verified. It also rejects any requested development/validation/holdout span that overlaps a supplied previously inspected research range.

Before a production research service is allowed to run v6, the service must assemble the complete v1-v5 inspected-range manifest and pass it to the runner. A missing provenance proof is an error, not permission to proceed.

Development, validation, and holdout ranges must be chronological and non-overlapping.

The holdout stays sealed unless the frozen validation gates pass.

## Frozen promotion gates

Validation requires all of the following:

- 120-minute source-feature test survives v6 multiplicity correction with positive IC;
- development primary candidate has at least 20 trades and positive stressed-cost expectancy;
- validation has at least 30 trades;
- validation spans at least 20 independent UTC-day blocks;
- stressed-cost validation expectancy is positive;
- dependence-adjusted validation p-value is <= 0.05;
- profit factor is > 1 when defined;
- no symbol contributes more than 70% of validation trades;
- one additional 5-minute entry-delay stress remains positive.

Only then may the holdout open.

Holdout requires:

- at least 20 trades;
- at least 15 independent UTC-day blocks;
- positive stressed-cost expectancy;
- dependence-adjusted p-value <= 0.05;
- profit factor > 1 when defined;
- no symbol contributes more than 60% of holdout trades;
- one-bar delay robustness remains positive.

Passing this research gate still does not authorize live crypto execution. A later VELUM/shadow/paper/production-readiness path remains required.

## Current state

The current implementation is intentionally research-only and has no broker path, scheduler, Railway service, or live configuration mutation.

It can be upgraded into the final subsystem topology after the ongoing ANEVUM/Codex infrastructure build stabilizes.
