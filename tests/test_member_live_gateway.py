from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.member_live import (
    AlpacaConnectLiveBroker, BrokerObservation, LiveAuthority, LiveIntent,
    LiveMemberGateway, LiveOrderDenied, LiveOrderJournal, LivePolicy,
    LiveRelease, MemberBinding, validate_live_intent,
)

NOW = 1_800_000_000
TOKEN = "test-only-fake-member-oauth-bearer-token-do-not-use"
SHA = "a" * 64


def binding(member="member-a", connection="live-connection-a", account="broker-a"):
    return MemberBinding(member, connection, account, "live")


def authority(**kwargs):
    required = dict(
        provider_live_approved=True, regulatory_review_complete=True,
        security_review_complete=True, operator_release_approved=True,
        deployment_live_enabled=True, broker_grant_valid=True,
        member_live_consent_current=True, session_verified=True,
        member_armed=True, scopes=("trading",),
    )
    required.update(kwargs)
    return LiveAuthority(**required)


def release():
    return LiveRelease("rhen-member-live-v1", SHA, ("SPY", "QQQ"))


def policy():
    return LivePolicy(
        max_order_notional_cents=10_000,
        max_gross_exposure_bp=3000,
        max_positions=2, max_daily_loss_cents=1_000,
        max_symbol_exposure_cents=10_000,
    )


def intent(id="decision-1", **kwargs):
    attrs = dict(
        signal_id=id, strategy_version="rhen-member-live-v1", symbol="SPY",
        side="buy", quantity=2, limit_price_cents=1000, observed_at=NOW,
    )
    attrs.update(kwargs)
    return LiveIntent(**attrs)


def observation(**kwargs):
    attrs = dict(
        broker_account_id="broker-a", snapshot_at=NOW, quote_at=NOW,
        market_open=True, tradable_us_equity=True,
        bid_cents=999, ask_cents=1000,
        equity_cents=100_000, buying_power_cents=50_000,
        gross_exposure_cents=0, pending_buy_exposure_cents=0,
        daily_realized_loss_cents=0, open_position_count=0,
        held_shares=0, pending_sell_shares=0, pending_new_positions=0,
        symbol_exposure_cents=0, symbol_pending_buy_exposure_cents=0,
    )
    attrs.update(kwargs)
    return BrokerObservation(**attrs)


def fake_broker(bound=None, **options):
    bound = bound or binding()
    calls = []
    def fake_transport(method, path, payload, token):
        assert token == TOKEN
        calls.append((method, path, payload))
        if method == "GET" and path == "/v2/account":
            return dict(
                id=options.get("account_id", bound.broker_account_id), status="ACTIVE",
                trading_blocked=False, account_blocked=False,
            )
        if method == "POST" and path == "/v2/orders":
            if options.get("timeout"):
                raise TimeoutError("Ambiguous simulated request timeout")
            return {
                "id": "broker-order-1", "client_order_id": payload["client_order_id"],
                "symbol": payload["symbol"], "side": payload["side"], "status": "accepted",
            }
        if method == "GET" and path.startswith("/v2/orders:by_client_order_id?"):
            return {
                "id": "broker-order-1", "client_order_id": path.split("=", 1)[1],
                "status": "accepted"
            }
        raise AssertionError(f"Unexpected broker operation: {method} {path}")
    broker = AlpacaConnectLiveBroker(
        bound, lambda connection: TOKEN if connection == bound.connection_id else "",
        transport=fake_transport,
    )
    return broker, calls


def test_gateway_defaults_off_even_with_all_upstream_grants():
    with TemporaryDirectory() as temp:
        journal = LiveOrderJournal(Path(temp) / "member-live.sqlite3")
        broker, calls = fake_broker()
        service = LiveMemberGateway(journal)
        with pytest.raises(LiveOrderDenied, match="OFF"):
            service.submit(binding(), authority(), release(), policy(), intent(),
                           observation(), broker, now=NOW)
        assert not calls
        assert journal.list_owned(binding()) == ()
        journal.close()


def test_every_independent_approval_is_required_before_any_broker_call():
    gates = (
        "provider_live_approved", "regulatory_review_complete",
        "security_review_complete", "operator_release_approved",
        "deployment_live_enabled", "broker_grant_valid",
        "member_live_consent_current", "session_verified",
        "member_armed",
    )
    with TemporaryDirectory() as temp:
        journal = LiveOrderJournal(Path(temp) / "isolated.sqlite3")
        service = LiveMemberGateway(journal, network_writes_enabled=True)
        broker, calls = fake_broker()
        for index, gate in enumerate(gates):
            with pytest.raises(LiveOrderDenied):
                service.submit(binding(), authority(**{gate: False}), release(), policy(),
                               intent(f"approval-{index}"), observation(), broker, now=NOW)
        for invalid in (
            authority(scopes=()), authority(scopes=("account:write",)),
            authority(revoked=True), authority(member_armed="true"), authority(scopes="trading"),
        ):
            with pytest.raises(LiveOrderDenied):
                service.submit(binding(), invalid, release(), policy(),
                               intent("invalid-" + str(len(calls))), observation(), broker, now=NOW)
        assert not calls
        assert journal.list_owned(binding()) == ()
        journal.close()


def test_bounded_live_buy_and_owner_separation_rules():
    b = binding()
    a = authority()
    r = release()
    p = policy()
    sample = intent()
    quote = observation()
    validate_live_intent(b, a, r, p, sample, quote, now=NOW)
    variants = [
        (sample, observation(broker_account_id="some-other-broker")),
        (sample, observation(snapshot_at=NOW - 11)),
        (sample, observation(quote_at=NOW - 6)),
        (sample, observation(market_open=False)),
        (sample, observation(tradable_us_equity=False)),
        (sample, observation(bid_cents=1000, ask_cents=1050)),
        (sample, observation(buying_power_cents=100)),
        (sample, observation(equity_cents=2000)),
        (sample, observation(daily_realized_loss_cents=1000)),
        (sample, observation(open_position_count=2)),
        (sample, observation(open_position_count=1, pending_new_positions=1)),
        (sample, observation(gross_exposure_cents=29_000)),
        (sample, observation(pending_buy_exposure_cents=29_000)),
        (sample, observation(symbol_exposure_cents=9_500)),
        (sample, observation(symbol_pending_buy_exposure_cents=9_500)),
        (replace(sample, quantity=100), quote),
        (replace(sample, limit_price_cents=2000), quote),
        (replace(sample, observed_at=NOW - 31), quote),
        (replace(sample, strategy_version="not-approved"), quote),
        (replace(sample, symbol="TSLA"), quote),
    ]
    for trade, snapshot in variants:
        with pytest.raises(LiveOrderDenied):
            validate_live_intent(b, a, r, p, trade, snapshot, now=NOW)
    with pytest.raises(LiveOrderDenied):
        MemberBinding("member-a", "conn", "broker-a", "live", owner_account=True)
    with pytest.raises(LiveOrderDenied):
        MemberBinding("member-a", "conn", "broker-a", "paper")


def test_pausing_blocks_new_buys_but_not_bounded_long_exits():
    stopped = authority(member_armed=False)
    sold = intent("exit", side="sell", quantity=3)
    snapshot = observation(held_shares=3)
    with pytest.raises(LiveOrderDenied):
        validate_live_intent(binding(), stopped, release(), policy(), intent(),
                             snapshot, now=NOW)
    validate_live_intent(binding(), stopped, release(), policy(), sold,
                         snapshot, now=NOW)
    with pytest.raises(LiveOrderDenied):
        validate_live_intent(binding(), stopped, release(), policy(), sold,
                             observation(held_shares=3, pending_sell_shares=1), now=NOW)
    with pytest.raises(LiveOrderDenied):
        validate_live_intent(binding(), authority(revoked=True), release(), policy(),
                             sold, snapshot, now=NOW)


def test_real_connect_adapter_receives_only_whitelisted_whole_share_day_limit_order():
    with TemporaryDirectory() as temp:
        journal = LiveOrderJournal(Path(temp) / "member.sqlite3")
        service = LiveMemberGateway(journal, network_writes_enabled=True)
        broker, calls = fake_broker()
        receipt = service.submit(
            binding(), authority(), release(), policy(), intent(), observation(),
            broker, now=NOW
        )
        assert receipt.state == "confirmed"
        assert receipt.broker_order_id == "broker-order-1"
        assert [op[0] for op in calls] == ["GET", "POST"]
        order = calls[-1][2]
        assert order == {
            "symbol": "SPY", "side": "buy", "qty": "2",
            "type": "limit", "limit_price": "10.00", "time_in_force": "day",
            "extended_hours": False, "client_order_id": receipt.client_order_id,
            "order_class": "simple",
        }
        # No credentials or trading secrets can be persisted in the journal or receipt.
        assert TOKEN not in str(receipt)
        assert "rhcl-" in receipt.client_order_id
        repeated = service.submit(binding(), authority(), release(), policy(),
                                  intent(), observation(), broker, now=NOW)
        assert repeated == receipt
        assert len(calls) == 2
        journal.close()

        reopened = LiveOrderJournal(Path(temp) / "member.sqlite3")
        service2 = LiveMemberGateway(reopened, network_writes_enabled=True)
        repeated = service2.submit(binding(), authority(), release(), policy(),
                                   intent(), observation(), broker, now=NOW)
        assert repeated == receipt
        assert len(calls) == 2
        with pytest.raises(LiveOrderDenied, match="Duplicate signal ID"):
            service2.submit(binding(), authority(), release(), policy(),
                            intent(quantity=3), observation(), broker, now=NOW)
        assert len(calls) == 2
        reopened.close()


def test_broker_account_mismatch_never_submits_order():
    with TemporaryDirectory() as temp:
        journal = LiveOrderJournal(Path(temp) / "journal.sqlite3")
        service = LiveMemberGateway(journal, network_writes_enabled=True)
        broker, calls = fake_broker(account_id="another-member-broker")
        receipt = service.submit(binding(), authority(), release(), policy(),
                                 intent(), observation(), broker, now=NOW)
        assert receipt.state == "blocked"
        assert [op[0] for op in calls] == ["GET"]
        assert journal.list_owned(binding())[0] == receipt
        journal.close()


def test_broker_timeout_never_blindly_retries_and_must_reconcile():
    with TemporaryDirectory() as temp:
        journal = LiveOrderJournal(Path(temp) / "journal.sqlite3")
        service = LiveMemberGateway(journal, network_writes_enabled=True)
        broker, calls = fake_broker(timeout=True)
        one = service.submit(binding(), authority(), release(), policy(),
                             intent(), observation(), broker, now=NOW)
        assert one.state == "uncertain"
        assert len(calls) == 2
        replay = service.submit(binding(), authority(), release(), policy(),
                                intent(), observation(), broker, now=NOW)
        assert replay == one and len(calls) == 2
        reconciled = service.reconcile(binding(), authority(member_armed=False), "decision-1", broker)
        assert reconciled.state == "confirmed"
        assert reconciled.broker_order_id == "broker-order-1"
        assert [x[0] for x in calls] == ["GET", "POST", "GET", "GET"]
        journal.close()


def test_two_member_broker_states_cannot_cross_read_or_dispatch():
    with TemporaryDirectory() as temp:
        journal = LiveOrderJournal(Path(temp) / "journal.sqlite3")
        service = LiveMemberGateway(journal, network_writes_enabled=True)
        b_a = binding()
        b_b = binding("member-b", "live-connection-b", "broker-b")
        broker_a, calls_a = fake_broker(b_a)
        broker_b, calls_b = fake_broker(b_b)
        receipt_a = service.submit(b_a, authority(), release(), policy(),
                                   intent("identical"), observation(), broker_a, now=NOW)
        receipt_b = service.submit(b_b, authority(), release(), policy(),
                                   intent("identical"), observation(broker_account_id="broker-b"),
                                   broker_b, now=NOW)
        assert receipt_a.state == receipt_b.state == "confirmed"
        assert receipt_a.client_order_id != receipt_b.client_order_id
        assert journal.read(b_a, "identical") == receipt_a
        assert journal.read(b_b, "identical") == receipt_b
        assert journal.list_owned(b_a) == (receipt_a,)
        assert journal.list_owned(b_b) == (receipt_b,)
        with pytest.raises(LiveOrderDenied):
            service.submit(b_a, authority(), release(), policy(), intent("cross"),
                           observation(), broker_b, now=NOW)
        assert len(calls_a) == 2 and len(calls_b) == 2
        journal.close()


def test_invalid_contracts_reject_abnormal_inputs_and_owner_data_path():
    for sample in [
        dict(member_id="", connection_id="abc", broker_account_id="123", environment="live"),
        dict(member_id="owner user", connection_id="abc", broker_account_id="123", environment="live"),
        dict(member_id="member", connection_id="abc", broker_account_id="123", environment="paper"),
    ]:
        with pytest.raises(LiveOrderDenied):
            MemberBinding(**sample)
    with pytest.raises(LiveOrderDenied):
        LiveOrderJournal("/data/rhen-core.db")
    for sample in (dict(quantity=0), dict(quantity=True), dict(limit_price_cents=-1),
                   dict(side="short"), dict(symbol="BTC/USD")):
        with pytest.raises(LiveOrderDenied):
            intent(**sample)
    with pytest.raises(LiveOrderDenied):
        LiveRelease("v1", "not-a-sha256", ("SPY",))
    with pytest.raises(LiveOrderDenied):
        LivePolicy(max_order_notional_cents=1000, max_gross_exposure_bp=15000,
                   max_positions=1, max_daily_loss_cents=100, max_symbol_exposure_cents=1000)
