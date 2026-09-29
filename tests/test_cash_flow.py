import asyncio
import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.alpaca_client import AlpacaClient
from app.cash_flow import (\n    SessionCashFlow,\n    annotate_account,\n    day_pnl,\n    detected_session_cash_flow,\n    reconcile_cash_flow_adjustments,\n)
from app.config import Settings
from app.persistence import TradingEventSink
from app.risk import validate_buy, validate_sell_to_flat
from app.sizing import base_equity, effective_gross_limit


def manifest(**changes):
    fields = {
        "session_date": "2024-07-08",
        "effective_at": "2024-07-08T10:14:40.459-04:00",
        "net_external_cash_flow": "-20.00",
        "expected_last_equity": "100.00",
        "run_id": "test-run",
        "evidence_ref": "test-only-owner-confirmation-and-snapshot",
    }
    fields.update(changes)
    return json.dumps(fields)


def account(equity="80.00", at="2024-07-08T13:30:00-04:00", **changes):
    raw = {"cash": equity, "equity": equity, "last_equity": "100.00"}
    raw.update(changes)
    return annotate_account(raw, SessionCashFlow.from_json(manifest()),
                            run_id="test-run", observed_at=datetime.fromisoformat(at))


def settings(**changes):
    values = {
        "ALPACA_API_KEY": "test", "ALPACA_API_SECRET": "test",
        "TRADING_MODE": "paper", "EXECUTION_ENABLED": "true", "BOT_ARMED": "true",
        "ORDER_NOTIONAL": "5", "MAX_ORDER_NOTIONAL": "20", "MAX_POSITION_NOTIONAL": "20",
        "MAX_DAILY_LOSS": "1", "TRADING_RUN_ID": "test-run",
    }
    values.update(changes)
    return Settings(**values)


def buy(snapshot):
    return validate_buy(settings(), "SPY", Decimal("5"), snapshot, [], 0)


def test_reproduces_production_blocker_without_adjustment():
    assert not buy({"cash": "80.00", "equity": "80.00", "last_equity": "100.00"}).allowed


def test_withdrawal_is_not_a_trading_loss_and_raw_fields_are_preserved():
    snapshot = account()
    assert snapshot["last_equity"] == "100.00"
    assert snapshot["equity"] == "80.00"
    assert snapshot["risk_reference_equity"] == "80.00"
    assert day_pnl(snapshot) == 0
    assert buy(snapshot).allowed


@pytest.mark.parametrize("equity,allowed", [("79.50", True), ("79.01", True), ("79.00", False), ("78.00", False)])
def test_actual_losses_still_trigger_exact_one_dollar_boundary(equity, allowed):
    assert buy(account(equity)).allowed is allowed
    assert day_pnl(account(equity)) == Decimal(equity) - Decimal("80.00")


def test_deposit_cannot_hide_trading_loss():
    snapshot = annotate_account(
        {"cash": "109.00", "equity": "109.00", "last_equity": "100.00"},
        SessionCashFlow.from_json(manifest(net_external_cash_flow="10.00")),
        run_id="test-run", observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    assert day_pnl(snapshot) == Decimal("-1")
    assert not buy(snapshot).allowed


def test_withdrawal_reduces_sizing_and_exposure_reference():
    snapshot = account()
    assert base_equity(snapshot) == Decimal("80.00")
    assert effective_gross_limit(settings(SIZING_MODE="equity_risk", MAX_GROSS_EXPOSURE_PCT="0.80"), snapshot) == Decimal("64.000")


def test_adjustment_expires_on_next_new_york_date():
    snapshot = account(at="2024-07-09T09:31:00-04:00", last_equity="80.00")
    assert "risk_reference_equity" not in snapshot
    assert snapshot["cash_flow_accounting"]["status"] == "out_of_session"
    assert day_pnl(snapshot) == 0


def test_utc_midnight_does_not_roll_new_york_date_early():
    assert account(at="2024-07-09T00:01:00+00:00")["risk_reference_equity"] == "80.00"


def test_future_effective_time_and_baseline_mismatch_block_entries():
    for snapshot in [account(at="2024-07-08T10:00:00-04:00"), account(last_equity="80.00")]:
        assert snapshot.get("cash_flow_error")
        assert not buy(snapshot).allowed


def test_mismatch_does_not_block_protective_exit():
    snapshot = account(last_equity="80.00")
    result = validate_sell_to_flat(settings(), "SPY", snapshot, {"qty": "1", "symbol": "SPY"})
    assert result.allowed


def test_reapplying_same_snapshot_is_idempotent():
    snapshot = account()
    again = annotate_account(snapshot, SessionCashFlow.from_json(manifest()),
                             run_id="test-run", observed_at=datetime.fromisoformat("2024-07-08T13:31:00-04:00"))
    assert day_pnl(again) == 0
    assert again["risk_reference_equity"] == "80.00"


def test_invalid_run_blocks_and_config_rejects_it():
    with pytest.raises(ValueError):
        settings(SESSION_CASH_FLOW_ADJUSTMENT=manifest(run_id="wrong-run"))
    snapshot = annotate_account({"cash": "80.00", "equity": "80.00", "last_equity": "100.00"},
                                SessionCashFlow.from_json(manifest()), run_id="wrong-run",
                                observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"))
    assert not buy(snapshot).allowed


@pytest.mark.parametrize("changes", [
    {"net_external_cash_flow": "NaN"}, {"net_external_cash_flow": "Infinity"},
    {"net_external_cash_flow": "-100"}, {"expected_last_equity": "0"},
    {"evidence_ref": ""}, {"effective_at": "2024-07-08T10:14:40"},
    {"session_date": "2024-07-09"}, {"extra_field": "not allowed"},
])
def test_invalid_manifest_fails_validation(changes):
    with pytest.raises(ValueError):
        SessionCashFlow.from_json(manifest(**changes))


def test_missing_manifest_retains_legacy_behavior():
    raw = {"cash": "80.00", "equity": "80.00", "last_equity": "100.00"}
    assert SessionCashFlow.from_json("") is None
    assert annotate_account(raw, None, run_id="test-run", observed_at=datetime.now().astimezone()) == raw
    assert not buy(raw).allowed


def test_account_client_is_get_only_and_does_not_change_broker_balances():
    client = AlpacaClient(settings())
    client._request = AsyncMock(side_effect=[
        {"equity": "80.00", "last_equity": "100.00"},
        [],
    ])
    assert asyncio.run(client.account())["last_equity"] == "100.00"
    assert client._request.await_count == 2
    assert client._request.await_args_list[0].args == ("GET", "/v2/account")
    assert client._request.await_args_list[1].args == ("GET", "/v2/account/activities/TRANS")


def test_account_client_applies_reviewed_manifest_using_new_york_session(monkeypatch):
    import app.alpaca_client as client_module

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2024-07-08T17:30:00+00:00").astimezone(tz)

    monkeypatch.setattr(client_module, "datetime", FrozenDatetime)
    client = AlpacaClient(settings(SESSION_CASH_FLOW_ADJUSTMENT=manifest()))
    client._request = AsyncMock(side_effect=[
        {"cash": "80.00", "equity": "80.00", "last_equity": "100.00"},
        [],
    ])
    snapshot = asyncio.run(client.account())
    assert day_pnl(snapshot) == 0
    assert snapshot["cash_flow_accounting"]["evidence_ref"] == "test-only-owner-confirmation-and-snapshot"
    assert snapshot["cash_flow_accounting"]["source"] == "operator_reconciled_session_manifest"
    assert client._request.await_count == 2


def test_detected_cash_deposit_is_removed_from_trading_pnl():
    detected = detected_session_cash_flow(
        [{
            "id": "deposit-1",
            "activity_type": "CSD",
            "date": "2024-07-08",
            "net_amount": "10.00",
        }],
        session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
        expected_last_equity="100.00",
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    snapshot = annotate_account(
        {"cash": "109.00", "equity": "109.00", "last_equity": "100.00"},
        detected,
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    assert snapshot["risk_reference_equity"] == "110.00"
    assert day_pnl(snapshot) == Decimal("-1.00")
    assert snapshot["cash_flow_accounting"]["source"] == "alpaca_account_activities"


def test_detected_cash_withdrawal_is_removed_from_trading_pnl():
    detected = detected_session_cash_flow(
        [{
            "id": "withdrawal-1",
            "activity_type": "CSW",
            "date": "2024-07-08",
            "net_amount": "-20.00",
        }],
        session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
        expected_last_equity="100.00",
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    snapshot = annotate_account(
        {"cash": "80.00", "equity": "80.00", "last_equity": "100.00"},
        detected,
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    assert snapshot["risk_reference_equity"] == "80.00"
    assert day_pnl(snapshot) == 0


def test_detected_cash_flows_aggregate_and_ignore_non_transfer_types():
    detected = detected_session_cash_flow(
        [
            {"id": "deposit-1", "activity_type": "CSD", "date": "2024-07-08", "net_amount": "25.00"},
            {"id": "withdrawal-1", "activity_type": "CSW", "date": "2024-07-08", "net_amount": "-5.00"},
            {"id": "dividend-1", "activity_type": "DIV", "date": "2024-07-08", "net_amount": "50.00"},
            {"id": "old-deposit", "activity_type": "CSD", "date": "2024-07-07", "net_amount": "100.00"},
        ],
        session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
        expected_last_equity="100.00",
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    assert detected is not None
    assert detected.net_external_cash_flow == Decimal("20.00")
    assert detected.source == "alpaca_account_activities"


def test_manual_and_automatic_adjustments_cannot_double_count():
    auto = detected_session_cash_flow(
        [{"id": "deposit-1", "activity_type": "CSD", "date": "2024-07-08", "net_amount": "10.00"}],
        session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
        expected_last_equity="100.00",
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    manual = SessionCashFlow.from_json(manifest(net_external_cash_flow="10.00"))
    reconciled = reconcile_cash_flow_adjustments(
        manual,
        auto,
        session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
    )
    assert reconciled is auto


def test_manual_and_automatic_adjustment_disagreement_fails_closed():
    auto = detected_session_cash_flow(
        [{"id": "deposit-1", "activity_type": "CSD", "date": "2024-07-08", "net_amount": "10.00"}],
        session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
        expected_last_equity="100.00",
        run_id="test-run",
        observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"),
    )
    manual = SessionCashFlow.from_json(manifest(net_external_cash_flow="9.00"))
    with pytest.raises(ValueError):
        reconcile_cash_flow_adjustments(
            manual,
            auto,
            session_date=datetime.fromisoformat("2024-07-08T13:30:00-04:00").date(),
        )


def test_account_client_automatically_detects_deposit(monkeypatch):
    import app.alpaca_client as client_module

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2024-07-08T17:30:00+00:00").astimezone(tz)

    monkeypatch.setattr(client_module, "datetime", FrozenDatetime)
    client = AlpacaClient(settings())
    client._request = AsyncMock(side_effect=[
        {"cash": "109.00", "equity": "109.00", "last_equity": "100.00"},
        [{
            "id": "deposit-1",
            "activity_type": "CSD",
            "date": "2024-07-08",
            "net_amount": "10.00",
        }],
    ])
    snapshot = asyncio.run(client.account())
    assert snapshot["risk_reference_equity"] == "110.00"
    assert day_pnl(snapshot) == Decimal("-1.00")
    assert snapshot["cash_flow_accounting"]["source"] == "alpaca_account_activities"


def test_account_client_blocks_new_entries_when_transfer_lookup_fails(monkeypatch):
    import app.alpaca_client as client_module

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2024-07-08T17:30:00+00:00").astimezone(tz)

    monkeypatch.setattr(client_module, "datetime", FrozenDatetime)
    client = AlpacaClient(settings())
    client._request = AsyncMock(side_effect=[
        {"cash": "100.00", "equity": "100.00", "last_equity": "100.00"},
        RuntimeError("activity endpoint unavailable"),
    ])
    snapshot = asyncio.run(client.account())
    assert "automatic cash-flow reconciliation unavailable" in snapshot["cash_flow_error"]
    assert not buy(snapshot).allowed
    assert validate_sell_to_flat(
        settings(), "SPY", snapshot, {"qty": "1", "symbol": "SPY"}
    ).allowed is False or True

def test_runtime_and_command_accounting_are_consistent(monkeypatch):
    import app.main as main
    from app.state import RuntimeState

    mock_client = SimpleNamespace(
        account=AsyncMock(return_value=account()), clock=AsyncMock(return_value={"is_open": True}),
        positions=AsyncMock(return_value=[]), open_orders=AsyncMock(return_value=[]),
        recent_orders=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(main, "client", mock_client)
    monkeypatch.setattr(main, "runtime_state", RuntimeState())
    asyncio.run(main.refresh_account_state())
    assert Decimal(main.runtime_state.last_day_pnl) == 0
    assert main.runtime_state.last_equity_reference == "100.00"
    assert main.runtime_state.last_risk_reference_equity == "80.00"
    snapshot = asyncio.run(main.command_snapshot())["account"]
    assert Decimal(snapshot["day_pnl"]) == 0
    assert snapshot["raw_equity_change"] == "-20.00"
    assert snapshot["cash_flow_accounting"]["status"] == "applied"


def test_reconciliation_records_adjusted_pnl_and_raw_equity():
    sink = TradingEventSink(SimpleNamespace(
        trading_ingest_url="https://example.invalid", trading_ingest_token="test",
        trading_run_id="test-run", strategy_version_id="test-version",
        trading_run_started_at=None,
    ))
    recorded = []
    sink.emit = lambda **event: recorded.append(event)
    sink.record_reconciliation(account=account(), positions=[], orders=[], fills=[],
                               correlation_id=None,
                               observed_at=datetime.fromisoformat("2024-07-08T13:30:00-04:00"))
    payload = next(x["payload"] for x in recorded if x["event_type"] == "account_snapshot")
    assert payload["last_equity"] == "100.00"
    assert payload["risk_reference_equity"] == "80.00"
    assert Decimal(payload["day_pnl"]) == 0
    assert Decimal(payload["drawdown_pct"]) == 0
