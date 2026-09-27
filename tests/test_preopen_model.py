import pytest

from app.preopen_state.model import LogisticArtifact
from app.preopen_state.research import fit_logistic, score_rows


def training_rows() -> list[dict]:
    return [
        {"x": float(index), "target": int(index > 0)}
        for index in range(-20, 21)
        if index != 0
    ]


def test_research_model_is_deterministic_and_checksum_bound():
    rows = training_rows()
    first = fit_logistic(
        rows,
        feature_names=("x",),
        target_name="target",
        model_key="unit-test",
        trained_through="2026-09-01",
    )
    second = fit_logistic(
        rows,
        feature_names=("x",),
        target_name="target",
        model_key="unit-test",
        trained_through="2026-09-01",
    )
    assert first == second

    model = LogisticArtifact.from_dict(first)
    assert model.probability({"x": 5.0}) > model.probability({"x": -5.0})


def test_model_artifact_rejects_tampering():
    artifact = fit_logistic(
        training_rows(),
        feature_names=("x",),
        target_name="target",
        model_key="unit-test",
        trained_through="2026-09-01",
    )
    artifact["intercept"] = float(artifact["intercept"]) + 1.0
    with pytest.raises(ValueError, match="checksum"):
        LogisticArtifact.from_dict(artifact)


def test_evaluation_reports_probability_skill_and_base_rates():
    rows = training_rows()
    artifact = fit_logistic(
        rows,
        feature_names=("x",),
        target_name="target",
        model_key="unit-test",
        trained_through="2026-09-01",
    )
    metrics = score_rows(rows, artifact)
    assert metrics["rows"] == len(rows)
    assert "brier_skill_score" in metrics
    assert "log_loss_skill_score" in metrics
    assert "majority_class_accuracy" in metrics
