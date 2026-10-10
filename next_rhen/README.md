# ANEVUM V5 — FOUNDATION | F1 builder handoff

**Program ID:** ANEVUM.V5.FOUNDATION.2026-10-09.001
**State:** Owner-locked architecture and first isolated implementation slice. NOT DEPLOYED.
**Tracking:** RHEN architecture [#468](https://github.com/anevum/rhen/issues/468), first journal [#470](https://github.com/anevum/rhen/issues/470), [Commons integration](https://github.com/anevum/anevum-web/issues/257) and [Commons V5.1 shell PR #256](https://github.com/anevum/anevum-web/pull/256).

## F0: Common contract

- contracts/foundation/workspace-state.v1.schema.json is mirrored byte-for-byte in anevum-web.
- FOUNDER_PRIVATE + LEGACY_FOUNDER is only the existing owner operator terminal. Ordinary MEMBER_PRIVATE uses RHEN_NEXT, never founder brokerage credentials.
- V1 member execution permission permits only NONE or PAPER_ONLY; live-member approval requires a separate reviewed version.
- Research evidence has explicit NOT_CONFIGURED / AWAITING_EVIDENCE / BLOCKED / REPRODUCIBLE states. REPRODUCIBLE is not evidence of trading alpha.
- contracts/foundation/decision-cycle.v1.schema.json defines complete private source observations; no raw candidate or account data goes into Commons.

## F1: What is actually implemented

The independent Python namespace next_rhen/evidence_journal.py is PAPER_RESEARCH_ONLY. It does not import legacy RHEN app, broker, Railway, Cloudflare, HTTP or model code. It writes all QUALIFIED/REJECTED/UNMEASURABLE candidates and actual feature values per decision cycle through an atomic, sequence-chained SQLite WAL with local SHA256 integrity checks.

Pre-append checks require a complete universe-to-candidate match, reason-coded decisions, no future market observation, strategy/config/version identity and raw market/bar/quote reference hashes when source status is COMPLETE. Duplicates are idempotent only when identical. Gaps, conflicting rewrites, invalid values and corrupted stored chains fail closed.

Run the isolated tests with: python -m pytest -q tests/test_v5_decision_journal.py.

Export is **PRIVATE** and deterministically checks the stored run. Every F1 export reports AWAITING_OFFHOST_ARCHIVE_AND_UPSTREAM_ATTESTATION: a local WAL is not an R2 backup, cannot prove an upstream cycle was never lost, and cannot prove original bars/quotes were actually archived.

## Canonical V5 product boundary

The owner consolidated **GRAEN, VELUM, and NOSTRA exploratory prototypes into RHEN** for the V5 successor architecture. Treat them as research concepts to assess and absorb, not separately deployed servers or separate paid products:

- `next_rhen/research/`: experiment registry, rule-set proposals, verification (GRAEN ideas).
- `next_rhen/replay/`: deterministic paper/frozen backtests, same engine/risk logic (VELUM ideas).
- `next_rhen/forecast/`: optional research-only regime/forecast evaluation (NOSTRA ideas).
- `next_rhen/ops/`: minimal deterministic scheduler, archive health, incident/log/Slack support.

These are **planned internal namespaces**, not implementation claims. They share one successor RHEN application/codebase; CPU-intensive research can run separately as **bounded jobs without live trading keys**, not always-on prototype services. Existing legacy imports remain untouched until safely migrated. Future **IREN** is outside the V5 critical path and can be reconsidered for local GPU/hosted intelligence later. RHEN retains native health, safety and notifications now, without an IREN dependency or GPU.

Decision record: https://github.com/anevum/rhen/blob/design/anevum-vnext-greenfield-platform-20261009/docs/architecture/2026-10-10-rhen-integrated-prototype-consolidation.md

## F2 follows before staging

1. Bounded separate off-host archive job: export from WAL to private R2 immutable batches, verify object SHA/length, archive acknowledgements and idempotent restart.
2. Independently attest source cycle sequences and expected symbol totals before the journal, with backpressure/retention incidents.
3. Prove download/restore and replay of one complete real paper session twice identically; negative results count as completed research.
4. Verify five consecutive sessions, storage budget and source integrity. Never weaken broker protective safety or auto-promote rule changes.

## Integration and safety

Commons V5.1 UI remains separately owned by web PR #256; the child website workstream only introduces versioned contracts, member-verified presentation guards and tests. No new UI data, D1 migration, auth secret, Stripe configuration, or member broker writes are activated.

DO NOT merge this branch into the live RHEN main branch or provision brokerage/Railway secrets without separate operator preflight, release window, and explicit authorization. A dedicated RHEN successor repository can later extract this isolated namespace when approved.
