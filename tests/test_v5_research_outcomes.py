"""Offline RHEN V5 Research outcome maturation; no order path or broker data."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from next_rhen.research.outcomes import (
    OutcomeContractError, mature_cycle, WINDOWS, SCHEMA,
)

W = "wrk_memberresearch1"
AT = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
SHA = "a" * 64


def iso(timestamp):
    return timestamp.isoformat().replace("+00:00", "Z")


def cycle():
    return {
        "schema_version": "anevum.decision-cycle.v1",
        "workspace_id": W,
        "run_id": "paper-outcome-run-1",
        "cycle_id": "cycle-00001",
        "sequence_no": 1,
        "session_date": "2026-10-09",
        "occurred_at": iso(AT),
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "strategy_version": "rhen-next-paper-test-1",
        "config_sha256": "b" * 64,
        "code_sha256": "c" * 64,
        "market_source": {
            "feed": "ALPACA_IEX_PAPER",
            "data_status": "COMPLETE",
            "asof_timestamp": iso(AT - timedelta(minutes=1)),
            "universe_ref": "d" * 64,
            "bars_ref": "e" * 64,
            "quotes_ref": "f" * 64,
        },
        "universe_symbols": ["AAPL", "MSFT", "TSLA"],
        "candidates": [
            {"symbol": "AAPL", "decision": "QUALIFIED",
             "reason": "passes_rule", "observed_at": iso(AT), "features": {"x": 1}},
            {"symbol": "MSFT", "decision": "REJECTED",
             "reason": "fails_rule", "observed_at": iso(AT), "features": {"x": 2}},
            {"symbol": "TSLA", "decision": "UNMEASURABLE",
             "reason": "no_quote", "observed_at": iso(AT), "features": {"x": None}},
        ],
    }


def point(minutes, close, ref=SHA):
    return {"observed_at": iso(AT + timedelta(minutes=minutes)),
            "close": close, "source_sha256": ref}


def input_prices():
    anchors = {"AAPL": point(-1, 100.0), "MSFT": point(-1, 200.0)}
    futures = {
        "AAPL": {"5": point(5, 101.0), "15": point(15, 99.0),
                 "60": point(60, 101.5)},
        "MSFT": {"5": point(5, 198.0), "15": point(15, 201.0),
                 "60": point(60, 202.0)},
    }
    return anchors, futures


def run(at=AT + timedelta(minutes=61), *, anchors=None, futures=None, source=None):
    default_anchors, default_futures = input_prices()
    return mature_cycle(
        source or cycle(),
        anchor_prices=default_anchors if anchors is None else anchors,
        horizon_prices=default_futures if futures is None else futures,
        evaluated_at=iso(at),
    )


def test_complete_horizons_are_counterfactual_research_not_trading_pnl():
    record = run()
    assert record["schema_version"] == SCHEMA
    assert record["horizons"] == list(WINDOWS)
    assert record["candidate_count"] == 3
    assert len(record["results"]) == 9
    assert record["status_counts"] == {
        "OBSERVED_UNATTESTED": 6, "NOT_YET_DUE": 0,
        "MISSING_ANCHOR": 3, "MISSING_HORIZON": 0,
    }
    by_key = {(r["symbol"], r["horizon_minutes"]): r for r in record["results"]}
    assert by_key["AAPL", 5]["gross_long_reference_bps"] == 100.0
    assert by_key["AAPL", 15]["gross_long_reference_bps"] == -100.0
    assert by_key["MSFT", 5]["gross_long_reference_bps"] == -100.0
    assert by_key["MSFT", 15]["gross_long_reference_bps"] == 50.0
    assert by_key["MSFT", 5]["recorded_decision"] == "REJECTED"
    assert by_key["TSLA", 60]["gross_long_reference_bps"] is None
    assert record["execution_mode"] == "PAPER_RESEARCH_ONLY"
    assert record["source_quality"] == "AWAITING_INDEPENDENT_ATTESTATION"
    assert record["alpha_validated"] is False
    assert record["execution_costs_included"] is False
    assert record["orders_or_positions"] is False
    assert record["strategy_promotion_allowed"] is False
    assert len(record["content_sha256"]) == 64


def test_early_replay_marks_not_due_and_does_not_invent_zero_return():
    anchors, futures = input_prices()
    futures = {"AAPL": {"5": futures["AAPL"]["5"]}}
    output = run(AT + timedelta(minutes=6), anchors=anchors, futures=futures)
    by_key = {(r["symbol"], r["horizon_minutes"]): r for r in output["results"]}
    assert by_key["AAPL", 5]["status"] == "OBSERVED_UNATTESTED"
    assert by_key["AAPL", 15]["status"] == "NOT_YET_DUE"
    assert by_key["MSFT", 5]["status"] == "MISSING_HORIZON"
    assert by_key["MSFT", 60]["status"] == "NOT_YET_DUE"
    assert by_key["TSLA", 5]["status"] == "MISSING_ANCHOR"
    for value in output["results"]:
        if value["status"] != "OBSERVED_UNATTESTED":
            assert value["gross_long_reference_bps"] is None


def test_identical_replay_has_identical_content_digest_without_network():
    result1 = run()
    result2 = run()
    assert result1 == result2
    assert len(result1["decision_cycle_sha256"]) == 64
    assert "account" not in result1
    assert "orders" not in result1


@pytest.mark.parametrize("value", [
    0, -1, True, float("nan"), float("inf"), "100", 10 ** 13
])
def test_invalid_prices_are_rejected(value):
    anchors, futures = input_prices()
    anchors["AAPL"]["close"] = value
    with pytest.raises(OutcomeContractError, match="price|numeric"):
        run(anchors=anchors, futures=futures)


@pytest.mark.parametrize("stamp", [
    AT + timedelta(seconds=1),
    AT - timedelta(minutes=4),
])
def test_future_or_stale_anchor_rejected(stamp):
    anchors, futures = input_prices()
    anchors["AAPL"]["observed_at"] = iso(stamp)
    with pytest.raises(OutcomeContractError, match="anchor timestamp"):
        run(anchors=anchors, futures=futures)


def test_wrong_horizon_mislabeled_or_future_unobserved_is_refused():
    anchors, futures = input_prices()
    futures["AAPL"]["5"]["observed_at"] = iso(AT + timedelta(minutes=6))
    with pytest.raises(OutcomeContractError, match="exact matured horizon"):
        run(anchors=anchors, futures=futures)
    anchors, futures = input_prices()
    with pytest.raises(OutcomeContractError, match="not yet observable"):
        run(AT + timedelta(minutes=7), anchors=anchors, futures=futures)


def test_foreign_symbol_and_unrecognized_horizon_are_forbidden():
    anchors, futures = input_prices()
    anchors["SPY"] = point(-1, 2.0)
    with pytest.raises(OutcomeContractError, match="outside recorded"):
        run(anchors=anchors, futures=futures)
    anchors, futures = input_prices()
    futures["MSFT"]["7"] = point(7, 199.0)
    with pytest.raises(OutcomeContractError, match="horizon set"):
        run(anchors=anchors, futures=futures)


def test_invalid_source_sha_and_untrusted_fields_are_rejected():
    anchors, futures = input_prices()
    futures["AAPL"]["15"]["source_sha256"] = "not-a-digest"
    with pytest.raises(OutcomeContractError, match="SHA"):
        run(anchors=anchors, futures=futures)
    anchors, futures = input_prices()
    futures["AAPL"]["15"]["broker_access_token"] = "not-a-real-token"
    with pytest.raises(OutcomeContractError, match="exact source fields"):
        run(anchors=anchors, futures=futures)


def test_tampered_cycle_and_future_eval_state_are_denied():
    corrupted = cycle()
    corrupted["candidates"].pop()
    with pytest.raises(OutcomeContractError, match="cycle contract"):
        run(source=corrupted)
    with pytest.raises(OutcomeContractError, match="evaluation timestamp"):
        run(AT - timedelta(seconds=1))


def test_unmeasurable_candidate_can_have_reference_return_without_trade():
    anchors, futures = input_prices()
    anchors["TSLA"] = point(-1, 250.0)
    futures["TSLA"] = {"5": point(5, 250.0)}
    output = run(anchors=anchors, futures=futures)
    tsla = [x for x in output["results"] if x["symbol"] == "TSLA"]
    assert tsla[0]["status"] == "OBSERVED_UNATTESTED"
    assert tsla[0]["recorded_decision"] == "UNMEASURABLE"
    assert tsla[0]["gross_long_reference_bps"] == 0
    assert tsla[1]["status"] == "MISSING_HORIZON"
