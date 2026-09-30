from datetime import datetime, timezone

import pytest

from graen.crypto.research_v7 import (
    METHODOLOGY_VERSION,
    candidate_specs,
    run_crypto_research_v7,
)


UTC = timezone.utc


def test_v7_search_batch_is_distinct_and_bounded():
    specs = candidate_specs()
    families = {row.family for row in specs}
    assert METHODOLOGY_VERSION == "graen-crypto-native-v7"
    assert len(specs) == 13
    assert families == {
        "cross_asset_lead_lag_response",
        "broad_market_laggard_response",
        "calendar_regime_drift",
    }
    assert all(row.hold_minutes in {60, 90, 120} for row in specs)
    assert all(row.scan_minutes in {15, 30} for row in specs)


def test_v7_empty_corpus_rejects_without_opening_holdout_or_execution():
    result = run_crypto_research_v7(
        bars_by_symbol={},
        development_start=datetime(2025, 1, 1, tzinfo=UTC),
        validation_start=datetime(2025, 1, 3, tzinfo=UTC),
        holdout_start=datetime(2025, 1, 4, tzinfo=UTC),
        holdout_end=datetime(2025, 1, 5, tzinfo=UTC),
        corpus_provenance_verified=True,
        previously_inspected_ranges=(),
    )
    assert result["status"] == "NO_VALIDATION_SURVIVOR"
    assert result["decision"] == "CONTINUE_RESEARCH"
    assert result["holdout"]["opened"] is False
    assert result["execution_authority"] is False
    assert result["broker_orders_possible"] is False
    assert result["risk_or_sizing_authority"] is False
    assert result["production_promotion_authority"] is False
    assert result["production_state_changed"] is False
    assert result["model_invoked"] is False


def test_v7_fails_closed_without_provenance_or_on_overlap():
    start = datetime(2025, 1, 1, tzinfo=UTC)
    validation = datetime(2025, 1, 3, tzinfo=UTC)
    holdout = datetime(2025, 1, 4, tzinfo=UTC)
    end = datetime(2025, 1, 5, tzinfo=UTC)

    with pytest.raises(ValueError, match="provenance"):
        run_crypto_research_v7(
            bars_by_symbol={},
            development_start=start,
            validation_start=validation,
            holdout_start=holdout,
            holdout_end=end,
        )

    with pytest.raises(ValueError, match="overlaps"):
        run_crypto_research_v7(
            bars_by_symbol={},
            development_start=start,
            validation_start=validation,
            holdout_start=holdout,
            holdout_end=end,
            corpus_provenance_verified=True,
            previously_inspected_ranges=[
                {
                    "id": "prior",
                    "start": "2025-01-02T00:00:00+00:00",
                    "end": "2025-01-02T12:00:00+00:00",
                }
            ],
        )
