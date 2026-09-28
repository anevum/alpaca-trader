from decimal import Decimal

from app.research.adaptive_decision_space import (
    DecisionVector,
    adaptive_priority,
    build_decision_vector,
)


def test_build_decision_vector_keeps_dimensions_independent():
    vector = build_decision_vector(
        attention_features={
            "intraday_return": "0.8",
            "session_range": "0.7",
            "relative_volume": "0.9",
            "liquidity": "1",
            "trend_persistence": "0.8",
        },
        qualification_features={
            "momentum": "0.9",
            "vwap_edge": "0.8",
            "confirmations": "1",
            "regime": "1",
            "spread": "0.9",
            "freshness": "0.9",
            "relative_volume": "0.8",
            "trend_persistence": "0.7",
        },
        timing_features={
            "momentum_acceleration": "0.1",
            "distance_from_vwap_optimum": "0.2",
            "bar_impulse": "0.1",
            "spread": "0.9",
            "freshness": "0.9",
            "confirmation_alignment": "0.8",
        },
        exit_features={
            "current_return": "0.5",
            "mfe_retention": "0.5",
            "momentum_health": "0.5",
            "regime_health": "0.5",
            "time_remaining": "0.5",
            "adverse_excursion": "0.5",
        },
        confidence_features={
            "coverage": "1",
            "sample_strength": "0.1",
            "stability": "0.5",
        },
    )
    assert vector.qualification > vector.timing
    assert Decimal("0") <= vector.confidence <= Decimal("1")


def test_confidence_penalizes_sparse_research_evidence():
    high = DecisionVector(
        attention=Decimal("0.9"),
        qualification=Decimal("0.9"),
        timing=Decimal("0.9"),
        exit_health=Decimal("0.9"),
        confidence=Decimal("1"),
    )
    sparse = DecisionVector(
        attention=Decimal("0.9"),
        qualification=Decimal("0.9"),
        timing=Decimal("0.9"),
        exit_health=Decimal("0.9"),
        confidence=Decimal("0"),
    )
    assert adaptive_priority(high) > adaptive_priority(sparse)


def test_features_are_clamped_not_allowed_to_explode_score():
    vector = build_decision_vector(
        attention_features={key: "100" for key in (
            "intraday_return", "session_range", "relative_volume", "liquidity",
            "trend_persistence",
        )},
        qualification_features={key: "-100" for key in (
            "momentum", "vwap_edge", "confirmations", "regime", "spread",
            "freshness", "relative_volume", "trend_persistence",
        )},
        timing_features={key: "100" for key in (
            "momentum_acceleration", "distance_from_vwap_optimum", "bar_impulse",
            "spread", "freshness", "confirmation_alignment",
        )},
        exit_features={key: "100" for key in (
            "current_return", "mfe_retention", "momentum_health", "regime_health",
            "time_remaining", "adverse_excursion",
        )},
        confidence_features={"coverage": "100", "sample_strength": "100", "stability": "100"},
    )
    assert vector.attention == Decimal("1")
    assert vector.qualification == Decimal("0")
    assert vector.timing == Decimal("1")
    assert vector.exit_health == Decimal("1")
    assert vector.confidence == Decimal("1")


def test_module_has_no_execution_authority_contract():
    vector = build_decision_vector(
        attention_features={},
        qualification_features={},
        timing_features={},
        exit_features={},
        confidence_features={},
    )
    payload = vector.as_dict()
    assert "action" not in payload
    assert "order" not in payload
    assert "quantity" not in payload
