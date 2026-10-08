# Production variable retirement inventory — 2026-10-08

Session: `ANEVUM.OPS.PACKAGE.2026-10-08.001.LEAN-SYSTEMS-OVERHAUL`

## Runtime locks applied

The production RHEN service is explicitly pinned so legacy resident/model paths cannot reactivate after restart:

- `GRAEN_RUNTIME_ENABLED=false`
- `VELUM_RUNTIME_ENABLED=false`
- `VELUM_AUTORUN=false`
- `NOSTRA_STANDALONE_RUNTIME_ENABLED=false`
- `RHEN_RESEARCH_STANDALONE_RUNTIME_ENABLED=false`
- `RHEN_RESEARCH_AGENT_ENABLED=false`
- `RHEN_RESEARCH_AUTORUN=false`
- `RHEN_RESEARCH_MODEL_ENABLED=false`
- `GRAEN_RESEARCH_DIRECTOR_AUTORUN=false`
- `IREN_AUTOPILOT_ENABLED=false`
- `IREN_EXECUTOR_ENABLED=false`
- `IREN_LEGACY_MODEL_WORKER_ENABLED=false`
- `IREN_MODEL_EXECUTION_AUTHORIZED=false`

These locks preserve embedded deterministic NOSTRA, embedded deterministic evidence review, and deterministic IREN scheduling/control.

## Crypto variables safe to remove from the Railway service

Active repository code no longer contains a crypto runtime. Only historical database migrations remain.

The following production variable names are therefore configuration debris, not active RHEN inputs:

- `BTC_DAY_PREVIEW_ENABLED`
- `CRYPTO_ALWAYS_INCLUDE`
- `CRYPTO_CONFIRMATION_SYMBOLS`
- `CRYPTO_EDGE_DISCOVERY_ENABLED`
- `CRYPTO_EDGE_V2_ENABLED`
- `CRYPTO_EDGE_V3_ENABLED`
- `CRYPTO_EXECUTION_ENABLED`
- `CRYPTO_EXECUTION_MODE`
- `CRYPTO_LANE_ENABLED`
- `CRYPTO_MAX_CONCURRENT_POSITIONS`
- `CRYPTO_MAX_ENTRIES_24H`
- `CRYPTO_MAX_HOLD_MINUTES`
- `CRYPTO_MAX_ORDER_NOTIONAL`
- `CRYPTO_MAX_QUOTE_AGE_SECONDS`
- `CRYPTO_MAX_SPREAD_PCT`
- `CRYPTO_MAX_TOTAL_POSITION_NOTIONAL`
- `CRYPTO_ONLY_RUNTIME`
- `CRYPTO_ORDER_NOTIONAL`
- `CRYPTO_PAPER_CANARY_URL`
- `CRYPTO_POLL_SECONDS`
- `CRYPTO_REENTRY_COOLDOWN_MINUTES`
- `CRYPTO_RESEARCH_ENABLED`
- `CRYPTO_STATS_START_AT`
- `CRYPTO_STOP_PCT`
- `CRYPTO_STRATEGY_FAMILY`
- `CRYPTO_STRATEGY_VERSION_ID`
- `CRYPTO_TARGET_PCT`

Historical migrations and research records must remain in source/history.

## Legacy model variables no longer required by the permanent runtime

After the lean cutover the permanent runtime does not require an OpenAI/model call.

Retirement candidates from the permanent Railway service include:

- `OPENAI_API_KEY`
- `GRAEN_RESEARCH_DIRECTOR_TOKEN`
- `GRAEN_RESEARCH_DIRECTOR_AUTORUN`
- `IREN_MODEL_DAILY_BUDGET_USD`
- `IREN_MODEL_EXECUTION_AUTHORIZED`
- `IREN_MODEL_JOB_BUDGET_USD`
- `IREN_MODEL_MAX_CALLS_PER_JOB`
- `IREN_MODEL_MAX_OUTPUT_TOKENS`
- `IREN_MODEL_NAME`
- `IREN_MODEL_REASONING_EFFORT`
- `RHEN_RESEARCH_AGENT_ENABLED`
- `RHEN_RESEARCH_AUTORUN`
- `RHEN_RESEARCH_MODEL`
- `RHEN_RESEARCH_MODEL_ENABLED`
- `RHEN_RESEARCH_REASONING_EFFORT`

If legacy semantic/director code is ever invoked as a bounded ephemeral job, supply its credentials to that job rather than restoring them as a permanent RHEN dependency.

## Removal limitation

The current Railway connector can set/overwrite variables but does not expose a delete-variable operation. Values were therefore not blanked or recreated. Delete the variables above through Railway's variable UI when convenient; the explicit false runtime locks already prevent activation.
