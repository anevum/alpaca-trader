# RHEN resource efficiency — measured optimization workstream

Session: `ANEVUM.OPS.PERF.2026-10-08.001.RHEN-RESOURCE-EFFICIENCY`  
Branch: `perf/rhen-resident-memory-profile-20261008`  
Status: diagnostic instrumentation / deliberately disabled by default

## Why

ANEVUM has one permanent Railway RHEN service, currently supervising four processes:

- `core` — canonical storage, embedded NOSTRA, deterministic research evidence
- `execution` — live market data, scanning, broker reconciliation, risk and orders
- `iren` — deterministic operations, incidents, scheduler
- `router` — public/private API gateway

Railway baselines captured 2026-10-08:

| Window | Average RAM | Maximum RAM | Average CPU | Maximum reported CPU |
| --- | ---: | ---: | ---: | ---: |
| 1 hour | 1.360 GB | 1.392 GB | 0.063 vCPU | 0.101 vCPU |
| 24 hours | 1.517 GB | 3.796 GB | 0.457 vCPU | 10.412 measured utilization units |
| 7 days | 0.876 GB | 3.796 GB | 0.205 vCPU | 10.412 measured utilization units |

The short 1-hour sample covers a settled deployment; the longer windows contain changing deployment and workload conditions. Railway CPU utilization maxima are platform-reported samples, not proof of a sustained 10-core entitlement. The 7-day average also includes older runtime states and should not be treated as the new steady-state cost. The 24-hour 3.8 GB memory peak is a key guardrail: do not optimize to idle memory alone or assume that all configured RAM headroom is continuously available. The live deployment was healthy with zero service warnings at capture. These samples are not a full business-cycle bill or statistically valid post-optimization comparison.

A large container limit is not free capacity; Railway bills actual compute consumption. Free memory beneath the service limit is useful for RHEN growth but does not offset current resource usage. Do not reduce execution reliability merely to meet a billing target.

## Phase 1 — empirical accounting (this PR)

The supervisor now contains an opt-in OS resource profiler (default OFF). It does not add a service, worker, port, broker API request, dependency, or additional scheduled trading/research job.

Enable only **after CI and an agreed, stable deployment window**, never by accident during an unrelated website or DB migration:

```text
RHEN_RESOURCE_PROFILE_INTERVAL_SECONDS=300
```

Allowed intervals: 60–3600 seconds. A missing, zero or invalid value disables the profiler.

Every 300 seconds it logs a `rhen_resource_profile` JSON record containing:

- process roles, PIDs and Linux process start-ticks (for restart-safe CPU rates)
- RSS and high-water mark
- PSS, when readable, with explicit partial-coverage markers
- anonymous and file-backed memory
- child per-interval CPU utilization
- container cgroup memory and aggregate CPU counters
- a monotonically timed measurement interval

It does NOT collect command lines, environment variables, broker credentials, trading signals, P&L, account numbers or raw market data. Profiling failures are nonfatal and produce only an exception class.

**Interpretation**: RSS sums are not billable memory because shared pages are double-counted. PSS distributes shared pages but may be inaccessible. Cgroup memory includes kernel/cache/supervisor overhead and is closest to actual container usage. Never present partial PSS as a whole-container total.

## Phase 2 — capture a representative workload

After deployment:

1. Confirm RHEN execution readiness, broker reconciliation, IREN health and public/API health.
2. Collect at least 12 consecutive five-minute profiler events before considering a first optimization.
3. Collect both market-active and market-idle periods (ideally a complete trading session plus overnight), plus a startup/maintenance window. Analyze the 24-hour memory peak rather than attributing it to any single process without evidence.
4. Export relevant Railway log lines to a local JSONL file; do not export secrets or private account data.
5. Run `python -m scripts.resource_profile_report profile.jsonl`.
6. Compare PSS per role, P95 container memory, CPU per role, and market-sensitive latencies before selecting an optimization.

The reporting command is offline; it summarizes only valid sanitized profiler events, does not connect to Railway, and cannot place broker orders.

## Phase 3 — optimization candidates ranked by evidence

Only implement a candidate after measuring where the resource is used:

| Candidate | Expected lever | Guardrails |
| --- | --- | --- |
| Core evidence projections / query memory | Reduce Python transient objects, heavy deserialization, growing in-memory caches | Preserve point-in-time records and all canonical evidence |
| Execution symbol-scanning memory | Bound per-symbol/interval histories, eliminate duplicated data, improve batch reuse | No candidate changes, missed scans, order latency or stale data |
| Router imports / ASGI resources | Remove unnecessary heavy imports or duplicated clients | Keep authentication, caching, privacy and public/private routes equivalent |
| IREN dependency footprint | Lazy-load heavy analysis code not needed for health/scheduling | Preserve drift, incident, scheduler, restart and protected review semantics |
| Python allocator/container tuning | Reduce fragmentation only if RSS/PSS and lifecycle evidence justify it | No OOMs, erratic latency or change to execution behavior |
| Database repacking | **Separate existing PR #447** — storage optimization, not a RAM reduction claim | Do not overlap database migration/repack with profiling deployment |

Do not collapse `execution` and `core` for cosmetic process-count savings before measuring imports and testing restart isolation. Their separate crash boundaries provide safety value.

## Promotion gates

No performance code may replace production on the basis of an import-memory estimate alone.

- CI tests and production equivalence tests pass.
- Current live strategy and authority fingerprint remain unchanged.
- Replay and deterministic evidence remain complete.
- Market data freshness, scan latency, order and protective-exit behavior do not regress.
- IREN incidents/recovery and broker reconciliation do not regress.
- Peak memory margin remains safe during the busiest market window.
- At least one representative observation window demonstrates a real reduction in cgroup memory without shifted costs or worse CPU.
- A rollback commit and old service configuration are recorded.

**Current target**: pursue sustained RAM reduction, e.g. 128–256 MiB as an initial engineering goal, **not** as a promised saving. Cgroup memory, not the sum of per-process RSS, is the acceptance metric.

## ANEVUM multi-app resource policy

- Public static portfolio, documents and feed display: Cloudflare-first, no always-on Railway instance per page.
- New stateless lightweight apps and APIs: evaluate Cloudflare Workers as the initial deployment; measure requests/cost against limits.
- Stateful or latency-sensitive workloads: Railway only if an always-on process is justified.
- CPU-heavy replay, evaluation, data transformations and AI research: bounded jobs, local/operator/Work runs or explicit scheduled windows, never idle permanent workers by default.
- Keep independent apps isolated at the code, credential, database-table/tenant and failure boundaries. Do not silently grant future app users RHEN broker credentials.
- Adopt a resource budget for every new app: expected requests, data retention, idle RAM/CPU, cold-start sensitivity, peak memory, and exit/teardown conditions.

This policy does not authorize moving RHEN's broker-write path or users' financial data to unvalidated infrastructure.
