# ANEVUM V5 FOUNDATION - F2b Evidence Vault remote protocol

**Program:** ANEVUM.V5.FOUNDATION.2026-10-09.001  
**Session:** ANEVUM.RHEN.BUILD.2026-10-09.001.F2B-REMOTE-EVIDENCE-VAULT  
**State:** DRAFT; offline/CI validation only; no production or real R2 write authorization.  
**Parent:** F2a #476; F1 #471; architecture #469.  
**Tracker:** RHEN #472; WEB master board #259.

## Implemented

The next_rhen/remote_vault.py module adds an injected S3-compatible private object client, limited to a server-owned workspace and run ID. R2S3ImmutableStore requires conditional create-only PutObject (IfNoneMatch: *) and exact-byte bounded GET readback. It refuses overwrite conflicts, invalid keys, cross-tenant access, unavailable APIs and truncated/oversized reads. ETag is never accepted as evidence of source identity.

RemoteVaultReplicator copies the previously sealed, locally verified F2a archive batches and a canonical remote receipt to the injected object store. It verifies both objects and receipt bytes before writing the separate local remote-protocol acknowledgment. A crash before the local acknowledgment permits safe idempotent remote retries. Restoring from the remote receipt list requires no original SQLite database and revalidates full candidate/rejection coverage, hashes, sequence continuity and optional separately pinned receipt digests.

Synthetic client and local-object tests cover failures and recovery without credentials or broker data.

## Truthful states

- F1: PENDING_OFFHOST_ARCHIVE is only accepted local WAL, not a complete source session.
- F2a: LOCAL_ARCHIVE_VERIFIED proves a locally sealed batch.
- F2b: REMOTE_PROTOCOL_VERIFIED proves the behavior of an injected storage transport in tests, not the identity of a real R2 provider. It deliberately leaves journal source archive_state at LOCAL_ARCHIVE_VERIFIED.
- Future: ARCHIVED_VERIFIED requires real private off-host R2 provider verification and separate owner authorization. It is NOT set by these mocks.
- Independent source-cycle counts, original raw bar/quote/universe objects, 5m/15m/60m outcomes, complete real market sessions, and replay/holdout remain separate acceptance gates. Research stays AWAITING_EVIDENCE; neither a local nor remote storage receipt implies profitable alpha or live broker authorization.

## Real-R2 activation remains gated

A private staging bucket is described in draft #473. Existing bucket creation does NOT authorize storage writes or prove backups. Before using that bucket:

1. Obtain explicit owner approval for a staged synthetic paper-data upload and restore trial, plus a storage/operation budget and rollback.
2. Verify it has no public r2.dev/custom domain, use staging-only least-privilege credentials kept out of GitHub and the conversation, and bind an authenticated server-owned workspace/run scope.
3. Prove conditional S3 writes against the actual provider. If the endpoint rejects IfNoneMatch: *, fail closed rather than racing with HEAD plus unconditional PUT.
4. Produce bounded deterministic source batches, off-host immutable objects and independent receipt digests; destroy local fixture state and restore entirely from R2.
5. Independently verify upstream full-cycle count and raw market source objects. Preserve reason-coded missing states; do not claim a complete research run based on object readback.
6. Design and test the distinct real off-host WAL ACK transaction, crash recovery, retention/object immutability and native RHEN Operations cost/backpressure alerts.
7. Complete one actual isolated paper session and five consecutive full-integrity sessions before reliability acceptance, without changing founder live strategy/risk/order behavior.

## Integration conflict

Draft RHEN #473 is an older single-session transport proof, while #476 -> #477 is the bounded WAL-backed implementation train. Do not merge duplicate archive abstractions independently; reconcile deliberately before main.

## Local test command

python -m pytest -q tests/test_v5_decision_journal.py tests/test_v5_evidence_vault.py tests/test_v5_remote_vault.py

There is NO live RHEN merge/restart, broker call, D1 migration, Worker deployment, R2 authorization or public Commons exposure in this F2b branch.
