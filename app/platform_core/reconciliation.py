from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any

from .broker import TenantAlpacaReadClient, TenantBrokerAccount

ACCOUNT_FIELDS = (
    "id",
    "status",
    "crypto_status",
    "currency",
    "cash",
    "buying_power",
    "portfolio_value",
    "equity",
    "last_equity",
    "long_market_value",
    "short_market_value",
    "initial_margin",
    "maintenance_margin",
    "trading_blocked",
    "transfers_blocked",
    "account_blocked",
    "pattern_day_trader",
    "daytrade_count",
)

POSITION_FIELDS = (
    "asset_id",
    "symbol",
    "exchange",
    "asset_class",
    "side",
    "qty",
    "avg_entry_price",
    "market_value",
    "cost_basis",
    "unrealized_pl",
    "unrealized_plpc",
    "current_price",
    "lastday_price",
    "change_today",
)

ORDER_FIELDS = (
    "id",
    "client_order_id",
    "created_at",
    "updated_at",
    "submitted_at",
    "filled_at",
    "expired_at",
    "canceled_at",
    "failed_at",
    "replaced_at",
    "replaced_by",
    "replaces",
    "asset_id",
    "symbol",
    "asset_class",
    "notional",
    "qty",
    "filled_qty",
    "filled_avg_price",
    "order_class",
    "order_type",
    "type",
    "side",
    "time_in_force",
    "limit_price",
    "stop_price",
    "status",
)


def _allowlist(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: payload[field] for field in fields if field in payload}


def safe_account_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return _allowlist(payload, ACCOUNT_FIELDS)


def safe_positions_snapshot(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_allowlist(item, POSITION_FIELDS) for item in payload if isinstance(item, dict)]


def safe_orders_snapshot(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_allowlist(item, ORDER_FIELDS) for item in payload if isinstance(item, dict)]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def reconciliation_hash(
    *,
    tenant_id: str,
    broker_account_id: str,
    provider_account_id: str,
    account_snapshot: dict[str, Any],
    positions_snapshot: list[dict[str, Any]],
    open_orders_snapshot: list[dict[str, Any]],
    recent_orders_snapshot: list[dict[str, Any]],
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "broker_account_id": broker_account_id,
        "provider_account_id": provider_account_id,
        "account": account_snapshot,
        "positions": positions_snapshot,
        "open_orders": open_orders_snapshot,
        "recent_orders": recent_orders_snapshot,
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BrokerReconciliationResult:
    status: str
    tenant_id: str
    broker_account_id: str
    environment: str
    provider: str
    provider_account_id_expected: str
    provider_account_id_observed: str | None
    observed_at: datetime
    account_snapshot: dict[str, Any] | None
    positions_snapshot: list[dict[str, Any]] | None
    open_orders_snapshot: list[dict[str, Any]] | None
    recent_orders_snapshot: list[dict[str, Any]] | None
    snapshot_hash: str | None
    error_code: str | None
    error_detail: str | None

    @property
    def ready(self) -> bool:
        return self.status == "SUCCESS"

    def is_fresh(
        self,
        *,
        max_age_seconds: int = 120,
        now: datetime | None = None,
    ) -> bool:
        if not self.ready:
            return False
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None or reference.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        age = reference.astimezone(timezone.utc) - self.observed_at.astimezone(timezone.utc)
        return timedelta(0) <= age <= timedelta(seconds=max_age_seconds)

    def to_record(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "tenant_id": self.tenant_id,
            "broker_account_id": self.broker_account_id,
            "environment": self.environment,
            "provider": self.provider,
            "provider_account_id_expected": self.provider_account_id_expected,
            "provider_account_id_observed": self.provider_account_id_observed,
            "observed_at": self.observed_at,
            "account_snapshot": self.account_snapshot,
            "positions_snapshot": self.positions_snapshot,
            "open_orders_snapshot": self.open_orders_snapshot,
            "recent_orders_snapshot": self.recent_orders_snapshot,
            "snapshot_hash": self.snapshot_hash,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
        }


class TenantBrokerReconciler:
    """Build one authoritative read-only Alpaca snapshot for a tenant account."""

    def __init__(
        self,
        account: TenantBrokerAccount,
        client: TenantAlpacaReadClient,
    ):
        self.account = account
        self.client = client

    async def reconcile(self) -> BrokerReconciliationResult:
        observed_at = datetime.now(timezone.utc)
        try:
            account = await self.client.account_snapshot()
            observed_id = str(account.get("id") or "").strip()
            expected_id = self.account.provider_account_id.strip()

            if not observed_id or observed_id != expected_id:
                return BrokerReconciliationResult(
                    status="IDENTITY_MISMATCH",
                    tenant_id=self.account.tenant_id,
                    broker_account_id=self.account.broker_account_id,
                    environment=self.account.environment.upper(),
                    provider="ALPACA",
                    provider_account_id_expected=expected_id,
                    provider_account_id_observed=observed_id or None,
                    observed_at=observed_at,
                    account_snapshot=None,
                    positions_snapshot=None,
                    open_orders_snapshot=None,
                    recent_orders_snapshot=None,
                    snapshot_hash=None,
                    error_code="broker_account_identity_mismatch",
                    error_detail="observed Alpaca account id does not match tenant broker account",
                )

            safe_account = safe_account_snapshot(account)
            positions = safe_positions_snapshot(await self.client.positions_snapshot())
            open_orders = safe_orders_snapshot(await self.client.open_orders_snapshot())
            recent_orders = safe_orders_snapshot(await self.client.recent_orders_snapshot())

            digest = reconciliation_hash(
                tenant_id=self.account.tenant_id,
                broker_account_id=self.account.broker_account_id,
                provider_account_id=observed_id,
                account_snapshot=safe_account,
                positions_snapshot=positions,
                open_orders_snapshot=open_orders,
                recent_orders_snapshot=recent_orders,
            )
            return BrokerReconciliationResult(
                status="SUCCESS",
                tenant_id=self.account.tenant_id,
                broker_account_id=self.account.broker_account_id,
                environment=self.account.environment.upper(),
                provider="ALPACA",
                provider_account_id_expected=expected_id,
                provider_account_id_observed=observed_id,
                observed_at=observed_at,
                account_snapshot=safe_account,
                positions_snapshot=positions,
                open_orders_snapshot=open_orders,
                recent_orders_snapshot=recent_orders,
                snapshot_hash=digest,
                error_code=None,
                error_detail=None,
            )
        except Exception as exc:
            return BrokerReconciliationResult(
                status="FAILED",
                tenant_id=self.account.tenant_id,
                broker_account_id=self.account.broker_account_id,
                environment=self.account.environment.upper(),
                provider="ALPACA",
                provider_account_id_expected=self.account.provider_account_id,
                provider_account_id_observed=None,
                observed_at=observed_at,
                account_snapshot=None,
                positions_snapshot=None,
                open_orders_snapshot=None,
                recent_orders_snapshot=None,
                snapshot_hash=None,
                error_code="broker_reconciliation_failed",
                error_detail=f"{type(exc).__name__}: {exc}"[:1000],
            )
