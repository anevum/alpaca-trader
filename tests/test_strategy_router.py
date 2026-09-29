from app.research_agent.strategy_router import rank_strategy_families


def test_router_defaults_to_no_trade_without_validated_family():
    result = rank_strategy_families(
        regime_state={"regime": "TREND_EXPANSION"},
        families=[],
    )
    assert result["selected_research_family"] == "NO_TRADE"
    assert result["no_trade_selected"] is True
    assert result["execution_authority"] is False


def test_router_rejects_immature_positive_family():
    result = rank_strategy_families(
        regime_state={"regime": "TREND_EXPANSION"},
        families=[
            {
                "family_key": "momentum",
                "validation_status": "ACTIVE_SHADOW",
                "regime_evidence": {
                    "TREND_EXPANSION": {
                        "expected_utility": "0.002",
                        "confidence": "0.9",
                        "minimums_met": True,
                    }
                },
            }
        ],
    )
    assert result["selected_research_family"] == "NO_TRADE"


def test_router_can_rank_validated_positive_family_in_research():
    result = rank_strategy_families(
        regime_state={"regime": "TREND_EXPANSION"},
        families=[
            {
                "family_key": "momentum",
                "validation_status": "FROZEN_VALIDATION",
                "regime_evidence": {
                    "TREND_EXPANSION": {
                        "expected_utility": "0.0015",
                        "confidence": "0.8",
                        "minimums_met": True,
                    }
                },
            },
            {
                "family_key": "other",
                "validation_status": "FROZEN_VALIDATION",
                "regime_evidence": {
                    "TREND_EXPANSION": {
                        "expected_utility": "-0.0002",
                        "confidence": "0.95",
                        "minimums_met": True,
                    }
                },
            },
        ],
    )
    assert result["selected_research_family"] == "momentum"
    assert result["no_trade_selected"] is False
    assert result["execution_authority"] is False
    assert result["promotion_authorized"] is False
