from decimal import Decimal

from app.execution import ExecutionEngine


def test_spread_pct_calculates_midpoint_relative_spread():
    spread = ExecutionEngine._spread_pct({"bp": "99.95", "ap": "100.05"})
    assert spread == Decimal("0.001")


def test_spread_pct_rejects_invalid_quote():
    assert ExecutionEngine._spread_pct({"bp": "0", "ap": "100"}) is None
    assert ExecutionEngine._spread_pct({"bp": "101", "ap": "100"}) is None
