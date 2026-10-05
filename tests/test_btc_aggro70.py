from datetime import datetime, timezone
from decimal import Decimal

from foundation.report_read import _btc_aggressive_70_projection


def _fill(side: str, price: str, qty: str = "1"):
    return {
        "occurred_at": datetime(2026, 10, 4, tzinfo=timezone.utc).isoformat(),
        "payload": {
            "activity": {
                "side": side,
                "price": price,
                "qty": qty,
            }
        },
    }


def test_aggressive_70_open_position_scales_canary_return_without_broker_orders():
    result = _btc_aggressive_70_projection(
        fill_events=[_fill("buy", "100")],
        position_open=True,
        current_return=Decimal("0.02"),
        max_favorable_return=Decimal("0.03"),
        max_adverse_return=Decimal("-0.01"),
    )

    expected_net = (
        (Decimal("1") - Decimal("0.0025"))
        * (Decimal("1") + Decimal("0.02"))
        * (Decimal("1") - Decimal("0.0025"))
        - Decimal("1")
    )
    expected_equity = (
        Decimal("7")
        + Decimal("63") * (Decimal("1") + expected_net)
    )

    assert result["campaign_id"] == "BTC-AGGRO70-001"
    assert result["mode"] == "SHADOW_PAPER_CAPITAL_POLICY"
    assert result["broker_orders_created"] is False
    assert result["live_execution_authorized"] is False
    assert result["initial_equity"] == "70"
    assert result["target_allocation_pct"] == "0.90"
    assert result["target_notional"] == "63.0000"
    assert result["completed_trades"] == 0
    assert result["current_equity"] == str(
        expected_equity.quantize(Decimal("0.0001"))
    )
    assert result["status"] == "OPEN_POSITION"
    assert result["sample_sufficient"] is False


def test_aggressive_70_compounds_completed_round_trip_after_fees():
    result = _btc_aggressive_70_projection(
        fill_events=[
            _fill("buy", "100"),
            _fill("sell", "102"),
        ],
        position_open=False,
        current_return=None,
        max_favorable_return=Decimal("0.02"),
        max_adverse_return=Decimal("-0.01"),
    )

    net = (
        (Decimal("1") - Decimal("0.0025"))
        * Decimal("1.02")
        * (Decimal("1") - Decimal("0.0025"))
        - Decimal("1")
    )
    expected = Decimal("7") + Decimal("63") * (Decimal("1") + net)

    assert result["completed_trades"] == 1
    assert result["realized_equity"] == str(expected.quantize(Decimal("0.0001")))
    assert result["current_equity"] == result["realized_equity"]
    assert result["status"] == "READY"


def test_aggressive_70_has_bounded_loss_policy():
    result = _btc_aggressive_70_projection(
        fill_events=[_fill("buy", "100")],
        position_open=True,
        current_return=Decimal("-0.05"),
        max_favorable_return=Decimal("0"),
        max_adverse_return=Decimal("-0.05"),
    )

    assert result["stop_pct"] == "0.05"
    assert result["max_campaign_drawdown_pct"] == "0.20"
    assert result["kill_switch_equity"] == "56.00"
    assert Decimal(result["current_equity"]) > Decimal("56")


def test_aggressive_70_is_projected_into_command_and_forward_report():
    import inspect

    from foundation import command_iren, report_read

    command_source = inspect.getsource(command_iren._btc_canary_activity)
    report_source = inspect.getsource(report_read._btc_canary_run_evidence)

    assert "_btc_aggressive_70_projection(" in command_source
    assert '"aggressive_70": aggressive_70' in command_source
    assert '"aggressive_70": _btc_aggressive_70_projection(' in report_source
