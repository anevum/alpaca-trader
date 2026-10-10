# ANEVUM V5 Foundation - F3a scanner-source reconciliation

Program: `ANEVUM.V5.FOUNDATION.2026-10-09.001`

Status: **isolated draft source-attestation proposal; not merged or deployed**.

## Problem

F1's decision journal can verify that recorded cycles are internally intact. It
cannot prove that an upstream scanner produced no additional cycles or that
all rejected candidates were captured before the journal append. F2 archives
inherit that blind spot, regardless of their transport integrity.

## New independent, prospective source contract

`next_rhen/source_attestation.py` supplies a separate SQLite/WAL
`SourceScanLedger`. The upstream scanner must capture and durably commit a
complete `anevum.scanner-source.v1` manifest BEFORE calling the F1 journal.
Each manifest contains source cycle and session identities, source universe,
all candidate dispositions including rejections, exact rejection reasons,
per-candidate feature SHA-256, market source digest, strategy/config/code
identities, and timestamps. Its append-only hash chain rejects rewinds,
conflicting rewrites, sequence gaps and corrupt records.

`verify_scan_population` independently checks the entire scanner ledger chain
and the F1 `DecisionJournal.export_session` output. It compares source and
journal cycles, identities, universe, market fingerprints and every candidate
without sampling. It accepts optional scheduler-origin expected-cycle counts
and an externally pinned scanner chain digest. Missing cycles, mismatches,
corrupted ledgers, cross-tenant data, an invalid external digest, or bounded
verification overflow must fail closed (`BLOCKED`).

A local match only yields `AWAITING_EVIDENCE`, *never* `PASS` or
`REPRODUCIBLE`. The scanner is NOT instrumented in production by this package;
source-origin trust, real feed/bar/quote provenance, R2 off-host completeness,
forward outcome maturation, five full trading sessions, and all broker-live
authorizations remain separate.

## Validation plan

The earlier external package passed **18 offline tests** and deliberately skipped
**3 real F1 integration tests**. This draft includes those tests, which MUST run
against the authentic F1 journal in GitHub CI, plus negative cases for malformed
scanner digest and malformed source-field types. Never treat a source/journal
match as proof of actual upstream scanner completeness.

## Next work after draft CI

1. Instrument a **new isolated paper scanner**, writing manifests before F1
   journaling. Neither source integration nor scheduler-origin upstream counts
   are supplied by these test fixtures.
2. Add independent scheduler-origin cycle counts and durable external scanner
   digest anchoring outside both SQLite databases before any completeness claim.
3. Stage one actual paper market session with independently trusted market
   data objects and full candidate counts, then verify its cold restore.
4. Coordinate with Commons member-workspace capabilities without exposing raw
   research, broker identities, or personal data.

## R2 status

Private Cloudflare R2 staging's real conditional S3 create-only upload,
conflicting-write rejection, and independent remote restoration **passed** in
[manual GitHub Actions run #38019261298](https://github.com/anevum/rhen/actions/runs/38019261298)
for **3 synthetic cycles / 6 candidates / 3 rejected**. This verifies storage
transport, NOT scanner-origin completeness, market source evidence, live RHEN,
production WAL `ARCHIVED_VERIFIED`, or five real paper trading sessions.
F1/F2a/F2b/F2c implementation branches remain unmerged.

## Privacy and safety

The F3a source module does not import Alpaca, live RHEN, Cloudflare, HTTP,
Stripe or the website. It cannot place trades, activate OAuth, access other
members' evidence or publish private source manifests to Commons. Installed
Alpaca Connect Client ID/secret are separate from and unusable for R2.
