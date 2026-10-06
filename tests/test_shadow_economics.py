from app.shadow_economics import candidate_shadow_economics


def test_shadow_economics_is_explicitly_non_authoritative():
    result = candidate_shadow_economics(
        target_pct="0.005",
        metadata={
            "momentum_pct": "0.0020",
            "vwap_edge_pct": "0.0015",
            "quality_score": 90,
            "market_quality": {"spread_pct": "0.0002"},
        },
    )

    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["changes_live_decision"] is False
    assert result["estimate"]["expected_net_bps"] == "11.400000"
    assert result["shadow_admission"]["would_admit"] is True


def test_shadow_economics_rejects_cost_dominated_candidate():
    result = candidate_shadow_economics(
        target_pct="0.005",
        metadata={
            "momentum_pct": "0.0004",
            "vwap_edge_pct": "0.0003",
            "quality_score": 40,
            "market_quality": {"spread_pct": "0.0003"},
        },
    )

    assert result["shadow_admission"]["would_admit"] is False
    assert result["shadow_admission"]["reason"] in {
        "expected_net_edge_nonpositive",
        "expected_net_edge_below_hurdle",
        "gross_to_cost_ratio_below_hurdle",
    }


def test_shadow_economics_missing_quality_is_conservative_and_deterministic():
    result = candidate_shadow_economics(
        target_pct="0.005",
        metadata={
            "momentum_pct": "0.001",
            "vwap_edge_pct": "0.001",
            "market_quality": {},
        },
    )

    assert result["estimate"]["confidence"] == "0"
    assert result["inputs"]["spread_bps"] == "0"
    assert result["estimate"]["expected_slippage_bps"] == "0.50"
    assert result["estimate"]["uncertainty_reserve_bps"] == "7"
