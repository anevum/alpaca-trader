"""Strict, server-owned contracts for member-owned Alpaca Connect LIVE accounts.

This module neither authenticates users nor authorizes broker writes on its own.
A trusted backend must supply account ownership, a verified OAuth grant, an
operator-approved release and broker-fetched risk observations. No member ID,
permission, account ID or price snapshot from the browser is trustworthy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IDENT = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SYMBOL = re.compile(r"^[A-Z]{1,7}$")
HASH = re.compile(r"^[a-f0-9]{64}$")
MAX_SNAPSHOT_AGE_SECONDS = 10
MAX_QUOTE_AGE_SECONDS = 5
MAX_SIGNAL_AGE_SECONDS = 30


class LiveOrderDenied(ValueError):
    """A live order was blocked before any external broker operation."""


def identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise LiveOrderDenied(f"Invalid {label}")
    return value


def positive_int(value: int, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise LiveOrderDenied(f"Invalid {label}")
    return value


@dataclass(frozen=True)
class MemberBinding:
    member_id: str
    connection_id: str
    broker_account_id: str
    environment: Literal["live"]
    owner_account: bool = False

    def __post_init__(self):
        identity(self.member_id, "member identity")
        identity(self.connection_id, "connection identity")
        identity(self.broker_account_id, "broker account identity")
        if self.environment != "live" or type(self.owner_account) is not bool or self.owner_account:
            raise LiveOrderDenied("Company accounts and non-live grants are not member-live accounts")


@dataclass(frozen=True)
class LiveRelease:
    strategy_version: str
    code_sha256: str
    allowed_symbols: tuple[str, ...]

    def __post_init__(self):
        identity(self.strategy_version, "strategy version")
        if not isinstance(self.code_sha256, str) or not HASH.fullmatch(self.code_sha256):
            raise LiveOrderDenied("Signed strategy release digest is required")
        if not isinstance(self.allowed_symbols, tuple) or not (1 <= len(self.allowed_symbols) <= 50):
            raise LiveOrderDenied("Approved symbol allowlist required")
        if len(set(self.allowed_symbols)) != len(self.allowed_symbols) or any(
            not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in self.allowed_symbols
        ):
            raise LiveOrderDenied("Invalid strategy symbol allowlist")


@dataclass(frozen=True)
class LiveAuthority:
    # All values MUST come from independent server/provider records, not the HTTP body.
    provider_live_approved: bool = False
    regulatory_review_complete: bool = False
    security_review_complete: bool = False
    operator_release_approved: bool = False
    deployment_live_enabled: bool = False
    broker_grant_valid: bool = False
    member_live_consent_current: bool = False
    session_verified: bool = False
    member_armed: bool = False
    revoked: bool = False
    scopes: tuple[str, ...] = ()

    def check(self, *, allow_exit_when_paused: bool) -> None:
        required = (
            self.provider_live_approved,
            self.regulatory_review_complete,
            self.security_review_complete,
            self.operator_release_approved,
            self.deployment_live_enabled,
            self.broker_grant_valid,
            self.member_live_consent_current,
            self.session_verified,
        )
        if any(type(flag) is not bool for flag in (*required, self.member_armed, self.revoked)):
            raise LiveOrderDenied("Non-boolean authorization signal")
        if not all(required) or self.revoked or "trading" not in self.scopes:
            raise LiveOrderDenied("Member live authorization incomplete or revoked")
        if not allow_exit_when_paused and not self.member_armed:
            raise LiveOrderDenied("Member has not armed new live orders")


@dataclass(frozen=True)
class LivePolicy:
    max_order_notional_cents: int
    max_gross_exposure_bp: int
    max_positions: int
    max_daily_loss_cents: int
    max_spread_bp: int = 80
    max_buy_chase_bp: int = 100

    def __post_init__(self):
        positive_int(self.max_order_notional_cents, "order cap")
        positive_int(self.max_daily_loss_cents, "daily loss cap")
        for v, low, high, label in (
            (self.max_gross_exposure_bp, 1, 10000, "gross exposure cap"),
            (self.max_positions, 1, 20, "position cap"),
            (self.max_spread_bp, 1, 500, "spread cap"),
            (self.max_buy_chase_bp, 0, 500, "buy chase cap"),
        ):
            if type(v) is not int or not low <= v <= high:
                raise LiveOrderDenied(f"Invalid {label}")


@dataclass(frozen=True)
class LiveIntent:
    signal_id: str
    strategy_version: str
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    limit_price_cents: int
    observed_at: int

    def __post_init__(self):
        identity(self.signal_id, "signal")
        identity(self.strategy_version, "strategy version")
        if not isinstance(self.symbol, str) or not SYMBOL.fullmatch(self.symbol):
            raise LiveOrderDenied("Only approved U.S. equity/ETF symbols permitted")
        if self.side not in ("buy", "sell"):
            raise LiveOrderDenied("No options, shorts, crypto or leverage")
        positive_int(self.quantity, "whole-share quantity")
        positive_int(self.limit_price_cents, "limit price")
        positive_int(self.observed_at, "signal timestamp")


@dataclass(frozen=True)
class BrokerObservation:
    # Must be fetched for the precise authenticated connection/account.
    broker_account_id: str
    snapshot_at: int
    quote_at: int
    market_open: bool
    tradable_us_equity: bool
    bid_cents: int
    ask_cents: int
    equity_cents: int
    buying_power_cents: int
    gross_exposure_cents: int
    pending_buy_exposure_cents: int
    daily_realized_loss_cents: int
    open_position_count: int
    held_shares: int
    pending_sell_shares: int

    def __post_init__(self):
        identity(self.broker_account_id, "snapshot account")
        positive_int(self.snapshot_at, "snapshot time")
        positive_int(self.quote_at, "quote time")
        if type(self.market_open) is not bool or type(self.tradable_us_equity) is not bool:
            raise LiveOrderDenied("Invalid broker market flags")
        for name in (
            "bid_cents", "ask_cents", "equity_cents", "buying_power_cents",
            "gross_exposure_cents", "pending_buy_exposure_cents",
            "daily_realized_loss_cents", "open_position_count",
            "held_shares", "pending_sell_shares"
        ):
            v = getattr(self, name)
            if type(v) is not int or v < 0:
                raise LiveOrderDenied("Invalid broker observation: " + name)


def validate_live_intent(
    binding: MemberBinding,
    authority: LiveAuthority,
    release: LiveRelease,
    policy: LivePolicy,
    intent: LiveIntent,
    snapshot: BrokerObservation,
    *,
    now: int
) -> None:
    """Fail closed before a broker write. A separate durable journal is still required."""
    if any(not isinstance(x, cls) for x, cls in (
        (binding, MemberBinding), (authority, LiveAuthority), (release, LiveRelease),
        (policy, LivePolicy), (intent, LiveIntent), (snapshot, BrokerObservation)
    )):
        raise LiveOrderDenied("Verified live gateway contracts required")
    if type(now) is not int or now < 1:
        raise LiveOrderDenied("Invalid reference clock")
    # A pause blocks additional buys but does not strand existing long exposure.
    authority.check(allow_exit_when_paused=intent.side == "sell")
    if snapshot.broker_account_id != binding.broker_account_id:
        raise LiveOrderDenied("Broker account is not the authorized member account")
    if intent.strategy_version != release.strategy_version or intent.symbol not in release.allowed_symbols:
        raise LiveOrderDenied("Unapproved strategy release or symbol")
    if intent.observed_at > now or now - intent.observed_at > MAX_SIGNAL_AGE_SECONDS:
        raise LiveOrderDenied("Stale or future strategy signal")
    if snapshot.snapshot_at > now or now - snapshot.snapshot_at > MAX_SNAPSHOT_AGE_SECONDS:
        raise LiveOrderDenied("Stale broker account snapshot")
    if snapshot.quote_at > now or now - snapshot.quote_at > MAX_QUOTE_AGE_SECONDS:
        raise LiveOrderDenied("Stale market quote")
    if not snapshot.market_open or not snapshot.tradable_us_equity:
        raise LiveOrderDenied("Live market closed or asset not tradable")
    if snapshot.bid_cents <= 0 or snapshot.ask_cents < snapshot.bid_cents:
        raise LiveOrderDenied("Invalid quote bid/ask")
    if (snapshot.ask_cents - snapshot.bid_cents) * 10000 > snapshot.ask_cents * policy.max_spread_bp:
        raise LiveOrderDenied("Spread exceeds limit")
    if snapshot.equity_cents <= 0:
        raise LiveOrderDenied("No verified positive equity")
    if intent.side == "sell":
        # Explicit whole-position reducing sells may remain permitted during pause/loss breaker.
        if intent.quantity > snapshot.held_shares - snapshot.pending_sell_shares:
            raise LiveOrderDenied("Sell exceeds uncommitted long holdings")
        return
    notional = intent.quantity * intent.limit_price_cents
    if snapshot.daily_realized_loss_cents >= policy.max_daily_loss_cents:
        raise LiveOrderDenied("Daily realized loss cap reached")
    if notional > policy.max_order_notional_cents:
        raise LiveOrderDenied("Order exceeds per-member notional cap")
    if notional > snapshot.buying_power_cents:
        raise LiveOrderDenied("Order exceeds broker buying power")
    if snapshot.held_shares == 0 and snapshot.open_position_count >= policy.max_positions:
        raise LiveOrderDenied("Member position count cap reached")
    if intent.limit_price_cents * 10000 > snapshot.ask_cents * (10000 + policy.max_buy_chase_bp):
        raise LiveOrderDenied("Limit buy price chases the market")
    exposure = snapshot.gross_exposure_cents + snapshot.pending_buy_exposure_cents + notional
    if exposure * 10000 > snapshot.equity_cents * policy.max_gross_exposure_bp:
        raise LiveOrderDenied("Gross exposure cap reached")
