from app.research_agent.nostra_transition import (
    build_transition_model,
    evaluate_transition_calibration,
    forecast_next_regime,
)


def observations(sessions=12):
    rows = []
    for day in range(sessions):
        session = f"2026-10-{day+1:02d}"
        sequence = [
            "OPENING_DISCOVERY",
            "BROAD_ADVANCE",
            "TREND_EXPANSION",
            "TREND_DECAY",
            "MIDDAY_COMPRESSION",
        ]
        for i, regime in enumerate(sequence):
            rows.append(
                {
                    "session": session,
                    "observed_at": f"{session}T{9+i:02d}:30:00-04:00",
                    "regime": regime,
                }
            )
    return rows


def test_transition_model_never_crosses_session_boundary():
    model = build_transition_model(observations(sessions=2))
    assert model["total_transitions"] == 8
    opening = model["states"]["OPENING_DISCOVERY"]
    assert opening["transition_count"] == 2


def test_smoothed_transition_forecast_is_research_only():
    model = build_transition_model(observations())
    forecast = forecast_next_regime(
        model,
        current_regime="OPENING_DISCOVERY",
    )
    assert forecast["next_regime"] == "BROAD_ADVANCE"
    assert forecast["next_probability"] > 0
    assert forecast["execution_authority"] is False


def test_immature_state_confidence_is_hard_capped():
    model = build_transition_model(observations(sessions=3))
    state = model["states"]["OPENING_DISCOVERY"]
    assert state["minimums_met"] is False
    assert state["confidence"] <= 0.49


def test_transition_calibration_scores_frozen_probabilities():
    rows = []
    for _ in range(120):
        rows.append(
            {
                "probabilities": {
                    "BROAD_ADVANCE": 0.8,
                    "TREND_EXPANSION": 0.2,
                },
                "realized_regime": "BROAD_ADVANCE",
            }
        )
    result = evaluate_transition_calibration(rows)
    assert result["observations"] == 120
    assert result["mature"] is True
    assert result["top1_accuracy"] == 1.0
    assert result["base_rate_brier"] is not None
    assert result["brier_skill_score"] is not None
    assert result["base_rate_log_loss"] is not None
    assert result["log_loss_skill_score"] is not None
    assert result["execution_authority"] is False



def test_transition_calibration_can_show_positive_skill_vs_base_rate():
    rows = []
    for i in range(120):
        realized = "BROAD_ADVANCE" if i % 2 == 0 else "BROAD_DECLINE"
        rows.append(
            {
                "probabilities": {
                    "BROAD_ADVANCE": 0.85 if realized == "BROAD_ADVANCE" else 0.15,
                    "BROAD_DECLINE": 0.85 if realized == "BROAD_DECLINE" else 0.15,
                },
                "realized_regime": realized,
            }
        )
    result = evaluate_transition_calibration(rows)
    assert result["observations"] == 120
    assert result["mature"] is True
    assert result["brier_skill_score"] > 0
    assert result["log_loss_skill_score"] > 0
    assert result["top1_accuracy"] > result["majority_class_accuracy"]
