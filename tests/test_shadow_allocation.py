from app.shadow_allocation import selected_entry_shadow_allocation


def economics(*, net_bps="10", confidence="0.8", admitted=True):
    return {
        "estimate": {
            "expected_net_bps": net_bps,
            "confidence": confidence,
        },
        "shadow_admission": {
            "would_admit": admitted,
            "reason": (
                "economic_gate_passed"
                if admitted
                else "expected_net_edge_below_hurdle"
            ),
        },
    }


def test_shadow_allocation_can_only_reduce_live_safe_notional():
    result = selected_entry_shadow_allocation(
        symbol="SPY",
        live_safe_notional="20",
        min_order_notional="1",
        expected_holding_minutes="15",
        shadow_economics=economics(),
    )

    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["changes_live_decision"] is False
    assert result["bounded_by_live_safe_notional"] is True
    assert result["live_safe_notional"] == "20.00"
    assert result["shadow_notional"] == "13.00"
    assert float(result["shadow_notional"]) <= float(result["live_safe_notional"])
    assert result["would_allocate"] is True
    assert float(result["capital_velocity_per_minute"]) > 0


def test_shadow_allocation_skips_cost_dominated_entry():
    result = selected_entry_shadow_allocation(
        symbol="SPY",
        live_safe_notional="20",
        min_order_notional="1",
        expected_holding_minutes="15",
        shadow_economics=economics(net_bps="-2", admitted=False),
    )

    assert result["shadow_notional"] == "0"
    assert result["shadow_multiplier"] == "0"
    assert result["would_allocate"] is False
    assert result["reason"] == "expected_net_edge_below_hurdle"


def test_shadow_allocation_respects_live_minimum():
    result = selected_entry_shadow_allocation(
        symbol="SPY",
        live_safe_notional="1",
        min_order_notional="1",
        expected_holding_minutes="15",
        shadow_economics=economics(net_bps="2", confidence="0.2", admitted=True),
    )

    assert result["shadow_notional"] == "0"
    assert result["would_allocate"] is False
    assert result["reason"] == "shadow_notional_below_live_minimum"
