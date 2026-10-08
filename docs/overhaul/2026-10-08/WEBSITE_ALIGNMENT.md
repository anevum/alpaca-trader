# Website and Command Alignment

## Dependency

The website overhaul is a prerequisite, not a parallel edit target.

Observed design package:

`ANEVUM.WEB.DESIGN.2026-10-08.002.PUBLIC-PRODUCT-PLATFORM`

Observed branch:

`web/2026-10-08-studio-portfolio-overhaul`

At preparation time it already established the correct public direction:

- ANEVUM is an independent software workshop/studio.
- RHEN is the initial public product.
- IREN, GRAEN, NOSTRA, and VELUM are internal architecture, not public products.
- Public state must be sourced and truthful.
- Public aggregation remains on Cloudflare rather than adding a Railway web service.
- Command becomes ANEVUM-wide while preserving RHEN operate/discover/review workflows.

The systems overhaul must preserve this.

## Post-overhaul public role descriptions

### RHEN
Trading and research system. The only public product in the initial registry.

### NOSTRA
Internal numerical forecasting component. Produces point-in-time forecasts and scored outcomes. Research-only unless a specific forecast feature is explicitly promoted.

### IREN
Internal operations control. Health, schedules, configuration identity, incidents, and recovery.

### GRAEN
Internal research methodology/workflow. Turns evidence into bounded experiments. Advanced model assistance is episodic, not an always-on production agent.

### VELUM
Internal replay/counterfactual laboratory invoked for experiments, validation, incidents, and promotion evidence.

## Command mapping

Keep the UX model already being built, but align the data semantics.

### Operate

Shows only live/operational RHEN state:

- market/runtime status;
- account/private execution state;
- live strategy/release;
- positions/orders as authorized;
- current risk state;
- current NOSTRA numerical observation if useful and clearly labeled research-only.

Do not show idle research services merely to fill space.

### Discover

Shows evidence generation and open questions:

- candidate/rejection distributions;
- regimes/features;
- NOSTRA forecast calibration;
- weak-session investigations;
- active research questions;
- experiment queue.

"Waiting for work" is valid when there is no experiment.

### Review

Shows the canonical post-session evidence package and decision history:

- daily/weekly evidence;
- unresolved degradations;
- AI/operator recommendation artifacts;
- proposed experiments;
- VELUM outcomes;
- promotion/rejection decisions.

### System

Shows the lean topology:

- one resident RHEN service;
- resource usage;
- scheduler health;
- IREN incidents/config drift;
- temporary jobs/shadows if active;
- public projection truth checks.

## Required website patch after runtime cutover

Do not redesign the newly published website.

Patch only architecture/runtime truth:

- update internal module copy;
- remove "Research Agent operational runtime" language if still present;
- update topology diagram;
- update status surfaces to distinguish resident vs on-demand;
- ensure no public page implies continuous AI reasoning;
- update release/Field Note describing the systems simplification;
- preserve route structure and visual design.

## Public claims

Never claim:

- "AI continuously optimizes the strategy";
- "self-improving" if that implies automatic live mutation;
- "real-time AI prediction" when the actual forecast is deterministic/statistical;
- profitability not supported by evidence.

Accurate language:

- continuously records evidence;
- produces numerical forecasts;
- runs deterministic review;
- uses AI-assisted research when invoked;
- validates changes before manual promotion.
