from app.research_agent.shadow_economics_validation import (
    evaluate_shadow_economics,
)


def candidate(
    key,
    *,
    expected_gross_bps,
    expected_net_bps,
    admitted,
    forward_10m,
    execution_authority=False,
):
    return {
        "candidate_id": key,
        "observed_at": "2026-10-06T14:00:00+00:00",
        "features": {
            "shadow_economics": {
                "methodology_version": "rhen-shadow-economics-v1",
                "research_only": True,
                "execution_authority": execution_authority,
                "expected_gross_bps": str(expected_gross_bps),
                "expected_net_bps": str(expected_net_bps),
                "would_admit": admitted,
                "reason": (
                    "economic_gate_passed"
                    if admitted
                    else "expected_net_edge_nonpositive"
                ),
            }
        },
        "outcomes": [
            {
                "horizon_minutes": 10,
                "status": "complete",
                "forward_return": str(forward_10m),
                "max_favorable_return": "0.004",
                "max_adverse_return": "-0.001",
            }
        ],
    }


def test_shadow_economics_validation_measures_admission_lift():
    result = evaluate_shadow_economics(
        [
            candidate(
                "a",
                expected_gross_bps=20,
                expected_net_bps=10,
                admitted=True,
                forward_10m="0.003",
            ),
            candidate(
                "b",
                expected_gross_bps=18,
                expected_net_bps=8,
                admitted=True,
                forward_10m="0.001",
            ),
            candidate(
                "c",
                expected_gross_bps=8,
                expected_net_bps=-2,
                admitted=False,
                forward_10m="0.0005",
            ),
        ],
        horizons=(10,),
    )

    horizon = result["horizons"][0]
    assert result["research_only"] is True
    assert result["execution_authority"] is False
    assert result["automatic_application_authorized"] is False
    assert result["candidate_count"] == 3
    assert horizon["state"] == "COLLECTING"
    assert horizon["complete_outcome_count"] == 3
    assert float(horizon["outcome_coverage"]) == 1.0
    assert horizon["admitted_count"] == 2
    assert horizon["rejected_count"] == 1
    assert float(horizon["admitted_mean_realized_net_bps"]) == 10.0
    assert float(horizon["rejected_mean_realized_net_bps"]) == -5.0
    assert float(horizon["admission_selection_lift_bps"]) == 15.0
    assert float(horizon["admitted_positive_realized_fraction"]) == 0.5
    assert float(horizon["rejected_positive_realized_fraction"]) == 0.0


def test_shadow_economics_validation_excludes_authoritative_or_missing_rows():
    result = evaluate_shadow_economics(
        [
            candidate(
                "bad-authority",
                expected_gross_bps=20,
                expected_net_bps=10,
                admitted=True,
                forward_10m="0.003",
                execution_authority=True,
            ),
            {
                "candidate_id": "missing-shadow",
                "observed_at": "2026-10-06T14:00:00+00:00",
                "features": {},
                "outcomes": [],
            },
        ],
        horizons=(10, 15),
    )

    assert result["candidate_count"] == 0
    assert result["independent_sessions"] == 0
    assert all(
        row["state"] == "NO_SHADOW_EVIDENCE"
        and row["complete_outcome_count"] == 0
        for row in result["horizons"]
    )
