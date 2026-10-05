# ANEVUM Core v3 — Runtime Consolidation

Status: PROPOSED / IMPLEMENTATION BRANCH  
Date: 2026-10-05  
Branch: `architecture/anevum-core-v3-20261005`

## Why this redesign exists

The current ANEVUM deployment has too many runtime boundaries for the workload and budget:

- RHEN project: 10 long-lived application services plus a scratch CI service.
- ANEVUM Core project: 4 application services plus PostgreSQL.
- Most services are built from the same repository and communicate over HTTP.
- PostgreSQL reached 4.99 GB on a 5 GB volume and crashed during WAL recovery.
- Railway metrics show PostgreSQL grew from roughly 3.45 GB to 4.99 GB in about 24 hours.
- `rhen.events` receives large high-frequency decision-cycle JSON that duplicates candidate features and replay context every 15–30 seconds.

The architecture problem is therefore not only database capacity. It is write amplification plus deployment fragmentation.

Core v3 is an architectural compression. It reuses the existing strategy/research code and removes unnecessary process/service boundaries.

## Logical system model

ANEVUM keeps only three top-level logical systems.

### IREN

Control plane.

Responsibilities:

- orchestration
- scheduler
- incidents and health
- human approvals
- GitHub/code-worker coordination
- Slack operational notifications
- unified system status

IREN does not trade.

### GRAEN

Research and intelligence plane.

GRAEN absorbs the former top-level VELUM and NOSTRA roles as internal capabilities:

- Research — hypothesis and strategy evaluation
- VELUM — replay/simulation engine
- NOSTRA — forecasting/calibration engine
- crypto edge discovery
- candidate forward-shadow observation
- research-agent workflows

VELUM and NOSTRA remain named modules in code/UI where useful, but are no longer independent deployment units or top-level subsystems.

GRAEN does not trade.

### RHEN

Execution plane.

Responsibilities:

- Alpaca credentials
- market-data acquisition
- equities and crypto lanes
- strategy selection
- paper/canary modes
- live execution
- order/fill reconciliation
- risk controls
- pre-open/equity session state
- bounded local evidence spool
- authenticated read-only market-data gateway for GRAEN

RHEN is the only system allowed to possess broker-order authority.

## Deployment topology

Target always-on topology:

1. `anevum-core`
   - IREN
   - GRAEN
   - Foundation API/storage
   - VELUM module
   - NOSTRA module
   - scheduler
   - research agent
   - crypto edge discovery
   - forward shadows
   - reporting/read models

2. `rhen`
   - current `alpaca-trader`
   - absorbs `btc-canary-001-paper`
   - absorbs `rhen-preopen-state`
   - broker execution and reconciliation
   - market-data gateway

3. Persistent Core volume
   - bounded Core v3 datastore
   - no raw market-bar archive
   - no unbounded telemetry archive

The public website/Command remains separate from this runtime topology and calls the unified Core API.

The existing scratch CI service remains non-production and one-shot only.

## Services to retire after cutover

These become modules/tasks inside `anevum-core`:

- `graen`
- `graen-research-executor`
- `rhen-crypto-edge-discovery`
- `rhen-velum`
- `rhen-research-scheduler`
- `rhen-research-agent`
- `nostra`
- `foundation-ingest`
- `foundation-evidence-probe`
- `foundation-migrator`
- `iren-executor`

These become RHEN modes/modules rather than services:

- `btc-canary-001-paper`
- `rhen-preopen-state`

No service is deleted until its replacement path is proven healthy.

## Storage model

Foundation v3 is a state store, not a raw-event warehouse.

### Keep durably

Indefinite or long-lived:

- strategy definitions and promoted versions
- broker orders/fills
- position/reconciliation records
- incidents and human decisions
- GRAEN candidate specs and final gate results
- VELUM replay summaries
- NOSTRA calibration/evaluation summaries
- deployment/source provenance
- configuration hashes

### Keep temporarily

Bounded retention:

- compact cycle summaries: 7 days
- candidate observations: 7 days
- position metrics: 14 days
- raw NOSTRA forecast/snapshot evidence: 30 days
- routine audit/job execution detail: 30–90 days

### Do not persist

- raw Alpaca bars that can be fetched again
- complete scan dictionaries every poll
- duplicate nested candidate metadata
- duplicate confirmation/regime structures
- full replay series when a summary + specification + source hash is enough
- routine hold/heartbeat events without state change

## Evidence compaction

The current `decision_cycle` event embeds full candidates and duplicated nested replay state each cycle. Core v3 replaces it with:

### cycle_summary

One compact record per cycle:

- timestamp
- market lane
- strategy version
- universe size
- candidate/qualified/rejected counts
- selected symbol/action
- reason codes
- cycle duration
- data health
- configuration hash

### candidate_observation

Persist only when one of these is true:

- candidate qualifies
- candidate is selected
- decision state changed
- rejection reason changed materially
- candidate is in a bounded top-N research sample

Stored feature vectors are normalized and non-duplicated.

### position_metric

Persist at a coarse interval or on material change, not every execution loop.

## Storage budget enforcement

Core v3 must have a hard storage budget.

Initial target:

- normal working set: under 500 MB
- warning state: 500 MB
- analytics shedding threshold: 750 MB
- critical records continue when analytics are shed
- never allow normal telemetry to consume the final 20% of the volume

A scheduled storage-maintenance task:

- applies retention
- checkpoints SQLite/WAL or performs database maintenance
- compacts rollups
- records current store size
- raises an IREN incident before the storage hard limit

## Datastore direction

Preferred Core v3 path: SQLite on the existing spare 5 GB evidence volume.

Why SQLite fits this topology:

- one `anevum-core` service is the only writer
- RHEN communicates through the Core HTTP API rather than direct database access
- no distributed writers are required
- removes an always-on PostgreSQL service
- removes PostgreSQL WAL/recovery overhead
- Python ships with SQLite
- WAL size can be bounded and checkpointed
- 5 GB is far more than required once evidence is compact and retained

The spare `rhen-evidence-spool-staging` volume currently uses only about 85 MB and can become the Core v3 volume after its current contents are accounted for.

The old PostgreSQL volume is not deleted during migration.

## Security boundaries

Consolidation must not broaden trading authority.

- `rhen` alone holds Alpaca order credentials and execution authority.
- `anevum-core` does not submit broker orders.
- GRAEN/IREN receive market data from an authenticated RHEN read API.
- Research and replay code remains `execution_authority=false`.
- Live promotion still requires explicit protected-state transition/human authority.
- GitHub/OpenAI credentials may live in Core because Core has no broker-order path.

## V15 status during migration

V15 remains the current BTC candidate:

`V15-R1-BTC-R2H-BREAKOUT-42-15`

The architecture migration must not change its frozen strategy specification.

During Core v3 work:

- V15 remains paper/shadow only.
- no live-money authority is enabled.
- historical evidence remains valid.
- forward evidence restarts only when the new Core evidence path is healthy.

## Migration sequence

### Phase 0 — Freeze unsafe expansion

- no new services
- no new top-level subsystem names
- no new raw evidence tables
- no live V15 promotion during infrastructure migration

### Phase 1 — Stop future storage amplification

- introduce compact telemetry payloads
- add storage budget/retention policy
- bound RHEN outbox size
- separate critical records from analytics

### Phase 2 — Build `anevum-core`

- unified FastAPI entrypoint
- Foundation v3 storage adapter
- IREN scheduler/control tasks
- GRAEN evaluator/research tasks
- VELUM and NOSTRA internal modules
- unified health/status endpoint

### Phase 3 — Rebuild active canonical state

Reconstruct only current/essential state from:

- GitHub canonical strategy/specification history
- Alpaca account/orders/fills/positions
- surviving RHEN durable spool
- current runtime configuration
- promoted GRAEN artifacts that are reproducible from source

Do not import the bloated raw telemetry archive.

### Phase 4 — Parallel verification

- point RHEN shadow evidence to Core v3
- run Core v3 beside legacy services
- compare health, order/fill projections, research state, scheduler state
- verify storage growth remains bounded

### Phase 5 — Collapse services

After verified parity, disable legacy services one by one.

No deletion in this phase.

### Phase 6 — Retire legacy storage

Only after explicit human approval:

- archive any required legacy evidence
- remove old PostgreSQL service/volume
- remove obsolete evidence-probe volume attachment if repurposed
- delete retired Railway services

## Definition of done

Core v3 is complete when:

- ANEVUM has three logical systems: IREN, GRAEN, RHEN.
- Only `anevum-core` and `rhen` need to be continuously running.
- VELUM/NOSTRA are internal GRAEN modules.
- Foundation is internal infrastructure.
- RHEN is the sole broker-authority boundary.
- routine telemetry cannot fill persistent storage.
- total Core storage stays below the defined budget.
- Command shows one live topology and real module activity.
- V15 can resume isolated forward shadow through the consolidated path.
