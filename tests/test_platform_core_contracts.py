from app.platform_core import (
    TradingEligibilityInput,
    deterministic_order_identity,
    evaluate_execution_eligibility,
)


def eligible_input(**overrides):
    values = {
        "environment": "PAPER",
        "tenant_status": "ACTIVE",
        "entitlement_status": "ACTIVE",
        "broker_account_status": "ACTIVE",
        "broker_crypto_enabled": True,
        "broker_trading_blocked": False,
        "broker_trading_scope": True,
        "broker_reconciled": True,
        "allocation_active": True,
        "allocation_positive": True,
        "risk_profile_active": True,
        "strategy_assignment_active": True,
        "strategy_release_state": "STABLE",
        "customer_trading_consent": True,
        "customer_bot_enabled": True,
        "iren_fleet_healthy": True,
        "tenant_execution_runtime_ready": True,
        "live_customer_authority": False,
    }
    values.update(overrides)
    return TradingEligibilityInput(**values)


def test_paper_entry_requires_every_platform_gate():
    result = evaluate_execution_eligibility(eligible_input())
    assert result.eligible is True
    assert result.reasons == ()


def test_live_customer_execution_is_closed_by_default():
    result = evaluate_execution_eligibility(
        eligible_input(environment="LIVE")
    )
    assert result.eligible is False
    assert result.reasons == ("live_customer_authority_missing",)


def test_explicit_live_authority_still_cannot_bypass_risk_or_iren():
    result = evaluate_execution_eligibility(
        eligible_input(
            environment="LIVE",
            live_customer_authority=True,
            risk_profile_active=False,
            iren_fleet_healthy=False,
        )
    )
    assert result.eligible is False
    assert "risk_profile_not_active" in result.reasons
    assert "iren_fleet_gate_closed" in result.reasons


def test_credentials_or_broker_status_alone_are_never_sufficient():
    result = evaluate_execution_eligibility(
        eligible_input(
            tenant_status="READY",
            entitlement_status="EXPIRED",
            allocation_positive=False,
            customer_trading_consent=False,
            customer_bot_enabled=False,
        )
    )
    assert result.eligible is False
    assert set(result.reasons) >= {
        "tenant_not_active",
        "entitlement_not_active",
        "capital_allocation_unavailable",
        "customer_consent_missing",
        "customer_bot_disabled",
    }


def test_non_executable_research_release_cannot_trade():
    result = evaluate_execution_eligibility(
        eligible_input(strategy_release_state="PAPER_PASSED")
    )
    assert result.eligible is False
    assert result.reasons == ("strategy_release_not_executable",)


def test_order_identity_is_retry_stable_and_tenant_scoped():
    kwargs = {
        "tenant_id": "tenant-a",
        "broker_account_id": "broker-a",
        "strategy_release_id": "RHEN-BTC-2.3.1",
        "signal_id": "signal-123",
        "symbol": "BTC/USD",
        "side": "BUY",
    }
    first = deterministic_order_identity(**kwargs)
    second = deterministic_order_identity(**kwargs)
    other_tenant = deterministic_order_identity(**{**kwargs, "tenant_id": "tenant-b"})

    assert first == second
    assert first != other_tenant
    assert first[0].startswith("rhen-intent-")
    assert first[1].startswith("anevum-rhen-")
    assert len(first[1]) <= 128


def test_order_identity_rejects_missing_components():
    try:
        deterministic_order_identity(
            tenant_id="tenant-a",
            broker_account_id="",
            strategy_release_id="RHEN-BTC-2.3.1",
            signal_id="signal-123",
            symbol="BTC/USD",
            side="BUY",
        )
    except ValueError as exc:
        assert "non-empty" in str(exc)
    else:
        raise AssertionError("missing order identity component was accepted")



def test_missing_broker_trading_scope_closes_execution_gate():
    result = evaluate_execution_eligibility(
        eligible_input(broker_trading_scope=False)
    )
    assert result.eligible is False
    assert result.reasons == ("broker_trading_scope_missing",)



def test_missing_tenant_execution_runtime_closes_gate():
    result = evaluate_execution_eligibility(
        eligible_input(tenant_execution_runtime_ready=False)
    )
    assert result.eligible is False
    assert result.reasons == ("tenant_execution_runtime_unavailable",)
