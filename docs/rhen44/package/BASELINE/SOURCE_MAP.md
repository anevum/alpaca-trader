# Existing foundation to preserve

The release is an extension of current RHEN architecture, not a replacement.

## RHEN runtime

- `app/execution.py` — current order-selection/management path. Do not create a second execution engine.
- `app/risk.py` — authoritative account/order safety gates. Adaptive policy must not bypass it.
- `app/sizing.py` — current equity-risk sizing, gross exposure, position limits, portfolio stop-risk budget.
- `app/capital_allocator.py` — RHEN 4.3 bounded capital ranking and opportunity multiplier primitives.
- `app/opportunity.py` and `app/execution_costs.py` — cost-aware opportunity economics from the 4.3 opportunity engine.
- `app/config.py` — runtime settings and protected configuration surface.
- `app/persistence.py` — canonical durable evidence.
- `app/main.py` — runtime API/Command contract.

## Existing adaptive research control

- `docs/iren-asc-v1.md` — ASC-001 through ASC-009 authority design.
- `docs/asc-002-nostra-regime.md` — deterministic NOSTRA point-in-time regime state, currently shadow/read-only.
- `app/research_agent/nostra_regime.py` — current transparent regime classifier.
- `app/research_agent/adaptation_proposal.py` — bounded proposal mechanism and parameter ranges.
- `app/research_agent/adaptive_shadow.py` — fixed-versus-adaptive next-session validation with no-lookahead controls.
- `docs/asc-007-adaptive-shadow.md` — validation floors: 10 sessions, 100 complete candidates, 30 differential decisions, >=95% coverage, consistency/sign-test/utility gates.
- `app/research_agent/graen_adaptive_validation.py` — selection-bias, dependence, multiple-testing, walk-forward, and frozen holdout checks.
- `app/research_agent/promotion_gate.py` — ASC-008 protected promotion gate and activation contract.
- `app/research_agent/strategy_router.py` — validated family ranking/NO_TRADE research surface.
- `app/research_agent/parameter_pressure.py` — detects repeated pressure toward adaptive bounds without widening them.
- `docs/graen-adaptive-validation.md` — research validation boundary.
- `docs/iren-asc-v1-rollout.md` — original shadow-only rollout/rollback contract.

## RHEN 4.3 opportunity engine foundation

- `docs/rhen-v4-wide-universe-opportunity-engine.md`
- `app/capital_allocator.py`
- `app/execution_costs.py`

Important 4.3 principle retained in 4.4:

> Broad discovery. Narrow execution. Variable allocation. Net expectancy first.

4.4 adds adaptive policy selection on top of that economic/risk framework.

## Operating authority

`docs/anevum-operating-model-v4.md` is controlling. Repetitive deterministic work may run automatically. Material production strategy changes, new hypothesis families, portfolio-risk expansion, broker-write expansion, and protected production release remain protected operations.

## Command/web integration

Repository: `anevum/anevum-web`

Relevant current surfaces include:

- `src/pages/Command.tsx`
- `src/components/CommandTradingLanes.tsx`
- `src/components/CommandDiscoveryDeck.tsx`
- `src/components/CommandResearchLab.tsx`
- `src/components/CommandReviewDeck.tsx`
- `src/components/CommandPerformance.tsx`
- `src/hooks/useCommandObservation.ts`
- `src/lib/command-events.ts`
- `src/styles/command-v4.css`
- command test files under `tests/`

4.4 should extend the current operator-question architecture rather than add an isolated "adaptive AI" page.


## RHEN 4.4.002 added sources

- completed Extended 24/5 deep research report bundled at `RESEARCH/EXTENDED_24_5_RESEARCH_REPORT.md`;
- Alpaca current Market Data API docs for Basic vs Algo Trader Plus entitlements;
- Alpaca current real-time stock WebSocket docs for IEX/SIP/BOATS/overnight feeds;
- Alpaca current 24/5 Trading docs for overnight eligibility/order/data semantics;
- Alpaca current Trading WebSocket docs for `trade_updates`.

These external capabilities must be reverified at implementation time if Alpaca documentation has changed.
