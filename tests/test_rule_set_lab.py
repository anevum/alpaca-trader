from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.rule_set_lab import (
    EntryRule,
    RuleSetSpec,
    EntryRuleFilteredStrategy,
    parse_rule_sets,
    run_rule_set_tournament,
)
from app.strategy import Signal


NY = ZoneInfo("America/New_York")


def settings(**overrides):
    base = dict(
        ALPACA_API_KEY="x",
        ALPACA_API_SECRET="y",
        TRADING_MODE="paper",
        STRATEGY_NAME="rolling_momentum_vwap",
        STRATEGY_SYMBOL="SPY",
        SCAN_SYMBOLS="SPY",
        ALLOWED_SYMBOLS="SPY,QQQ",
        CONFIRMATION_SYMBOLS="QQQ",
        MIN_CONFIRMATIONS="1",
        ORDER_NOTIONAL="20",
        MAX_ORDER_NOTIONAL="80.35",
        MAX_POSITION_NOTIONAL="80.35",
        MAX_TOTAL_POSITION_NOTIONAL="80",
        MAX_CONCURRENT_POSITIONS="1",
        MAX_NEW_ENTRIES_PER_CYCLE="1",
        MAX_DAILY_ORDERS="12",
        MAX_DAILY_LOSS="5",
        STOP_PCT="0.0035",
        TARGET_PCT="0.005",
        ENTRY_START="09:31",
        ENTRY_CUTOFF="15:30",
        FORCE_FLAT_TIME="15:55",
        MAX_HOLD_MINUTES="15",
        SIZING_MODE="fixed",
        FAST_WINDOW="3",
        SLOW_WINDOW="8",
        MIN_MOMENTUM_PCT="0.0005",
        MIN_VWAP_EDGE_PCT="0",
        MAX_SPREAD_PCT="0.002",
    )
    base.update(overrides)
    return Settings(**base)


def bars(count=15, *, volume_override=None):
    result = []
    for i in range(count):
        stamp = datetime(2026, 9, 21, 9, 30, tzinfo=NY) + timedelta(minutes=i)
        close = Decimal("100") + Decimal(i) / Decimal("10")
        volume = (
            volume_override.get(i, 1000)
            if volume_override is not None else 1000
        )
        result.append({
            "t": stamp.isoformat(),
            "o": str(close),
            "h": str(close + Decimal("0.03")),
            "l": str(close - Decimal("0.03")),
            "c": str(close),
            "v": volume,
            "vw": str(close),
        })
    return result


class AlwaysBuy:
    def evaluate(self, **kwargs):
        visible = kwargs["bars"]
        return Signal(
            action="buy", symbol=kwargs["symbol"], notional=Decimal("20"),
            reference_price=Decimal(str(visible[-1]["c"])),
        )


def test_rule_set_rejects_untrusted_code_and_unbounded_search():
    with pytest.raises(ValueError, match="untrusted"):
        parse_rule_sets([{
            "name": "unsafe",
            "hypothesis": "Arbitrary execution must fail",
            "entry_rules": [{"indicator": "python_eval", "threshold": "1"}],
        }])
    with pytest.raises(ValueError, match="at most 8"):
        parse_rule_sets([{
            "name": f"variant-{i}",
            "hypothesis": "Bounded observation",
            "entry_rules": [{"indicator": "relative_volume", "threshold": "1"}],
        } for i in range(9)])
    with pytest.raises(ValueError, match="finite"):
        EntryRule("relative_volume", Decimal("NaN"), 3)


def test_relative_volume_rule_uses_only_visible_completed_bars():
    spec = RuleSetSpec(
        name="volume-verified",
        hypothesis="Stronger volume improves momentum entry selection",
        entry_rules=(EntryRule("relative_volume", Decimal("2"), 3),),
    )
    strategy = EntryRuleFilteredStrategy(AlwaysBuy(), spec)
    history = bars(4, volume_override={3: 5000})
    call = dict(
        confirmation_bars={}, symbol="SPY", has_position=False,
        order_notional=Decimal("20"),
        now=datetime(2026, 9, 21, 9, 34, tzinfo=NY),
    )
    insufficient = strategy.evaluate(bars=history[:2], **call)
    rejected = strategy.evaluate(bars=history[:3], **call)
    accepted = strategy.evaluate(bars=history[:4], **call)
    early_call = dict(call)
    early_call["now"] = datetime(2026, 9, 21, 9, 33, tzinfo=NY)
    future_not_allowed = strategy.evaluate(bars=history[:4], **early_call)
    assert future_not_allowed.action == "hold"
    assert insufficient.action == "hold"
    assert insufficient.metadata["research_rule_rejected"] is True
    assert rejected.action == "hold"
    assert accepted.action == "buy"
    assert accepted.metadata["research_rule_set"] == "volume-verified"
    assert accepted.metadata["research_rule_diagnostics"][0]["passed"] is True


def test_rule_set_tournament_freezes_control_and_refuses_promotion():
    spec = parse_rule_sets([{
        "name": "rv-15",
        "hypothesis": "Volume spike confirmation improves selection",
        "entry_rules": [{"indicator": "relative_volume", "threshold": "1.5"}],
    }])
    data = {"SPY": bars(), "QQQ": bars()}
    result = run_rule_set_tournament(
        settings=settings(),
        bars_by_symbol=data,
        rule_sets=spec,
        initial_equity=Decimal("100"),
        spread_bps=Decimal("5"),
        slippage_bps=Decimal("2"),
    )
    assert result["research_only"] is True
    assert result["automatic_promotion_authorized"] is False
    assert result["live_strategy_mutation"] is False
    assert result["status"] == "EXPLORATORY_NOT_VALIDATED"
    assert len(result["results"]) == 2
    assert result["results"][0]["name"] == "production"
    assert result["results"][1]["name"] == "rv-15"
    assert all(not row["validated_alpha"] for row in result["results"])
    assert all(not row["promotion_authorized"] for row in result["results"])
    assert len(result["fingerprint"]) == 64
    assert len(result["manifest"]["data_fingerprint"]) == 64
    families = result["research_family_registry"]["families"]
    challenger_family = next(
        item for item in families if item["family_key"] == "rhen-rule-rv-15"
    )
    assert challenger_family["status"] == "SPEC_ONLY"
    assert challenger_family["automatic_promotion_authorized"] is False
    assert result["results"][0]["assumptions"]["broker_orders_possible"] is False


def test_rule_set_fingerprint_changes_with_data_even_when_rules_do_not():
    cfg = settings()
    first_data = {"SPY": bars(), "QQQ": bars()}
    second_data = {"SPY": bars(volume_override={14: 5000}), "QQQ": bars()}
    args = dict(
        settings=cfg, rule_sets=(), initial_equity=Decimal("100"),
        spread_bps=Decimal("5"), slippage_bps=Decimal("2"),
    )
    left = run_rule_set_tournament(bars_by_symbol=first_data, **args)
    right = run_rule_set_tournament(bars_by_symbol=second_data, **args)
    assert left["fingerprint"] != right["fingerprint"]
