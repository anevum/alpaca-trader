from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.graen.adaptive_hypothesis import build_spec
from graen.crypto.cross_sectional_intraday import (
    _candidate_at,
    build_series,
)
from graen.engineering import compile_bundle, validate_spec


UTC = timezone.utc


def _five_minute_fixture():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = []
    price = 100.0
    for index in range(13):
        price *= 1.003
        rows.append(
            {
                "t": (start + timedelta(minutes=5 * index)).isoformat(),
                "o": price,
                "h": price * 1.006,
                "l": price * 0.994,
                "c": price,
                "v": 1000 + index,
                "n": 100 + index,
            }
        )
    return start, rows


def _one_minute_equivalent(rows):
    output = []
    for bar in rows:
        start = datetime.fromisoformat(bar["t"])
        for offset in range(5):
            output.append(
                {
                    **bar,
                    "t": (start + timedelta(minutes=offset)).isoformat(),
                    "v": float(bar["v"]) / 5.0,
                    "n": float(bar["n"]) / 5.0,
                }
            )
    return output


def test_cross_sectional_v2_compiler_targets_trusted_evaluator():
    now = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)
    spec = build_spec(
        1,
        exposure_artifact_id=str(uuid4()),
        search_history=["v1 exhausted"],
        now=now,
    )

    assert validate_spec(spec)
    assert spec["mechanism"] == "cross_sectional_intraday_v1"
    files = compile_bundle(spec)
    generated = next(
        body
        for path, body in files.items()
        if path.startswith("graen/crypto/generated/")
    )
    assert (
        "from graen.crypto.cross_sectional_intraday import evaluate_stage"
        in generated
    )
    assert "alpaca_client" not in generated
    assert spec["execution_authority"] is False


def test_one_and_five_minute_sources_normalize_to_same_research_grid():
    _, rows = _five_minute_fixture()
    one_minute = _one_minute_equivalent(rows)

    five = build_series({
        "BTC/USD": rows,
        "ETH/USD": rows,
        "SOL/USD": rows,
    })
    one = build_series({
        "BTC/USD": one_minute,
        "ETH/USD": one_minute,
        "SOL/USD": one_minute,
    })

    assert set(five["BTC/USD"]) == set(one["BTC/USD"])
    for at in five["BTC/USD"]:
        assert float(five["BTC/USD"][at]["c"]) == float(
            one["BTC/USD"][at]["c"]
        )


def test_cross_sectional_candidate_uses_only_completed_bar_history():
    start, rows = _five_minute_fixture()
    series = build_series({
        "BTC/USD": rows,
        "ETH/USD": rows,
        "SOL/USD": rows,
    })
    spec = build_spec(
        12,
        exposure_artifact_id=str(uuid4()),
        search_history=["fixture"],
        now=datetime(2026, 10, 6, 8, 0, tzinfo=UTC),
    )
    at = start + timedelta(minutes=65)

    candidate = _candidate_at(series, spec, "BTC/USD", at)

    assert candidate is not None
    assert candidate["return_5m"] > 0
    assert candidate["return_15m"] > 0
    assert candidate["return_60m"] > 0
    assert candidate["expected_gross_move_pct"] > 0
