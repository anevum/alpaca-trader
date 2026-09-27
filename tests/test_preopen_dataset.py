from datetime import date, timedelta

import pytest

from app.preopen_state.dataset import chronological_split


def rows(count: int) -> list[dict]:
    start = date(2026, 1, 1)
    return [
        {"trade_date": (start + timedelta(days=index)).isoformat(), "value": index}
        for index in range(count)
    ]


def test_chronological_split_preserves_order_and_embargoes_boundaries():
    train, validation, holdout = chronological_split(
        rows(30),
        train_fraction=0.60,
        validation_fraction=0.20,
        embargo_sessions=1,
    )

    assert train[-1]["value"] == 17
    assert validation[0]["value"] == 19
    assert validation[-1]["value"] == 23
    assert holdout[0]["value"] == 25
    assert all(a["value"] < b["value"] for a, b in zip(train, train[1:]))
    assert train[-1]["value"] + 1 < validation[0]["value"]
    assert validation[-1]["value"] + 1 < holdout[0]["value"]


def test_split_rejects_dataset_too_small_for_embargo():
    with pytest.raises(ValueError, match="too small"):
        chronological_split(rows(4), embargo_sessions=1)
