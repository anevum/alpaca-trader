from app.research_agent.classification import classify_structured_evidence
from app.research_agent.models import ResearchCategory


def test_evidence_integrity_defect_outranks_strategy_fields():
    result = classify_structured_evidence(
        {
            "missing_session_count": 1,
            "expectancy": "0.01",
            "profit_factor": "1.2",
        }
    )
    assert result.category is ResearchCategory.DATA_QUALITY
    assert result.evidence_integrity_blocker is True


def test_corpus_quality_failure_is_not_performance_failure():
    result = classify_structured_evidence(
        {
            "survivor_state": "corpus_quality_failed_pre_performance",
            "performance_evaluated": False,
        }
    )
    assert result.category is ResearchCategory.DATA_QUALITY
    assert "CORPUS_QUALITY_FAILED_PRE_PERFORMANCE" in result.reason_codes


def test_explicit_operational_and_strategy_structures_classify_safely():
    operational = classify_structured_evidence({"runtime_error_count": 1})
    strategy = classify_structured_evidence(
        {"strategy_sample_count": 25, "expectancy": "0.02"}
    )
    assert operational.category is ResearchCategory.OPERATIONAL_DEFECT
    assert strategy.category is ResearchCategory.STRATEGY_HYPOTHESIS


def test_ambiguous_evidence_defaults_to_noise_and_review():
    result = classify_structured_evidence({"free_form_note": "maybe"})
    assert result.category is ResearchCategory.NOISE_INSUFFICIENT
    assert result.ambiguous is True
    assert result.requires_semantic_review is True

