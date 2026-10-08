# Target Architecture

## Design rule

A component receives permanent compute only when its job has a real continuous-time dependency.

Everything else becomes:

- an embedded library;
- a scheduled deterministic job;
- an on-demand replay/research workload; or
- an operator/ChatGPT Work workflow.

## Target flow

```
MARKET DATA
    |
    v
RHEN LIVE RUNTIME
    |- market observation
    |- feature calculation
    |- deterministic strategy
    |- risk / sizing
    |- execution / exits
    |- embedded NOSTRA forecast
    |- canonical event/evidence persistence
    |- lightweight IREN health + scheduler
    |
    +--> POST-SESSION EVIDENCE PACKAGE
             |
             +--> GRAEN / operator / ChatGPT Work analysis
                     |
                     +--> bounded experiment specification
                              |
                              +--> VELUM replay / validation
                                       |
                                       +--> evidence
                                                |
                                                +--> MANUAL PROMOTION
```

## RHEN

RHEN owns all always-on market/trading work.

Required responsibilities:

- Alpaca market/broker connectivity;
- candidate universe and scan cycle;
- feature computation;
- deterministic live strategy;
- risk controls and sizing;
- order lifecycle;
- protective exits;
- reconciliation;
- canonical event recording;
- public-safe projection;
- deterministic evidence capture;
- scheduler/health primitives required for the above.

No advanced language model is required for RHEN to continue trading safely.

## NOSTRA

NOSTRA becomes an embedded numerical forecast library.

Target behavior:

1. On a canonical candidate/decision timestamp, build a point-in-time feature vector.
2. Produce forecasts from one or more cheap deterministic/statistical models.
3. Persist forecast identity, model identity, horizon, features/version, and predicted value before the outcome exists.
4. Mature the outcome after the horizon.
5. Score calibration and directional/value accuracy.
6. Compare continuously against naive baselines.
7. Expose evidence to Discover/Review.

Initial authority:

- research-only;
- no order submission;
- no risk or sizing authority;
- no automatic strategy mutation.

Only after statistically credible forward validation may a specific NOSTRA output be proposed as an execution feature, and that still requires explicit promotion.

Target implementation form:

- Python module/library called by RHEN;
- no dedicated uvicorn process;
- no dedicated Railway service;
- no LLM.

## IREN

IREN becomes deterministic operations control.

Keep:

- health checks;
- scheduler ownership;
- configuration fingerprint/drift;
- incident state;
- restart/recovery observation;
- stale-data detection;
- broker/runtime readiness checks;
- operator notifications;
- protected configuration acceptance.

Remove/demote from production:

- model "autopilot" expectations;
- reasoning-agent identity;
- model-driven execution routing;
- model budgets as a required runtime concept.

Target implementation form:

- lightweight in-process scheduler/watchdog or a minimal child only if isolation is proven necessary;
- no broker-write authority of its own;
- no model required for health.

## GRAEN

GRAEN becomes the research method, evidence interpretation workflow, and experiment specification layer.

Always-on duties are reduced to deterministic bookkeeping only if they are truly needed.

Advanced reasoning moves to an episodic workflow:

- consume canonical evidence package;
- identify the highest-information unresolved question;
- propose a bounded, falsifiable experiment;
- cite the evidence that caused the proposal;
- define success/failure criteria;
- hand the experiment to VELUM.

Preferred advanced-model execution:

- ChatGPT Work / operator session using exported evidence;
- no broker credentials;
- no direct live configuration mutation;
- no permanent Railway model worker.

A future API worker is allowed only if it is cheaper and measurably useful. It must be isolated from broker credentials and invoked on demand.

## VELUM

VELUM becomes an on-demand replay laboratory.

Keep:

- historical replay;
- execution-friction assumptions;
- stress tests;
- bootstrap/counterfactual evidence;
- identical-strategy comparison;
- run manifests and provenance.

Change:

- no default 24/7 resident process;
- no automatic daily replay merely because a day ended;
- no permanent service waiting for work.

Trigger VELUM when:

- a bounded experiment exists;
- a release/promotion candidate needs replay evidence;
- an incident needs a counterfactual check;
- an operator explicitly requests it.

Implementation options, in priority order:

1. in-process/CLI job in the RHEN container during low-load windows;
2. Railway job/function spawned for a bounded run;
3. temporary staging/shadow service for promotion validation only.

## Research Agent

The "Research Agent" name is retired as a permanent runtime identity.

Its useful code is split into:

- deterministic evidence compiler -> RHEN evidence pipeline;
- semantic review -> GRAEN episodic AI workflow;
- adaptation proposal compiler -> experiment specification;
- promotion authority -> remains operator/manual, not an agent.

## Pre-open

Pre-open stays only if its forward evidence demonstrates incremental value.

If retained, it should run in a bounded pre-market window and not remain resident all day.

Preferred form:

- scheduled pre-market task inside RHEN;
- state written to canonical evidence;
- process terminates or idles without a dedicated server.

## Railway topology target

Permanent production:

- one `RHEN` project;
- one permanent `rhen` service;
- one persistent data volume only where necessary.

Temporary resources permitted:

- promotion shadow/staging service;
- bounded replay/research job;
- emergency diagnostic service.

Temporary resources must have a defined owner, purpose, start condition, and teardown condition.

## Authority boundaries

No architecture simplification may weaken these boundaries:

- live broker writes: RHEN execution only;
- model/AI: never in the required live order path;
- research: no automatic live promotion;
- replay: no broker writes;
- forecasting: research-only until explicitly promoted;
- public website: sanitized projection only;
- operator: final promotion authority.
