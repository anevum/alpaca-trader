from decimal import Decimal

from app.strategy import Signal
from app.strategy_004_candidate_c import (
    MAX_MOMENTUM_PCT,
    MAX_TREND_PERSISTENCE,
    strategy_004_candidate_c_gate,
)


def signal(**metadata):
    return Signal(
        action="buy",
        symbol="TEST",
        reference_price=Decimal("100"),
        metadata=metadata,
    )


def test_candidate_c_accepts_controlled_continuation():
    decision = strategy_004_candidate_c_gate(
        signal(
            momentum_pct="0.0020",
            trend_persistence="0.625",
        )
    )

    assert decision.allowed is True
    assert decision.lane == "controlled_continuation"


def test_candidate_c_rejects_mature_momentum_chase():
    decision = strategy_004_candidate_c_gate(
        signal(
            momentum_pct=str(MAX_MOMENTUM_PCT + Decimal("0.0001")),
            trend_persistence="0.5",
        )
    )

    assert decision.allowed is False
    assert decision.details["momentum_ok"] is False


def test_candidate_c_rejects_overly_persistent_path():
    decision = strategy_004_candidate_c_gate(
        signal(
            momentum_pct="0.0020",
            trend_persistence=str(
                MAX_TREND_PERSISTENCE + Decimal("0.125")
            ),
        )
    )

    assert decision.allowed is False
    assert decision.details["persistence_ok"] is False


def test_candidate_c_fails_closed_when_required_metadata_is_missing():
    decision = strategy_004_candidate_c_gate(
        signal(momentum_pct="0.0020")
    )

    assert decision.allowed is False
    assert "requires momentum" in decision.reason
