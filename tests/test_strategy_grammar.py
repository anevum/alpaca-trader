import pytest

from app.research_agent.strategy_grammar import (
    StrategyGrammarError,
    build_manifest,
    manifest_hash,
    validate_manifest,
)


def manifest(**changes):
    values = {
        "hypothesis_id": "H-001",
        "family": "cross_asset_diffusion",
        "mechanism": "information diffuses from BTC to lagging assets",
        "information_source": "cross_asset_returns",
        "feature": "lead_lag_gap",
        "transformation": "residualize_btc",
        "regime": "dispersion_bucket",
        "trigger": "threshold",
        "entry": "delayed_market",
        "exit": "time_120m",
        "sizing": "research_fixed_unit",
        "execution": "stressed_market",
        "parameters": {"lookback_minutes": 60, "threshold": 0.004},
        "symbols": ("BTC/USD", "ETH/USD", "SOL/USD"),
        "timeframe": "5m",
        "falsification_statement": "Reject if net expectancy is nonpositive under stressed cost.",
    }
    values.update(changes)
    return build_manifest(**values)


def test_strategy_manifest_is_data_driven_and_hash_stable():
    first = manifest()
    second = manifest()
    result = validate_manifest(first)
    assert result["trusted_compiler_compatible"] is True
    assert result["software_change_required"] is False
    assert manifest_hash(first) == manifest_hash(second)


def test_new_primitive_becomes_software_requirement_instead_of_arbitrary_code():
    value = manifest(feature="order_book_imbalance")
    result = validate_manifest(value)
    assert result["trusted_compiler_compatible"] is False
    assert result["software_change_required"] is True
    assert result["missing_primitives"] == {"feature": "order_book_imbalance"}


def test_strategy_manifest_requires_falsification_and_symbols():
    with pytest.raises(StrategyGrammarError):
        manifest(falsification_statement="")
    with pytest.raises(StrategyGrammarError):
        manifest(symbols=())
