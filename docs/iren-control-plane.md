# IREN deterministic control plane v1

IREN shares the canonical scheduler runtime. The existing Railway service is
retained so internal DNS, credentials, and scheduled workflow ownership stay
stable. Start with `uvicorn app.iren.service:app --host 0.0.0.0 --port 8080`.

The supervisor observes RHEN, VELUM, pre-open capture, and GRAEN once per minute.
It reads the canonical job ledger and RHEN's protected configuration fingerprint.
It does not invoke a model, consume ChatGPT work credits, place orders, change
parameters, restart services, create infrastructure, or promote research.
Research Agent sleep is expected; IREN does not repeatedly wake it for health
checks. NOSTRA remains explicitly waiting for activation. Its separate foundation
PR must be validated before activation.

## State and incident rules

Rules live in `app/iren/policy.json`. Broker reconciliation, unexpected live
strategy, crypto execution policy mismatch, and configuration drift raise an
immediate critical incident. Service and evidence warnings open after two
observations. Recovery requires three healthy observations. Pending warnings
already make aggregate health DEGRADED. Old scheduler misses remain history;
only the latest execution per workflow within 24 hours affects current state.

The first protected fingerprint is an **observed** baseline, not evidence of
human approval. IREN does not automatically accept later drift. The live
strategy and crypto execution policy also have explicit expected values.
Clearing a legitimate fingerprint change requires a separately reviewed policy
or baseline update; there is no autonomous reset endpoint.

`private.iren_control_state` preserves counters, incidents, fingerprint, observed
metrics, source commit, and scheduler state through restarts. An atomic revision
check fences concurrent observers. Incident transitions are written in the same
transaction to `private.iren_control_events`. Only transition notifications are
sent through the existing Slack routing layer. Delivery has a 120-second lease
and at most three attempts. Delivery is at-least-once: a crash after Slack accepts
but before completion persistence can duplicate a notification. No action or
trade is duplicated by this outbox.

The gateway uses the existing revoked-token-aware ingest authentication. Tables
are private with RLS and no public/user grants; the SQL function is invoker-only.
The service receives no Alpaca credentials or production ADMIN_TOKEN.

## Interfaces

- `/health`: process readiness, current control state, and durable freshness.
  Readiness is distinct from downstream health to avoid restart loops during an
  incident. `durable_state_current=false` is never a healthy control observation.
- `/v1/iren/status`: authenticated durable state, transitions, revision, and
  stale marker; header `x-anevum-scheduler-token` required.
- `/v1/iren/policy`: authenticated rules and capability boundary.
- Existing scheduler `/v1/status`, `/v1/diagnostics`, `/v1/registry` are preserved.
- RHEN `/v1/scheduler/configuration`: authenticated fingerprint read only.
- Scheduler gateway `iren_read`: canonical state for a future Command consumer.

Command can consume the gateway/server API; this release does not modify the web
frontend. IREN runtime identity comes from Railway's commit, not GitHub's latest
branch head. Deployment/config inventory is verified during release; v1 does not
hold Railway management credentials or claim to monitor provider inventory.

## Release and rollback

Run the IREN tests plus scheduler, production hardening, research, VELUM and
pre-open safety tests. Apply the additive private schema migration, deploy the
gateway, deploy RHEN's read-only configuration endpoint, then switch the existing
scheduler service's start command. Preserve every existing scheduling variable.
Require fresh persisted observations, a working authenticated status response,
and a second revision before declaring runtime verified.

Rollback the scheduler start command to
`uvicorn app.orchestration_scheduler:app --host 0.0.0.0 --port 8080` and redeploy.
Additive private tables and gateway actions can remain without affecting trading
or scheduling. Historical evidence and terminal research generations are kept.
