# ANEVUM V5 Foundation F3d — off-database witness contract

Program: ANEVUM.V5.FOUNDATION.2026-10-09.001
Base: F3c draft RHEN #483 → F3b #482 → F3a #481 → F2c #479 → F2b #477 → F2a #476 → F1 #471
State: ISOLATED DRAFT. MOCK STORAGE ONLY. NO PRODUCTION RELEASE.

## Problem addressed

Local SQLite scanners, scheduled-slot tables, the F1 journal and R2 source
receipts might all agree with each other while still missing an entire
upstream scan. A content hash kept alongside that content cannot establish
an independent time-of-capture or trustworthy schedule count.

This draft introduces strict *interfaces* for two separately held evidence
objects, without falsely claiming that mock storage is an external witness:

1. The planner declares up to ten paper-only slots BEFORE source collection.
   F3d reads the existing hash-chained F3b planning WAL, validates every
   slot, scope and sequence, and writes an immutable plan object to an
   injected witness store. The returned exact object key and SHA-256 must
   be recorded through a different independently controlled channel.
2. Once an F3c provider-source archive exists, the caller can bind its exact
   receipt key + SHA-256 to a cycle in the PREEXISTING plan witness.
   Source objects are replayed before the witness receipt is accepted;
   the snapshot digest and session/cycle scope are pinned as well.
3. Restore from remote-only objects and externally supplied pins can replay
   the entire planned set, reject duplicate or missing source receipts,
   verify original raw provider pages through the F3c parser, and match
   recovered slot IDs/timestamps to the witnessed plan WITHOUT the local
   planner, scanner, journal or market-object directories.
4. Failure to read an immutable object, mismatch of SHA-256, workspace
   crossover, object tampering, missing planned slot, duplicated receipt,
   changed source snapshot or incorrect plan binding BLOCKS. No fallback to
   estimated scan counts, unverified hashes or fabricated source rows.

## Trust boundary

This is a source/archive-integration **contract**, not externally
authenticated proof. All automated tests use TWO independent in-memory
StrictSyntheticS3 backends; they do NOT create real Cloudflare R2 buckets,
send real Alpaca requests, prove different provider accounts, or prove
an out-of-band witness controls the receipt digests.

The module marks all of the following FALSE after a successful mock restore:
- Original market data independently signed or feed-entitled.
- Real scheduler origin signed and all expected scheduled cycles delivered.
- Witness custody/scope externally independently verified.
- Independent hash pins checked with a different owner/security principal.
- R2 restoration of the actual F3c source format.
- F1 journal production off-host ARCHIVED_VERIFIED transaction ACK.
- Full future market session, research alpha or broker write permission.

Final result remains AWAITING_EVIDENCE, never ARCHIVED_VERIFIED, PASS,
REPRODUCIBLE, market-complete or trading authorized. Broken witness source
is BLOCKED. The existing real [F2c synthetic private R2 S3 verification]
(https://github.com/anevum/rhen/actions/runs/38019261298) is a distinct
transport-only acceptance; it does not upgrade F3d market provenance.

## Security and budget

- Witness objects are private, write-once, exact-byte readback and
  SHA-256 validated, scoped to workspace/run/session and a single cycle.
- At most ten paper scans per witness plan; at most 40 KB per witness object.
  F3c historical API fetch has separate fixed GET-only route and paging
  budgets; no new network transport is constructed in this module.
- Source store and witness store must not be the same object reference.
  That is a basic guard, NOT cryptographic proof of different accounts,
  separate key custody or independent cloud providers.
- The witness publisher does not provision credentials, read the website
  environment, require a GPU, call Alpaca trading/order routes, or update
  the private founder's original RHEN account.
- External hash pins must survive loss of all source evidence and remain
  inaccessible to the source-writing identity. Distinct IAM principals,
  distinct R2 buckets and ideally a different provider/object-lock custody
  arrangement should be reviewed before live paper use.
- No web user can view raw quotes, private market bodies or member broker
  credentials through this API.

## Verification

No credentials or network:

    python -m pytest -q tests/test_v5_paper_witness.py

The draft RHEN GitHub CI also runs F1-F3d suites and all legacy regressions.
The paper witness stage is NOT registered as a new automatic cloud workflow;
no production secrets are injected into PR checks.

## Next gates

1. Select actual separate, least-privilege identity + durable trust-anchor
   backend, and protect its SHA-256 pins outside both source storage and
   local SQLite. Verify the true account/bucket separation. Review costs.
2. Provision separate *read-only* market-data credentials approved for
   staging and validate actual feed entitlements and historical bar/quote
   access. Do not reuse production RHEN keys or Alpaca Connect OAuth secrets.
3. Connect a real independent paper scanner to predeclare all schedule slots,
   issue witnesses BEFORE ingest, then link actual R2 F3c source receipts.
4. Execute a manual, bounded, human-approved genuine market-data + R2
   staging trial with separate secret environment and no broker orders.
5. Rebuild the full paper session from remote-only market and F1 evidence
   and demonstrate external schedule count and 5 consecutive real sessions.
6. Only after separate compliance/security and operational approval consider
   public product activation. Draft PRs are not merged or deployed by F3d.
