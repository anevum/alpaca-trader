from decimal import Decimal

from app.research_agent.models import canonical_json, deterministic_dict


def test_canonical_json_serializes_decimal_deterministically():
    payload = {
        "alpha": Decimal("0.00120"),
        "nested": {"beta": Decimal("-2.50")},
    }
    assert deterministic_dict(payload) == {
        "alpha": "0.00120",
        "nested": {"beta": "-2.50"},
    }
    assert canonical_json(payload) == (
        '{"alpha":"0.00120","nested":{"beta":"-2.50"}}'
    )
