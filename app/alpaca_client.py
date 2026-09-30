from __future__ import annotations

from typing import Any
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import httpx

from .config import Settings
from .cash_flow import (
    NY,
    annotate_account,
    detected_reference_window_cash_flow,
    reconcile_cash_flow_adjustments,
)


class AlpacaClient:
    """Thin Alpaca Trading API client.

    Automated order submission is exposed here, but every entry/exit is gated by
    ExecutionEngine and the risk layer before this client is called.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._previous_market_close_cache: dict[str, datetime] = {}

    @property
    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.settings.alpaca_api_key,
            "APCA-API-SECRET-KEY": self.settings.alpaca_api_secret,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        allow_404: bool = False,
        **kwargs,
    ) -> Any:
        if not self.settings.credentials_configured:
            raise RuntimeError("Alpaca credentials are not configured")
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(
                method,
                f"{self.settings.base_url}{path}",
                headers=self.headers,
                **kwargs,
            )
            if allow_404 and response.status_code == 404:
                return None
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = response.text.strip()
                raise RuntimeError(
                    f"Alpaca {method} {path} failed "
                    f"({response.status_code}): {detail}"
                ) from exc
            if response.status_code == 204:
                return None
            return response.json()

    async def account(self) -> dict[str, Any]:
        raw = await self._request("GET", "/v2/account")
        observed_at = datetime.now(timezone.utc)
        session_date = observed_at.astimezone(NY).date()

        try:
            reference_start = await self.previous_market_close(session_date)
            activities = await self.transfer_activities(
                after=reference_start.isoformat(),
            )
            detected = detected_reference_window_cash_flow(
                activities,
                session_date=session_date,
                expected_last_equity=raw.get("last_equity"),
                run_id=self.settings.trading_run_id,
                observed_at=observed_at,
                reference_start=reference_start,
            )
            adjustment = reconcile_cash_flow_adjustments(
                self.settings.session_cash_flow_adjustment,
                detected,
                session_date=session_date,
            )
        except (RuntimeError, ValueError) as exc:
            result = dict(raw)
            result["cash_flow_error"] = (
                "automatic cash-flow reconciliation unavailable: " + str(exc)
            )
            result["cash_flow_accounting"] = {
                "status": "unavailable",
                "source": "alpaca_account_activities_reference_window",
                "session_date": session_date.isoformat(),
                "observed_at": observed_at.isoformat(),
            }
            return result

        return annotate_account(
            raw,
            adjustment,
            run_id=self.settings.trading_run_id,
            observed_at=observed_at,
        )

    async def previous_market_close(self, session_date: date) -> datetime:
        """Return the most recent regular-session close before session_date."""
        cache_key = session_date.isoformat()
        cached = self._previous_market_close_cache.get(cache_key)
        if cached is not None:
            return cached

        start_date = session_date - timedelta(days=14)
        result = await self._request(
            "GET",
            "/v2/calendar",
            params={
                "start": start_date.isoformat(),
                "end": session_date.isoformat(),
            },
        )
        sessions = []
        for item in result if isinstance(result, list) else []:
            try:
                item_date = date.fromisoformat(str(item.get("date") or "")[:10])
            except (TypeError, ValueError):
                continue
            if item_date < session_date:
                sessions.append((item_date, item))

        if not sessions:
            raise ValueError("prior market close is unavailable")
        _, prior_session = max(sessions, key=lambda pair: pair[0])
        close_raw = str(prior_session.get("close") or "").strip()
        if not close_raw:
            raise ValueError("prior market close is missing close timestamp")
        try:
            close_at = datetime.fromisoformat(close_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("prior market close timestamp is invalid") from exc
        if close_at.tzinfo is None or close_at.utcoffset() is None:
            close_at = close_at.replace(tzinfo=NY)
        close_at = close_at.astimezone(timezone.utc)
        self._previous_market_close_cache[cache_key] = close_at
        return close_at

    async def clock(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/clock")

    async def positions(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/v2/positions")

    async def asset(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/assets/{symbol.upper()}")

    async def assets(
        self,
        *,
        status: str = "active",
        asset_class: str = "us_equity",
    ) -> list[dict[str, Any]]:
        result = await self._request(
            "GET",
            "/v2/assets",
            params={"status": status, "asset_class": asset_class},
        )
        return result if isinstance(result, list) else []

    async def open_orders(self) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/v2/orders",
            params={"status": "open", "nested": "true"},
        )

    async def recent_orders(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._request(
            "GET",
            "/v2/orders",
            params={
                "status": "all",
                "limit": limit,
                "direction": "desc",
                "nested": "true",
            },
        )

    async def transfer_activities(
        self,
        *,
        date: str | None = None,
        after: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "direction": "desc",
            "page_size": min(max(limit, 1), 100),
        }
        if date:
            params["date"] = date
        if after:
            params["after"] = after
        if until:
            params["until"] = until
        result = await self._request(
            "GET",
            "/v2/account/activities/TRANS",
            params=params,
        )
        return result if isinstance(result, list) else []

    async def fill_activities(
        self,
        *,
        date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "direction": "desc",
            "page_size": min(max(limit, 1), 100),
        }
        if date:
            params["date"] = date
        return await self._request(
            "GET",
            "/v2/account/activities/FILL",
            params=params,
        )

    async def order_by_client_order_id(
        self,
        client_order_id: str,
    ) -> dict[str, Any] | None:
        return await self._request(
            "GET",
            "/v2/orders:by_client_order_id",
            allow_404=True,
            params={"client_order_id": client_order_id},
        )

    async def submit_crypto_market_buy(
        self,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol.upper(),
                "qty": qty,
                "side": "buy",
                "type": "market",
                "time_in_force": "gtc",
                "client_order_id": client_order_id,
            },
        )

    async def submit_crypto_market_sell(
        self,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol.upper(),
                "qty": qty,
                "side": "sell",
                "type": "market",
                "time_in_force": "gtc",
                "client_order_id": client_order_id,
            },
        )

    async def submit_crypto_stop_limit_sell(
        self,
        symbol: str,
        qty: str,
        stop_price: str,
        limit_price: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol.upper(),
                "qty": qty,
                "side": "sell",
                "type": "stop_limit",
                "time_in_force": "gtc",
                "stop_price": stop_price,
                "limit_price": limit_price,
                "client_order_id": client_order_id,
            },
        )

    async def submit_market_buy(
        self,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": client_order_id,
            },
        )

    async def submit_bracket_market_buy(
        self,
        symbol: str,
        qty: str,
        take_profit_price: str,
        stop_price: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": "buy",
                "type": "market",
                "time_in_force": "day",
                "order_class": "bracket",
                "take_profit": {"limit_price": take_profit_price},
                "stop_loss": {"stop_price": stop_price},
                "client_order_id": client_order_id,
            },
        )

    async def submit_market_sell(
        self,
        symbol: str,
        qty: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": "sell",
                "type": "market",
                "time_in_force": "day",
                "client_order_id": client_order_id,
            },
        )

    async def submit_stop_sell(
        self,
        symbol: str,
        qty: str,
        stop_price: str,
        client_order_id: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v2/orders",
            json={
                "symbol": symbol,
                "qty": qty,
                "side": "sell",
                "type": "stop",
                "time_in_force": "day",
                "stop_price": stop_price,
                "client_order_id": client_order_id,
            },
        )

    async def replace_stop_order(
        self,
        order_id: str,
        stop_price: str,
    ) -> dict[str, Any]:
        existing = await self._request("GET", f"/v2/orders/{order_id}")
        existing_client_order_id = str(existing.get("client_order_id") or "")
        payload: dict[str, Any] = {"stop_price": stop_price}

        if (
            existing_client_order_id.startswith("anevum-")
            and "-hardstop-" in existing_client_order_id
        ):
            base = existing_client_order_id.rsplit("-", 1)[0]
            payload["client_order_id"] = f"{base}-{uuid4().hex[:12]}"[:128]

        return await self._request(
            "PATCH",
            f"/v2/orders/{order_id}",
            json=payload,
        )

    async def cancel_order(self, order_id: str) -> None:
        try:
            await self._request("DELETE", f"/v2/orders/{order_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
