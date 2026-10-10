# ANEVUM V5 Foundation F3c - read-only equities source and remote-source proof

Program: ANEVUM.V5.FOUNDATION.2026-10-09.001
Base: F3b draft RHEN #482, above F3a #481, F2c #479, F2b #477, F2a #476 and F1 #471.
Status: ISOLATED DRAFT. NOT DEPLOYED. NO AUTHENTICATED PROVIDER REQUEST HAS BEEN RUN.

## New bounded contract

1. Read-only, fixed-host HTTPS transport for Alpaca Trading API market-data
   key/secret authentication, separate from Alpaca Connect application
   client ID and client secret. Only two GET endpoints are callable:
   https://data.alpaca.markets/v2/stocks/bars
   https://data.alpaca.markets/v2/stocks/quotes
   No POST, account, order, broker, or OAuth API method is available.
   Importing the module, CI tests and mock runs make zero network calls.
2. Bounded, minute-aligned as-of historical fetch for a separately planned
   F3b paper scan slot. Explicit IEX feed (default) or SIP; completed
   one-minute bars are fully paginated ascending, while quotes are sampled
   independently for each symbol using descending limit=1 latest-as-of.
   The quote-history cursor is preserved, NOT fully exhausted, with hard
   time/byte/row/request ceilings. Future or incomplete bars,
   out-of-range quotes, duplicate observations, repeated tokens,
   out-of-universe symbols and invalid price/size contracts fail closed.
3. Every raw provider page, sanitized query, response hash, endpoint identity
   and paging continuation is preserved alongside a derived F3b scanner input.
   Any missing-symbol values remain missing, never estimated.
4. The F3b scanner can consume the records, preserving ALL evaluated symbols,
   including rejected and reason-coded UNMEASURABLE observations.
   Source origin always remains UNATTESTED_IMPORTED; journal market status
   stays PARTIAL, never COMPLETE based only on authenticated transport.
5. A separate injected immutable object store (mock in CI, real R2 only with
   explicit staging authorization) seals all provider pages, a deterministic
   source manifest and pinned receipt in a scoped private namespace.
   Independent remote-only replay requires the receipt key plus a pinned
   SHA-256 held outside the remote source. It replays ALL original provider
   fetched pages through the same ingest parser after local WAL and market loss.
   Unrequested older quote history is never presented as captured.
   Corruption, omitted pages, scope confusion and invalid anchors fail closed.

## Hard request and storage bounds

- At most 10 equity symbols, 4 fully paginated bar pages plus 1 latest-quote
  page per requested symbol (14 GETs maximum), 1,200 combined records.
- Quote results are explicitly latest-as-of SAMPLES, not a complete
  historical quote stream or proof of all-market venue coverage.
- At most 256 KB per HTTPS response and 1 MB total recorded response evidence.
- At most 16 immutable source archive objects and 1.3 MB attempted bytes per cycle.
- 8-second HTTP timeout, HTTPS host data.alpaca.markets only, GET only.
- Redirects, non-200 status, invalid pagination, malformed content,
  unsupported feeds, unknown symbols, nonfinite or future values fail.
- The market-source format is still a DEVELOPMENT contract, not a licensed
  realtime SIP feed, official venue-complete proof or production service.
- IEX coverage does not imply all-exchange completeness; SIP might require
  additional paid subscription and separate broker/data entitlements.
- Provider records and authenticated HTTPS are not provider-signed proofs.
- A local fake response cannot prove that a real market session happened.
- This mock test DOES NOT run F3c on actual R2. Earlier F2c genuine S3
  transport verification passed separately in:
  https://github.com/anevum/rhen/actions/runs/38019261298
  (three SYNTHETIC cycles and two remote receipts).

## Testing

Run offline without credentials:

    python -m pytest -q tests/test_v5_paper_market_feed.py

RHEN CI exercises the F1-F3c stack, full regression, Postgres and Foundation
audit. Successful local consistency remains AWAITING_EVIDENCE.

## Gated real paper-session prerequisites

1. Isolated, separately approved market-data credentials. Suggested secret
   names: ANEVUM_F3C_PAPER_DATA_KEY_ID, ANEVUM_F3C_PAPER_DATA_SECRET_KEY.
   Do not reuse the installed ALPACA_CONNECT_CLIENT_ID or
   ALPACA_CONNECT_CLIENT_SECRET, founder RHEN trading secrets, member tokens
   or production website Worker bindings. A trading API key may itself grant
   orders even if this read-only adapter cannot call any order endpoint.
2. Confirm data entitlements, feed timeliness and rate limits without
   enabling trade endpoints.
3. Independently originate schedule counts and anchor their checksums in an
   external durable trust store. The local planner WAL is not independent.
4. Verify upstream raw universe, bars and quotes across an entire actual
   prospective market session, including missing minutes, halts and latency.
5. Test actual private R2 immutable F3c provider-source write/read/recovery
   under separately authorized staging, not mock-only.
6. Verify an actual full paper session, then five consecutive sessions
   of source/restore integrity before any research-ready claims.
7. Require separate release, provider permissions, privacy and compliance
   review before enabling members or trades. No auto-promotion or brokerage
   write authorization can result from this change.

Official API references:
https://docs.alpaca.markets/us/docs/about-market-data-api
https://docs.alpaca.markets/us/reference/stockbars
https://docs.alpaca.markets/us/reference/stockquotes-1
