"""F3e: prove fair latest-as-of quote sampling for 10 equities, never full history.

No real Alpaca requests, account keys, R2 credentials or broker order actions.
"""
from __future__ import annotations

from copy import deepcopy
import pytest

from next_rhen.paper_market_feed import (
    BARS_PATH, QUOTES_PATH, MAX_TOTAL_PAGES, build_paper_snapshot, MarketFeedError,
)
from next_rhen.paper_source_archive import (
    seal_provider_snapshot, restore_provider_snapshot, PaperRemoteIntegrityError,
)
from next_rhen.remote_vault import R2S3ImmutableStore
from scripts.v5_f2c_r2_stage_probe import StrictSyntheticS3, BUCKET
from test_v5_paper_capture import W, R, D, slot, ts

TEN = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOG",
       "META", "TSLA", "AMD", "INTC", "QQQ"]


class TenSymbolProvider:
    def __init__(self):
        self.calls = []
        self.pages = {}
        for i in range(4):
            portion = TEN[3*i:3*(i+1)]
            rows = {}
            for symbol in portion:
                rows[symbol] = [{
                    "t": ts(-1), "o": 100.0, "h": 101.0,
                    "l": 99.0, "c": 100.2, "v": 100,
                }]
            self.pages[(BARS_PATH, None if i == 0 else f"bar-{i}")] = {
                "bars": rows,
                "next_page_token": f"bar-{i+1}" if i < 3 else None,
            }
        for symbol in TEN:
            self.pages[(QUOTES_PATH, symbol)] = {
                "quotes": {symbol: [{
                    "t": ts(0,-1), "bp": 100.0, "ap": 100.1,
                    "bs": 10, "as": 10,
                }]},
                "next_page_token": "older-observations-available",
            }

    def get(self, *, path, params):
        self.calls.append((path, dict(params)))
        key = (path, params.get("page_token") if path == BARS_PATH
               else params.get("symbols"))
        if key not in self.pages:
            raise MarketFeedError("missing bounded market response page")
        return deepcopy(self.pages[key])


def test_ten_symbol_ceiling_is_fair_and_restores_all_fetched_pages():
    provider = TenSymbolProvider()
    capture = build_paper_snapshot(
        provider, slot=slot(), symbols=TEN, feed="iex",
    )
    assert len(provider.calls) == MAX_TOTAL_PAGES == 14
    quotes = [(path,query) for path,query in provider.calls if path == QUOTES_PATH]
    assert [query["symbols"] for _,query in quotes] == TEN
    assert all(query["sort"] == "desc" and query["limit"] == "1"
               and "page_token" not in query for _,query in quotes)
    assert len(capture["snapshot"]["bars"]) == len(TEN)
    assert len(capture["snapshot"]["quotes"]) == len(TEN)
    assert capture["provenance"]["quote_symbols_requested"] == TEN
    assert capture["provenance"]["quote_history_exhausted"] is False
    assert capture["provenance"]["bar_pagination_exhausted"] is True
    assert capture["provenance"]["response_pagination_exhausted"] is False
    assert capture["provenance"]["page_count"] == 14
    assert capture["evidence_state"] == "AWAITING_EVIDENCE"
    sdk = StrictSyntheticS3()
    store = R2S3ImmutableStore(
        sdk, BUCKET, workspace_id=W, run_id=R,
    )
    receipt = seal_provider_snapshot(store, slot=slot(), captured=capture)
    assert receipt["remote_object_count"] == 16
    assert receipt["independent_anchor_pinned_by_this_module"] is False
    replayed = restore_provider_snapshot(
        store, receipt_key=receipt["receipt_key"],
        pinned_receipt_sha256=receipt["receipt_sha256"],
        workspace_id=W, run_id=R, cycle_id=slot()["cycle_id"],
        session_date=D,
    )
    assert replayed["raw_market_page_count"] == 14
    assert replayed["snapshot"] == capture["snapshot"]
    assert replayed["remote_source_replay_verified"] is True
    assert replayed["broker_calls"] == 0
    assert replayed["provider_independently_attested"] is False
    assert replayed["evidence_state"] == "AWAITING_EVIDENCE"


def test_missed_symbol_quote_page_fails_before_any_scanner_commit():
    provider = TenSymbolProvider()
    del provider.pages[(QUOTES_PATH, "QQQ")]
    with pytest.raises(MarketFeedError, match="missing bounded market response page"):
        build_paper_snapshot(provider, slot=slot(), symbols=TEN, feed="iex")


def test_omitted_quote_page_in_provider_archive_fails_closed():
    provider = TenSymbolProvider()
    captured = build_paper_snapshot(provider, slot=slot(), symbols=TEN, feed="iex")
    captured["provenance"]["raw_pages"].pop()
    captured["provenance"]["page_count"] -= 1
    sdk = StrictSyntheticS3()
    store = R2S3ImmutableStore(sdk, BUCKET, workspace_id=W, run_id=R)
    with pytest.raises(PaperRemoteIntegrityError, match="missing, excess, or reordered"):
        seal_provider_snapshot(store, slot=slot(), captured=captured)
    assert not sdk.objects


def test_quote_history_must_never_be_promoted_to_exhaustive():
    captured = build_paper_snapshot(TenSymbolProvider(), slot=slot(), symbols=TEN)
    captured["provenance"]["response_pagination_exhausted"] = True
    store = R2S3ImmutableStore(StrictSyntheticS3(), BUCKET, workspace_id=W, run_id=R)
    with pytest.raises(PaperRemoteIntegrityError, match="honest quote sampling"):
        seal_provider_snapshot(store, slot=slot(), captured=captured)
