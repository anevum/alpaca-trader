import pytest

from app.research_agent.ads002 import (
    FORWARD_HORIZONS_MINUTES,
    METHODOLOGY_VERSION,
    PRE_SAMPLE_CONFIDENCE_CAP,
    pretrade_composite,
    score_attention,
    score_confidence,
    score_exit_health,
    score_pretrade,
    score_qualification,
    score_timing,
)


CONFIG = {
    "min_momentum_pct": "0.001",
    "target_pct": "0.005",
    "max_spread_pct": "0.002",
    "max_bar_age_seconds": 60,
    "max_vwap_extension_pct": "0.01",
}

FEATURES = {
    "relative_volume_ratio": 1.5,
    "momentum_pct": 0.003,
    "vwap_edge_pct": 0.002,
    "trend_persistence": 0.75,
    "fresh_confirmation_passes": 2,
    "independent_confirmation_count": 3,
    "regime_confirmation_passes": 2,
    "regime_confirmation_count": 2,
    "spread_pct": 0.0005,
    "bar_age_seconds": 5,
    "quote_age_ms": 750,
}


@pytest.mark.parametrize(
    "scorer",
    [score_attention, score_qualification, score_timing],
)
def test_predecision_scores_are_bounded(scorer):
    result = scorer(FEATURES, CONFIG)
    assert 0 <= result["score"] <= 100
    assert all(0 <= value <= 1 for value in result["components"].values())


def test_attention_is_direction_neutral_for_velocity():
    positive = dict(FEATURES, momentum_pct=0.003)
    negative = dict(FEATURES, momentum_pct=-0.003)
    assert score_attention(positive, CONFIG)["score"] == pytest.approx(
        score_attention(negative, CONFIG)["score"]
    )


def test_qualification_rewards_positive_not_negative_momentum():
    positive = score_qualification(dict(FEATURES, momentum_pct=0.003), CONFIG)
    negative = score_qualification(dict(FEATURES, momentum_pct=-0.003), CONFIG)
    assert positive["score"] > negative["score"]


def test_timing_marks_quote_age_imputation():
    features = dict(FEATURES)
    features.pop("quote_age_ms")
    result = score_timing(features, CONFIG)
    assert result["details"]["quote_age_imputed"] is True
    assert result["components"]["quote_freshness"] == pytest.approx(
        result["components"]["bar_freshness"]
    )


def test_exit_health_fails_closed_without_excursion_data():
    result = score_exit_health(
        realized_return=0.01,
        max_favorable_excursion=None,
        max_adverse_excursion=-0.01,
    )
    assert result["score"] is None
    assert result["reason_code"] == "MISSING_EXCURSION_DATA"


def test_exit_health_is_bounded_when_complete():
    result = score_exit_health(
        realized_return=0.01,
        max_favorable_excursion=0.02,
        max_adverse_excursion=-0.01,
    )
    assert 0 <= result["score"] <= 100


def test_confidence_is_capped_before_minimum_samples():
    result = score_confidence(
        direct_attribution_coverage=1,
        feature_and_forward_completeness=1,
        independent_sessions=9,
        research_eligible_candidates=1000,
        closed_direct_trades=1000,
        primary_effect_sign_consistency=1,
        median_abs_primary_session_spearman=1,
        distinct_regime_buckets=3,
    )
    assert result["hard_cap_active"] is True
    assert result["score"] <= float(PRE_SAMPLE_CONFIDENCE_CAP)
    assert "MIN_INDEPENDENT_SESSIONS" in result["missing_requirements"]


def test_confidence_can_exceed_cap_after_minimum_samples():
    result = score_confidence(
        direct_attribution_coverage=1,
        feature_and_forward_completeness=1,
        independent_sessions=10,
        research_eligible_candidates=100,
        closed_direct_trades=30,
        primary_effect_sign_consistency=1,
        median_abs_primary_session_spearman=0.2,
        distinct_regime_buckets=3,
    )
    assert result["hard_cap_active"] is False
    assert result["score"] > float(PRE_SAMPLE_CONFIDENCE_CAP)


def test_pretrade_composite_uses_frozen_weights():
    assert pretrade_composite(
        attention=100,
        qualification=50,
        timing=0,
    ) == pytest.approx(45)


def test_score_pretrade_has_no_post_event_inputs():
    result = score_pretrade(FEATURES, CONFIG)
    assert result["methodology_version"] == METHODOLOGY_VERSION
    assert set(result) == {
        "methodology_version",
        "attention",
        "qualification",
        "timing",
        "pretrade_composite",
    }


def test_forward_horizons_are_frozen_v2_contract():
    assert FORWARD_HORIZONS_MINUTES == (1, 3, 5, 10, 15, 30, 60)
