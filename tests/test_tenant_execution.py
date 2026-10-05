from decimal import Decimal

from app.platform_core.tenant_execution import (
    TenantEntryRiskInput,
    TenantPaperSignal,
    canonical_crypto_symbol,
    evaluate_tenant_entry_risk,
)


def signal(**overrides):
    values = dict(
        signal_id="sig-1",
        strategy_release_id="release-1",
        action="ENTER_LONG",
        symbol="BTC/USD",
        reference_price=Decimal("100"),
        target_allocation_fraction=Decimal("0.10"),
        stop_price=Decimal("95"),
        stop_limit_price=Decimal("94"),
        take_profit_price=Decimal("110"),
        metadata={},
    )
    values.update(overrides)
    return TenantPaperSignal(**values)


def risk(**overrides):
    values = dict(
        allocation_fraction=Decimal("1"),
        absolute_cap=Decimal("100"),
        max_position_fraction=Decimal("0.25"),
        max_gross_exposure_fraction=Decimal("1"),
        max_daily_loss_fraction=Decimal("0.05"),
        max_drawdown_fraction=Decimal("0.20"),
        max_concurrent_positions=2,
        high_water_equity=Decimal("100"),
    )
    values.update(overrides)
    return TenantEntryRiskInput(**values)


def account(**overrides):
    values = dict(
        id="acct-1",
        equity="100",
        last_equity="100",
        cash="100",
        non_marginable_buying_power="100",
        trading_blocked=False,
    )
    values.update(overrides)
    return values


def test_entry_risk_sizes_from_tenant_allocation_not_global_account():
    decision = evaluate_tenant_entry_risk(
        signal(target_allocation_fraction=Decimal("0.50")),
        risk(
            allocation_fraction=Decimal("0.50"),
            absolute_cap=Decimal("40"),
            max_position_fraction=Decimal("0.25"),
        ),
        account(equity="1000", non_marginable_buying_power="1000"),
        [],
        [],
    )
    assert decision.allowed is True
    assert decision.authorized_allocation == Decimal("40")
    assert decision.requested_notional == Decimal("20.0")
    assert decision.approved_notional == Decimal("10.00")
    assert decision.approved_qty == Decimal("0.100000000")


def test_manual_position_counts_against_tenant_account_risk():
    decision = evaluate_tenant_entry_risk(
        signal(),
        risk(max_concurrent_positions=1),
        account(),
        [{"symbol": "ETHUSD", "asset_class": "crypto", "qty": "1", "market_value": "10"}],
        [],
    )
    assert decision.allowed is False
    assert "max_concurrent_positions_reached" in decision.reasons


def test_margin_buying_power_does_not_expand_crypto_capacity():
    decision = evaluate_tenant_entry_risk(
        signal(),
        risk(),
        account(cash="0", buying_power="1000", non_marginable_buying_power="0"),
        [],
        [],
    )
    assert decision.allowed is False
    assert "crypto_capacity_unavailable" in decision.reasons


def test_daily_loss_and_high_water_drawdown_are_enforced():
    daily = evaluate_tenant_entry_risk(
        signal(),
        risk(max_daily_loss_fraction=Decimal("0.05")),
        account(equity="90", last_equity="100"),
        [],
        [],
    )
    assert "daily_loss_limit_reached" in daily.reasons

    drawdown = evaluate_tenant_entry_risk(
        signal(),
        risk(
            max_daily_loss_fraction=Decimal("0.50"),
            max_drawdown_fraction=Decimal("0.10"),
            high_water_equity=Decimal("120"),
        ),
        account(equity="100", last_equity="100"),
        [],
        [],
    )
    assert "drawdown_limit_reached" in drawdown.reasons


def test_existing_position_or_open_entry_blocks_duplicate_symbol():
    existing = evaluate_tenant_entry_risk(
        signal(),
        risk(),
        account(),
        [{"symbol": "BTCUSD", "asset_class": "crypto", "qty": "0.1", "market_value": "10"}],
        [],
    )
    assert "symbol_position_already_open" in existing.reasons

    open_order = evaluate_tenant_entry_risk(
        signal(),
        risk(),
        account(),
        [],
        [{"symbol": "BTCUSD", "asset_class": "crypto", "side": "buy", "status": "new"}],
    )
    assert "symbol_entry_order_already_open" in open_order.reasons


def test_crypto_symbol_normalizes_only_broker_classified_crypto():
    assert canonical_crypto_symbol("BTCUSD", asset_class="crypto") == "BTC/USD"
    assert canonical_crypto_symbol("BTC/USD") == "BTC/USD"
    assert canonical_crypto_symbol("USDU", asset_class="us_equity") == "USDU"
