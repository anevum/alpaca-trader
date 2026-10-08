# Configuration Retirement Matrix

This document defines categories, not an instruction to delete variables immediately.

Never remove a configuration key until code search, tests, runtime paths, and rollback requirements confirm it is unused.

## Category A — keep in permanent RHEN

Expected to remain:

- Alpaca credentials
- live execution authorization gates
- current strategy identity
- market data/universe configuration
- entry/exit/risk configuration
- reconciliation cadence
- canonical evidence/core storage configuration
- public/private access controls required by RHEN
- lightweight scheduler/health configuration
- Slack alert route if retained

## Category B — keep only if validated trading-critical

Candidates:

- extended-equity lane settings
- pre-open feature settings
- NOSTRA numerical feature/model settings
- volatility-aware/profit-protection/thesis-exit settings
- dynamic-universe tuning

These must survive only because they improve trading/evidence, not because a subsystem name exists.

## Category C — move out of permanent production

Target for episodic/on-demand execution:

- RHEN research semantic model settings
- GRAEN research director model settings
- model reasoning-effort settings
- model tool-call limits
- per-model research worker settings
- VELUM autorun/polling settings
- GRAEN autorun settings that only wait for research work
- Research Agent autorun settings

## Category D — retire after code migration

Strong retirement candidates:

- `IREN_MODEL_EXECUTION_AUTHORIZED`
- `IREN_MODEL_DAILY_BUDGET_USD`
- `IREN_MODEL_JOB_BUDGET_USD`
- `IREN_MODEL_MAX_CALLS_PER_JOB`
- `IREN_MODEL_MAX_OUTPUT_TOKENS`
- `IREN_MODEL_NAME`
- `IREN_MODEL_REASONING_EFFORT`
- `GRAEN_RESEARCH_DIRECTOR_AUTORUN`
- permanent production `OPENAI_API_KEY`
- obsolete independent-service URLs once modules are embedded/on-demand

Exact removal depends on implementation.

## Category E — legacy scope cleanup

Crypto has been removed from the active ANEVUM/RHEN direction.

The production variable surface still includes many `CRYPTO_*` names.

The migration must:

1. confirm no current equity/runtime path depends on them;
2. preserve old research evidence in source/history;
3. remove crypto execution/research configuration from the active production service;
4. remove obsolete Command/public references;
5. keep historical release/research records truthful.

Do not delete historical evidence merely because the lane is retired.

## Category F — topology identity cleanup

Observed role registry still contains legacy references to:

- `anevum/alpaca-trader`;
- previous service names/IDs that are no longer the active Railway topology.

After website publication and runtime cutover:

- rebuild the role registry from current service IDs/names;
- remove dead independent-service expectations;
- make the one-service runtime the canonical topology;
- keep migration aliases only where an external compatibility dependency still exists.

## Acceptance

The final production variable set should be understandable as:

`broker + strategy + risk + evidence + scheduler + access`

If a variable exists only to make an idle "agent" appear autonomous, it does not belong in permanent production.
