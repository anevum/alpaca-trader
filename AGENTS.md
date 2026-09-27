# RHEN Agent Operating Contract

This repository may be worked on by multiple human operators and ChatGPT/Work agents from separate workspace profiles. GitHub, Supabase, Railway, and other explicitly shared infrastructure are canonical. Chat history is not canonical state.

## Required start procedure

Before making changes:

1. Fetch current `main`.
2. Record the exact starting SHA.
3. Read the current task and its explicit allowed and forbidden surfaces.
4. Inspect existing branches, pull requests, migrations, and relevant production state before editing.
5. Do not rely on another chat's memory as authority when durable repository/system evidence exists.

## One bounded objective

Each agent task must have one bounded objective. Do not silently expand scope.

If unrelated defects are discovered, record them as follow-up work instead of fixing them unless they create an immediate safety or correctness blocker for the assigned objective.

## Canonical boundaries

Unless a task explicitly authorizes otherwise:

- Do not change RHEN live strategy behavior.
- Do not change risk, sizing, capital allocation, execution gates, broker integration, credentials, market universe, or production trading behavior.
- Do not promote research automatically into production.
- Do not open VALIDATION or HOLDOUT.
- Do not access quarantined/protected research data.
- Do not revive experiments or strategy families marked terminal/rejected.
- Do not modify frozen research methodology after seeing results.
- Do not configure an LLM/model provider for Research Agent work unless explicitly authorized.
- Do not create schedulers/cron/recurring autonomous execution unless explicitly authorized.
- Do not expose secrets in GitHub, logs, tests, reports, or UI.

The deterministic Research Agent safety policy is authoritative for research-agent code.

## Branching and concurrency

- One implementation branch per bounded task.
- Do not implement directly on `main`.
- Re-fetch current `main` before final integration.
- Do not stack work on another unmerged branch unless the dependency is explicit.
- Two write-capable agents must not edit the same subsystem concurrently without an explicit dependency/lease.
- A verification agent should remain read-only with respect to the implementation it verifies.
- Database migrations require unique timestamps and collision checks against canonical migrations.
- Any Railway, Supabase, GitHub, broker, or deployment mutation must be disclosed in the handoff.

## Agent roles

### Research Implementation Agent

May implement explicitly approved research tooling, tests, research-only scripts, and research schema changes.

Default forbidden surfaces: live strategy, risk, sizing, broker execution, production credentials, protected research stages, automatic promotion, terminal experiment revival.

### Verification Agent

Independently inspects diffs, tests, canonical state, logs, and evidence.

Returns PASS, FAIL, or BLOCKED with evidence. Does not repair the implementation it is evaluating unless given a separate task.

### Command / Website Agent

Works on presentation, telemetry visualization, navigation, responsive layout, and explanatory UI in the website/Command codebase.

It must not alter trading strategy or research methodology and must not introduce schema changes solely for visual convenience without an approved integration task.

### Operations / Observability Agent

Inspects health, deployment status, data freshness, report completeness, and incidents.

Default posture is read-only. It may propose a bounded repair task but must not silently mutate live trading behavior.

## Testing and evidence

Every implementation must run the narrowest relevant tests plus any repository-required suite. Never report success from code inspection alone when runnable verification exists.

A failed test may not be weakened merely to make the change pass unless the task explicitly changes the requirement and the evidence supports that change.

## Production safety

Production-affecting work requires an explicit task that names the affected production surface and authorization.

Research, test, development, and verification work must remain isolated from live execution unless explicitly required.

## Required handoff

Every implementation agent must return:

- objective
- starting canonical SHA
- branch
- final branch SHA
- PR number
- files changed
- migrations created and whether applied
- GitHub actions performed
- Supabase actions performed
- Railway actions performed
- tests run and exact result
- production impact
- known blockers
- unresolved work
- recommended next bounded task

Every verifier must additionally state:

- artifact/PR verified
- evidence inspected
- PASS / FAIL / BLOCKED
- any acceptance criterion not proven

## Coordination anchor

Multi-agent coordination is tracked in GitHub issue #45. Durable repository/system evidence overrides stale notes in that issue when they differ.
