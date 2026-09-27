import pytest

from app.research_agent.models import ResearchCategory
from app.research_agent.priority import calculate_priority


def test_exact_priority_formula():
    assert calculate_priority(
        ResearchCategory.STRATEGY_HYPOTHESIS,
        severity=2,
        recurrence=3,
        research_value=2,
        readiness=1,
        estimated_compute_cost=2,
        estimated_llm_cost=1,
    ) == 58


def test_priority_clamps_to_zero_and_one_hundred():
    assert calculate_priority(
        ResearchCategory.OPERATIONAL_DEFECT,
        severity=3,
        recurrence=3,
        research_value=3,
        readiness=2,
        estimated_compute_cost=0,
        estimated_llm_cost=0,
    ) == 100
    assert calculate_priority(
        ResearchCategory.NOISE_INSUFFICIENT,
        severity=0,
        recurrence=0,
        research_value=0,
        readiness=0,
        estimated_compute_cost=3,
        estimated_llm_cost=3,
    ) == 0


def test_priority_rejects_out_of_range_inputs():
    with pytest.raises(ValueError):
        calculate_priority(
            ResearchCategory.DATA_QUALITY,
            severity=4,
            recurrence=0,
            research_value=0,
            readiness=0,
            estimated_compute_cost=0,
            estimated_llm_cost=0,
        )

