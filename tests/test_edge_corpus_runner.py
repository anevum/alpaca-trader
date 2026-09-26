from decimal import Decimal

from app.edge_corpus_runner import _panel_integrity, _shared_panel
from app.edge_corpus import CorpusManifest, CorpusWindow


def manifest():
    return CorpusManifest(
        version="v1",
        created_at="2026-09-25",
        data_feed="iex",
        timeframe="1Min",
        candidate_symbols=("AAPL", "MSFT", "NVDA", "AMD", "AMZN"),
        confirmation_symbols=("SPY",),
        windows=(
            CorpusWindow(
                window_id="dev",
                start=__import__("datetime").date(2026, 1, 5),
                end=__import__("datetime").date(2026, 1, 9),
                role="development",
            ),
            CorpusWindow(
                window_id="val",
                start=__import__("datetime").date(2026, 2, 2),
                end=__import__("datetime").date(2026, 2, 6),
                role="validation",
            ),
            CorpusWindow(
                window_id="holdout",
                start=__import__("datetime").date(2026, 3, 2),
                end=__import__("datetime").date(2026, 3, 6),
                role="holdout",
            ),
        ),
        notes=(),
    )


def payload(eligible):
    return {
        "coverage": {
            "eligible_candidate_symbols": eligible,
        }
    }


def test_shared_panel_is_intersection_in_manifest_order():
    result = _shared_panel(
        [
            payload(["AAPL", "MSFT", "NVDA", "AMD"]),
            payload(["AAPL", "NVDA", "AMD", "AMZN"]),
        ],
        manifest(),
    )

    assert result == ("AAPL", "NVDA", "AMD")


def test_panel_integrity_requires_eighty_percent():
    expected = ("AAPL", "MSFT", "NVDA", "AMD", "AMZN")
    good = _panel_integrity(
        ("AAPL", "MSFT", "NVDA", "AMD"),
        expected,
    )
    bad = _panel_integrity(
        ("AAPL", "MSFT", "NVDA"),
        expected,
    )

    assert good["passed"] is True
    assert bad["passed"] is False
    assert Decimal(good["shared_ratio"]) == Decimal("0.8")
