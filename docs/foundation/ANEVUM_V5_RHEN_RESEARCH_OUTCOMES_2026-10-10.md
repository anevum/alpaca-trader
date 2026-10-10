# ANEVUM V5 — RHEN Research R1 counterfactual outcome maturation

**Program:** ANEVUM V5 Unified Overhaul. **Reference:** [anevum-web #259](https://github.com/anevum/anevum-web/issues/259) and [RHEN #468](https://github.com/anevum/rhen/issues/468).  
**Release:** DRAFT CODE / PAPER RESEARCH ONLY / NOT CONNECTED TO LIVE BROKER OR MEMBER EXECUTION.  
**Module:** `next_rhen/research/outcomes.py`; tests `tests/test_v5_research_outcomes.py`.

## Function

`mature_cycle(cycle, anchor_prices, horizon_prices, evaluated_at)` accepts one previously recorded canonical F1 decision-cycle event and explicitly supplied 1-minute-price close observations:

- **Universe:** Exactly the journal's full candidate population, including **QUALIFIED**, **REJECTED** and **UNMEASURABLE** rows. No post-selection survivors-only sample.
- **Windows:** Exactly **5, 15 and 60 minutes** after the decision timestamp. Return label is hypothetical long-reference gross mark-to-market basis points, `10000 * (horizon_close / anchor_close - 1)`. No fills, brokerage marks, commissions, spreads, funding, orders, executed P&L or risk-adjusted alpha.
- **Source contract:** Each anchor/future price needs an explicit timezone timestamp and 64-digit source content digest. Anchors must be at/before the decision and no more than three minutes old. Future marks must have the **exact specified due timestamp** and must not be later than `evaluated_at`. Extra symbols, horizons, fields, malformed refs, nonpositive/nonfinite prices, invalid/changed full population, or future timestamps cause explicit errors.
- **Missing maturity:** `NOT_YET_DUE`, `MISSING_ANCHOR` and `MISSING_HORIZON` produce **null** gross return values, never fabricated zeroes, stale backfills or optimistic outcome counts.
- **Evidence quality:** Even complete supplied price tuples remain `OBSERVED_UNATTESTED` and overall `AWAITING_INDEPENDENT_ATTESTATION`. A source SHA is a reference, not proof of upstream complete collection or immutable off-host custody.
- **Authority:** The output explicitly reports `execution_mode=PAPER_RESEARCH_ONLY`, `orders_or_positions=false`, `execution_costs_included=false`, `alpha_validated=false` and `strategy_promotion_allowed=false`.
- **Repeatability:** Results are deterministic and include the canonical F1 decision-cycle digest and a canonical output content digest.

## Tests and integration

Full RHEN CI runs an isolated test matrix proving 5/15/60-maturity status, explicit missing data, negative returns for both accepted and rejected candidates, unmeasurable candidates with independent hypothetical marks, adversarial symbol/time/price/source scope rejection, and repeatable output digests.

The module is a **foundation primitive**. To complete the actual research program, independently attest the upstream full scanner population, verify remote raw bar/quote storage and timestamped source windows, connect authenticated read-only market evidence to this pure scorer, verify all expected scheduled cycles, and score prospective paper-session data with a cost-aware replay contract and untouched HOLDOUT evaluation. No acceptance of profitability, user accounts, live/paper order submission, or promotion follows from these tests.

## Non-goals and blockers

- No live market-data HTTP, Alpaca OAuth, broker orders, capital, positions, or founder brokerage access.
- No user-facing dashboard, production D1 changes, Railway provisioning, Cloudflare bindings, R2 data writes, or PR merges.
- No inference that a source hash authenticates an upstream observation. Never label the resulting paper gross returns as profits or a validated trading edge.
