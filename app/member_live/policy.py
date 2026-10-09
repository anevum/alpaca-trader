"""Compile bounded RHEN Cloud member preferences into a live risk policy.

Member preferences come from the independently authenticated ANEVUM account
service after tenant verification. This compiler never accepts preferences as
broker authority and never enables the order path. The *operator ceiling*
always wins. No website request may set operator limits, account equity,
market snapshots, strategy release, grant or live execution permission.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contracts import LiveOrderDenied, LivePolicy


@dataclass(frozen=True)
class VerifiedMemberRiskPreferences:
    """Validated member-owned draft, provided only by a trusted backend."""
    member_id: str
    max_open_positions: int
    max_total_exposure_percent: int
    max_position_percent: int

    def __post_init__(self):
        from .contracts import identity
        identity(self.member_id, "member identity")
        for value, maximum, label in (
            (self.max_open_positions, 10, "positions"),
            (self.max_total_exposure_percent, 100, "total exposure"),
            (self.max_position_percent, 100, "single position"),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise LiveOrderDenied("Invalid member risk preference: " + label)
        if self.max_position_percent > self.max_total_exposure_percent:
            raise LiveOrderDenied("Single-position preference exceeds total exposure")


def compile_member_live_policy(
    preferences: VerifiedMemberRiskPreferences,
    operator_ceiling: LivePolicy,
    *,
    verified_member_id: str,
    broker_equity_cents: int,
) -> LivePolicy:
    """Return limits no looser than either member preferences or operator caps.

    broker_equity_cents MUST come from the current authenticated Alpaca account
    observation. Call again whenever broker equity or member limits change.
    This does not verify the upstream draft or release, and cannot arm trading.
    """
    if not isinstance(preferences, VerifiedMemberRiskPreferences) or not isinstance(operator_ceiling, LivePolicy):
        raise LiveOrderDenied("Server-verified risk preferences and operator ceiling required")
    if type(verified_member_id) is not str or verified_member_id != preferences.member_id:
        raise LiveOrderDenied("Member risk preference belongs to another account")
    if type(broker_equity_cents) is not int or broker_equity_cents <= 0:
        raise LiveOrderDenied("Broker equity must be independently verified")
    member_total_bp = preferences.max_total_exposure_percent * 100
    single_position_cents = broker_equity_cents * preferences.max_position_percent // 100
    if single_position_cents < 1:
        raise LiveOrderDenied("Broker equity too small for bounded member position")
    return LivePolicy(
        max_order_notional_cents=min(
            operator_ceiling.max_order_notional_cents,
            single_position_cents,
        ),
        max_gross_exposure_bp=min(
            operator_ceiling.max_gross_exposure_bp,
            member_total_bp,
        ),
        max_positions=min(operator_ceiling.max_positions, preferences.max_open_positions),
        max_daily_loss_cents=operator_ceiling.max_daily_loss_cents,
        max_symbol_exposure_cents=min(
            operator_ceiling.max_symbol_exposure_cents,
            single_position_cents,
        ),
        max_spread_bp=operator_ceiling.max_spread_bp,
        max_buy_chase_bp=operator_ceiling.max_buy_chase_bp,
    )
