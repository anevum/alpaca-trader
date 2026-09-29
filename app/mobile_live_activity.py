from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Awaitable, Callable

import httpx
import jwt


SnapshotProvider = Callable[[], Awaitable[dict[str, Any]]]


class MobileLiveActivityService:
    """Best-effort RHEN -> ActivityKit delivery.

    This service is display-only. It has no authority to alter strategy, risk,
    sizing, orders, positions, broker state, or execution controls.
    """

    def __init__(self, settings: Any, snapshot_provider: SnapshotProvider):
        self.settings = settings
        self.snapshot_provider = snapshot_provider
        self.registry_url = str(
            getattr(settings, "iren_mobile_registry_url", "")
            or "https://mfntzxheldzdvlokyntk.supabase.co/functions/v1/iren-mobile-registry"
        ).strip()
        self.public_feed_url = str(
            getattr(settings, "iren_public_feed_url", "")
            or "https://mfntzxheldzdvlokyntk.supabase.co/functions/v1/trading-public-feed"
        ).strip()
        self.ingest_token = str(getattr(settings, "trading_ingest_token", "") or "").strip()

        self.team_id = str(getattr(settings, "iren_apns_team_id", "") or "").strip()
        self.key_id = str(getattr(settings, "iren_apns_key_id", "") or "").strip()
        self.private_key = str(getattr(settings, "iren_apns_private_key", "") or "").replace("\\n", "\n").strip()
        self.bundle_id = str(getattr(settings, "iren_bundle_id", "com.anevum.iren") or "com.anevum.iren").strip()

        self.interval_seconds = max(
            15,
            int(getattr(settings, "iren_mobile_push_interval_seconds", 30) or 30),
        )
        self.heartbeat_seconds = max(
            60,
            int(getattr(settings, "iren_mobile_push_heartbeat_seconds", 300) or 300),
        )

        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._client: httpx.AsyncClient | None = None
        self._jwt_token: str | None = None
        self._jwt_issued_at = 0.0
        self._last_fingerprint: str | None = None
        self._last_push_at = 0.0
        self.last_error: str | None = None
        self.last_success_at: str | None = None
        self.push_count = 0
        self.failed_push_count = 0

    @property
    def registry_configured(self) -> bool:
        return bool(self.registry_url and self.ingest_token)

    @property
    def apns_configured(self) -> bool:
        return bool(
            self.registry_configured
            and self.team_id
            and self.key_id
            and self.private_key
            and self.bundle_id
        )

    def status(self) -> dict[str, Any]:
        return {
            "running": self._task is not None,
            "registry_configured": self.registry_configured,
            "apns_configured": self.apns_configured,
            "bundle_id": self.bundle_id,
            "interval_seconds": self.interval_seconds,
            "heartbeat_seconds": self.heartbeat_seconds,
            "push_count": self.push_count,
            "failed_push_count": self.failed_push_count,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(http2=True, timeout=10.0)
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def wake(self) -> None:
        self._wake.set()

    async def register(
        self,
        *,
        push_token: str,
        activity_id: str,
        apns_environment: str,
    ) -> dict[str, Any]:
        if not self.registry_configured:
            raise RuntimeError("IREN mobile registry is not configured")
        client = self._require_client()
        response = await client.post(
            f"{self.registry_url}?action=register",
            headers=self._registry_headers(),
            json={
                "push_token": push_token,
                "activity_id": activity_id,
                "apns_environment": apns_environment,
                "bundle_id": self.bundle_id,
            },
        )
        response.raise_for_status()
        self.wake()
        payload = response.json()
        return {
            **payload,
            "remote_push_configured": self.apns_configured,
        }

    async def deactivate(self, activity_id: str) -> None:
        if not self.registry_configured:
            return
        try:
            await self._require_client().post(
                f"{self.registry_url}?action=deactivate",
                headers=self._registry_headers(),
                json={"activity_id": activity_id},
            )
        except Exception:
            return

    async def _loop(self) -> None:
        while True:
            try:
                await self._push_if_needed()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(
                    "IREN_LIVE_ACTIVITY_ERROR",
                    {"error": type(exc).__name__},
                    flush=True,
                )

            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    async def _push_if_needed(self) -> None:
        if not self.registry_configured:
            return

        tokens = await self._active_tokens()
        if not tokens:
            return

        private_snapshot, public_feed = await asyncio.gather(
            self.snapshot_provider(),
            self._public_feed(),
        )
        content_state = self._content_state(private_snapshot, public_feed)
        fingerprint = hashlib.sha256(
            json.dumps(content_state, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        now = time.time()

        if (
            fingerprint == self._last_fingerprint
            and now - self._last_push_at < self.heartbeat_seconds
        ):
            return

        # Token registration remains useful before APNs credentials are installed.
        if not self.apns_configured:
            self._last_fingerprint = fingerprint
            return

        delivered_any = False
        for token in tokens:
            delivered = await self._push_token(
                token=token,
                content_state=content_state,
                fingerprint=fingerprint,
            )
            delivered_any = delivered_any or delivered

        if delivered_any:
            self._last_fingerprint = fingerprint
            self._last_push_at = now
            self.last_success_at = self._iso_now()

    async def _active_tokens(self) -> list[dict[str, Any]]:
        response = await self._require_client().get(
            f"{self.registry_url}?action=list",
            headers=self._registry_headers(),
        )
        response.raise_for_status()
        payload = response.json()
        tokens = payload.get("tokens")
        return tokens if isinstance(tokens, list) else []

    async def _public_feed(self) -> dict[str, Any]:
        response = await self._require_client().get(
            self.public_feed_url,
            headers={"accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    def _content_state(
        self,
        private_snapshot: dict[str, Any],
        public_feed: dict[str, Any],
    ) -> dict[str, Any]:
        performance = public_feed.get("performance") or {}
        telemetry = public_feed.get("telemetry") or {}
        bot = private_snapshot.get("bot") or {}
        market = private_snapshot.get("market") or {}
        positions = private_snapshot.get("positions") or []
        open_orders = private_snapshot.get("open_orders") or []
        recent_orders = private_snapshot.get("recent_orders") or []

        system_state = "RUNNING"
        if bot.get("runtime_paused"):
            system_state = "PAUSED"
        elif bot.get("last_error") or int(telemetry.get("errors_2h") or 0) > 0:
            system_state = "DEGRADED"
        elif not bot.get("bot_armed"):
            system_state = "DISARMED"

        curve = performance.get("curve") or []
        points: list[float] = []
        for item in curve[-24:]:
            if not isinstance(item, dict):
                continue
            try:
                points.append(float(item.get("return_pct")))
            except (TypeError, ValueError):
                continue
        if not points:
            points = [0.0]

        activity: list[dict[str, Any]] = []
        for order in recent_orders[:3]:
            if not isinstance(order, dict):
                continue
            symbol = str(order.get("symbol") or "--").upper()
            side = str(order.get("side") or "").upper()
            status = str(order.get("status") or "UPDATED").replace("_", " ").upper()
            activity.append({
                "id": str(order.get("id") or f"order-{len(activity)}"),
                "kind": "ORDER",
                "headline": f"{side} {symbol}".strip(),
                "detail": status,
                "timestamp": str(
                    order.get("filled_at")
                    or order.get("canceled_at")
                    or order.get("submitted_at")
                    or ""
                ),
                "isPrivate": True,
            })

        for event in (public_feed.get("events") or []):
            if len(activity) >= 3 or not isinstance(event, dict):
                break
            activity.append({
                "id": hashlib.sha1(
                    json.dumps(event, sort_keys=True).encode()
                ).hexdigest()[:16],
                "kind": str(event.get("type") or event.get("kind") or "EVENT").upper(),
                "headline": str(event.get("label") or "RHEN event"),
                "detail": str(event.get("kind") or "system").upper(),
                "timestamp": str(event.get("at") or ""),
                "isPrivate": False,
            })

        pending_statuses = {
            "new",
            "accepted",
            "pending_new",
            "partially_filled",
            "held",
            "pending_replace",
            "accepted_for_bidding",
        }
        pending_orders = sum(
            1
            for order in open_orders
            if isinstance(order, dict)
            and str(order.get("status") or "").lower() in pending_statuses
        )

        return {
            "systemState": system_state,
            "marketState": "OPEN" if market.get("is_open") else "CLOSED",
            "accountReturnPct": self._optional_float(performance.get("account_return_pct")),
            "drawdownPct": self._optional_float(performance.get("max_drawdown_pct")),
            "performancePoints": points,
            "openPositions": len(positions),
            "pendingOrders": pending_orders,
            "errors2h": int(telemetry.get("errors_2h") or 0),
            "latestActivity": activity,
            # Swift Date's default Codable representation is seconds since
            # Apple's 2001-01-01 reference date.
            "updatedAt": time.time() - 978307200.0,
        }

    async def _push_token(
        self,
        *,
        token: dict[str, Any],
        content_state: dict[str, Any],
        fingerprint: str,
    ) -> bool:
        activity_id = str(token.get("activity_id") or "")
        push_token = str(token.get("push_token") or "")
        environment = str(token.get("apns_environment") or "production").lower()
        bundle_id = str(token.get("bundle_id") or self.bundle_id)
        if not activity_id or not push_token:
            return False

        host = (
            "https://api.sandbox.push.apple.com"
            if environment == "sandbox"
            else "https://api.push.apple.com"
        )
        url = f"{host}/3/device/{push_token}"
        started = time.monotonic()
        response_status = 0
        apns_id = None
        reason = None
        delivered = False

        try:
            response = await self._require_client().post(
                url,
                headers={
                    "authorization": f"bearer {self._apns_jwt()}",
                    "apns-push-type": "liveactivity",
                    "apns-topic": f"{bundle_id}.push-type.liveactivity",
                    "apns-priority": "10",
                    "content-type": "application/json",
                },
                json={
                    "aps": {
                        "timestamp": int(time.time()),
                        "event": "update",
                        "content-state": content_state,
                        "stale-date": int(time.time()) + 180,
                    }
                },
            )
            response_status = response.status_code
            apns_id = response.headers.get("apns-id")
            delivered = response.is_success
            if not delivered:
                try:
                    reason = str(response.json().get("reason") or "apns_error")
                except Exception:
                    reason = "apns_error"
            if delivered:
                self.push_count += 1
            else:
                self.failed_push_count += 1
        except Exception as exc:
            self.failed_push_count += 1
            reason = f"{type(exc).__name__}: {exc}"[:500]
        finally:
            await self._record_delivery(
                activity_id=activity_id,
                fingerprint=fingerprint,
                status=response_status,
                apns_id=apns_id,
                reason=reason,
                delivered=delivered,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        return delivered

    async def _record_delivery(
        self,
        *,
        activity_id: str,
        fingerprint: str,
        status: int,
        apns_id: str | None,
        reason: str | None,
        delivered: bool,
        duration_ms: int,
    ) -> None:
        if not self.registry_configured:
            return
        try:
            await self._require_client().post(
                f"{self.registry_url}?action=delivery",
                headers=self._registry_headers(),
                json={
                    "activity_id": activity_id,
                    "event": "update",
                    "payload_fingerprint": fingerprint,
                    "apns_status": status,
                    "apns_id": apns_id,
                    "response_reason": reason,
                    "delivered": delivered,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            return

    def _apns_jwt(self) -> str:
        now = time.time()
        if self._jwt_token and now - self._jwt_issued_at < 50 * 60:
            return self._jwt_token
        self._jwt_token = jwt.encode(
            {"iss": self.team_id, "iat": int(now)},
            self.private_key,
            algorithm="ES256",
            headers={"kid": self.key_id},
        )
        self._jwt_issued_at = now
        return self._jwt_token

    def _registry_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-anevum-ingest-token": self.ingest_token,
        }

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("IREN mobile delivery client is not started")
        return self._client

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _iso_now() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()
