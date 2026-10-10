"""Fail-closed contract tests: member risk draft -> isolated RHEN live caps."""
from dataclasses import replace

import pytest

from app.member_live import (
    BrokerObservation, LiveAuthority, LiveIntent, LiveOrderDenied,
    LivePolicy, LiveRelease, MemberBinding,
    VerifiedMemberRiskPreferences, compile_member_live_policy,
    validate_live_intent,
)


def operator_limits(**overrides):
    values = {
        "max_order_notional_cents": 30000,
        "max_gross_exposure_bp": 4500,
        "max_positions": 6,
        "max_daily_loss_cents": 1200,
        "max_symbol_exposure_cents": 20000,
        "max_spread_bp": 65,
        "max_buy_chase_bp": 50,
    }
    values.update(overrides)
    return LivePolicy(**values)


def preferences(**overrides):
    values = {
        "member_id": "member-a",
        "max_open_positions": 2,
        "max_total_exposure_percent": 30,
        "max_position_percent": 10,
    }
    values.update(overrides)
    return VerifiedMemberRiskPreferences(**values)


def test_member_preferences_compile_to_more_restrictive_server_policy():
    caps = compile_member_live_policy(
        preferences(), operator_limits(),
        verified_member_id="member-a", broker_equity_cents=100000
    )
    assert caps.max_order_notional_cents == 10000
    assert caps.max_symbol_exposure_cents == 10000
    assert caps.max_gross_exposure_bp == 3000
    assert caps.max_positions == 2
    assert caps.max_daily_loss_cents == 1200
    assert caps.max_spread_bp == 65
    assert caps.max_buy_chase_bp == 50


def test_member_preferences_can_only_lower_company_risk_caps():
    global_caps = operator_limits(
        max_order_notional_cents=1500, max_symbol_exposure_cents=3000,
        max_gross_exposure_bp=1000, max_positions=1, max_spread_bp=30
    )
    chosen = preferences(
        max_open_positions=10, max_total_exposure_percent=100,
        max_position_percent=100
    )
    caps = compile_member_live_policy(
        chosen, global_caps, verified_member_id="member-a", broker_equity_cents=5000000
    )
    assert caps == global_caps
    assert chosen.max_total_exposure_percent > caps.max_gross_exposure_bp // 100


def test_cross_account_and_unverified_inputs_never_compile():
    base = preferences()
    limits = operator_limits()
    for member in ("member-b", "", None, "owner"):
        with pytest.raises(LiveOrderDenied):
            compile_member_live_policy(base, limits, verified_member_id=member, broker_equity_cents=100000)
    for equity in (0, -1, True, 1.5, "100000", None):
        with pytest.raises(LiveOrderDenied):
            compile_member_live_policy(base, limits, verified_member_id="member-a", broker_equity_cents=equity)
    for untrusted in ({"member_id": "member-a"}, object(), None, True):
        with pytest.raises(LiveOrderDenied):
            compile_member_live_policy(untrusted, limits, verified_member_id="member-a", broker_equity_cents=100000)
    for no_operator in ({}, None, 999):
        with pytest.raises(LiveOrderDenied):
            compile_member_live_policy(base, no_operator, verified_member_id="member-a", broker_equity_cents=100000)
    with pytest.raises(LiveOrderDenied, match="too small"):
        compile_member_live_policy(preferences(max_position_percent=1), limits,
                                   verified_member_id="member-a", broker_equity_cents=1)


def test_member_risk_draft_contract_rejects_unsupported_or_unsafe_settings():
    for value in (
        {"max_open_positions": 0}, {"max_open_positions": 11},
        {"max_open_positions": True}, {"max_open_positions": 2.5},
        {"max_total_exposure_percent": 101}, {"max_total_exposure_percent": 0},
        {"max_position_percent": -1}, {"max_position_percent": 101},
        {"max_position_percent": 25, "max_total_exposure_percent": 10},
        {"member_id": ""}, {"member_id": "bad member ID"},
    ):
        with pytest.raises(LiveOrderDenied):
            preferences(**value)


def test_compiled_symbol_cap_rejects_accumulation_even_if_each_buy_is_small():
    compiled = compile_member_live_policy(preferences(), operator_limits(),
        verified_member_id="member-a", broker_equity_cents=100000)
    binding = MemberBinding("member-a", "connection-a", "broker-a", "live")
    consent = LiveAuthority(
        provider_live_approved=True, regulatory_review_complete=True,
        security_review_complete=True, operator_release_approved=True,
        deployment_live_enabled=True, broker_grant_valid=True,
        member_live_consent_current=True, session_verified=True,
        member_armed=True, scopes=("trading",)
    )
    release = LiveRelease("strategy-v1", "a" * 64, ("SPY",))
    intent = LiveIntent("signal-1", "strategy-v1", "SPY", "buy", 2, 1000, 1800000000)
    observation = BrokerObservation(
        broker_account_id="broker-a", snapshot_at=1800000000, quote_at=1800000000,
        market_open=True, tradable_us_equity=True, bid_cents=999, ask_cents=1000,
        equity_cents=100000, buying_power_cents=100000,
        gross_exposure_cents=9000, pending_buy_exposure_cents=0,
        daily_realized_loss_cents=0, open_position_count=1,
        held_shares=0, pending_sell_shares=0, pending_new_positions=0,
        symbol_exposure_cents=9000, symbol_pending_buy_exposure_cents=0
    )
    with pytest.raises(LiveOrderDenied, match="Single-symbol"):
        validate_live_intent(binding, consent, release, compiled, intent, observation, now=1800000000)
    # Pending buys count the same as filled positions.
    with pytest.raises(LiveOrderDenied, match="Single-symbol"):
        validate_live_intent(binding, consent, release, compiled, intent,
            replace(observation, symbol_exposure_cents=1000,
                    symbol_pending_buy_exposure_cents=8001), now=1800000000)
    # Within the member and company limits, the pretrade check succeeds.
    validate_live_intent(binding, consent, release, compiled, intent,
        replace(observation, symbol_exposure_cents=3000, gross_exposure_cents=3000),
        now=1800000000)
