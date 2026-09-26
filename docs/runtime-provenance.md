# RHEN runtime provenance

RHEN separates strategy provenance from runtime provenance.

- `private.trading_strategy_versions.git_commit` and `deployment_id` identify the code/deployment that defined or activated that immutable strategy version.
- `private.trading_runs.git_commit` and `deployment_id` identify the most recent runtime/deployment executing the existing logical run.
- `private.trading_runs.config_snapshot.provenance.run_origin` preserves the run's pre-provenance origin values.
- Every `runtime_start` event is append-only and contains a unique `runtime_instance_id` plus Railway deployment metadata.
- A new deployment with the same strategy/run updates runtime provenance but does not create a duplicate run.
- A restart of the same deployment produces a new runtime instance event with the same deployment ID.
- Missing commit/deployment metadata is recorded explicitly as partial; no SHA or deployment ID is fabricated.
- Startup reconciliation semantics remain unchanged.
