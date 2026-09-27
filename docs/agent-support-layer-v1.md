# RHEN Agent Support Layer v1

The registry in `app/agent_support/registry.json` is a versioned declaration of
Research Agent, Coordinator and Verifier authority. The Coordinator and Verifier
are contract-only roles; this package does not invoke an LLM or authorize any
trading action. The Research Agent remains owned by its separate runtime.

## Deterministic input contract

`adapter.from_canonical_sources` takes the existing authenticated
`trading-report-read?latest=command` response, the existing period report
inputs (`start`/`end`), the actual exchange calendar, Railway service status
and configuration observed at check time, pre-open health, and the Research
Agent's existing sanitized `GET /v1/readiness/public` response. It extracts
only the fields the evaluator needs. The caller must supply a completed-session
calendar; weekday guesses and raw 1-minute bar counts are not substitutes.

The support layer consumes the Research Agent's `IDLE`, `READY`, `WAITING`, or
`BLOCKED` readiness verdict and its sanitized blocker, limitation, monitor,
and waiting-requirement codes. It does not repeat
the Research Agent's report or queue classification, invoke its model, or treat
`READY` as authority to run it. Missing, malformed, or inconsistent readiness
fails closed. A `BLOCKED` verdict blocks the support context for agent use;
`WAITING` is degraded and preserves the Research Agent's nonblocking historical
limitations and monitors separately from active blockers. The support layer
does not decide which evidence limitation blocks a question or reorder active
failure priority. The bounded snapshot includes only readiness state, counts,
codes, waiting requirements, trigger reference, and evidence cutoff; internal
question identities are excluded.

`app/agent_support/railway_roles.json` is the explicitly maintained **current**
RHEN project/environment role map. Each stable service ID is checked against its
live repository or image source and start command; the scheduler also requires
a cron schedule. A repurposed ID loses its mapped role and blocks integrity.
An unmapped service remains unclassified. A current name change is reported as
name drift while the source-verified role stays intact. Compare production's
observed ID and current name with canonical runtime provenance. Generated
domains and old private-endpoint labels never assign roles.

The current map records production trading, Research Agent, pre-open state and
Research Agent scheduler. It has **no** shadow/comparison assignment: Railway
currently has no separate service for that role. This yields
`SHADOW_SERVICE_UNRESOLVED`, never an implicit reassignment of the Research
Agent. Update the map only after verifying current live configuration.

`integrity.evaluate` returns `HEALTHY`, `DEGRADED`, or `BLOCKED` with stable
reason codes and evidence references. Missing critical evidence fails closed.
Daily coverage is based on expected session representation after the report
deadline (16:50 Eastern). Weekly completeness uses the canonical weekly report's
expected and included session arrays. Market-open telemetry freshness is 15
minutes. The adapter does not read protected research corpora: it accepts only
explicit required artifact references and availability flags.

`snapshot.build_snapshot` exposes a small allowlist of current service IDs,
names, roles, deployment IDs, strategy identity, report session coverage,
telemetry freshness and integrity reasons. It does not echo source payloads,
secrets, account details, orders, or research data. The CLI can evaluate a
locally supplied authorized bundle:

```bash
python -m scripts.agent_support_snapshot evidence.json
```

## Escalation persistence

`escalation.reconcile` maps reason/source/reference to one durable key. Repeated
abnormal checks update the same open record's last-observed time; disappearance
produces one resolution only when that source was actually assessed. A failed
read cannot clear an existing alert. Healthy checks create no record. The additive private
table migration `database/20260927195537_rhen_agent_support_alerts.sql` stores
these records separately from `trading_incidents`, which already participates
in canonical weekly trading reporting. The table has RLS and no public Data API
grant. Apply actions in a single transaction with an authorized dedicated
server-side connection. No chain-of-thought or full source payload is stored.

## Activation boundary

This commit provides the deterministic contracts, normalization, bounded
context, migration and tests. It does **not** schedule a worker, apply the
migration, provide new credentials, or deploy a service. Continuous checks and
durable alert writes require a separate support-only runner with scoped
read access to the existing sources and a narrow alert-table writer. Enable
that runner only after the concurrent Research Agent and Railway identity work
settles, with an authenticated private read surface for the snapshot. Never
attach it to the trading execution loop.
