from decimal import Decimal

from app.strategy_lab_runner import aggregate_periods, parse_period


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
