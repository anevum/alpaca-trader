"""Verify paper-only Replay never converts missing outcomes into fake P&L."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from next_rhen.research.outcomes import mature_cycle
from next_rhen.replay.scorecard import score_paper_horizons, ReplayContractError

AT = datetime(2026, 10, 9, 14, 30, tzinfo=timezone.utc)
SHA = "a" * 64
COSTS = {
    "spread_bps": 2.0,
    "entry_slippage_bps": 1.0,
    "exit_slippage_bps": 1.0,
    "roundtrip_fees_bps": 0.5,
}


def ts(offset=0):
    return (AT + timedelta(minutes=offset)).isoformat().replace("+00:00", "Z")


def point(offset, price):
    return {"observed_at": ts(offset), "close": price, "source_sha256": SHA}


def source():
    return {
        "schema_version": "anevum.decision-cycle.v1",
        "workspace_id": "wrk_memberresearch1", "run_id": "paper-replay-run-1",
        "cycle_id": "cycle-0001", "sequence_no": 1,
        "session_date": "2026-10-09", "occurred_at": ts(0),
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "strategy_version": "paper-test", "config_sha256": "b" * 64,
        "code_sha256": "c" * 64,
        "market_source": {
            "feed": "ALPACA_IEX_PAPER", "data_status": "COMPLETE",
            "asof_timestamp": ts(-1), "universe_ref": "d" * 64,
            "bars_ref": "e" * 64, "quotes_ref": "f" * 64,
        },
        "universe_symbols": ["AAPL", "MSFT"],
        "candidates": [
            {"symbol": "AAPL", "decision": "QUALIFIED",
             "reason": "qualified", "observed_at": ts(-1), "features": {}},
            {"symbol": "MSFT", "decision": "REJECTED",
             "reason": "rejected", "observed_at": ts(-1), "features": {}},
        ],
    }


def observations():
    return mature_cycle(source(),
        anchor_prices={"AAPL":point(-1,100), "MSFT":point(-1,200)},
        horizon_prices={
            "AAPL":{"5":point(5,101),"15":point(15,99),"60":point(60,101.5)},
            "MSFT":{"5":point(5,198),"15":point(15,202)},
        },
        evaluated_at=ts(61))


def fingerprint(document):
    core={k:v for k,v in document.items() if k!="content_sha256"}
    return sha256(json.dumps(core,sort_keys=True,separators=(",",":"),
                 allow_nan=False).encode()).hexdigest()


def test_complete_population_cost_model_is_deterministic_without_orders():
    a=score_paper_horizons(observations(),cost_model=COSTS)
    b=score_paper_horizons(observations(),cost_model=COSTS)
    assert a==b
    assert a["evaluation_mode"]=="OFFLINE_PAPER_COUNTERFACTUAL"
    assert a["source_quality"]=="AWAITING_INDEPENDENT_ATTESTATION"
    assert a["modeled_roundtrip_cost_bps"]==4.5
    assert a["net_execution_pnl"] is None
    assert a["broker_order_authority"] is False
    assert a["validated_alpha"] is False
    assert a["promotion_allowed"] is False
    five=a["horizon_summary"]["5"]
    assert five["observed_candidates"]==2
    assert five["missing_candidates"]==0
    assert five["by_recorded_decision"]["QUALIFIED"]==1
    assert five["by_recorded_decision"]["REJECTED"]==1
    assert five["mean_gross_reference_bps"]==0.0
    assert five["mean_net_assumption_bps"]==-4.5
    sixty=a["horizon_summary"]["60"]
    assert sixty["observed_candidates"]==1
    assert sixty["missing_candidates"]==1
    assert sixty["mean_net_assumption_bps"]==145.5
    assert any(x["symbol"]=="MSFT" and x["horizon_minutes"]==60
               and x["hypothetical_net_after_assumed_cost_bps"] is None
               for x in a["hypothetical_marks"])


@pytest.mark.parametrize("overrides",[
    {"spread_bps":-1},{"entry_slippage_bps":float("nan")},
    {"exit_slippage_bps":float("inf")},{"roundtrip_fees_bps":1001},
    {"spread_bps":True},{"roundtrip_fees_bps":"5"},
])
def test_negative_nonnumeric_or_unbounded_costs_are_rejected(overrides):
    with pytest.raises(ReplayContractError,match="cost|numeric|negative|nonfinite"):
        score_paper_horizons(observations(),cost_model={**COSTS,**overrides})


def test_missing_cost_component_is_never_silently_assumed_zero():
    short=dict(COSTS)
    short.pop("spread_bps")
    with pytest.raises(ReplayContractError,match="exact declared"):
        score_paper_horizons(observations(),cost_model=short)
    with pytest.raises(ReplayContractError,match="exact declared"):
        score_paper_horizons(observations(),cost_model={**COSTS,"leverage":5})


def test_modified_returns_cannot_reuse_recorded_source_digest():
    tampered=observations()
    tampered["results"][0]["gross_long_reference_bps"]=999999
    with pytest.raises(ReplayContractError,match="digest mismatch"):
        score_paper_horizons(tampered,cost_model=COSTS)


def test_missing_mark_cannot_be_forged_into_zero_or_validated_alpha():
    tampered=observations()
    target=next(x for x in tampered["results"] if x["symbol"]=="MSFT" and
                x["horizon_minutes"]==60)
    assert target["status"]=="MISSING_HORIZON"
    target["gross_long_reference_bps"]=0
    tampered["content_sha256"]=fingerprint(tampered)
    with pytest.raises(ReplayContractError,match="missing source"):
        score_paper_horizons(tampered,cost_model=COSTS)


def test_replay_does_not_accept_live_mode_or_promotion_even_with_new_digest():
    for key,new in [
        ("execution_mode","LIVE"),("source_quality","PROVEN"),
        ("alpha_validated",True),("strategy_promotion_allowed",True),
        ("orders_or_positions",True),("execution_costs_included",True)
    ]:
        tampered=observations()
        tampered[key]=new
        tampered["content_sha256"]=fingerprint(tampered)
        with pytest.raises(ReplayContractError,match="paper source"):
            score_paper_horizons(tampered,cost_model=COSTS)


def test_horizon_specific_survivor_drop_and_duplicates_are_blocked():
    tampered=observations()
    tampered["results"].pop()
    tampered["content_sha256"]=fingerprint(tampered)
    with pytest.raises(ReplayContractError,match="full candidate horizon grid"):
        score_paper_horizons(tampered,cost_model=COSTS)
    tampered=observations()
    tampered["results"][-1]=deepcopy(tampered["results"][0])
    tampered["content_sha256"]=fingerprint(tampered)
    with pytest.raises(ReplayContractError,match="duplicate or malformed"):
        score_paper_horizons(tampered,cost_model=COSTS)


def test_unverified_price_sources_cannot_claim_broker_equity_curve():
    card=score_paper_horizons(observations(),cost_model=COSTS)
    text=json.dumps(card)
    for field in ['"orders"', '"positions"', '"equity_curve"', '"realized_profit"']:
        assert field not in text
    assert card["source_outcome_sha256"]==observations()["content_sha256"]
