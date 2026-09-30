# IREN v1 production verification — 2026-09-30

Verified at approximately 07:40 America/New_York.

## Release identity

- Runtime source commit: `1b7166856acf530e2e6deadd94ec1741ed9e909c`.
- Implementation: https://github.com/anevum/alpaca-trader/pull/115 (merged).
- Runtime verification hardening: https://github.com/anevum/alpaca-trader/pull/116 (merged).
- CI: https://github.com/anevum/alpaca-trader/actions/runs/36709338124 (success).
- Full local staging suite: **662 passed**, with four existing FastAPI deprecation warnings.
- IREN version: `iren-control-v1.0.0`.
- Scheduler version: `anevum-scheduler-v1.0.0`.
- IREN/scheduler deployment: `dd6ac966-5091-4df5-b55a-185ce60babff`.
- RHEN deployment: `966c0ff4-4df8-4415-8534-f9ef45657819`.
- Scheduler gateway: version 3.
- Migration: `20260930112436_iren_deterministic_control_plane_v1`.

## Runtime evidence

IREN is running in the existing `rhen-research-scheduler` service. No additional
worker service was created. The continuous scheduler and supervisor remain
separate tasks in one process; there is one canonical scheduling ledger.

Verified production API responses: unauthenticated IREN status **401**,
authenticated IREN status **200**, preserved scheduler registry **200**.
The gateway independently rejects an unauthenticated `iren_read` request with
**401**. Anonymous/user roles cannot read the private IREN tables or invoke its
commit function. Database transaction checks confirmed idempotency and revision
conflicts without leaving synthetic production records.

Durable revision **11**, observed **2026-09-30T11:40:16.743440+00:00**, came from
the final runtime commit. It preserved the protected configuration baseline
through deployment. Scheduler last success was
**2026-09-30T11:40:01.798381+00:00**; scheduler error was false.

The scheduler experienced a startup read timeout, recovered through its existing
retry loop, and IREN closed the incident after three healthy observations.
Historical preflight/open misses remain preserved but no longer appear as
current-day incidents.

## Current state and remaining work

**DEGRADED**: one open warning, `evidence.loss`, with reason
`runtime_event_loss_increasing`. Increasing dropped telemetry is real and remains
unrepaired. Its notification was delivered through the existing fallback Slack
webhook. Dedicated `iren-control` routing is not configured on this service.

RHEN remains reconciled, on `LIVE-2026-09-25-003`; crypto execution is false.
Before/after checks confirmed trading mode, execution authorization, arming,
execution-enabled, and scan-only flags were unchanged.

Pre-open capture remains on `preopen-state-v1-20260927`.
Research Agent sleep remains expected and it was not awakened by supervision.
NOSTRA remains waiting for activation. This release provides authenticated
durable APIs; the Command frontend has not yet been connected to IREN state.
IREN has no live strategy, risk, broker, promotion, or infrastructure write
authority, and invokes no model.

Next stabilization priorities:
1. Repair telemetry loss and verify evidence completeness.
2. Display IREN state and incidents in Command.
3. Validate and integrate the pending NOSTRA foundation before activation.
4. Reconcile the pre-open branch as a separately scoped release.

## Next scheduled executions

Actual registry-derived times, America/New_York:

| Workflow | Next execution |
| --- | --- |
| IREN scheduler self-health | Sep 30, 07:45 |
| VELUM crypto replay | Sep 30, 08:05; hourly at :05 |
| RHEN preflight | Sep 30, 08:45 |
| RHEN market-open heartbeat | Sep 30, 09:30 |
| RHEN session-close record | Sep 30, 16:15 |
| RHEN research review | Sep 30, 16:25 |
| VELUM equity replay | Sep 30, 16:35 |
| GRAEN research checkpoint | Sep 30, 16:50 |
| Weekly operating review | Oct 2, 17:30 |

IREN observation runs once per minute independently of the scheduled self-health
record. Exchange-calendar offsets determine the equity schedules, including
early closes. The five NOSTRA scheduler hooks remain disabled.

Rollback procedure is in `docs/iren-control-plane.md`.
