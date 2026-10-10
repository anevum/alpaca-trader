# ANEVUM V5 — live legacy retirement inventory and stop controls

**Program:** `ANEVUM.V5.FOUNDATION.2026-10-09.001`  
**Tracking:** [#474](https://github.com/anevum/rhen/issues/474)  
**Source:** Authenticated read-only Railway and `anevum/rhen:main` code inspection on 2026-10-09 EDT.  
**Status:** IMPLEMENTATION DRAFT. No live RHEN deployment change, broker orders, account state or deletions have occurred.

## Why this is not a straightforward process shutdown

The account currently has **one Railway service**, `rhen`, running `python -m app.rhen_core.supervisor`, with a persistent `/data` volume. Inside this **one container**, the supervisor launches 11 child process definitions:

| Child | Enabled/config | V5 disposition |
| --- | --- | --- |
| core | Always-on critical | Keep until RHEN V5 Evidence Vault and original ledger are safely migrated |
| execution | Always-on critical | Stop **new entries first**; protect existing positions, exits and reconciliation |
| observer | `RHEN_OBSERVER_ENABLED` | Retain read-only broker verification during retirement; later unified RHEN observer |
| graen | `GRAEN_RUNTIME_ENABLED` | Fold verified research routines into `rhen.research` |
| velum | `VELUM_RUNTIME_ENABLED` | Fold verified replay/simulation into `rhen.replay` |
| research-agent | `RHEN_RESEARCH_STANDALONE_RUNTIME_ENABLED` | Remove legacy always-on agent after queue/data inventory; optional on-demand tools later |
| nostra | `NOSTRA_STANDALONE_RUNTIME_ENABLED` | Optional offline `rhen.forecast`, not permanent service |
| iren | Unconditionally launched; no `IREN_RUNTIME_ENABLED` gate | Defer advanced IREN; replace mandatory health, evidence/alert and schedule functions inside RHEN |
| iren-executor | `IREN_EXECUTOR_ENABLED` | Retire only after no required automation/external callers |
| preopen | `PREOPEN_STATE_ENABLED` | Reevaluate once new RHEN core/market session logic passes parity |
| router | Always-on critical | Keep until private Commons↔RHEN API route cutover passes authentication tests |

The presence of historical variable names does not prove a process is enabled or safe to delete. Variables returned through OAuth are **names only**. Read each actual runtime health and downstream dependency before disabling any child. Never delete research history as a shortcut to reducing process count.

## Actual Railway footprint (source read 2026-10-09 EDT)

| Project | Environment | Services | Volumes | Cleanup |
| --- | --- | --- | --- | --- |
| RHEN | production | `rhen` (1 active) | `rhen-data` mounted 5 GB; `rhen44-shadow-data` detached 100 MB | Retain until broker flat + verified backup / restore; detached data not yet inventoried |
| RHEN Archive | production | none | none | Empty administrative project candidate; no live workload |
| ANEVUM Core | staging | none | none | Empty administrative project candidate; no live workload |

The operational RHEN service is an **aggregated supervisor**, not 11 separately billable Railway services. Empty projects do not create per-service runtime charges. No standalone Railway GRAEN/VELUM/NOSTRA projects or web servers were found.

### Immutable baseline

Current RHEN Railway production `rhen` deployment: `2924116a-0073-4b46-8280-24e6c3435a7e`, GitHub source commit `232cbfa7b6a028db99e34f9ce94caeb2fd024e70`. Service source `anevum/rhen`, branch `main`, volume mount `/data`, one replica.

Rollback is technically offered for current deployment by Railway, but **rolling back code does not constitute a backup of mutable volume data**. Preserve database and archive snapshots independently, verify checksums and isolated restore before any migration or permanent deletion.

## Trading stop safety — avoid erroneous emergency controls

Existing code gates execution through `BOT_ARMED`, `EXECUTION_ENABLED`, `LIVE_TRADING` and acknowledgments. **Do not set these false** while active positions may need the Python exit engine; `run_once()` monitors protective stops and exits only when the execution loop is invoked. Likewise `SCAN_ONLY=true` changes broker reconciliation and execution paths.

**Dangerous zero semantics:**
- `MAX_DAILY_ORDERS=0` means **no daily limit**, not zero entries.
- `EXTENDED_EQUITY_MAX_ENTRIES_PER_SESSION=0` means **no per-session limit**.
- `MAX_NEW_ENTRIES_PER_CYCLE=0` means unbounded in risk-based portfolio mode and invalid in count-based mode.

Existing `POST /v1/command/entries/disable` is safer for immediate stop but a memory-only state change. A restart reinitializes entries unless permanent guard exists.

### Dedicated persistent migration guard (draft PR #475)

`RHEN_LEGACY_NEW_ENTRY_LOCK=true`, when deployed in a reviewed release, will:

- Force `runtime_state.entries_enabled=False` at application startup;
- Reject regular and extended **buy** decisions at separate risk-validation boundaries even if API bypass or state drift occurs;
- Return HTTP 409 if operator Command tries to enable entries while the lock is active;
- **Not** change the broker execution authorization or existing sell-to-flat exit gate;
- Default to false so existing production remains unchanged until explicit rollout.

No new order is created by this guard. With open positions it is intended to retain exit-management; however, new code deployment **restarts** the runtime, so broker-authoritative exposure and order state must be checked before rolling it out.

## Operational evidence gates and handoffs

1. **Broker source:** read-only Alpaca Trading account `positions`, all open orders (including extended-session and protective stop), fills and lifecycle after market close. This session's connected Alpaca app only exposes market data; counts remain unknown. No brokerage liquidations/cancellations without separately specified owner instruction.
2. **Release:** CI green; diff shows only entry gate and tests; backup/restart/rollback owner preflight. Lock must be durably activated, not merely set on an unapplied staged Railway patch.
3. **Observe:** Verify successful settled deployment, two lanes blocked from new buys and existing exit monitoring unchanged; report any unexpected broker order statuses. Live frontend must label trading `SUSPENDED_FOR_REBUILD`, not display a false flatline or simulated live execution.
4. **Archive:** Export RHEN Core SQLite DB (including WAL snapshot), original broker fills/lineage, config/provenance fingerprints, GRAEN/VELUM/NOSTRA prototype artifacts and shadow volume material; independently hash and test restore in private staging R2, never publish raw broker data to Commons.
5. **Decommission:** Only after zero broker exposure, no open orders, restored archive and finished member-private RHEN V5 paper workspace, stop retired subprocesses and legacy service; then evaluate unused volumes/empty projects for deletion.
6. **Founder migration:** Founder uses their verified Commons member identity + an `RHEN_NEXT` workspace and the **same future Alpaca OAuth onboarding** as every member. Historical founder ledger becomes read-only archival data. Founder/admin permissions are a separate control plane and grant no broker authority or visibility into other members' account data.

**Status remains no live changes.** The retirement project is safe only if no one mistakes draft code, empty projects, or lossless R2 staging bucket creation for broker-flat confirmation or complete research provenance.
