# RHEN Verifier v1 deterministic foundation

Status: **DEVELOPMENT only**. This package has no endpoint, scheduler, database
client, production credentials, alert writer, Railway client, Alpaca client, or
deployment. It does not authorize a release, merge, or research-stage transition.

## Flow and authority

Authorized bounded evidence provider → `VerificationRequest.parse` → pure
profile checks in `verify` → `VerificationResult.as_dict` → optional explanation.
The optional formatter copies the complete deterministic result and accepts only
a text note. A Workspace Agent may explain the result but cannot revise verdicts,
reason codes, or canonical severity. Nothing in this package calls a model.

The provider is responsible for authenticated, current reads. The Verifier
accepts no credentials and performs no infrastructure reads. `bounded_railway_roles`
uses the merged Agent Support maintained map and `normalize_services`; it
validates current source and start command before discarding raw configuration.
The normalized role must carry the validation marker and match the maintained
map. Display names can yield `SERVICE_NAME_DRIFT` (DEGRADED), while domains
never grant a role. Repurposed IDs become unclassified and fail role checks.

| Capability | Verifier | Future bounded provider / human |
| --- | --- | --- |
| Read scoped canonical evidence | Receives validated snapshots | Authorized provider reads it |
| Compare claims and report gaps | Yes | May supply evidence |
| Write alerts, data, strategy, orders, migrations | Never | Outside Verifier authority |
| Deploy, merge, approve, open research stages | Never | Human-controlled processes only |

## Input contract

`rhen-verifier-input-v1` requires `verification_id`, `environment`, `profile`,
`subject`, bounded `assertions`, and evidence sections. Every section has one to
four records with `source_id`, `source_version`, `observed_at` (timezone aware),
`reference`, `authority` (`CANONICAL`, `OPERATOR_ATTESTED`, `REPORTED`,
`UNAVAILABLE`), and an
allowlisted `data` object. Unknown sections, fields, secret-like keys, oversized
values, and invalid metadata are rejected. Known sections are `github`,
`release`, `deployment`, `runtime`, `broker_account`, `railway`, `readiness`,
`support`, `telemetry`, `reports`, `migration`, `artifacts`, and `activation`.
Only the bounded account fields belong in `broker_account`; no credentials, raw
broker payload, raw report body, Railway config, or hidden model reasoning
belongs in this contract.

The seven profiles are `repository`, `release_deployment`,
`research_readiness`, `agent_support`, `telemetry_reports`,
`migration_artifacts`, and `activation_gates`. The last profile composes the
other checks plus explicit named gates. Source observations older than 24 hours
or more than five minutes in the future cannot prove current state. Telemetry
has an independent claimed freshness window (default 15 minutes).

## Evidence hierarchy and semantics

Current canonical observations with compatible identities establish facts.
Multiple conflicting canonical observations yield `CONTRADICTORY`. A reported
activation outcome or historical document is context, not current live proof.
`OPERATOR_ATTESTED` is explicit lower-authority context: the request subject and
evidence section identify its subject/scope, while source ID, source version,
timestamp, and reference preserve provenance. It cannot satisfy an independent
machine-verification invariant or override contradictory canonical evidence.
Its presence remains distinguishable from both generic `REPORTED` evidence and
completely absent evidence.
The migration artifact in Git establishes only its existence; it cannot prove
application. The reported application of `20260927195537` in unfinished
activation is `REPORTED` until a bounded authorized live read confirms it.
Missing, stale, unavailable, reported-only, or contradictory critical evidence
yields `INCONCLUSIVE` absent a separately proven violation. A proven invariant
violation or canonical blocker yields `BLOCKED`.

Verdict precedence is **BLOCKED** (proven blocking failure), **INCONCLUSIVE**
(unestablished necessary fact), **DEGRADED** (canonical nonblocking finding),
then **VERIFIED** (all checked invariants pass). A canonical DEGRADED finding
never becomes BLOCKED by profile or explanation. Agent Support reasons retain
their exact severity; `SHADOW_SERVICE_UNRESOLVED` is DEGRADED. A contradictory
claim that it is BLOCKED makes the evidence inconclusive rather than promoting
the condition. Missing daily/weekly reports retain Agent Support's DEGRADED
severity; freshness violation and duplicate identity are blocking.

The Research Agent's sanitized `state`, blocker count/codes, limitations,
monitors, and waiting requirements are consumed as given. The Verifier does not
reclassify raw evidence. `WAITING` with zero blockers verifies the narrow
nonblocking readiness claim, including
`MULTIPLE_INDEPENDENT_SESSIONS`, `INCOMPLETE_FORWARD_OUTCOMES`,
`UNRECONSTRUCTABLE_EVIDENCE`, and `CANONICAL_OPERATIONAL_INCIDENTS`.
`BLOCKED` with canonical blockers is blocking. A claim of `READY` while the
canonical state is `WAITING` fails even though WAITING itself is nonblocking.

GitHub `main` and a branch/PR are separate facts. An open, draft, or unmerged
PR cannot establish that its change is canonical. `main_contains` cannot prove
production rollout: a production claim also checks release and current
deployment source commit. An older document saying a service was absent is
never a substitute for a current Railway observation.
Deployment identity likewise cannot prove runtime health. An explicit runtime
health claim requires a separate current canonical runtime observation tied to
the deployment ID and source commit.

## Output contract and integration

`rhen-verifier-result-v1` includes ID/time/environment/profile/subject,
assertions, final verdict, each invariant's expected and observed value,
status and canonical severity, failed/degraded/missing/contradictory lists,
evidence references with source identity/version/timestamps, repository SHA,
release and strategy identity, warnings, recommended human action, and a
re-verification flag. Stable codes make the invariant matrix usable by the
existing RHEN Verifier Workspace Agent after an authorized bounded evidence
feed is available. Only this structured result should be passed for optional
human-readable explanation; no hidden reasoning is stored or exposed.
The result object is recursively immutable; `as_dict()` returns a detached
JSON-friendly copy for downstream formatting.

Agent Support activation is separate in draft PR #51. This foundation imports
only the merged Agent Support integrity schema and maintained Railway normalizer,
not PR #51's unpublished gateway or runtime. Production verification still
requires a canonically verified migration read, authenticated bounded support
snapshot and alert state, read-only Railway observation, deployed support runner
with tested dry/persisted/idempotent/resolution behavior, and the approved
capacity/scheduling path. None are supplied or activated by this PR.
