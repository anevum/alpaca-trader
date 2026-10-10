# ANEVUM V5 FOUNDATION — F2c R2 staging acceptance

**Program:** `ANEVUM.V5.FOUNDATION.2026-10-09.001`  
**Session:** `ANEVUM.RHEN.BUILD.2026-10-09.001.F2C-STAGING-R2-ARCHIVE`  
**Status:** Staging diagnostic verified; S3 conditional acceptance not yet executed.  
**Tracks:** RHEN #472, stacked F2a #476 / F2b #477, Commons master web #259.  
**Production and live broker:** UNCHANGED.

## Verified Cloudflare observations

The actual R2 staging bucket `anevum-rhen-v5-evidence-staging` was inspected using the connected Cloudflare API. The bucket is private: **r2.dev disabled**, no custom domains. This does not imply object-lock retention policy (none configured). An earlier single 127-byte synthetic REST upload/readback passed.

An additional synthetic diagnostic was executed against the **real Cloudflare R2 bucket** using the supported Cloudflare REST object API, not the production S3 writer:
- Namespace: `private/anevum-v5/wrk_stageprobe001/paper-f2c-rest/mv1r962v-e08b8f342c/`.
- Three separate objects: gzip-compressed JSONL encoded as base64 ASCII **for REST transport only**, a canonical manifest, and a diagnostic receipt. Their combined uploaded REST payload was **1,666 bytes**.
- Independently pinned manifest SHA256: `0f376d126f8e6363f3b90557937fd6e8371d230de7377805088046af16ff92fa`.
- Raw event JSONL SHA256: `bcf75f215168d6d7cad646f1f40dcc1b213c00b2cd248d0f18c01ac48eca0a41`.
- Compressed gzip SHA256: `ba36f2197492cb1cc15842e74da79891c8bcea1e0df88467c959c97258395c1c`.
- An independent second API operation used **only the remote object keys and pinned SHA** to retrieve, decode/decompress and verify event sequencing, scope, hashes and **3 cycles / 7 candidates / 4 rejected / 1 unmeasurable**. All matched.
- Both probes used synthetic fake symbols and no actual broker data. No data in this test establishes genuine market feed completeness or financial performance.

**Test result:** `INDEPENDENT_REAL_R2_REST_RESTORE_VERIFIED`. This establishes a *small real off-host REST diagnostic restore*, not an end-to-end production Evidence Vault.

### What specifically remains unproven

The current Cloudflare API connector's R2 REST object PUT does not expose the conditional `If-None-Match: *` semantics of the S3 adapter. We did NOT test write-once preconditions, binary-native `.jsonl.gz` S3 transport, production WAL off-host ACK, multi-session private backups, independently attested upstream scans/bars/quotes, five consecutive real paper sessions, or real member authentication.

A temporary **isolated staging Worker** with an R2 binding was uploaded to explore a native conditional-write test, but the Cloudflare connection blocks outbound `workers.dev` URL fetches. Its execution was never reached. The temporary Worker was disabled/deleted and the target native-test object prefix remained empty. No existing Worker was altered. This is a tool execution limitation, **not evidence of R2 conditional failure**.

## Repeatable S3 acceptance runner

`scripts/v5_f2c_r2_stage_probe.py` is now the scoped real-S3 acceptance program. It reuses the existing F1/F2a/F2b code paths and creates at most three fully synthetic paper decision cycles (explicitly **PARTIAL** market source status), a two-batch local archive, and remote conditional-write/receipt/independent-restore verification.

Offline/no credentials:

```sh
python -m scripts.v5_f2c_r2_stage_probe
python -m pytest -q tests/test_v5_f2c_stage_probe.py
```

Real private S3/R2 (DO NOT run until stage-specific keys and budget are authorized):

```sh
python -m pip install boto3
ANEVUM_F2C_STAGING_APPROVED=YES_SYNTHETIC_R2_ONLY python -m scripts.v5_f2c_r2_stage_probe --execute
```

The `--execute` invocation also requires `ANEVUM_F2C_R2_ACCOUNT_ID`, `ANEVUM_F2C_R2_ACCESS_KEY_ID`, and `ANEVUM_F2C_R2_SECRET_ACCESS_KEY` to have been securely injected into **that isolated process** (not passed through the command line or printed; never commit them). The runner accepts **only the exact staging bucket** and derives the official R2 S3 endpoint from the account identifier. It uses only the single synthetic workspace, a random stage-scoped run ID, explicit S3v4 signing, and a hard 128 KB aggregate upload limit with finite request limits.

On success it proves that actual R2 S3 conditional PUT rejects a conflicting overwrite, GET verifies the sealed source and receipt, and independent R2 object-only restore returns the entire synthetic population. If conditional PUT is unsupported or any integrity/tenant check fails, it **fails closed** and never upgrades the WAL archive state or research readiness. No production Cloudflare Worker/Railway/RHEN instance is involved.

**Separation:** These are development-only source and offline CI tests. There is no R2 credential included here, and no automatic cloud-enabled GitHub Action on pull requests. When real staging keys are available, operator must review the run, store only a redacted signed report, and independently validate the Cloudflare bucket and object count.

## Gates remaining after real S3 success

1. Independently verify source scan cycle counts and actual raw bar/quote/universe objects, with data gaps explicitly marked rather than guessed.
2. Authenticated staging paper runtime capture -> WAL -> private R2 restore from one genuine (non-synthetic) paper session, then five consecutive complete sessions.
3. Distinct `ARCHIVED_VERIFIED` production off-host ACK state transition, legitimate retention/lock rules, native RHEN Operations backpressure + billing guard, rollback/recovery.
4. Security review and **separate explicit deployment authorization**. Founder RHEN live execution and public Commons/member broker permissions remain unchanged.
