from app.research_agent.shadow_allocation_validation import (
    evaluate_shadow_allocation,
)


def candidate(
    key,
    *,
    ratio_pct,
    forward_10m,
    expected_gross_bps=20,
    expected_net_bps=10,
    execution_authority=False,
):
    return {
        "candidate_id": key,
        "observed_at": "2026-10-06T14:00:00+00:00",
        "features": {
            "shadow_economics": {
                "methodology_version": "rhen-shadow-economics-v1",
                "research_only": True,
                "execution_authority": False,
                "expected_gross_bps": str(expected_gross_bps),
                "expected_net_bps": str(expected_net_bps),
                "would_admit": True,
            }
        },
        "shadow_allocation": {
            "methodology_version": "rhen-shadow-allocation-v1",
            "research_only": True,
            "execution_authority": execution_authority,
            "changes_live_decision": False,
            "bounded_by_live_safe_notional": True,
            "shadow_to_live_pct": str(ratio_pct),
            "would_allocate": float(ratio_pct) > 0,
        },
        "outcomes": [
            {
                "horizon_minutes": 10,
                "status": "complete",
                "forward_return": str(forward_10m),
            }
        ],
    }


def test_shadow_allocation_validation_balances_loss_avoidance_and_gain_sacrifice():
    result = evaluate_shadow_allocation(
        [
            candidate("loser", ratio_pct=0, forward_10m="-0.002"),
            candidate("winner", ratio_pct=50, forward_10m="0.003"),
        ],
        horizons=(10,),
    )

    horizon = result["horizons"][0]
    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["capital_scaling_authorized"] is False
    assert horizon["selected_entry_count"] == 2
    assert horizon["complete_outcome_count"] == 2
    assert float(horizon["outcome_coverage"]) == 1.0
    assert float(horizon["mean_shadow_to_live_pct"]) == 25.0
    assert float(horizon["mean_live_proxy_net_bps"]) == 0.0
    assert float(horizon["mean_shadow_proxy_net_bps"]) == 5.0
    assert float(horizon["mean_allocation_delta_bps"]) == 5.0
    assert horizon["improved_entry_count"] == 1
    assert horizon["worsened_entry_count"] == 1
    assert float(horizon["improved_entry_fraction"]) == 0.5
    assert horizon["zero_allocation_count"] == 1
    assert float(horizon["mean_avoided_loss_bps"]) == 30.0
    assert float(horizon["mean_sacrificed_gain_bps"]) == 10.0


def test_shadow_allocation_validation_rejects_unsafe_or_uncosted_rows():
    result = evaluate_shadow_allocation(
        [
            candidate(
                "authority",
                ratio_pct=50,
                forward_10m="0.003",
                execution_authority=True,
            ),
            {
                "candidate_id": "uncosted",
                "observed_at": "2026-10-06T14:00:00+00:00",
                "features": {},
                "shadow_allocation": {
                    "methodology_version": "rhen-shadow-allocation-v1",
                    "research_only": True,
                    "execution_authority": False,
                    "bounded_by_live_safe_notional": True,
                    "shadow_to_live_pct": "50",
                    "would_allocate": True,
                },
                "outcomes": [],
            },
        ],
        horizons=(10, 15),
    )

    assert result["selected_entry_count"] == 0
    assert result["independent_sessions"] == 0
    assert all(
        row["state"] == "NO_SHADOW_EVIDENCE"
        and row["complete_outcome_count"] == 0
        for row in result["horizons"]
    )
