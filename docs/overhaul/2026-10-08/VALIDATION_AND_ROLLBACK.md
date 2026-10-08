# Validation and Rollback

## Core migration rule

Do not combine an architecture cutover with an unexplained live-strategy change.

The first production cutover must preserve the currently authorized execution behavior unless a separately identified strategy release has completed its own evidence/promotion process.

## Pre-cutover capture

Before changing production:

- record website production SHA;
- record RHEN production SHA;
- record current Railway service/deployment IDs;
- export non-secret variable names;
- record current strategy/config fingerprint;
- record broker account/position/order reconciliation state;
- record canonical evidence database integrity;
- record 24h/7d resource baseline;
- record scheduler recent-run state;
- record current public feed truth state;
- record current shadow purpose/status.

## Validation layers

### 1. Static/CI

Required:

- unit tests;
- strategy/replay tests;
- authority-boundary tests;
- no-broker-credential tests for research/replay modules;
- configuration validation;
- website privacy/public-contract tests where affected.

### 2. Process topology

Prove:

- only intended permanent processes start;
- no duplicate scheduler exists;
- no model worker starts automatically;
- no VELUM/GRAEN server waits resident without a reason;
- router/health endpoints remain compatible where required.

### 3. Trading behavior

Prove:

- scanner cadence unchanged or intentionally improved;
- candidate decisions match the baseline for the same input/config where no strategy change was intended;
- risk/sizing outputs match;
- order intent/reconciliation behavior matches;
- protective exit behavior matches;
- restart recovery works.

### 4. NOSTRA

Prove:

- forecasts are recorded before outcomes;
- no future leakage;
- model/version identity is durable;
- matured outcomes score correctly;
- baseline comparison exists;
- no execution authority appears.

### 5. Evidence pipeline

Prove a completed session generates one canonical package containing:

- session metrics;
- trade/rejection evidence;
- incidents/data-quality warnings;
- forecast outcomes;
- current config identity;
- experiment queue state.

No model call is required.

### 6. GRAEN/AI workflow

Using a saved package:

- run an operator/Work review;
- produce a schema-valid bounded experiment;
- verify it cannot authorize live execution;
- persist the proposal as evidence.

### 7. VELUM

Invoke VELUM only for the experiment:

- replay completes;
- run manifest/provenance exists;
- baseline/stress evidence exists;
- no broker writes are possible;
- resource returns to idle/terminated after completion.

### 8. Website/Command

Verify:

- public RHEN evidence remains truthful;
- Command Operate/Discover/Review/System render the new semantics;
- no dead service is shown as live;
- no internal architecture is promoted to a public product;
- stale/missing data renders honestly.

## Rollback boundaries

Maintain independent rollback points:

1. website publication rollback;
2. RHEN architecture rollback;
3. strategy release rollback;
4. data migration rollback.

Do not make one monolithic rollback that requires undoing all four.

## Shadow teardown

The current permanent `rhen44-shadow` may be removed only after:

- its required 4.4 evidence has been harvested;
- the relevant 4.4 code is either promoted/merged or rejected;
- any unique state needed for reproducibility is persisted elsewhere;
- the operator can recreate the shadow from source if needed.

## Completion gate

Close this package only when all of the following are true:

- website dependency complete;
- runtime cutover complete;
- trading behavior verified;
- one permanent RHEN service;
- deterministic evidence package verified;
- NOSTRA point-in-time scoring verified;
- IREN deterministic operations verified;
- on-demand GRAEN/VELUM workflow verified;
- no permanent production model dependency;
- shadow removed or explicitly time-bounded for an active gate;
- 7-day post-cutover resource report recorded;
- public/Command architecture copy synchronized;
- final release note/Field Note published.
