from decimal import Decimal

from app.strategy_lab_runner import (
    aggregate_periods,
    parse_period,
    strategy_004_promotion_gate,
)


def row(name, trades, pnl, expectancy, pf, return_pct, drawdown, wins=0):
    return {
        "variant": name,
        "description": "",
        "parameters": {},
        "summary": {
            "trades": trades,
            "wins": wins,
            "losses": trades - wins,
            "net_pnl": str(pnl),
            "gross_profit": str(max(Decimal(str(pnl)), Decimal("0")) + Decimal("1")),
            "gross_loss": "1",
            "profit_factor": pf,
            "expectancy_per_trade": str(expectancy),
            "return_pct": str(return_pct),
            "max_drawdown": str(drawdown),
            "max_drawdown_pct": str(drawdown),
        },
    }


def test_parse_period_accepts_21_day_window():
    start, end = parse_period("2026-09-01:2026-09-21")
    assert start.isoformat() == "2026-09-01"
    assert end.isoformat() == "2026-09-21"


def test_parse_period_rejects_long_window():
    try:
        parse_period("2026-09-01:2026-09-22")
    except ValueError as exc:
        assert "21 calendar days" in str(exc)
    else:
        raise AssertionError("expected period length guard")


def test_aggregate_prefers_sampled_consistent_variant():
    periods = [
        {
            "leaderboard": [
                row("sampled", 15, "1", "0.06", 1.4, "0.01", "0.01", wins=9),
                row("tiny", 2, "2", "1.00", 10, "0.10", "0.01", wins=2),
            ]
        },
        {
            "leaderboard": [
                row("sampled", 15, "1", "0.06", 1.4, "0.01", "0.01", wins=9),
                row("tiny", 1, "1", "1.00", 10, "0.08", "0.01", wins=1),
            ]
        },
    ]

    ranked = aggregate_periods(periods, min_trades=20)

    assert ranked[0]["variant"] == "sampled"
    assert ranked[0]["eligible_for_ranking"] is True
    tiny = next(item for item in ranked if item["variant"] == "tiny")
    assert tiny["eligible_for_ranking"] is False
    assert "3/20" in tiny["sample_note"]


def test_strategy_004_gate_requires_history_and_forward_shadow():
    rows = [
        {
            "variant": "strategy_004_candidate_c_controlled",
            "description": "",
            "parameters": {},
            "eligible_for_ranking": True,
            "sample_note": "",
            "summary": {
                "trades": 45,
                "wins": 28,
                "losses": 17,
                "net_pnl": "4.50",
                "gross_profit": "8.00",
                "gross_loss": "5.00",
                "profit_factor": 1.6,
                "expectancy_per_trade": "0.1000",
                "average_period_return": "0.015",
                "positive_periods": 3,
                "periods": 3,
                "max_drawdown": "2.00",
                "max_drawdown_pct": "0.02",
            },
        }
    ]

    historical_only = strategy_004_promotion_gate(rows)
    assert historical_only["historical_gate_passed"] is True
    assert historical_only["scalable_capital_merge_allowed"] is False

    forward_validated = strategy_004_promotion_gate(
        rows,
        forward_shadow_validated=True,
    )
    assert forward_validated["scalable_capital_merge_allowed"] is True


def test_strategy_004_gate_rejects_negative_expectancy():
    rows = [
        {
            "variant": "strategy_004_candidate_c_controlled",
            "description": "",
            "parameters": {},
            "eligible_for_ranking": True,
            "sample_note": "",
            "summary": {
                "trades": 50,
                "wins": 20,
                "losses": 30,
                "net_pnl": "-1.00",
                "gross_profit": "4.00",
                "gross_loss": "5.00",
                "profit_factor": 0.8,
                "expectancy_per_trade": "-0.0200",
                "average_period_return": "-0.003",
                "positive_periods": 1,
                "periods": 3,
                "max_drawdown": "4.00",
                "max_drawdown_pct": "0.04",
            },
        }
    ]

    gate = strategy_004_promotion_gate(
        rows,
        forward_shadow_validated=True,
    )
    assert gate["historical_gate_passed"] is False
    assert gate["criteria"]["positive_expectancy"]["passed"] is False
    assert gate["scalable_capital_merge_allowed"] is False
