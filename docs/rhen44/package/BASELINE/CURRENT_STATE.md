# Preparation baseline

Package-preparation snapshot, 2026-10-06:

- GitHub repository: `anevum/rhen`
- default branch: `main`
- observed main SHA: `01ad6a06ce68aba5cd9e6699b6ed617fa542875b`
- observed main change: `Close incidents for retired scheduler workflows (#416)`
- Railway project: `RHEN` (`808098a9-937e-4ca4-ac98-dd2dcfef5d0c`)
- Railway production environment: `63a64723-574d-497b-b01b-a9fef7ea78ab`
- Railway service: `rhen` (`f933a669-8591-4233-8510-e0db1548e463`)
- persistent volume: `rhen-data`, 5 GB mounted at `/data`
- observed deployment during package preparation: `c07a1d2b-d1a5-4192-a86a-7ec7fc4fb862`, state `DEPLOYING`

This snapshot is intentionally NOT the RHEN 4.4 activation baseline because 4.3 work was still moving when the package was prepared.

## Required baseline freeze after 4.3

Before any 4.4 implementation branch is merged, record a fresh immutable baseline containing:

- final 4.3 main commit SHA;
- final 4.3 Railway deployment ID and status SUCCESS;
- actual runtime strategy version ID;
- execution authorization state;
- reconciliation state;
- current regular and extended equity configuration fingerprints;
- protected-configuration identity;
- current policy/risk values;
- active database schema/migration head;
- current Command deployment/revision;
- CI result for final 4.3 main;
- open position/order state;
- the rollback commit/deployment identifiers.

## 4.3 completion gate

Do not begin production-changing 4.4 work until all are true:

- 4.3 main is settled and no expected merge remains;
- Railway RHEN production is SUCCESS and healthy;
- IREN is not degraded by an active current workflow incident;
- broker/canonical reconciliation is safe;
- no unexplained position or open-order divergence exists;
- no unrelated migration or infrastructure change is staged;
- the 4.3 runtime configuration identity is recorded;
- rollback to the final 4.3 deployment is possible.

Development on a feature branch may start earlier, but the branch must be rebased on the frozen 4.3 baseline before merge and the full test suite rerun.
