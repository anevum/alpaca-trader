# RHEN Agent Task Template

Use this template when handing a bounded task to another profile/agent.

## Identity

Role: <Research Implementation | Verification | Command/Website | Operations/Observability>

Repository/service: <exact target>

Canonical branch: main

Starting SHA: FETCH CURRENT MAIN BEFORE WORK

## Objective

<one bounded outcome>

## Allowed

- <explicit files/subsystems/services>
- <explicit actions>

## Forbidden

- live strategy changes
- risk/sizing/capital-allocation changes
- broker/execution changes
- credential changes
- production promotion
- protected research-stage access
- methodology changes after results
- unrelated cleanup

Add/remove forbidden items only when the task explicitly requires it.

## Required procedure

1. Fetch current main and report the SHA.
2. Inspect relevant existing implementation and open work.
3. Create one task branch.
4. Make only the bounded change.
5. Run relevant tests/verification.
6. Re-fetch main before handoff and report divergence/conflicts.
7. Return the handoff below.

## Acceptance criteria

- [ ] <criterion>
- [ ] <criterion>
- [ ] No forbidden surface changed.
- [ ] Production impact is explicitly stated.

## Handoff

Objective:

Starting canonical SHA:

Branch:

Final branch SHA:

PR:

Files changed:

Migrations created/applied:

GitHub actions:

Supabase actions:

Railway actions:

Tests/results:

Production impact:

Known blockers:

Unresolved work:

Recommended next bounded task:
