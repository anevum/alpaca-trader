from decimal import Decimal

from app.math_kernel import (
    arithmetic_return,
    drawdown,
    forward_return,
    log_return,
    mae,
    mfe,
    normalized_spread,
    pearson_correlation,
    rank_descending,
    rolling_momentum,
    rolling_realized_volatility,
    spread_bps,
    volatility_normalized_momentum,
)


def test_shared_math_primitives_are_market_agnostic():
    prices = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("101"), Decimal("103")]
    equity = {
        "ret": arithmetic_return(prices[0], prices[-1]),
        "momentum": rolling_momentum(prices, 2),
        "vol": rolling_realized_volatility(prices, 4),
        "z": volatility_normalized_momentum(prices, 2, 4),
    }
    crypto = {
        "ret": arithmetic_return(prices[0], prices[-1]),
        "momentum": rolling_momentum(prices, 2),
        "vol": rolling_realized_volatility(prices, 4),
        "z": volatility_normalized_momentum(prices, 2, 4),
    }
    assert crypto == equity


def test_shared_math_reference_values():
    assert arithmetic_return("100", "105") == Decimal("0.05")
    assert forward_return("100", "105") == Decimal("0.05")
    assert round(log_return(100, 105), 8) == 0.04879016
    assert spread_bps("99", "101") == Decimal("200")
    assert normalized_spread("10", "5") == Decimal("2")
    assert drawdown([100, 110, 99, 105]) == Decimal("-0.1")
    assert mfe("100", [101, 103, 102]) == Decimal("0.03")
    assert mae("100", [99, 98, 100]) == Decimal("-0.02")
    assert pearson_correlation([1, 2, 3], [2, 4, 6]) == 1.0
    assert rank_descending([0.2, 0.9, 0.9, -1]) == (1, 2, 0, 3)
