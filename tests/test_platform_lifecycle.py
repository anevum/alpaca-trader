from app.platform_core.lifecycle import (
    PaperCustomerFacts,
    derive_paper_customer_lifecycle,
    paper_funding_projection,
)


def facts(**overrides):
    base = dict(
        tenant_status="ACTIVE",
        broker_connected=True,
        broker_active=True,
        reconciliation_success=True,
        funded=True,
        allocation_active=True,
        risk_active=True,
        strategy_assigned=True,
        customer_consented=True,
        bot_enabled=False,
        execution_eligible=False,
        execution_reasons=(),
    )
    base.update(overrides)
    return PaperCustomerFacts(**base)


def test_paper_funding_uses_broker_values_only():
    projection = paper_funding_projection(
        {"equity": "100000", "cash": "95000", "buying_power": "190000"},
        observed_at="2026-10-05T00:00:00Z",
    )
    assert projection["source"] == "ALPACA"
    assert projection["funded"] is True
    assert projection["equity"] == "100000"
    assert projection["external_money_movement_enabled"] is False


def test_unknown_broker_balance_is_not_assumed_funded():
    projection = paper_funding_projection({})
    assert projection["known"] is False
    assert projection["funded"] is False


def test_lifecycle_prioritizes_broker_then_funding_then_configuration():
    assert derive_paper_customer_lifecycle(
        facts(broker_connected=False)
    )["state"] == "BROKER_SETUP_REQUIRED"
    assert derive_paper_customer_lifecycle(
        facts(reconciliation_success=False)
    )["state"] == "BROKER_PENDING"
    assert derive_paper_customer_lifecycle(
        facts(funded=False)
    )["state"] == "FUNDING_REQUIRED"
    assert derive_paper_customer_lifecycle(
        facts(allocation_active=False)
    )["state"] == "TRADING_CONFIGURATION_REQUIRED"


def test_configuration_complete_is_ready_even_if_executor_is_pending():
    result = derive_paper_customer_lifecycle(
        facts(
            bot_enabled=True,
            execution_reasons=("tenant_execution_runtime_unavailable",),
        )
    )
    assert result["state"] == "READY"
    assert result["setup_ready"] is True
    assert result["execution_ready"] is False
    assert result["next_action"] == "Await tenant execution runtime"


def test_execution_eligible_customer_is_active():
    result = derive_paper_customer_lifecycle(
        facts(bot_enabled=True, execution_eligible=True)
    )
    assert result["state"] == "ACTIVE"
    assert result["execution_ready"] is True


def test_restricted_tenant_never_projects_ready():
    result = derive_paper_customer_lifecycle(
        facts(tenant_status="RESTRICTED", execution_eligible=True)
    )
    assert result["state"] == "RESTRICTED"
    assert result["setup_ready"] is False
    assert result["execution_ready"] is False


def test_margin_buying_power_alone_does_not_count_as_crypto_funding():
    projection = paper_funding_projection(
        {
            "equity": "1000",
            "cash": "0",
            "buying_power": "2000",
            "non_marginable_buying_power": "0",
        }
    )
    assert projection["funded"] is False
    assert projection["available_for_crypto"] == "0"
