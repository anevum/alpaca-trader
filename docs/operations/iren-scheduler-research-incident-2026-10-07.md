# October 7 scheduler and research retention repair

Session: ANEVUM.IREN.INCIDENT.2026-10-07.001.SCHEDULER-RESEARCH-DEPENDENCY

The daily workflow failed with `evidence_unavailable` / `canonical_report_not_current`: analytics storage pressure shed the report while the reporter interpreted HTTP 200 as durable completion. Daily/weekly report inputs now survive shedding and require an insert receipt. Today's canonical report was confirmed before the bounded idempotent workflow recovery. Workflow `rhen.research.daily:1.0.6` succeeded; IREN subsequently verified healthy observations and closed the incident.

The high-volume raw `scan` diagnostics accounted for most retained payload storage. Raw scan JSON now uses a lossless `zlib-json-v1` envelope when smaller. Readers decode both historical plain JSON and the envelope. Startup migration processes at most 40,000 scans, verifies decoded equality before each update, and leaves event identity, timestamps, execution truth, canonical candidates, decisions, outcomes and report contents intact. Existing retention and storage thresholds are unchanged. Public projections remain decoded logical payloads.

Compatibility: deployments sharing this database must retain the codec reader. A rollback to code predating the reader requires decoding migrated scans first; do not roll back the database reader independently. RHEN 4.4 crossover must incorporate these Core repairs before promotion.

Five-minute extended-hours checkpoints retain fresh scan timestamps, rejection reasons, and decision metadata under the extended strategy identity, independently of the execution loop. They expire after 35 days and mark forward outcomes `NOT_MEASURED`; checkpoints do not count as independent research sessions. Receipt shedding counters and daily warnings distinguish computed measurements from durable evidence. No live strategy thresholds, risk, capital, or trading authority changed.

Validation: logical round-trip and idempotent migration checks, unchanged broker-fill payload check, bounded decoder check, shed-receipt visibility, scheduler retry/readiness tests, and full suite. Hosted deployment and canonical post-repair health must be verified after merge.
