from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.research import (
    enrich_signal,
    record_scan_samples,
    research_summary,
    resolve_forward_outcomes,
)
from app.strategy import Signal

NY = ZoneInfo("America/New_York")


def bar(minute: int, close: str) -> dict:
    price = Decimal(close)
    return {
        "t": f"2026-09-24T10:{minute:02d}:00-04:00",
        "o": str(price),
        "h": str(price + Decimal("0.10")),
        "l": str(price - Decimal("0.10")),
        "c": str(price),
        "v": "1000",
        "vw": str(price),
    }


def test_research_enrichment_is_observational():
    now = datetime(2026, 9, 24, 10, 20, tzinfo=NY)
    bars = [bar(i, str(100 + i * 0.05)) for i in range(0, 20)]
    confirmation_bars = {
        "QQQ": [bar(i, str(200 + i * 0.04)) for i in range(0, 20)],
        "SMH": [bar(i, str(300 + i * 0.03)) for i in range(0, 20)],
    }
    signal = Signal(
        action="buy",
        symbol="AAPL",
        notional=Decimal("20"),
        reference_price=Decimal("100.95"),
        stop_price=Decimal("100.60"),
        take_profit_price=Decimal("101.45"),
        reason="qualified",
        metadata={
            "bar_time": "2026-09-24T10:19:00-04:00",
            "momentum_pct": "0.0015",
            "vwap_edge_pct": "0.0010",
            "confirmation_passes": 2,
            "min_confirmations": 1,
        },
    )

    enriched = enrich_signal(signal, bars, confirmation_bars, now)

    assert enriched.action == "buy"
    assert enriched.stop_price == Decimal("100.60")
    assert enriched.take_profit_price == Decimal("101.45")
    assert enriched.metadata["research"]["behavior_changed"] is False
    assert 0 <= enriched.metadata["research"]["score"] <= 100


def test_samples_dedupe_and_forward_outcomes_resolve():
    state = SimpleNamespace(research_samples=[], research_sample_keys=set())
    observed = datetime(2026, 9, 24, 10, 10, tzinfo=NY)
    scan = {
        "AAPL": {
            "action": "buy",
            "reason": "qualified",
            "reference_price": "100",
            "metadata": {
                "bar_time": "2026-09-24T10:09:00-04:00",
                "research": {
                    "score": 75.0,
                    "market_regime": {"label": "risk_on"},
                    "atr_pct": "0.003",
                    "realized_volatility_pct": "0.001",
                },
            },
        }
    }

    record_scan_samples(state, scan, observed)
    record_scan_samples(state, scan, observed)

    assert len(state.research_samples) == 1

    market_bars = {
        "AAPL": [
            bar(9, "100"),
            bar(10, "100.10"),
            bar(11, "100.20"),
            bar(15, "101.00"),
        ]
    }
    resolved = resolve_forward_outcomes(
        state,
        market_bars,
        datetime(2026, 9, 24, 10, 17, tzinfo=NY),
    )

    assert resolved >= 2
    sample = state.research_samples[0]
    assert "1m" in sample["outcomes"]
    assert "5m" in sample["outcomes"]
    assert Decimal(sample["outcomes"]["1m"]["return_pct"]) > 0


def test_research_summary_reports_observed_results():
    state = SimpleNamespace(
        research_samples=[
            {
                "action": "buy",
                "reason": "qualified",
                "score": 80.0,
                "market_regime": "risk_on",
                "outcomes": {"5m": {"return_pct": "0.01"}},
            },
            {
                "action": "hold",
                "reason": "momentum low",
                "score": 30.0,
                "market_regime": "mixed",
                "outcomes": {"5m": {"return_pct": "-0.005"}},
            },
        ]
    )

    summary = research_summary(state)

    assert summary["sample_count"] == 2
    assert summary["horizons"]["5m"]["count"] == 2
    assert summary["horizons"]["5m"]["positive_rate"] == 0.5
