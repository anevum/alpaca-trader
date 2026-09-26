import json
from datetime import date
from pathlib import Path

import pytest

from app.edge_corpus import (
    CorpusManifest,
    CorpusWindow,
    _coverage,
    load_manifest,
    manifest_sha256,
    validate_manifest,
    windows_for_roles,
)


def write_manifest(tmp_path: Path, payload):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def minimal_payload():
    return {
        "version": "test-v1",
        "created_at": "2026-09-25",
        "data_feed": "iex",
        "timeframe": "1Min",
        "candidate_symbols": ["AAPL", "MSFT"],
        "confirmation_symbols": ["SPY"],
        "windows": [
            {
                "id": "dev",
                "start": "2026-01-05",
                "end": "2026-01-09",
                "role": "development",
            },
            {
                "id": "val",
                "start": "2026-02-02",
                "end": "2026-02-06",
                "role": "validation",
            },
            {
                "id": "holdout",
                "start": "2026-03-02",
                "end": "2026-03-06",
                "role": "holdout",
            },
        ],
        "notes": [],
    }


def test_manifest_requires_chronological_roles(tmp_path):
    payload = minimal_payload()
    payload["windows"][0]["role"] = "validation"
    payload["windows"][1]["role"] = "development"

    with pytest.raises(ValueError, match="chronological"):
        load_manifest(write_manifest(tmp_path, payload))


def test_manifest_rejects_overlapping_windows(tmp_path):
    payload = minimal_payload()
    payload["windows"][1]["start"] = "2026-01-09"
    payload["windows"][1]["end"] = "2026-01-13"

    with pytest.raises(ValueError, match="overlap"):
        load_manifest(write_manifest(tmp_path, payload))


def test_manifest_hash_is_deterministic(tmp_path):
    path = write_manifest(tmp_path, minimal_payload())
    first = load_manifest(path)
    second = load_manifest(path)

    assert manifest_sha256(first) == manifest_sha256(second)


def test_window_roles_can_be_selected(tmp_path):
    manifest = load_manifest(write_manifest(tmp_path, minimal_payload()))
    selected = windows_for_roles(
        manifest,
        {"development", "validation"},
    )

    assert [window.window_id for window in selected] == ["dev", "val"]
    assert all(window.role != "holdout" for window in selected)


def test_window_fetch_limit_is_enforced():
    manifest = CorpusManifest(
        version="test",
        created_at="2026-09-25",
        data_feed="iex",
        timeframe="1Min",
        candidate_symbols=("AAPL",),
        confirmation_symbols=("SPY",),
        windows=(
            CorpusWindow(
                window_id="too-long",
                start=date(2026, 1, 1),
                end=date(2026, 1, 31),
                role="development",
            ),
            CorpusWindow(
                window_id="val",
                start=date(2026, 2, 2),
                end=date(2026, 2, 6),
                role="validation",
            ),
            CorpusWindow(
                window_id="holdout",
                start=date(2026, 3, 2),
                end=date(2026, 3, 6),
                role="holdout",
            ),
        ),
        notes=(),
    )

    with pytest.raises(ValueError, match="21-calendar-day"):
        validate_manifest(manifest)


def test_sparse_iex_density_does_not_mean_incomplete_history():
    expected = (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
    )
    bars = {
        "AAPL": [
            {"t": "2026-01-05T14:30:00Z"},
            {"t": "2026-01-05T14:31:00Z"},
            {"t": "2026-01-06T14:30:00Z"},
            {"t": "2026-01-06T14:31:00Z"},
            {"t": "2026-01-07T14:30:00Z"},
            {"t": "2026-01-07T14:31:00Z"},
        ],
        "MSFT": [
            {"t": "2026-01-05T14:30:00Z"},
            {"t": "2026-01-06T14:30:00Z"},
            {"t": "2026-01-07T14:30:00Z"},
        ],
        "SPY": [
            {"t": "2026-01-05T14:30:00Z"},
            {"t": "2026-01-06T14:30:00Z"},
            {"t": "2026-01-07T14:30:00Z"},
        ],
    }

    result = _coverage(
        bars,
        candidate_symbols=("AAPL", "MSFT"),
        confirmation_symbols=("SPY",),
        expected_sessions=expected,
    )

    assert result["iex_bar_density_ratio"]["MSFT"] == 0.5
    assert result["coverage_ratio"]["MSFT"] == 1.0
    assert result["eligible_candidate_symbols"] == ["AAPL", "MSFT"]


def test_missing_regular_session_is_incomplete_even_with_high_bar_count():
    expected = (
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
    )
    bars = {
        "AAPL": [
            {"t": "2026-01-05T14:30:00Z"},
            {"t": "2026-01-06T14:30:00Z"},
            {"t": "2026-01-07T14:30:00Z"},
        ],
        "MSFT": [
            {"t": "2026-01-05T14:30:00Z"},
            {"t": "2026-01-05T14:31:00Z"},
            {"t": "2026-01-06T14:30:00Z"},
            {"t": "2026-01-06T14:31:00Z"},
            {"t": "2026-01-07T02:00:00Z"},
        ],
        "SPY": [
            {"t": "2026-01-05T14:30:00Z"},
            {"t": "2026-01-06T14:30:00Z"},
            {"t": "2026-01-07T14:30:00Z"},
        ],
    }

    result = _coverage(
        bars,
        candidate_symbols=("AAPL", "MSFT"),
        confirmation_symbols=("SPY",),
        expected_sessions=expected,
    )

    assert result["iex_bar_density_ratio"]["MSFT"] > 1.0
    assert result["coverage_ratio"]["MSFT"] == 0.666667
    assert result["missing_sessions"]["MSFT"] == ["2026-01-07"]
    assert result["eligible_candidate_symbols"] == ["AAPL"]
