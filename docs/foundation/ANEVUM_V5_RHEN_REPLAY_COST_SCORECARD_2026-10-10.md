# ANEVUM V5 — RHEN Replay R1: cost-aware, never-executed paper reference scorecards

**Status:** Development primitive only. **Source:** `next_rhen/replay/scorecard.py` consumes `next_rhen/research/outcomes.py`; both are separate from legacy VELUM/GRAEN/NOSTRA deployments. [Unified V5 program](https://github.com/anevum/anevum-web/issues/259).

## What is modeled

The Replay scorer takes a complete R1 outcome document, verifies its canonical content SHA, its declared paper-only, unverified-source, no-trade and no-promotion flags, and requires **every candidate at every 5/15/60-minute window**. Qualified, rejected and unmeasurable candidates are all included to avoid hiding nonselected opportunities.

A declared static research cost model requires four bounded numeric nonnegative assumptions: **spread**, entry slippage, exit slippage and round-trip fees (in basis points). A hypothetical long reference net value is simply the observed *gross reference mark* minus the sum of the assumed costs; neither the entry nor the exit is a simulated actual fill. The output shows the observed/absent candidate count and mean gross and modeled net marks **only for supplied, observed horizons**, with a separated by-decision observed count. Missing marks are `null`, never zeros.

## What it is not

- No executed trades, fill simulator, brokerage balance, spread history, portfolio ledger, user-specific capital, margin, shorting, copied trades, or broker connection.
- No validated strategy, training/validation/holdout promotion, risk-adjusted edge, capital governor, order routing, independently attested market source or production-ready execution path.
- A source SHA proves at most internal deterministic data consistency; it cannot authenticate an off-host broker snapshot or complete upstream scanner market data.

The returned scorecard preserves `net_execution_pnl: null`, `broker_order_authority: false`, `validated_alpha: false`, `promotion_allowed: false`, and `source_quality: AWAITING_INDEPENDENT_ATTESTATION`. Live or promoted source records, tampered SHA, missing modeled costs, duplicate/scoped-out symbols, horizon-specific survivor filtering, negative/unbounded costs and fabricated zero gross returns are rejected.

## Next acceptance

Connect these pure functions to independently attested, timestamped full-population raw evidence after real paper read-only source validation. Then complete deterministic executable replay, spread/slippage calibration, out-of-sample testing, and evidence archive restore. Trading stays off until separately authorized.
