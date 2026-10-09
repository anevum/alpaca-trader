from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.member_paper import PaperExecutionKernel, PaperPolicy, PaperSignal


EPOCH = 1_800_000_000


def policy():
    return PaperPolicy(
        symbols=("SPY", "QQQ"), strategy_version="rhen-approved-paper-v1",
        max_positions=2, max_total_exposure_bp=5000,
        max_position_notional_cents=3000, max_order_notional_cents=2000,
    )


def signal(signal_id, symbol="SPY", side="buy", cents=1000, notional=1500, version="rhen-approved-paper-v1", published=EPOCH):
    return PaperSignal(
        signal_id=signal_id, strategy_version=version,
        symbol=symbol, side=side, price_cents=cents,
        published_at=published, requested_notional_cents=notional if side == "buy" else 0,
    )


def test_paper_only_state_is_private_by_member_and_idempotent_across_restarts():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "isolated-paper.sqlite3"
        kernel = PaperExecutionKernel(path)
        kernel.create_paper_account("user-a", 10000, policy())
        kernel.create_paper_account("user-b", 25000, policy())
        scope_a = kernel.verified_scope("user-a")
        scope_b = kernel.verified_scope("user-b")
        with pytest.raises(sqlite3.IntegrityError):
            kernel.create_paper_account("user-a", 1, policy())
        with pytest.raises(LookupError):
            kernel.verified_scope("unknown-user")

        assert scope_a.status()["paused"] is True
        scope_a.resume_new_entries()
        first = scope_a.submit_signal(signal("identical-signal"), now=EPOCH)
        assert first.status == "simulated"
        assert first.cash_delta_cents < 0
        assert scope_b.status()["cashCents"] == 25000
        assert scope_b.status()["receiptCount"] == 0
        assert scope_b.status()["positions"] == []

        repeated = scope_a.submit_signal(signal("identical-signal"), now=EPOCH + 1000)
        assert first == repeated
        assert scope_a.status()["receiptCount"] == 1
        assert scope_b.submit_signal(signal("identical-signal"), now=EPOCH).reason == "paused"
        assert scope_b.status()["receiptCount"] == 1
        assert scope_b.status()["cashCents"] == 25000
        assert scope_a.status()["mode"] == "paper"
        assert scope_a.status()["brokerageConnected"] is False
        assert scope_a.status()["liveExecutionEnabled"] is False
        kernel.close()

        reopened = PaperExecutionKernel(path)
        persisted_a = reopened.verified_scope("user-a")
        assert persisted_a.submit_signal(signal("identical-signal"), now=EPOCH+4000) == first
        assert persisted_a.status()["receiptCount"] == 1
        assert reopened.verified_scope("user-b").status()["positions"] == []
        reopened.close()


def test_stale_unapproved_symbols_strategy_and_pause_never_buy():
    db = PaperExecutionKernel(":memory:")
    db.create_paper_account("one", 10000, policy())
    account = db.verified_scope("one")
    assert account.submit_signal(signal("paused"), now=EPOCH).reason == "paused"
    account.resume_new_entries()
    cases = [
        ("old", signal("old", published=EPOCH-91), "stale_or_future_signal"),
        ("future", signal("future", published=EPOCH+6), "stale_or_future_signal"),
        ("wrong", signal("wrong",version="not-approved"), "unapproved_strategy_version"),
        ("symbol", signal("symbol",symbol="AAPL"), "not_allowed"),
        ("unknown-sell", signal("unknown-sell",side="sell"), "no_long_position"),
    ]
    for _, sample, expected in cases:
        receipt = account.submit_signal(sample, now=EPOCH)
        assert receipt.status == "rejected"
        assert receipt.reason == expected
        assert receipt.cash_delta_cents == 0
    assert account.status()["cashCents"] == 10000
    assert account.status()["positions"] == []
    db.close()


def test_bounded_cash_exposure_and_no_shorting_or_leverage():
    db = PaperExecutionKernel(":memory:")
    db.create_paper_account("member", 10000, policy())
    a = db.verified_scope("member")
    a.resume_new_entries()
    first=a.submit_signal(signal("first",notional=5000), now=EPOCH)
    assert first.status == "simulated"
    assert first.cash_delta_cents == -2000
    second=a.submit_signal(signal("second",symbol="QQQ",notional=5000), now=EPOCH)
    assert second.status == "simulated"
    assert second.cash_delta_cents == -2000
    third=a.submit_signal(signal("third",symbol="SPY",notional=5000), now=EPOCH)
    assert third.status == "simulated"
    assert third.cash_delta_cents == -1000
    fourth=a.submit_signal(signal("fourth",symbol="QQQ",notional=5000), now=EPOCH)
    assert fourth.status == "rejected"
    assert fourth.reason == "risk_budget_exhausted"
    assert a.status()["cashCents"] == 5000
    assert sum(p["cost_basis_cents"] for p in a.status()["positions"]) == 5000
    assert a.status()["positions"][0]["quantity_microshares"] > 0
    assert a.status()["realizedPnLCents"] == 0

    a.pause_new_entries()
    rejected=a.submit_signal(signal("paused-new"), now=EPOCH)
    assert rejected.reason == "paused"
    sold=a.submit_signal(signal("flat-spy",side="sell",cents=1250), now=EPOCH)
    assert sold.status == "simulated"
    assert sold.reason == "simulated_flat_exit"
    assert sold.cash_delta_cents > 0
    assert a.status()["realizedPnLCents"] > 0
    assert a.submit_signal(signal("flat-again",side="sell"), now=EPOCH).reason == "no_long_position"
    assert all(p["symbol"] != "SPY" for p in a.status()["positions"])
    db.close()


def test_reject_invalid_contracts_and_no_account_takeover():
    for args in [
        dict(symbols=("spy",), strategy_version="approved"),
        dict(symbols=("SPY","SPY"), strategy_version="approved"),
        dict(symbols=("SPY",), strategy_version="approved", max_positions=0),
        dict(symbols=("SPY",), strategy_version="approved", max_total_exposure_bp=10001),
        dict(symbols=("SPY",), strategy_version="approved", max_order_notional_cents=True),
        dict(symbols=(), strategy_version="approved"),
    ]:
        with pytest.raises(ValueError):
            PaperPolicy(**args)
    for kwargs in [
        dict(signal_id="x",strategy_version="approved",symbol="SPY",side="short",price_cents=500,published_at=EPOCH),
        dict(signal_id="x",strategy_version="approved",symbol="SPY",side="buy",price_cents=-100,published_at=EPOCH,requested_notional_cents=100),
        dict(signal_id="x",strategy_version="approved",symbol="SPY",side="buy",price_cents=500,published_at=EPOCH,requested_notional_cents=0),
    ]:
        with pytest.raises(ValueError):
            PaperSignal(**kwargs)
    db = PaperExecutionKernel(":memory:")
    with pytest.raises(ValueError):
        db.create_paper_account("bad identity space", 100, policy())
    with pytest.raises(ValueError):
        db.create_paper_account("user", 0, policy())
    db.close()
