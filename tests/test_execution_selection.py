from decimal import Decimal
from types import SimpleNamespace

from app.execution import ExecutionEngine
from app.strategy import Signal


def engine_with_settings(max_hold=15, cooldown=2):
    engine = object.__new__(ExecutionEngine)
    engine.settings = SimpleNamespace(
        max_hold_minutes=max_hold,
        reentry_cooldown_minutes=cooldown,
    )
    return engine


def signal(symbol, momentum, vwap_edge, confirmations):
    return Signal(
        action="buy",
        symbol=symbol,
        metadata={
            "momentum_pct": str(momentum),
            "vwap_edge_pct": str(vwap_edge),
            "confirmation_passes": confirmations,
        },
    )


def test_signal_rank_prefers_stronger_momentum_first():
    weak = signal("SPY", Decimal("0.0006"), Decimal("0.002"), 2)
    strong = signal("NVDA", Decimal("0.0012"), Decimal("0.001"), 1)

    assert ExecutionEngine._signal_rank(strong) > ExecutionEngine._signal_rank(weak)


def test_signal_rank_uses_vwap_edge_as_tiebreaker():
    a = signal("SPY", Decimal("0.001"), Decimal("0.001"), 1)
    b = signal("QQQ", Decimal("0.001"), Decimal("0.002"), 1)

    assert ExecutionEngine._signal_rank(b) > ExecutionEngine._signal_rank(a)


def test_same_symbol_lockout_extends_beyond_max_hold_window():
    engine = engine_with_settings(max_hold=15, cooldown=2)
    assert engine._same_symbol_lockout_minutes() == 17


def test_same_symbol_lockout_uses_cooldown_when_no_max_hold():
    engine = engine_with_settings(max_hold=0, cooldown=3)
    assert engine._same_symbol_lockout_minutes() == 3


def test_same_symbol_lockout_can_be_disabled():
    engine = engine_with_settings(max_hold=15, cooldown=0)
    assert engine._same_symbol_lockout_minutes() == 0
