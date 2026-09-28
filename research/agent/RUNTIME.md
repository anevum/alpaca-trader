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

The separate `rhen-research-scheduler` invokes this API after the canonical post-close report. The agent has no internal timer. Railway cron remains `10 20,21 * * 1-5`; the one-shot Python entrypoint accepts delayed starts between 16:10 and 17:00 America/New_York and skips the other DST slot. It verifies the actual exchange calendar and current report date before invocation. The last trading session of a week also invokes the existing weekly review. Model invocation is opt-in per request and also requires `RHEN_RESEARCH_MODEL_ENABLED=true`.

A run first reads canonical evidence, executes the deterministic review, and only invokes the model when the deterministic review says semantic review is warranted and no evidence-integrity blocker exists. The model has no tools. Its output is constrained to NOOP, BLOCKED, or PROPOSE_EXPERIMENT plus a concise rationale and optional proposal JSON. Any proposal is reparsed and revalidated deterministically.

The service persists its audit and the existing bounded search-ledger artifacts through the dedicated gateway. Combined audit/search-ledger persistence is one transaction. It does not write experiments, decisions, questions, manifests, stages, market data, or trading state. A failed write cannot produce a successful audit for a partial transaction.

Read-only gateway requests retry transient failures up to three times. Review requests have a 240-second deadline and reject overlaps within the single worker. Canonical unique run keys protect completed audit identity. Keep one replica/worker; distributed pre-model claim protection is required before adding research replicas. Stale/failed completion is never represented as a confirmed scheduler success.

`SLACK_WEBHOOK_URL`, when configured through the existing notification route, receives meaningful completed/failed/recovered events. It grants no broker authority. Notification delivery is bounded and independent of research or trading completion.

Run `python -m scripts.staging_check` from a clean checkout to execute credential-free, network-denied simulation tests. This path does not use production or paper broker accounts.
