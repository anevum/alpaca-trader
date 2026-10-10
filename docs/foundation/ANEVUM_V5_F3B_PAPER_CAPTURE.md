# ANEVUM V5 Foundation — F3b offline paper scanner capture

Program: ANEVUM.V5.FOUNDATION.2026-10-09.001  
Branch: build/anevum-v5-foundation-f3b-paper-capture-20261009  
Parent: F3a scanner source draft #481 → F2c S3 staging draft #479 → F2b #477 → F2a #476 → F1 #471  
Scope: **draft, paper research only, no live market feed, no deployment**

## What this change adds

F3a established a separate, hash-chained scanner-origin manifest and candidate-level
comparison with the F1 journal. F3b provides a concrete, bounded **offline paper
scanner** that exercises the intended causal order and recovers original market
objects locally:

1. An independent caller PREDECLARES scan slots in a separate, append-only SQLite
   WAL (F3b PaperScheduleLedger); each slot includes time, sequence, scope, cycle
   ID and explicit paper mode. Gap, duplicate, conflict, tampering or changed
   plan hashes are rejected. This is a local planning ledger, not an externally
   authenticated schedule.
2. A supplied, time-bounded **offline/unverified** market snapshot names the
   universe, source label, one-minute completed bars and as-of quotes. Input must
   be explicit SYNTHETIC_OFFLINE or UNATTESTED_IMPORTED. Both lack provider
   authentication. Records with future timestamps, invalid OHLC, duplicates,
   out-of-universe symbols, invalid quote spreads or scope mismatches fail.
3. Local content-addressed, write-once, read-back-verified objects preserve
   individual bars and quotes plus immutable per-cycle manifests. Shared 1m
   records deduplicate across scans within the same private workspace/run.
4. A deterministic, **NON-TRADING** body/spread research baseline evaluates
   exactly one row for every universe symbol, including REJECTED and
   reason-coded UNMEASURABLE candidates (no bar, quote or fresh data).
   Qualification does **not** mean an order may be placed.
5. The F3a scanner manifest is committed FIRST. The F1 decision journal is
   committed SECOND, after market blobs. A crash after scanner commit but before
   F1 append leaves an explicitly incomplete session; an identical rerun resumes
   idempotently. No cross-database distributed transaction is claimed.
6. A paper-session audit reconciles independent planned slots, source scanner
   events, F1 journal rows, restored local market objects and regenerated
   candidate decisions. Missing, extra, modified or stale evidence BLOCKS.

### Evidence states

- Any gap/corruption/mismatch: BLOCKED.
- Internally matching offline input: **AWAITING_EVIDENCE** — never PASS,
  ARCHIVED_VERIFIED, or a green research-ready badge.
- The F1 market data_status is always PARTIAL, even when a fixture includes
  a bar and quote for every symbol. The raw objects are *locally* verified,
  not independently authenticated vendor evidence and not an R2 restore.
- No re-created or estimated missing scanner events.
- Raw values never enter the Commons public data plane.

### Isolation and fixed cost ceilings

- Max 100 symbols per scan, max 6,000 completed-bar inputs / 100 quotes;
  max 1 MB canonical source/document, max 3,000 planned slots per session.
- Only Python standard library + private existing F1/F2/F3a modules;
  no Alpaca SDK, network calls, production Railway runtime, authentication,
  Cloudflare Worker or trading imports.
- The scheduler, scanner and journal must have THREE DIFFERENT SQLite files;
  local market records are restricted to workspace/run-specific key prefixes.
- The scheduler has no broker credentials, and a verified research candidate
  has no execution authority. The owner's retired legacy RHEN and Alpaca
  Connect application are unaffected.
- Content-addressed blobs are local development evidence, **not an off-host
  backup**. No production encryption, retention, supplier signatures or external
  pinned scheduler digest is supplied in this slice.

## How to verify

Run (offline, no credentials):

    python -m pytest -q tests/test_v5_paper_capture.py
    python -m scripts.v5_f3b_paper_capture_probe

The GitHub PR CI includes F1/F2/F3a/F3b tests, a full RHEN regression and
separate PostgreSQL / Foundation audits.

The known real private Cloudflare R2 S3 provider gate passed **separately**:
[manual F2c staging run #38019261298](https://github.com/anevum/rhen/actions/runs/38019261298)
(3 SYNTHETIC cycles / 6 candidates / 3 rejected, 2 remote receipts).
That gate proves S3 create-only/overwrite refusal/remote restore protocol; it
does NOT mean this F3b offline scanner used live R2 or traded.

## Still needed before a real paper-session claim

1. An independent scheduled scan producer with a separately authenticated,
   immutable **schedule count and checksum anchor**, written before scanner
   capture and recoverable after losing all local SQLite state.
2. An isolated, authenticated **paper-only market data adapter** with
   entitlement/feed/session metadata; deduped raw universe, full source bars
   and quotes, missing-minute/halt/stale reasons, as-of leakage tests, and
   independently restorable R2 objects.
3. Durable production-compatible crash/outage and archive-ACK policy that
   never calls a trading/order/risk endpoint; bounded backpressure and cost.
4. One actual complete market session proving scheduled scan → all source
   candidates → validated independent bar/quote sources → R2 remote-only
   restore → paper replay. Then **five consecutive sessions** of verified
   pipeline reliability (NOT a claim of profitable alpha).
5. Security/compliance review and separate approval before releasing any
   public RHEN member feature or brokerage connection. All stacked
   Foundation PRs remain unmerged and paper-only.

Historical broker/trade data is not silently reconstructed; the user's
prior authorized legacy retirement does not attest a new trading account.
