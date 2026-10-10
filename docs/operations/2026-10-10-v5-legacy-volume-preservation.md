# ANEVUM V5 — legacy RHEN volume preservation runbook

**Tracking:** ANEVUM.V5.FOUNDATION.2026-10-09.001  
**Issue:** https://github.com/anevum/rhen/issues/474  
**Scope:** Evidence preservation; not a trading release  
**Status:** Draft tools and tests. NO live volume has been copied, restored, or certified by this PR.

## Known live baseline

- Railway RHEN production service rhen: successfully redeployed as b6602844-6cdd-411b-a542-b3d8ea096aa9; app logs show execution_enabled=False, bot_armed=False, live_trading=False, extended_equity_lane_enabled=False, and extended_equity_execution_enabled=False.
- User reports zero positions and zero open orders in Alpaca. Trading API source verification not independently available through the current ChatGPT Alpaca market-data connection.
- Mounted rhen-data volume: 5 GB allocated, approx. 1.22 GB used, mount /data. Detached rhen44-shadow-data volume: 100 MB allocated. No known reliable backup/restore has yet been verified.
- Core, execution, observer, IREN and router child processes still run in the otherwise disarmed legacy Railway service. Old processes are not equivalent to separately billable Railway services.

## STEP 1 — Railway-native volume snapshot FIRST

The connected Railway MCP integration does not expose a backup-create or backup-list API. Do not claim a backup already exists.

1. Open the [RHEN Railway production service](https://railway.com/project/808098a9-937e-4ca4-ac98-dd2dcfef5d0c/service/f933a669-8591-4233-8510-e0db1548e463?environmentId=63a64723-574d-497b-b01b-a9fef7ea78ab).
2. Select the **Backups** tab for the attached rhen-data volume and choose **Create backup**. Verify its time, completion state, and retention; optionally lock it against expiration. Review associated backup storage charges.
3. **Never restore into live production.** A test restore belongs in an isolated disposable destination. Do not touch the mounted production volume.
4. Record backup ID/time/status in PRIVATE operations notes, not a public GitHub issue. A persistent volume is not itself a backup. Do not delete the original project or volume.
5. Plan a **separate** preservation/inspection path for the detached rhen44-shadow-data volume. It was not part of the attached-volume snapshot.

Official Railway docs: https://docs.railway.com/overview/the-basics#backups and https://docs.railway.com/volumes/backups

Railway snapshots may remain tied to the Railway project and incur incremental storage charges. They do not prove that missing historical candidate decisions were ever persisted. A provider-independent off-host backup and tested restore are still required before source deletion.

## STEP 2 — private read-only application-aware export (DRAFT ONLY)

The one-shot tool scripts/v5_legacy_volume_snapshot.py is introduced in this PR.

It traverses a local, private source directory, refuses symlinks, special files, output inside the source, overwrites, or changing ordinary files, copies ordinary files with source-stat verification, and uses SQLite's **online backup API** for databases so committed yet uncheckpointed WAL rows are included. Paired live WAL/SHM files are omitted only for recognized SQLite databases; orphaned sidecars remain as forensic files.

It writes a private payload directory, per-file SHA256 digest manifest and independently verifiable local READY marker. It never authenticates to Alpaca, Cloudflare or Railway, never places broker orders, and never uploads evidence.

Example operator command, only after the script is delivered into a controlled private runtime with enough space OUTSIDE the original volume:

    python -m scripts.v5_legacy_volume_snapshot snapshot --source /data --output /private/rhen-legacy-20261010
    python -m scripts.v5_legacy_volume_snapshot verify --snapshot /private/rhen-legacy-20261010

Output states explicitly:
- LOCAL_VERIFIED_NOT_OFFHOST (not yet transferred to independent storage);
- per-database-consistent;not-globally-atomic;
- research_completeness_proven=false;
- broker_flat_independently_proven=false;
- ready_for_volume_deletion=false.

The copied data may contain **private brokerage and personal records**. Restrictive local permissions are not encryption. Do not publish, attach to public tickets, email plaintext backups or commit to GitHub. For transfer to private Cloudflare R2 use independently vetted encryption/short-lived least-privilege credentials and validate the remote restored content. The R2 staging bucket exists but no actual authenticated Railway data transfer has been performed.

## STEP 3 — independent restore acceptance

Restore the provider-native snapshot to a SEPARATE environment, never live RHEN. Validate SQLite integrity, file hashes, historical decision/fill records and source-provenance records. The exported manifest verifies accidental corruption, but is not a digitally signed, independently authenticated certificate. Existing compacted or never-captured decision populations must remain labeled incomplete; missing events cannot be synthesized from future market bars.

## STEP 4 — only then retire legacy services

| Target | Gate |
| --- | --- |
| Always-on embedded IREN | Replace minimum health, alert and schedule functions with native RHEN Operations and verify website callers |
| GRAEN, VELUM, NOSTRA children | Preserve original experiments, forecasts and replay evidence; map consumers; migrate tested capabilities inside RHEN |
| Legacy owner-only Command | Complete Commons owner/member RHEN_NEXT workspace, same secure Alpaca linking as any user, historical owner records read-only |
| Live legacy Railway RHEN | Broker flat verified, reliable off-provider backup **and isolated restore**, production/staging data consumers migrated |
| Mounted and detached volumes | Separate content verification, migration, retention and explicit deletion approval |
| Empty ANEVUM Core / RHEN Archive Railway projects | Check remaining external dependencies before irreversible project removal |

This PR does **not** change execution settings or deploy the new backup script. Existing execution disablement is already confirmed live by runtime logs. Keep production volume and service until the snapshot and independent restore gates have passed.
