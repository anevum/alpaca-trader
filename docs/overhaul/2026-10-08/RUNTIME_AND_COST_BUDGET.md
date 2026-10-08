# Runtime and Cost Budget

## Objective

Spend persistent compute on trading reliability and market observation, not on idle service topology.

## Baseline

Observed main RHEN service:

| Window | Avg CPU | Avg RAM |
| --- | ---: | ---: |
| 7 days | ~0.205 vCPU | ~0.832 GB |
| 24 hours | ~0.477 vCPU | ~1.510 GB |

Observed permanent shadow, 24 hours:

| Service | Avg CPU | Avg RAM |
| --- | ---: | ---: |
| rhen44-shadow | ~0.072 vCPU | ~0.129 GB |

The baseline is high enough that architecture cost must be treated as an engineering constraint.

## Budget philosophy

The first target is not an arbitrary CPU number. It is:

1. one permanent service;
2. zero unnecessary resident subprocesses;
3. no permanent shadow;
4. no permanent model worker;
5. stable execution latency/reliability;
6. then measure actual Railway cost.

## Resource targets

These are engineering targets, not guarantees.

### Required

- permanent Railway services: 1
- permanent shadow services: 0
- permanent LLM workers: 0
- live replicas: 1 unless resilience evidence justifies more
- duplicated schedulers: 0

### Initial optimization target

After a stable 7-day post-cutover window:

- average memory materially below the pre-overhaul 0.832 GB 7-day baseline;
- average CPU materially below the pre-overhaul 0.205 vCPU 7-day baseline;
- no increase in missed scan cycles, stale market data, order-management errors, or reconciliation failures.

### Stretch economic target

Drive measured included-resource usage toward the Railway Hobby included amount without degrading trading reliability.

If the system cannot remain near the included amount, document exactly which trading-critical workload causes the excess. Do not disable safety or evidence collection merely to hit a billing number.

## Measurement windows

Capture:

- 1 hour after deployment;
- 24 hours;
- 3 trading sessions;
- 7 calendar days.

For each window record:

- CPU average/max;
- RAM average/max;
- disk usage;
- network usage;
- scan-cycle timing;
- event-loop/scheduler lag if available;
- market data freshness;
- order/reconciliation errors;
- number of completed scheduled evidence jobs.

## Cost attribution

Classify runtime work into:

- LIVE_TRADING_CRITICAL
- EVIDENCE_CRITICAL
- OPTIONAL_RESEARCH
- PRESENTATION
- LEGACY

Only the first two are allowed to justify permanent compute by default.

## Storage

Do not delete canonical evidence to save pennies.

Do:

- remove abandoned temporary artifacts;
- compact/vacuum databases when safe;
- set retention for reproducible caches;
- keep immutable run manifests/release evidence;
- distinguish allocated volume size from actual usage.

## Shadow policy

A shadow service is a test instrument, not a permanent subsystem.

Every shadow must declare:

- candidate release;
- question being tested;
- start timestamp;
- minimum observation duration/sample;
- stop criteria;
- promotion or rejection outcome;
- automatic/manual teardown owner.

After the current 4.4 validation decision, the permanent `rhen44-shadow` service should be removed unless a specific active promotion gate still requires it.
