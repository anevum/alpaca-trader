from datetime import datetime, timedelta, timezone

import pytest

from graen.crypto.research_v6 import (
    COST_HURDLE_MULTIPLE,
    EXECUTION_UNIVERSE,
    FEATURE_HORIZONS,
    HOLD_MINUTES,
    METHODOLOGY_VERSION,
    STRATEGY_VERSION_ID,
    build_nostra_snapshot,
    build_series,
    candidate_specs,
    collect_opportunities,
    opportunity_state,
    reclaim_at,
    round_trip_cost_bps,
    shock_threshold,
    simulate_candidate,
    run_crypto_research_v6,
)


SYMBOLS = ("BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD", "AVAX/USD", "LINK/USD")


def _panel():
    start = datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc)
    rows = {symbol: [] for symbol in SYMBOLS}
    prices = {
        "BTC/USD": 60000.0,
        "ETH/USD": 3000.0,
        "SOL/USD": 150.0,
        "XRP/USD": 0.60,
        "AVAX/USD": 25.0,
        "LINK/USD": 14.0,
    }
    opportunity_end = start + timedelta(minutes=480)

    for index in range(140):
        stamp = start + timedelta(minutes=5 * index)
        for symbol in SYMBOLS:
            previous = prices[symbol]
            step = 0.0001
            if symbol == "ETH/USD" and index in {93, 94, 95}:
                step = -0.004
            elif symbol == "ETH/USD" and index == 96:
                step = 0.003
            current = previous * (1.0 + step)
            rows[symbol].append(
                {
                    "t": stamp.isoformat().replace("+00:00", "Z"),
                    "o": previous,
                    "h": max(previous, current) * 1.0002,
                    "l": min(previous, current) * 0.9998,
                    "c": current,
                    "v": 1000 + index,
                    "n": 25 + index % 4,
                    "vw": (previous + current) / 2.0,
                }
            )
            prices[symbol] = current
    return rows, start, opportunity_end


def test_v6_candidate_family_is_narrow_and_frozen():
    specs = candidate_specs()
    assert METHODOLOGY_VERSION == "graen-crypto-native-v6"
    assert STRATEGY_VERSION_ID == "CRYPTO-RESIDUAL-RECLAIM-001"
    assert FEATURE_HORIZONS == (90, 120, 180)
    assert HOLD_MINUTES == 120
    assert EXECUTION_UNIVERSE == ("BTC/USD", "ETH/USD", "SOL/USD")
    assert [row["candidate_id"] for row in specs] == [
        "CRR_IMMEDIATE_CONTROL",
        STRATEGY_VERSION_ID,
    ]
    assert specs[0]["confirmatory"] is False
    assert specs[1]["confirmatory"] is True


def test_shock_threshold_requires_two_stressed_round_trip_costs():
    expected = COST_HURDLE_MULTIPLE * round_trip_cost_bps("ETH/USD", "high") / 10000.0
    assert shock_threshold("ETH/USD", 0.0) == expected
    assert shock_threshold("ETH/USD", 0.01) == 0.015


def test_relative_downshock_creates_research_only_nostra_snapshot():
    raw, start, opportunity_end = _panel()
    series = build_series(
        raw,
        start=opportunity_end - timedelta(hours=1),
        end=opportunity_end + timedelta(hours=3),
    )
    opportunity = opportunity_state(series, "ETH/USD", opportunity_end)
    assert opportunity is not None
    assert opportunity.residual_15 < 0
    assert abs(opportunity.residual_15) > opportunity.threshold
    snapshot = opportunity.snapshot
    assert snapshot["symbol"] == "ETH/USD"
    assert snapshot["as_of_timestamp"] == opportunity_end.isoformat()
    assert snapshot["feature_set_version"].startswith("crypto-residual-reclaim-v6")
    assert snapshot["research_only"] is True
    assert snapshot["execution_authority"] is False
    assert snapshot["source"]["canonical_nostra_ledger_pending"] is True
    assert "outcome" not in snapshot
    assert "realized_return" not in snapshot

    state = {
        "residual_15": -0.01,
        "return_5": -0.002,
        "return_15": -0.01,
        "return_60": -0.005,
        "market_factor_15": 0.001,
        "activity_ok": True,
        "trade_count_60": 100,
        "nonzero_trade_bars_60": 12,
    }
    direct = build_nostra_snapshot(
        symbol="ETH/USD",
        as_of_timestamp=opportunity_end,
        state=state,
        residual_sigma=0.002,
        threshold=0.004,
    )
    assert direct["execution_authority"] is False


def test_reclaim_waits_for_positive_bar_and_improving_residual():
    raw, start, opportunity_end = _panel()
    series = build_series(
        raw,
        start=opportunity_end - timedelta(hours=1),
        end=opportunity_end + timedelta(hours=3),
    )
    opportunity = opportunity_state(series, "ETH/USD", opportunity_end)
    assert opportunity is not None
    entry = reclaim_at(series, opportunity)
    assert entry == opportunity_end + timedelta(minutes=5)


def test_opportunity_snapshot_is_unchanged_by_future_price_mutation():
    raw, start, opportunity_end = _panel()
    series = build_series(
        raw,
        start=opportunity_end - timedelta(hours=1),
        end=opportunity_end + timedelta(hours=3),
    )
    before = opportunity_state(series, "ETH/USD", opportunity_end)
    assert before is not None

    future_end = opportunity_end + timedelta(minutes=5)
    series["ETH/USD"][future_end] = {
        **series["ETH/USD"][future_end],
        "c": float(series["ETH/USD"][future_end]["c"]) * 10.0,
    }
    after = opportunity_state(series, "ETH/USD", opportunity_end)
    assert after is not None
    assert before.residual_15 == after.residual_15
    assert before.threshold == after.threshold
    assert before.snapshot == after.snapshot


def test_reclaim_candidate_enters_later_than_immediate_control_and_never_executes():
    raw, start, opportunity_end = _panel()
    end = opportunity_end + timedelta(hours=3)
    series = build_series(
        raw,
        start=opportunity_end - timedelta(hours=1),
        end=end,
    )
    opportunities = collect_opportunities(
        series,
        start=opportunity_end,
        end=opportunity_end + timedelta(minutes=5),
    )
    assert opportunities
    immediate = simulate_candidate(
        series,
        opportunities,
        timing="immediate",
        start=opportunity_end,
        end=end,
        scenario="high",
    )
    reclaim = simulate_candidate(
        series,
        opportunities,
        timing="reclaim",
        start=opportunity_end,
        end=end,
        scenario="high",
    )
    assert immediate
    assert reclaim
    assert immediate[0].entry_at == opportunity_end
    assert reclaim[0].entry_at == opportunity_end + timedelta(minutes=5)
    assert reclaim[0].candidate_id == STRATEGY_VERSION_ID
    assert reclaim[0].nostra_snapshot_id == opportunities[0].snapshot["snapshot_id"]


def test_v6_fails_closed_without_verified_new_corpus():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="provenance"):
        run_crypto_research_v6(
            bars_by_symbol={},
            development_start=start,
            validation_start=start + timedelta(days=10),
            holdout_start=start + timedelta(days=20),
            holdout_end=start + timedelta(days=30),
        )


def test_v6_rejects_overlap_with_prior_research_range():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="overlaps"):
        run_crypto_research_v6(
            bars_by_symbol={},
            development_start=start,
            validation_start=start + timedelta(days=10),
            holdout_start=start + timedelta(days=20),
            holdout_end=start + timedelta(days=30),
            corpus_provenance_verified=True,
            previously_inspected_ranges=[
                {
                    "id": "prior",
                    "start": (start - timedelta(days=1)).isoformat(),
                    "end": (start + timedelta(days=1)).isoformat(),
                }
            ],
        )
