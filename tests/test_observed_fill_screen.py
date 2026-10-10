"""No winning strategy may be inferred from filtered historical fill outcomes."""
from __future__ import annotations

from decimal import Decimal
from copy import deepcopy

import pytest

from app.research_agent.observed_fill_screen import screen_observed_trades


def fixture():
    return {
        "schema_version": "anevum-rhen-email-fill-observed-trade-screen-v1",
        "source": {
            "broker_fills": "108 Alpaca confirmation emails",
            "market_features": "historical completed 1-minute IEX bars",
            "strategy_version_id": "LIVE-2026-09-25-003",
        },
        "trades": [
            {
                "session": "2026-10-07", "symbol": "ONE",
                "entry_et": "10:00", "exit_et": "10:10",
                "quantity": "1", "entry_price": "100", "exit_price": "99.9",
                "gross_realized_pnl": "-0.1",
                "relative_volume_21bars": "2",
                "trend_persistence_last_9_closes": "0.75",
                "completed_bars_available": 30,
            },
            {
                "session": "2026-10-08", "symbol": "TWO",
                "entry_et": "10:00", "exit_et": "10:10",
                "quantity": "1", "entry_price": "100", "exit_price": "100.1",
                "gross_realized_pnl": "0.1",
                "relative_volume_21bars": "0.9",
                "trend_persistence_last_9_closes": "0.75",
                "completed_bars_available": 30,
            },
            {
                "session": "2026-10-09", "symbol": "THREE",
                "entry_et": "10:00", "exit_et": "10:10",
                "quantity": "1", "entry_price": "100", "exit_price": "100.2",
                "gross_realized_pnl": "0.2",
                "relative_volume_21bars": "2",
                "trend_persistence_last_9_closes": "0.5",
                "completed_bars_available": 30,
            },
        ],
    }


def test_frozen_rule_slate_is_research_only_and_confirms_foregone_winners():
    evidence = fixture()
    out = screen_observed_trades(evidence)
    assert out["control"] == "production"
    assert out["original_filled_trade_count"] == 3
    assert out["compared_challengers"] == 3
    assert out["independent_session_count"] == 3
    assert out["full_candidate_universe_included"] is False
    assert out["same_strategy_execution_path_replayed"] is False
    assert out["automatic_promotion_authorized"] is False
    assert out["new_live_entries_authorized"] is False
    assert out["decision"] == "EXPLORATORY_OBSERVED_ENTRIES_NO_WINNER_VALIDATED"
    results = {row["name"]: row for row in out["screen_results"]}
    assert len(results) == 4
    assert results["production"]["original_exits_retained"]["winners"] == 2
    assert results["relative-volume-150"]["original_exits_retained"]["count"] == 2
    assert results["relative-volume-150"]["foregone_historical_winners"] == 1
    assert results["trend-persistence-067"]["original_exits_retained"]["count"] == 2
    assert results["volume-trend-confirmed"]["original_exits_retained"]["count"] == 1
    assert results["volume-trend-confirmed"]["foregone_historical_winners"] == 2
    assert Decimal(results["volume-trend-confirmed"]["delta_to_unfiltered_original_pnl"]) == Decimal("-0.3")
    assert "ONE" not in str(out)  # Never re-export symbol/fill details.
    assert out["source_fingerprint"] == screen_observed_trades(evidence)["source_fingerprint"]


@pytest.mark.parametrize(
    "attribute,value,expected",
    [
        ("gross_realized_pnl", "1.1", "does not reconcile"),
        ("relative_volume_21bars", "NaN", "finite Decimal"),
        ("trend_persistence_last_9_closes", "1.2", "inconsistent"),
        ("completed_bars_available", 7, "inconsistent"),
        ("entry_et", "10:00:40", "strict HH:MM"),
        ("quantity", "0", "positive quantity"),
    ],
)
def test_partial_or_forged_input_must_fail_closed(attribute, value, expected):
    evidence = fixture()
    evidence["trades"][0][attribute] = value
    with pytest.raises(ValueError, match=expected):
        screen_observed_trades(evidence)


def test_duplicate_roundtrip_and_source_provenance_rejected():
    evidence = fixture()
    evidence["trades"].append(deepcopy(evidence["trades"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        screen_observed_trades(evidence)
    evidence = fixture()
    evidence["source"].pop("market_features")
    with pytest.raises(ValueError, match="IEX"):
        screen_observed_trades(evidence)
