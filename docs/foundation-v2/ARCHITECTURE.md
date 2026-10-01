# ANEVUM Foundation v2

Status: ACTIVE architecture migration
Date locked for implementation: 2026-10-01

## Objective

Rebuild ANEVUM around a small number of explicit infrastructure authorities while the legacy RHEN production stack remains operational until verified cutover.

## Canonical infrastructure

- GitHub: source, CI, release history.
- Railway: backend compute, private service networking, PostgreSQL, staging and production runtimes.
- Cloudflare: anevum.com, edge security, public API proxy, Command access.
- Alpaca: broker and market-data authority for RHEN.
- Slack webhook: operational notification surface.
- OpenAI: optional model execution invoked by bounded ANEVUM services; never a persistence authority.

Supabase is a migration source, not part of the target architecture.

## Runtime boundaries

IREN
- Owns orchestration, schedules, commands, incidents, cross-system state and operator-facing coordination.
- Does not own trading decisions or broker execution.

RHEN
- Owns market observation, risk evaluation, execution, reconciliation and canonical trading evidence.
- Alpaca remains the external authority for actual orders, positions and account state.

GRAEN
- Owns mathematical/research campaigns, hypotheses, candidates, evaluations and durable research decisions.
- Has no broker execution authority.

VELUM
- Owns deterministic replay, simulation, counterfactual evaluation and reproducibility manifests.
- Has no broker execution authority.

NOSTRA
- Owns forecasts, forward outcomes and calibration.
- Has no broker execution authority.

## Repository target

The target repository is `anevum/anevum-core`.

Until that repository is created, this branch is the migration staging area. No production Railway service should permanently follow this branch.

Target layout:

```
services/
  iren/
  rhen/
  graen/
  velum/
  nostra/

packages/
  database/
  events/
  market_data/
  telemetry/
  security/
  models/

db/
  migrations/

tests/
docs/
```

All production services will ultimately deploy from the same canonical `main` commit, with service-specific entrypoints and Docker/build configuration.

## Database ownership

One PostgreSQL cluster initially, with schema-level ownership boundaries:

- `anevum.*` shared artifacts, releases, service registry and audit records.
- `iren.*` orchestration/control state.
- `rhen.*` trading evidence and broker-derived state.
- `graen.*` research campaigns and decisions.
- `velum.*` replay and simulation evidence.
- `nostra.*` forecasts and calibration.

Large generated artifacts and datasets belong in object storage. PostgreSQL stores metadata, hashes, lineage and locations.

## Evidence durability

Critical RHEN evidence must not depend on an in-memory bounded queue.

Target write path:

```
RHEN -> local durable spool -> PostgreSQL rhen.events -> projections/read models
```

Requirements:
- append-only canonical event rows;
- idempotent event keys;
- deterministic replay from stored events;
- bounded retry with explicit failure state;
- broker reconciliation independent of telemetry transport;
- no silent event dropping;
- persistence health surfaced to IREN.

The local spool implementation will be file/SQLite-backed on persistent storage for RHEN before Supabase cutover.

## Environments

Legacy:
- Railway project: RHEN
- Environment: production
- Remains unchanged until migration gates pass.

Foundation v2:
- Railway project: ANEVUM Core
- Environment: staging
- Production environment will be created only after staging verification.

No production service may run permanently from a feature branch.

## Migration gates

A subsystem moves only after:
1. schema migration is applied in staging;
2. service tests pass;
3. staging health passes;
4. dual-write or replay comparison shows no material divergence;
5. rollback path is documented;
6. production change is explicit and traceable.

RHEN execution logic, risk controls, position sizing, capital allocation and broker behavior are outside this infrastructure migration unless explicitly reopened.

## Initial sequence

1. Provision staging PostgreSQL.
2. Apply canonical schemas.
3. Implement durable RHEN evidence transport.
4. Dual-write RHEN evidence to Supabase and PostgreSQL.
5. Verify completeness/reconciliation.
6. Make PostgreSQL canonical for RHEN evidence.
7. Migrate IREN state/scheduling.
8. Migrate GRAEN, VELUM and NOSTRA persistence.
9. Replace Supabase Command auth with Cloudflare Access.
10. Replace remaining Supabase Edge Functions.
11. Verify no required Supabase dependency remains.
12. Retire legacy infrastructure only after production verification.
