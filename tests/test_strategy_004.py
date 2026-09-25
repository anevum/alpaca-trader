from app.strategy import Signal
from app.strategy_004 import strategy_004_entry_gate


def signal(
    *,
    quality="83",
    momentum="0.0022",
    vwap_edge="0.0073",
    relative_volume="1.0",
    confirmations=2,
    bar_age=5,
    current_close="100.05",
    previous_close="100",
):
    return Signal(
        action="buy",
        symbol="TEST",
        metadata={
            "quality_score": quality,
            "momentum_pct": momentum,
            "vwap_edge_pct": vwap_edge,
            "relative_volume_ratio": relative_volume,
            "confirmation_passes": confirmations,
            "current_close": current_close,
            "previous_close": previous_close,
            "market_quality": {
                "bar_age_seconds": bar_age,
            },
        },
    )


def test_strategy_004_accepts_exceptional_impulse():
    decision = strategy_004_entry_gate(
        signal(
            quality="92",
            momentum="0.0054",
            vwap_edge="0.0100",
            relative_volume="2.3",
            confirmations=2,
            bar_age=5,
            current_close="101",
            previous_close="100",
        )
    )

    assert decision.allowed is True
    assert decision.lane == "impulse"


def test_strategy_004_accepts_controlled_continuation():
    decision = strategy_004_entry_gate(signal())

    assert decision.allowed is True
    assert decision.lane == "continuation"


def test_strategy_004_rejects_medium_chase_structure():
    decision = strategy_004_entry_gate(
        signal(
            quality="85",
            momentum="0.0030",
            vwap_edge="0.0070",
            relative_volume="1.0",
            bar_age=5,
            current_close="100.25",
            previous_close="100",
        )
    )

    assert decision.allowed is False
    assert decision.lane == "reject"


def test_strategy_004_rejects_stale_continuation():
    decision = strategy_004_entry_gate(
        signal(
            bar_age=45,
            relative_volume="1.2",
        )
    )

    assert decision.allowed is False


def test_strategy_004_rejects_low_participation_when_not_ultrafresh():
    decision = strategy_004_entry_gate(
        signal(
            bar_age=20,
            relative_volume="0.2",
        )
    )

    assert decision.allowed is False


def test_strategy_004_allows_ultrafresh_continuation_despite_low_relative_volume():
    decision = strategy_004_entry_gate(
        signal(
            bar_age=5,
            relative_volume="0.05",
        )
    )

    assert decision.allowed is True
    assert decision.lane == "continuation"


def test_strategy_004_requires_completed_bar_prices():
    decision = strategy_004_entry_gate(
        signal(
            current_close="0",
            previous_close="0",
        )
    )

    assert decision.allowed is False
    assert "requires current and previous" in decision.reason
