# ANEVUM V5 F3e — fair bounded as-of quote sampling

Program: ANEVUM.V5.FOUNDATION.2026-10-09.001
Branch: build/anevum-v5-foundation-f3e-fair-asof-quotes-20261010
Parent: F3d draft RHEN #484 (which includes F3c #483, F3b #482, F3a #481, F2c #479, F2b #477, F2a #476, F1 #471)
Status: ISOLATED DRAFT. Synthetic/mocked tests only, never merged to production.

## Confirmed problem and actual read-only observation

A connected Alpaca market-data tool returned 8 IEX one-minute bars for AAPL/MSFT
in a narrow October 9, 2026 historical window (four each). A single combined
quote query with limit 16 returned 16 AAPL and 0 MSFT even though separate
queries returned MSFT quotes. This is a shared quote limit / pagination
starvation issue, NOT evidence that MSFT was missing market data.

A further read-only provider tool check using **one symbol, sort=desc, limit=1**
returned latest records just before 2026-10-09 14:33:00 UTC:
AAPL 14:32:59.971237 and MSFT 14:32:59.955230.
These provider tool checks are external observations, NOT authenticated requests
through RHEN's new staging code and NOT input to the F3e CI mock suite.

## F3e implementation

- Bar fetch: one multi-symbol ascending 1Min page stream; exhaust all pages
  within MAX_PAGES_PER_ENDPOINT=4. The provider query ends one microsecond
  before the scheduled scan time to exclude the not-yet-completed bar starting
  at exactly that boundary. A returned future/incomplete bar FAILS rather than
  being silently trimmed.
- Quote fetch: one separate GET per symbol, **descending limit 1** over the
  bounded two-minute lookback, as-of the scheduled scan boundary. At most 10
  symbols and 10 quote GETs per cycle. No symbol may consume another symbol's
  quote allocation.
- Retain complete raw responses and request params for all ACTUALLY FETCHED
  bar and quote pages; any older quote continuation marker is retained as
  evidence. Do NOT follow it because the intended policy is one latest
  snapshot per symbol, not quote-history ingestion.
- Explicit source lineage: quote_sampling_mode =
  PER_SYMBOL_DESC_LATEST_ASOF_V1, quote_history_exhausted = false,
  response_pagination_exhausted = false, bar_pagination_exhausted = true,
  and the exact ordered quote_symbols_requested list. A missing symbol quote
  is reason-coded UNMEASURABLE by F3b rather than filled or fabricated.
- The F3c remote page/archive receipt verifier refuses omitted, reordered,
  multi-symbol, over-limit, or falsely exhaustive quote pages; replay
  repeats the exact per-symbol GET sequence without the local WAL.
- Raised per-cycle remote archive object cap from 12 to 16: 4 bar pages +
  up to 10 quote pages + manifest + receipt. Actual byte cap remains
  1.3 MB, and combined bar + quote records remain capped at 1,200.
- New negative and maximum-universe tests cover all 10 requested equities,
  correct 14 HTTP GET budget, all 16 mock S3 objects, missing source pages,
  altered completeness flags, secretless read-only requests and replay.

## Security and trust state

All code remains a stacked draft. No real Alpaca request is made from the
F3e module in CI. Alpaca Connect OAuth application secrets MUST NOT be used
as market-data keys. No live brokerage orders, user-account changes, Railway
or website release, production R2 uploads, paid data subscriptions or new
secrets are authorized by this PR.

IEX is a limited exchange feed. A single latest as-of quote is a **sample**,
not every tick, complete NBBO/SIP coverage, or an independently provider-signed
market tape. A matching receipt remains AWAITING_EVIDENCE, while missing or
contradictory evidence BLOCKS. Research-ready, ARCHIVED_VERIFIED, alpha
and live-trading authorization remain false.

The genuine isolated F2c S3 staging archive/restore protocol passed earlier
at https://github.com/anevum/rhen/actions/runs/38019261298 using synthetic
events; F3e's actual market-source format has NOT been tested on real R2.

## Remaining independent acceptance

1. Obtain separately approved, least-privileged *paper-data* API credentials
   and confirm feed entitlements + allowed quote/bar timestamps.
2. Run a bounded, manually approved F3c/F3e test against real data using
   distinct staging credentials and source-specific R2 storage, preserving
   all as-of pages, data-scope labels and market-source hashes.
3. Independently anchor exact scheduler counts and source receipts with
   externally controlled credentials, truly distinct immutable custody and
   independently retained trust pins (beyond the two mocked stores).
4. Prove full genuine scheduled paper session provenance, remote-only replay,
   reason-coded 5m/15m/60m outcomes and five subsequent sessions. Use
   separate security/compliance and release gates before any product launch.

No F1–F3e implementation code has been merged into the RHEN production main.
