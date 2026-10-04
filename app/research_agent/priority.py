from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from .models import QueueItem, ResearchCategory


CATEGORY_BASE = {
    ResearchCategory.OPERATIONAL_DEFECT: 35,
    ResearchCategory.DATA_QUALITY: 30,
    ResearchCategory.STRATEGY_HYPOTHESIS: 20,
    ResearchCategory.RISK_SIZING_OBSERVATION: 10,
    ResearchCategory.NOISE_INSUFFICIENT: 0,
}


def _bounded(name: str, value: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or value > maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")
    return value


def calculate_priority(
    category: ResearchCategory,
    *,
    severity: int,
    recurrence: int,
    research_value: int,
    readiness: int,
    estimated_compute_cost: int,
    estimated_llm_cost: int,
    novelty: int = 0,
    falsifiability: int = 0,
    decision_value: int = 0,
    branch_elimination_value: int = 0,
    data_cost: int = 0,
    statistical_search_cost: int = 0,
) -> int:
    severity = _bounded("severity", severity, 3)
    recurrence = _bounded("recurrence", recurrence, 3)
    research_value = _bounded("research_value", research_value, 3)
    readiness = _bounded("readiness", readiness, 2)
    estimated_compute_cost = _bounded(
        "estimated_compute_cost", estimated_compute_cost, 3
    )
    estimated_llm_cost = _bounded("estimated_llm_cost", estimated_llm_cost, 3)
    novelty = _bounded("novelty", novelty, 3)
    falsifiability = _bounded("falsifiability", falsifiability, 3)
    decision_value = _bounded("decision_value", decision_value, 3)
    branch_elimination_value = _bounded("branch_elimination_value", branch_elimination_value, 3)
    data_cost = _bounded("data_cost", data_cost, 3)
    statistical_search_cost = _bounded("statistical_search_cost", statistical_search_cost, 3)

    score = (
        CATEGORY_BASE[category]
        + 8 * severity
        + 6 * recurrence
        + 5 * research_value
        + 4 * readiness
        - 4 * estimated_compute_cost
        - 2 * estimated_llm_cost
        + 4 * novelty
        + 4 * falsifiability
        + 5 * decision_value
        + 4 * branch_elimination_value
        - 3 * data_cost
        - 4 * statistical_search_cost
    )
    return max(0, min(100, score))


def calculate_information_value(
    *,
    probability_useful: int,
    decision_value: int,
    novelty: int,
    falsifiability: int,
    information_gain: int,
    estimated_compute_cost: int,
    estimated_data_cost: int,
    statistical_search_cost: int,
) -> int:
    """Score the expected value of learning, not expected backtest performance.

    Each benefit input is 0..4. Costs are 0..4. The score is a bounded
    prioritization aid; it never changes statistical gates or promotion authority.
    """
    probability_useful = _bounded("probability_useful", probability_useful, 4)
    decision_value = _bounded("decision_value", decision_value, 4)
    novelty = _bounded("novelty", novelty, 4)
    falsifiability = _bounded("falsifiability", falsifiability, 4)
    information_gain = _bounded("information_gain", information_gain, 4)
    estimated_compute_cost = _bounded("estimated_compute_cost", estimated_compute_cost, 4)
    estimated_data_cost = _bounded("estimated_data_cost", estimated_data_cost, 4)
    statistical_search_cost = _bounded("statistical_search_cost", statistical_search_cost, 4)

    benefit = (
        5 * probability_useful
        + 5 * decision_value
        + 4 * novelty
        + 4 * falsifiability
        + 5 * information_gain
    )
    cost = (
        3 * estimated_compute_cost
        + 3 * estimated_data_cost
        + 4 * statistical_search_cost
    )
    return max(0, min(100, benefit - cost))


def information_value_from_evidence(evidence: object) -> int:
    if not isinstance(evidence, dict):
        return 0
    raw = evidence.get("information_value_inputs")
    if not isinstance(raw, dict):
        return 0

    def value(name: str) -> int:
        candidate = raw.get(name, 0)
        if isinstance(candidate, bool):
            return 0
        try:
            return int(candidate)
        except (TypeError, ValueError):
            return 0

    try:
        return calculate_information_value(
            probability_useful=value("probability_useful"),
            decision_value=value("decision_value"),
            novelty=value("novelty"),
            falsifiability=value("falsifiability"),
            information_gain=value("information_gain"),
            estimated_compute_cost=value("estimated_compute_cost"),
            estimated_data_cost=value("estimated_data_cost"),
            statistical_search_cost=value("statistical_search_cost"),
        )
    except ValueError:
        return 0


def _reviewed_at(item: QueueItem) -> datetime:
    value = item.snapshot.last_reviewed_at
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def queue_sort_key(item: QueueItem) -> tuple[object, ...]:
    return (
        -item.priority_score,
        -information_value_from_evidence(item.snapshot.evidence_summary),
        -int(item.classification.evidence_integrity_blocker),
        -item.severity,
        -item.recurrence,
        _reviewed_at(item),
        item.research_question_id,
    )


def prioritize(items: Iterable[QueueItem]) -> list[QueueItem]:
    return sorted(items, key=queue_sort_key)

