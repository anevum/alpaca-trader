from app.research_agent.parameter_pressure import compute_parameter_pressure


def report(value=None, *, valid=True):
    selected = None
    if value is not None:
        selected = {
            "requested_value": value,
            "validity_passed": valid,
        }
    return {
        "counterfactual_lab": {
            "rolling_searches": {
                "min_momentum_pct": {
                    "selected": selected,
                }
            }
        }
    }


def test_parameter_pressure_collects_until_five_valid_observations():
    result = compute_parameter_pressure([
        report("0.0005"),
        report("0.0005"),
        report("0.0005"),
        report("0.0005"),
    ])
    row = next(
        item for item in result["parameters"]
        if item["parameter"] == "min_momentum_pct"
    )
    assert row["status"] == "COLLECTING"
    assert row["observations"] == 4


def test_repeated_lower_boundary_pressure_becomes_degraded():
    result = compute_parameter_pressure([
        report("0.0005") for _ in range(6)
    ])
    row = next(
        item for item in result["parameters"]
        if item["parameter"] == "min_momentum_pct"
    )
    assert row["status"] == "DEGRADED"
    assert row["dominant_boundary"] == "LOWER"
    assert row["boundary_fraction"] == "1"


def test_invalid_counterfactuals_do_not_create_pressure():
    result = compute_parameter_pressure([
        report("0.0005", valid=False) for _ in range(10)
    ])
    row = next(
        item for item in result["parameters"]
        if item["parameter"] == "min_momentum_pct"
    )
    assert row["observations"] == 0
    assert row["status"] == "COLLECTING"
