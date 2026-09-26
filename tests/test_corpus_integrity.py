from datetime import date

from app.corpus_integrity import (
    build_corpus_integrity_report,
    build_window_integrity,
)
from app.edge_corpus import CorpusManifest, CorpusWindow


def manifest():
    return CorpusManifest(
        version="test-v1",
        created_at="2026-09-26",
        data_feed="iex",
        timeframe="1Min",
        candidate_symbols=("AAPL", "MSFT"),
        confirmation_symbols=("SPY",),
        windows=(),
        notes=(),
    )


def test_window_integrity_reports_symbol_level_coverage_and_sessions():
    window = CorpusWindow(
        window_id="dev-01",
        start=date(2026, 1, 5),
        end=date(2026, 1, 7),
        role="development",
    )
    bars = {
        "AAPL": [
            {"t": "2026-01-05T14:31:00Z"},
            {"t": "2026-01-05T14:32:00Z"},
            {"t": "2026-01-06T14:31:00Z"},
        ],
        "MSFT": [
            {"t": "2026-01-05T14:31:00Z"},
            {"t": "2026-01-06T14:31:00Z"},
            {"t": "2026-01-07T14:31:00Z"},
        ],
        "SPY": [
            {"t": "2026-01-05T14:31:00Z"},
            {"t": "2026-01-05T14:32:00Z"},
            {"t": "2026-01-06T14:31:00Z"},
            {"t": "2026-01-07T14:31:00Z"},
        ],
    }
    pagination = {
        "pagination_complete": False,
        "batches": [
            {
                "batch_index": 0,
                "symbols": ["AAPL", "MSFT"],
                "pages_fetched": 2,
                "pagination_complete": True,
                "next_page_token_remaining": False,
            },
            {
                "batch_index": 1,
                "symbols": ["SPY"],
                "pages_fetched": 50,
                "pagination_complete": False,
                "next_page_token_remaining": True,
            },
        ],
    }

    result = build_window_integrity(
        manifest(),
        window,
        bars,
        expected_sessions=[
            date(2026, 1, 5),
            date(2026, 1, 6),
            date(2026, 1, 7),
        ],
        pagination=pagination,
    )
    symbols = {item["symbol"]: item for item in result["symbols"]}

    assert symbols["AAPL"]["bar_count"] == 3
    assert symbols["AAPL"]["trading_days_represented"] == 2
    assert symbols["AAPL"]["missing_sessions"] == ["2026-01-07"]
    assert symbols["AAPL"]["first_timestamp"] == "2026-01-05T14:31:00Z"
    assert symbols["AAPL"]["last_timestamp"] == "2026-01-06T14:31:00Z"
    assert symbols["AAPL"]["pagination_complete"] is True
    assert symbols["AAPL"]["current_coverage_ratio"] == 0.666667

    assert symbols["MSFT"]["current_coverage_ratio"] == 1.0
    assert symbols["MSFT"]["iex_bar_density_ratio"] == 0.75
    assert symbols["SPY"]["pagination_complete"] is False
    assert symbols["SPY"]["missing_sessions"] == []
    assert result["iex_bar_density_reference_count"] == 4
    assert result["summary"]["pagination_complete"] is False


def test_corpus_report_counts_every_window_symbol_record():
    window = {
        "symbols": [
            {
                "pagination_complete": True,
                "missing_session_count": 0,
                "unparseable_timestamp_count": 0,
            },
            {
                "pagination_complete": True,
                "missing_session_count": 1,
                "unparseable_timestamp_count": 0,
            },
            {
                "pagination_complete": True,
                "missing_session_count": 0,
                "unparseable_timestamp_count": 0,
            },
        ]
    }

    report = build_corpus_integrity_report(manifest(), [window, window])

    assert report["scope"]["role"] == "development"
    assert report["scope"]["window_count"] == 2
    assert report["scope"]["symbol_count"] == 3
    assert report["scope"]["record_count"] == 6
    assert report["summary"]["pagination_complete"] is True
    assert report["summary"]["records_with_missing_sessions"] == 2
    assert report["corpus"]["iex_bar_density_is_diagnostic_only"] is True
