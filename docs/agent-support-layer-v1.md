# RHEN Agent Support Layer v1

The registry in `app/agent_support/registry.json` is a versioned declaration of
Research Agent, Coordinator and Verifier authority. The Coordinator and Verifier
are contract-only roles; this package does not invoke an LLM or authorize any
trading action. The Research Agent remains owned by its separate runtime.

## Deterministic input contract

`adapter.from_canonical_sources` takes the existing authenticated
`trading-report-read?latest=command` response, the existing period report
inputs (`start`/`end`), the actual exchange calendar, Railway service status
and configuration observed at check time, and pre-open health. It extracts
only the fields the evaluator needs. The caller must supply a completed-session
calendar; weekday guesses and raw 1-minute bar counts are not substitutes.

Railway roles derive from start commands and, if there are two `app.main`
services, an observed `scan_only` boolean. A missing value leaves those roles
unclassified. No current service name is pinned into the registry. Compare the
observed stable ID and current name with canonical runtime provenance. A
historical domain or alias is not proof of a current service role.

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
