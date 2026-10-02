# IREN → Codex Handoff v1

Canonical source is GitHub main, runtime is Railway, durable state is Foundation PostgreSQL.
This feature extends the existing planner and jobs; it creates no competing state system.

## Contract and manual workflow
Ask Command "what's next?" or "prepare for Codex". The same planner chooses the action.
Deterministic actions remain native; protected actions require Devon.
Software actions produce a CODEX_HANDOFF job with an immutable codex_handoff.v1 package
in output.package, and transitions in iren.job_events. Copy the full prompt in the existing dock.
The package preserves objective criteria, dependencies, exact main SHA, timestamp, command/job IDs,
runtime/configuration baseline, incidents, scope, tests, deployment limits and rollback instructions.
No credentials are included. Re-read current main before implementation.

Use codex/handoff/<job UUID> and exact IREN-Handoff / IREN-Objective PR-body lines.
The bounded GET-only GitHub reader discovers that branch. Alternatively send
"associate Codex handoff <UUID> PR <number>" in Command. Association is a candidate, never proof.
IREN does not merge. The prompt itself does not authorize merging.

## Lifecycle and verification
PREPARED → IN_PROGRESS (explicit association) → PR_OPEN → VERIFYING → VERIFIED.
A merged PR remains VERIFYING until evidence passes. FAILED and SUPERSEDED are terminal.
A fresh preparation supersedes an untouched PREPARED package if main or objective identity changed.
In-progress changes require "supersede Codex handoff <UUID>" followed by preparation; they are never silently replaced.

Each minute, the existing work loop reads GitHub branch/PR/CI evidence through iren-executor,
canonical control state, Foundation migration names and application health. It requires:
exact metadata/branch/repository association, merged commit ancestry on current main, successful
required CI on PR head and current main, scoped changed paths, fresh healthy control state,
no incidents, unchanged protected configuration, expected deployed revision, unchanged observed
unaffected deployment IDs, applied required migrations, zero paid-worker budgets, and actual
objective-specific observation values matching the ORIGINAL success criteria.

Criteria use the existing criteria_satisfied predicate. Objective and handoff completion are one
Foundation transaction; no callback or generic job update can mark a handoff complete.
A changed objective, missing identity, uncertain deployment, missing check or unmatched criterion
remains WAITING with structured blockers. The planner resumes after atomic completion, activates
dependency-satisfied future objectives, and exposes the next action through Command.

The current topology does not cover all nine RHEN-side runtimes, and legacy workers may omit
deployment provenance. Do not claim complete provider isolation from that subset. The next
software objective iren.runtime-evidence.v1 explicitly closes this observed evidence gap.
No provider credentials or runtime privileges are added by this feature. Packages captured before complete inventory evidence remain blocked even if a future observation becomes complete: explicitly supersede and rebaseline them. The verifier must not retroactively invent a missing before-deployment baseline.

## API and persistence
Existing /v1/command/iren GET exposes work.next_action, execution_mode and handoffs including
full copyable prompt, lifecycle, association and verification. Existing POST enqueues aliases.
Cloudflare Access and owner checks remain unchanged. Private scheduler gateway adds
iren_handoff_prepare/associate/verify/evidence. Authenticated executor GET /v1/codex/github is
read-only. There is no unauthenticated handoff endpoint.

Migration 0015 adds indexes and seeds the concrete evidence-gap objective plus one
owner-requested preparation command. It preserves previous migration files, budgets and settings.

## Disabled model worker and future reuse
Manual Codex is the implementation backend. No model call occurs in preparation or verification.
Both paid budgets remain zero. Future explicitly authorized model execution can consume the
same output.package.prompt and association identifiers, retaining the same verifier and scope.
This capability does not activate that worker or grant merge, trading, broker, spending,
credential, capital, publication or destructive infrastructure authority.

## Deployment and rollback
Changed app/iren paths are excluded from the trader's Railway watch patterns.
IREN, iren-executor, Foundation gateway and migrator are the expected backend deployments.
Verify actual deployment IDs plus application readiness after merge; provider online alone is insufficient.
Rollback only affected IREN/Foundation application images to immediately preceding builds;
retain the additive migration and durable handoff history. Never restart RHEN for rollback.
