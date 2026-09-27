# RHEN Research Agent v1 runtime

This runtime is the first bounded model-backed RHEN agent. It wraps the existing deterministic Research Agent v1 foundation; it does not replace it.

## Authority boundary

The runtime may:
- read canonical RHEN evidence through the dedicated research-agent gateway;
- run the existing deterministic daily/weekly review and queue logic;
- invoke one configured OpenAI model for a schema-constrained semantic review;
- propose a new experiment;
- pass the proposal through the existing deterministic proposal parser and design checks;
- persist a completed agent-run audit record.

The runtime cannot:
- receive Alpaca credentials;
- call a broker;
- alter the live strategy, risk, sizing, universe, or execution configuration;
- freeze methodology;
- open DEVELOPMENT, VALIDATION, or HOLDOUT;
- access quarantine;
- promote a challenger or deploy production changes;
- revive the five terminal Edge Discovery v1 families;
- reopen Residual Downshock Rebound v2.1.

Model output is advisory. A proposed experiment remains an unapproved proposal even when deterministic design checks pass. Exact authorization remains required by the existing authorization and workflow-state machinery.

## Runtime behavior

The Railway service exposes:
- `GET /health` — configuration and isolation status only;
- `GET /v1/status` — authenticated deterministic canonical research status;
- `POST /v1/review` — authenticated manual daily/weekly review.

There is no scheduler and no autorun in v1. Model invocation is opt-in per request and also requires `RHEN_RESEARCH_MODEL_ENABLED=true`.

A run first reads canonical evidence, executes the deterministic review, and only invokes the model when the deterministic review says semantic review is warranted and no evidence-integrity blocker exists. The model has no tools. Its output is constrained to NOOP, BLOCKED, or PROPOSE_EXPERIMENT plus a concise rationale and optional proposal JSON. Any proposal is reparsed and revalidated deterministically.

The service persists only `private.trading_research_agent_runs` through the dedicated gateway. It does not write experiments, decisions, questions, manifests, stages, market data, or trading state.
