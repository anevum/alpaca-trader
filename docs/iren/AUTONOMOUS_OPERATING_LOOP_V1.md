# ANEVUM Autonomous Operating Loop v1

Status: draft implementation on `feature/autonomous-operating-loop-v1`  
Authority: research/paper autonomy only; no runtime source-code or live-risk escalation authority.

## Objective

ANEVUM should make productive forward progress without an operator prompt. A healthy
system is not merely a set of HTTP 200 responses; it is a system that is actively
observing, researching, validating, replaying, shadowing, paper-testing, executing an
already-authorized production policy, or waiting on a legitimate external dependency.

The default loop is:

```text
IREN objective
  -> GRAEN hypothesis planner
  -> DEVELOPMENT
  -> VALIDATION
  -> HOLDOUT
  -> VELUM
  -> FORWARD SHADOW
  -> PAPER CANARY
  -> HUMAN live-risk decision
  -> RHEN (only after explicit protected authorization)
  -> new evidence back to GRAEN/IREN
```

A failed research or paper stage automatically returns to the hypothesis planner.
A missing software capability becomes `ENGINEERING_REQUIRED`. Independent research
continues while that software task waits.

## Operating conditions

The control plane uses six explicit conditions.

- `OPERATING`: normal operational work is progressing.
- `RESEARCHING`: no accepted edge exists yet, but a bounded research stage is progressing.
- `ENGINEERING_REQUIRED`: progress on one branch requires a software/infrastructure capability not available to runtime services.
- `HUMAN_DECISION_REQUIRED`: a protected decision is required, such as increasing live financial authority.
- `BLOCKED`: evidence/infrastructure integrity prevents safe continuation and no normal dependency wait applies.
- `IDLE`: no work is scheduled. For an active ANEVUM objective, prolonged unexplained IDLE is unhealthy.

## Standing autonomy charter

The standing charter allows GRAEN/IREN to:

- ingest and verify data;
- generate hypotheses from the trusted strategy grammar;
- preregister/freeze research methodology;
- open DEVELOPMENT, VALIDATION, and HOLDOUT only through deterministic gates;
- reject/archive failed hypotheses;
- run falsification and VELUM replay;
- activate broker-proof forward shadow;
- activate bounded Alpaca paper execution after forward-shadow evidence passes;
- reduce/stop already-authorized execution;
- recover idempotent operational work;
- schedule the next independent experiment.

The standing charter never allows a runtime service to:

- edit source code;
- create or merge software PRs;
- deploy software;
- alter credentials;
- create new spend;
- add a broker/exchange;
- weaken statistical safeguards;
- bypass holdout;
- increase live-risk limits;
- promote unrestricted live trading;
- make legal/irreversible publication/external-capital decisions.

Those boundaries are code-enforced in `app/research_agent/autonomy.py`.

## Research integrity

Every generic strategy candidate is a frozen manifest built from a trusted grammar.
The manifest describes:

```text
information source
  -> feature
  -> transformation
  -> regime
  -> trigger
  -> entry
  -> exit
  -> sizing
  -> execution model
```

The manifest is hashed before corpus access. Search generation is recorded and
confirmatory alpha is spent online. Validation and holdout are not opened unless
their predecessor passes. Previously inspected confirmatory intervals are not reused
as untouched evidence.

The hypothesis graph retains rejected/null outcomes so GRAEN does not repeatedly
rediscover the same failed branch.

## Forward shadow

The canonical forward-shadow host is `app/graen/shadow_host.py`, embedded in RHEN's
FastAPI app. It is deliberately broker-proof:

- execution, live trading, bot arming, and crypto execution are forced false;
- no broker order interface is exposed;
- validated candidates may queue durably;
- one candidate is observed at a time for clean attribution;
- exact activation identity is stable across transient worker retries;
- terminal evidence is retained by activation ID;
- a strategy-runner success projects to `READY_FOR_PAPER`;
- a rejection returns to autonomous research.

Endpoints:

- `POST /v1/candidate-shadow/activate`
- `GET /v1/candidate-shadow/status`
- `GET /v1/candidate-shadow/checkpoint/{activation_id}`

Required shared secret: `GRAEN_SHADOW_TOKEN` (or the existing bounded GRAEN gateway
token fallback).

## Paper canary

The canonical paper adapter is `app/graen/paper_host.py`, also embedded in the RHEN
app code but intended to run in a dedicated paper-mode deployment/account.

Paper authorization requires all of the following:

```text
TRADING_MODE=paper
EXECUTION_ENABLED=true
BOT_ARMED=true
LIVE_TRADING=false
CRYPTO_EXECUTION_ENABLED=false
GRAEN_PAPER_ENABLED=true
I_ACKNOWLEDGE_GRAEN_AUTONOMOUS_PAPER=YES
valid Alpaca paper credentials
GRAEN_PAPER_TOKEN=<shared bounded secret>
```

The adapter additionally requires a clean crypto paper account when a candidate is
admitted. It persists critical intent before broker submission, uses deterministic
client order IDs, reconciles ambiguity, places protective exits, and fails safe.

Only one candidate owns the paper account at a time. Other paper-eligible candidates
wait and retry after the active candidate reaches a fully closed terminal checkpoint.

A paper candidate becomes `PAPER_PASSED` only after its fixed evidence gates pass.
A paper pass does **not** authorize live trading. It creates a protected IREN
`HUMAN_DECISION_REQUIRED` objective for that exact candidate.

Endpoints:

- `POST /v1/graen-paper/activate`
- `GET /v1/graen-paper/status`
- `GET /v1/graen-paper/checkpoint/{activation_id}`

## Engineering handoff

When GRAEN needs a new software primitive or trusted compiler capability it emits an
immutable `ENGINEERING_REQUIRED` package with:

- reason and requested capability;
- blocked hypothesis/problem;
- affected components;
- suggested file scope;
- acceptance tests;
- continuation policy for unrelated research;
- a complete ChatGPT/Codex handoff prompt.

IREN persists that requirement as a manual `SOFTWARE_BUILD` objective. Autopilot
skips the software objective and continues independent safe work. If the objective is
routed to the work engine, it remains `WAITING` with:

```text
manual_chatgpt_workspace_required=true
paid_model_execution=false
auto_merge=false
```

After a human-run software update lands and deploys, GRAEN verifies the exact trusted
compiler bytes in its runtime before resuming the blocked research branch.

## Productivity health

IREN distinguishes process health from mission health. Examples:

```text
GRAEN HTTP 200 + no research progress + no valid wait
  => productivity incident

GRAEN waiting for an uninspected corpus until a declared date
  => legitimate productive wait

GRAEN waiting on ENGINEERING_REQUIRED while another branch is running
  => system continues; operator task remains visible

paper candidate collecting evidence
  => productive

paper pass waiting for live-risk authorization
  => HUMAN_DECISION_REQUIRED, not automatic promotion
```

The control plane must never infer `HEALTHY` solely from container/process uptime.

## Deployment sequence

This branch must not self-deploy.

1. Get the draft PR green in repository CI.
2. Review the autonomy boundary diff.
3. Manually merge the software PR.
4. Deploy canonical `main` to the existing RHEN/GRAEN/IREN services.
5. Verify runtime commit/deployment identities.
6. Configure `RHEN_SHADOW_SERVICE_URL` for the research executor to the canonical
   RHEN endpoint and configure the matching shadow token.
7. Create/verify the dedicated paper-mode RHEN deployment/account.
8. Configure `GRAEN_PAPER_SERVICE_URL` and matching paper token for the research executor.
9. Confirm paper host reports `paper_execution_authorized=true` and
   `live_execution_authorized=false`.
10. Verify Command shows active research/productivity state rather than service-only health.
11. Allow GRAEN to resume the planner. No live-risk authorization is added by this deployment.

## Verification blockers

A merge is not justified by source review alone. Required automated tests cover:

- standing-charter authorization and identity-scoped overrides;
- hypothesis graph and strategy grammar;
- information-value prioritization;
- no runtime code/Git/merge/deploy authority;
- manual engineering handoff scope;
- IREN productivity/legitimate-wait behavior;
- forward-shadow queue/restart/exact checkpoint behavior;
- paper-only authorization/restart/terminal checkpoint behavior;
- paper-pass -> protected live-risk decision.

If GitHub Actions cannot start jobs, the PR remains draft until the runner/account issue
is repaired and the complete suite executes successfully.
