from decimal import Decimal

from app.research_agent.adaptation_proposal import proposal_from_counterfactual
from app.research_agent.counterfactual_lab import (
    frozen_search_grid,
    run_counterfactual_search,
)


def rows_for_sessions(
    *,
    sessions=6,
    candidates_per_session=20,
    current="0.0020",
    good_looser=True,
):
    rows = []
    current_f = float(current)
    for s in range(sessions):
        for i in range(candidates_per_session):
            below = i < candidates_per_session // 2
            momentum = current_f - 0.00015 if below else current_f + 0.0003
            if below:
                forward = 0.0010 if good_looser else -0.0010
            else:
                forward = 0.0004
            rows.append(
                {
                    "candidate_id": f"{s}-{i}",
                    "session": f"2026-09-{10+s:02d}",
                    "gate_inputs": {
                        "momentum_pct": momentum,
                        "other_gates_passed": True,
                    },
                    "forward_outcomes": [
                        {
                            "horizon_minutes": 15,
                            "status": "complete",
                            "forward_return": forward,
                            "max_favorable_return": abs(forward) + 0.0005,
                            "max_adverse_return": -0.0003,
                        }
                    ],
                }
            )
    return rows


def test_search_grid_is_frozen_and_bounded():
    grid = frozen_search_grid("min_momentum_pct", "0.0020")
    assert grid == (
        Decimal("0.0016"),
        Decimal("0.0018"),
        Decimal("0.0022"),
        Decimal("0.0024"),
    )


def test_one_session_is_observation_only():
    result = run_counterfactual_search(
        rows=rows_for_sessions(sessions=1, candidates_per_session=80),
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost="0.0002",
    )
    assert result["validity_passed"] is False
    assert result["selected"] is None
    assert all(
        "INSUFFICIENT_INDEPENDENT_SESSIONS" in row["reason_codes"]
        for row in result["results"]
        if row["affected_candidates"]
    )


def test_repeated_positive_added_candidates_can_pass_research_validity():
    result = run_counterfactual_search(
        rows=rows_for_sessions(),
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost="0.0002",
    )
    assert result["validity_passed"] is True
    selected = result["selected"]
    assert selected is not None
    assert float(selected["requested_value"]) < 0.002
    assert float(selected["expected_improvement"]) > 0
    assert selected["selection_bias_status"] == "CONTROLLED"
    assert selected["dependence_status"] == "CONTROLLED"
    assert selected["interpretation"].startswith("screening counterfactual")


def test_bad_looser_threshold_does_not_pass():
    result = run_counterfactual_search(
        rows=rows_for_sessions(good_looser=False),
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost="0.0002",
    )
    looser = [
        row for row in result["results"]
        if float(row["requested_value"]) < 0.002 and row["affected_candidates"]
    ]
    assert looser
    assert all(row["validity_passed"] is False for row in looser)


def test_missing_cost_model_blocks_validity():
    result = run_counterfactual_search(
        rows=rows_for_sessions(),
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost=None,
    )
    assert result["validity_passed"] is False
    assert all(
        "TRANSACTION_COST_MODEL_REQUIRED" in row["reason_codes"]
        for row in result["results"]
    )


def test_missing_gate_isolation_is_excluded():
    rows = rows_for_sessions()
    for row in rows:
        row["gate_inputs"].pop("other_gates_passed")
    result = run_counterfactual_search(
        rows=rows,
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost="0.0002",
    )
    assert result["validity_passed"] is False
    assert all(row["affected_candidates"] == 0 for row in result["results"])


def test_search_ledger_records_multiplicity_and_dependence_controls():
    result = run_counterfactual_search(
        rows=rows_for_sessions(),
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost="0.0002",
    )
    ledger = result["search_ledger"]
    assert ledger["grid_frozen_before_evaluation"] is True
    assert ledger["comparison_count"] == 4
    assert ledger["multiple_testing_method"] == "bonferroni_exact_session_sign_test"
    assert ledger["dependence_method"] == "session_level_effects"


def test_valid_counterfactual_flows_into_bounded_proposal_engine():
    lab = run_counterfactual_search(
        rows=rows_for_sessions(),
        parameter="min_momentum_pct",
        current_value="0.0020",
        round_trip_cost="0.0002",
    )
    proposal = proposal_from_counterfactual(
        parameter="min_momentum_pct",
        current_value="0.0020",
        alternatives=lab["results"],
        control_state="ADAPT",
    )
    assert proposal is not None
    assert proposal["authorization_required"] is True
    assert proposal["automatic_application_authorized"] is False
    assert proposal["execution_authority"] is False
