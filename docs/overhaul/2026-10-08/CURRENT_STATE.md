# Current State Baseline

Snapshot date: 2026-10-08.

## Repository and deployment

Observed RHEN `main` head:

`f9dad71c079aa246df1baecf5ec0a24786ca1318`

Observed Railway project `RHEN` contains two live services:

1. `rhen`
   - source: `anevum/rhen:main`
   - start command: `python -m app.rhen_core.supervisor`
   - one replica
   - persistent volume: `rhen-data`, 5,000 MB allocation, mounted at `/data`
2. `rhen44-shadow`
   - source: `anevum/rhen:work/rhen44-research-pipeline-20261007`
   - start command: staging market-fabric app
   - one replica
   - persistent volume: 100 MB

`ANEVUM Core` and `RHEN Archive` had no live services at the observed snapshot.

## Current resident process topology

The main RHEN supervisor can launch:

- Core persistence API
- Execution API
- GRAEN API
- VELUM API
- Research Agent API
- NOSTRA API
- IREN API
- IREN Executor API
- Pre-open API
- Router API

This means a single Railway container has been consolidated at the billing/service level while still retaining many independently resident Python/uvicorn processes.

That is the central resource problem this package addresses.

## Current resource baseline

Observed main `rhen` service:

### Last 7 days
- average CPU: approximately 0.205 vCPU
- average memory: approximately 0.832 GB
- average disk used metric: approximately 0.547 GB

### Last 24 hours
- average CPU: approximately 0.477 vCPU
- average memory: approximately 1.510 GB
- average disk used metric: approximately 1.204 GB

Observed `rhen44-shadow`, last 24 hours:

- average CPU: approximately 0.072 vCPU
- average memory: approximately 0.129 GB
- average disk used metric: approximately 0.042 GB

These are infrastructure metrics, not invoice totals. They are the baseline against which the overhaul must be measured.

## Current scheduling model

The canonical schedule registry currently includes:

- preflight
- market open
- session close evidence
- daily research
- daily VELUM equity replay
- GRAEN research checkpoint
- weekly operating review
- 15-minute IREN scheduler health

NOSTRA is represented as an independent autorun runtime.

The overhaul changes this from "every subsystem should be alive" to "only work with a real time dependency should remain resident."

## Current model/API surface

The production service currently exposes configuration names for:

- OpenAI API credentials
- RHEN research model enablement/model/reasoning
- GRAEN research director autorun/model calls
- IREN model execution and model budgets
- IREN autopilot/executor
- NOSTRA autorun
- VELUM autorun
- GRAEN autorun
- Research Agent autorun

The Research Agent implementation already has a useful boundary:

- deterministic review can complete without a model;
- semantic review is optional and explicit;
- OpenAI calls are forbidden from broker authority.

The overhaul preserves that safety property but removes the production assumption that semantic/model reasoning should live in the always-on service.

## Current NOSTRA reality

NOSTRA currently contains deterministic point-in-time forecast infrastructure, including:

- zero-return baseline;
- `shrunken_drift` model;
- forecast persistence;
- later scoring/outcome evaluation;
- research-only and no-execution-authority guards.

This is valuable and should be retained.

The mismatch is presentation/topology: NOSTRA behaves like a numerical research component but is deployed as an independently resident service.

## Current website dependency

At preparation time, `anevum/anevum-web:web/2026-10-08-studio-portfolio-overhaul` was:

- 53 commits ahead of `main`;
- 0 commits behind.

That branch already moves the public architecture in the desired direction:

- RHEN is the only initial public product.
- IREN, GRAEN, NOSTRA, and VELUM are internal architecture/modules.
- Public data must be truthful and sourced.
- Command remains RHEN-operational but sits inside an ANEVUM-wide shell.
- Public feed aggregation remains on Cloudflare rather than requiring another Railway web service.

The systems overhaul should align to that design rather than reintroducing subsystem-as-product complexity.
