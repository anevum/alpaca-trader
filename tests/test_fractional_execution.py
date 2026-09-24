from decimal import Decimal

from app.execution import ExecutionEngine


def test_managed_price_exit_triggers_stop():
    position = {
        "avg_entry_price": "100",
        "current_price": "99.64",
    }
    action = ExecutionEngine._managed_price_exit(
        position,
        Decimal("0.0035"),
        Decimal("0.005"),
    )
    assert action is not None
    tag, reason = action
    assert tag == "stop"
    assert "stop loss" in reason


def test_managed_price_exit_triggers_target():
    position = {
        "avg_entry_price": "100",
        "current_price": "100.50",
    }
    action = ExecutionEngine._managed_price_exit(
        position,
        Decimal("0.0035"),
        Decimal("0.005"),
    )
    assert action is not None
    tag, reason = action
    assert tag == "target"
    assert "take profit" in reason


def test_managed_price_exit_holds_inside_band():
    position = {
        "avg_entry_price": "100",
        "current_price": "100.20",
    }
    action = ExecutionEngine._managed_price_exit(
        position,
        Decimal("0.0035"),
        Decimal("0.005"),
    )
    assert action is None


def test_managed_price_exit_ignores_missing_prices():
    position = {
        "avg_entry_price": "0",
        "current_price": "100",
    }
    action = ExecutionEngine._managed_price_exit(
        position,
        Decimal("0.0035"),
        Decimal("0.005"),
    )
    assert action is None
