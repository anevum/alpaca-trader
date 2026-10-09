"""Alpaca Connect Trading API adapter for *member-owned* live accounts only.

A separate server-side OAuth callback/token vault must provision the grant.
No browser-supplied API keys, owner RHEN credentials or generic URL overrides.
Never call submit_order before the live gateway passes independent release gates.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .contracts import LiveIntent, LiveOrderDenied, MemberBinding

LIVE_TRADING_BASE = "https://api.alpaca.markets"
TOKEN_MIN_LENGTH = 16


class AlpacaConnectLiveBroker:
    def __init__(
        self,
        verified_binding: MemberBinding,
        server_token_resolver: Callable[[str], str],
        *,
        transport: Callable[[str, str, dict | None, str], dict] | None = None,
    ):
        if not isinstance(verified_binding, MemberBinding):
            raise LiveOrderDenied("Server-verified member binding required")
        if not callable(server_token_resolver):
            raise LiveOrderDenied("Encrypted backend token resolver required")
        self._binding = verified_binding
        self._resolver = server_token_resolver
        self._transport = transport or self._https_request

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if method not in ("GET", "POST") or not path.startswith("/v2/"):
            raise LiveOrderDenied("Disallowed broker operation")
        # The identifier is a SERVER-scoped connection ID, never an access token.
        token = self._resolver(self._binding.connection_id)
        if not isinstance(token, str) or len(token) < TOKEN_MIN_LENGTH or any(c.isspace() for c in token):
            raise LiveOrderDenied("Live OAuth grant is unavailable or revoked")
        result = self._transport(method, path, payload, token)
        if not isinstance(result, dict):
            raise LiveOrderDenied("Invalid broker response")
        return result

    @staticmethod
    def _https_request(method: str, path: str, payload: dict | None, token: str) -> dict:
        # Host is a constant; callers can NEVER inject a server URL.
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8") if payload is not None else None
        request = Request(
            LIVE_TRADING_BASE + path,
            data=data,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=7) as response:
                if response.status < 200 or response.status >= 300:
                    raise LiveOrderDenied("Live broker did not accept request")
                raw = response.read(65537)
            if len(raw) > 65536:
                raise LiveOrderDenied("Unexpectedly large broker response")
            return json.loads(raw)
        except (HTTPError, URLError, ValueError, OSError) as error:
            # Never propagate broker HTTP bodies or authorization tokens to member logs.
            raise LiveOrderDenied("Alpaca Connect request failed or access revoked") from None

    def verify_account(self) -> str:
        account = self._request("GET", "/v2/account")
        if str(account.get("id", "")) != self._binding.broker_account_id:
            raise LiveOrderDenied("OAuth connection resolves to another brokerage account")
        if account.get("status") != "ACTIVE" or account.get("trading_blocked") is not False or account.get("account_blocked") is not False:
            raise LiveOrderDenied("Broker has not confirmed an active, unblocked live account")
        return self._binding.broker_account_id

    def submit_limit_day(self, intent: LiveIntent, client_order_id: str) -> dict:
        if not isinstance(intent, LiveIntent) or not isinstance(client_order_id, str) or not (1 <= len(client_order_id) <= 128):
            raise LiveOrderDenied("Invalid live order intent")
        # Whole-share LIMIT DAY orders only; deliberately exclude market, fractionals,
        # extended sessions, GTC, options, crypto, shorting and margin expansion.
        limit = str((Decimal(intent.limit_price_cents) / Decimal(100)).quantize(Decimal("0.01")))
        body = {
            "symbol": intent.symbol,
            "side": intent.side,
            "qty": str(intent.quantity),
            "type": "limit",
            "limit_price": limit,
            "time_in_force": "day",
            "extended_hours": False,
            "client_order_id": client_order_id,
            "order_class": "simple",
        }
        response = self._request("POST", "/v2/orders", body)
        if response.get("client_order_id") != client_order_id or not response.get("id"):
            raise LiveOrderDenied("Uncertain broker acknowledgement: reconciliation required")
        if response.get("symbol") != intent.symbol or response.get("side") != intent.side:
            raise LiveOrderDenied("Broker acknowledgement does not match requested order")
        return {"broker_order_id": response["id"], "status": response.get("status", "unknown")}

    def find_by_client_order_id(self, client_order_id: str) -> dict | None:
        if not isinstance(client_order_id, str) or not client_order_id.startswith("rhcl-"):
            raise LiveOrderDenied("Invalid RHEN Cloud order identifier")
        try:
            order = self._request(
                "GET", "/v2/orders:by_client_order_id?client_order_id=" + quote(client_order_id, safe="")
            )
        except LiveOrderDenied:
            # A lookup failure is INDETERMINATE, not proof that no order exists.
            raise
        if order.get("client_order_id") != client_order_id or not order.get("id"):
            raise LiveOrderDenied("Broker reconciliation response mismatch")
        return {"broker_order_id": order["id"], "status": order.get("status", "unknown")}
