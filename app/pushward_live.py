from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx


SnapshotProvider = Callable[[], Awaitable[dict[str, Any]]]


class PushWardLiveService:
    """Best-effort RHEN -> PushWard Live Activity delivery.

    Display-only. It cannot alter RHEN strategy, risk, broker state, positions,
    orders, or execution controls.
    """

    PERFORMANCE_SLUG = "rhen-performance"
    ACTIVITY_SLUG = "rhen-activity"
    STATUS_SLUG = "rhen-status"

    def __init__(self, settings: Any, snapshot_provider: SnapshotProvider):
        self.settings = settings
        self.snapshot_provider = snapshot_provider
        self.api_key = str(getattr(settings, "pushward_api_key", "") or "").strip()
        self.api_url = str(
            getattr(settings, "pushward_api_url", "") or "https://api.pushward.app"
        ).rstrip("/")
        self.public_feed_url = str(
            getattr(settings, "iren_public_feed_url", "")
            or "https://mfntzxheldzdvlokyntk.supabase.co/functions/v1/trading-public-feed"
        ).strip()
        self.interval_seconds = max(
            30,
            int(getattr(settings, "pushward_interval_seconds", 300) or 300),
        )
        self.heartbeat_seconds = max(
            300,
            int(getattr(settings, "pushward_heartbeat_seconds", 1800) or 1800),
        )

        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._client: httpx.AsyncClient | None = None
        self._fingerprints: dict[str, str] = {}
        self._last_sent_at: dict[str, float] = {}
        self._quota_checked_at = 0.0

        self.authenticated = False
        self.subscribed: bool | None = None
        self.updates_used: int | None = None
        self.updates_limit: int | None = None
        self.quota_resets_at: str | None = None
        self.last_error: str | None = None
        self.last_success_at: str | None = None
        self.push_count = 0
        self.failed_push_count = 0
        self.activities_ready = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.api_url)

    def status(self) -> dict[str, Any]:
        remaining = None
        if self.updates_limit is not None and self.updates_used is not None:
            remaining = max(self.updates_limit - self.updates_used, 0)
        return {
            "running": self._task is not None,
            "configured": self.configured,
            "authenticated": self.authenticated,
            "activities_ready": self.activities_ready,
            "subscribed": self.subscribed,
            "updates_used": self.updates_used,
            "updates_limit": self.updates_limit,
            "updates_remaining": remaining,
            "quota_resets_at": self.quota_resets_at,
            "interval_seconds": self.interval_seconds,
            "heartbeat_seconds": self.heartbeat_seconds,
            "push_count": self.push_count,
            "failed_push_count": self.failed_push_count,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "activities": [
                self.PERFORMANCE_SLUG,
                self.ACTIVITY_SLUG,
                self.STATUS_SLUG,
            ],
        }

    async def start(self) -> None:
        if self._task is not None:
            return
        self._client = httpx.AsyncClient(timeout=12.0)
        if not self.configured:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def wake(self) -> None:
        if self._task is not None:
            self._wake.set()

    async def sync_now(self) -> None:
        if not self.configured:
            return
        await self._ensure_account()
        await self._ensure_activities()
        await self._sync_surfaces(force=True)

    async def _loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self._ensure_account()
                await self._ensure_activities()
                await self._sync_surfaces(force=False)
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:500]
                self.failed_push_count += 1
                print(
                    "PUSHWARD_LIVE_ERROR",
                    {"error": type(exc).__name__},
                    flush=True,
                )

            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    async def _ensure_account(self) -> None:
        now = time.time()
        if self.authenticated and now - self._quota_checked_at < 15 * 60:
            return
        response = await self._request("GET", "/auth/me")
        payload = response.json()
        self.authenticated = True
        self.subscribed = payload.get("subscribed")
        self.updates_used = self._int_or_none(payload.get("live_activity_updates_used"))
        self.updates_limit = self._int_or_none(payload.get("live_activity_updates_limit"))
        self.quota_resets_at = payload.get("quota_resets_at")
        self._quota_checked_at = now

    async def _ensure_activities(self) -> None:
        definitions = {
            self.PERFORMANCE_SLUG: "IREN · RHEN PERFORMANCE",
            self.ACTIVITY_SLUG: "IREN · RHEN ACTIVITY",
            self.STATUS_SLUG: "IREN · RHEN STATUS",
        }
        for slug, name in definitions.items():
            response = await self._request("GET", f"/activities/{slug}", allow_404=True)
            if response.status_code == 404:
                await self._request(
                    "POST",
                    "/activities",
                    json_body={"slug": slug, "name": name},
                )
        self.activities_ready = True

    async def _sync_surfaces(self, *, force: bool) -> None:
        private_snapshot, public_feed = await asyncio.gather(
            self.snapshot_provider(),
            self._public_feed(),
        )
        model = self._display_model(private_snapshot, public_feed)

        await self._push_performance(model, force=force)
        await self._push_status(model, force=force)
        await self._push_activity(model, force=force)

    async def _push_performance(self, model: dict[str, Any], *, force: bool) -> None:
        value = round(float(model["return_pct"]), 2)
        content = {
            "template": "timeline",
            "value": {"Return": value},
            "unit": "%",
            "decimals": 2,
            "smoothing": True,
            "primary_series": "Return",
            "state": f'{model["system_state"]} · {model["market_state"]}',
            "icon": "chart.xyaxis.line",
            "accent_color": "#6FD1FF",
            "background_color": "#071426",
            "text_color": "#C9D7EA",
            "tap_action": {
                "url": "https://anevum.com/iren",
                "foreground": True,
            },
        }
        fingerprint = self._fingerprint(
            {
                "return": value,
                "system": model["system_state"],
                "market": model["market_state"],
            }
        )

        # On a limited/free tier, preserve quota by sampling the graph at most
        # every 30 minutes. Paid/unlimited accounts can use the configured cadence.
        minimum_interval = self.interval_seconds
        if self.updates_limit is not None:
            minimum_interval = max(minimum_interval, 30 * 60)
            remaining = max((self.updates_limit or 0) - (self.updates_used or 0), 0)
            if remaining < 25:
                minimum_interval = max(minimum_interval, 60 * 60)

        await self._patch_if_changed(
            self.PERFORMANCE_SLUG,
            content,
            fingerprint,
            force=force,
            minimum_interval=minimum_interval,
        )

    async def _push_status(self, model: dict[str, Any], *, force: bool) -> None:
        content = {
            "template": "board",
            "state": f'{model["system_state"]} · {model["market_state"]}',
            "icon": "waveform.path.ecg",
            "accent_color": "#6FD1FF",
            "background_color": "#071426",
            "text_color": "#C9D7EA",
            "tap_action": {
                "url": "https://anevum.com/iren",
                "foreground": True,
            },
            "tiles": [
                {
                    "label": "Market",
                    "value": model["market_state"],
                    "icon": "clock",
                    "color": "#6FD1FF",
                    "trend": "flat",
                    "url_action": {
                        "url": "https://anevum.com/iren",
                        "foreground": True,
                    },
                },
                {
                    "label": "Positions",
                    "value": str(model["open_positions"]),
                    "icon": "chart.bar.doc.horizontal",
                    "color": "#C9D7EA",
                },
                {
                    "label": "Orders",
                    "value": str(model["pending_orders"]),
                    "icon": "arrow.left.arrow.right",
                    "color": "#C9D7EA",
                },
                {
                    "label": "Errors",
                    "value": str(model["errors_2h"]),
                    "icon": "exclamationmark.triangle",
                    "color": "orange" if model["errors_2h"] else "#6FD1FF",
                    "trend": "up" if model["errors_2h"] else "flat",
                },
            ],
        }
        fingerprint = self._fingerprint(
            {
                "system": model["system_state"],
                "market": model["market_state"],
                "positions": model["open_positions"],
                "orders": model["pending_orders"],
                "errors": model["errors_2h"],
            }
        )
        await self._patch_if_changed(
            self.STATUS_SLUG,
            content,
            fingerprint,
            force=force,
            minimum_interval=0,
        )

    async def _push_activity(self, model: dict[str, Any], *, force: bool) -> None:
        lines = model["activity_lines"]
        if not lines:
            lines = [
                {
                    "text": f'RHEN {model["system_state"]} · market {model["market_state"]}',
                    "at": int(time.time()),
                    "level": "info",
                }
            ]
        content = {
            "template": "log",
            "state": "RHEN live operations",
            "icon": "waveform.path.ecg.rectangle",
            "accent_color": "#6FD1FF",
            "background_color": "#071426",
            "text_color": "#C9D7EA",
            "tap_action": {
                "url": "https://anevum.com/iren",
                "foreground": True,
            },
            "lines": lines[:10],
        }
        stable_lines = [
            {"text": line.get("text"), "level": line.get("level")}
            for line in lines[:10]
        ]
        fingerprint = self._fingerprint(stable_lines)
        important_fingerprint = self._fingerprint(
            [
                line.get("text")
                for line in lines[:10]
                if str(line.get("text") or "").startswith(("ORDER ·", "POSITION ·"))
            ]
        )
        previous_important = self._fingerprints.get(
            f"{self.ACTIVITY_SLUG}:important"
        )
        urgent = previous_important is not None and previous_important != important_fingerprint
        minimum_interval = 0
        if self.updates_limit is not None and not urgent:
            minimum_interval = 10 * 60

        sent = await self._patch_if_changed(
            self.ACTIVITY_SLUG,
            content,
            fingerprint,
            force=force,
            minimum_interval=minimum_interval,
        )
        if sent:
            self._fingerprints[f"{self.ACTIVITY_SLUG}:important"] = important_fingerprint

    async def _patch_if_changed(
        self,
        slug: str,
        content: dict[str, Any],
        fingerprint: str,
        *,
        force: bool,
        minimum_interval: int,
    ) -> bool:
        now = time.time()
        previous = self._fingerprints.get(slug)
        last_sent = self._last_sent_at.get(slug, 0.0)

        if not force and previous == fingerprint:
            if now - last_sent < self.heartbeat_seconds:
                return False
        if not force and minimum_interval and now - last_sent < minimum_interval:
            return False

        await self._request(
            "PATCH",
            f"/activities/{slug}",
            json_body={"state": "ongoing", "content": content},
            merge_patch=True,
        )
        self._fingerprints[slug] = fingerprint
        self._last_sent_at[slug] = now
        self.push_count += 1
        self.last_success_at = datetime.utcnow().isoformat() + "Z"
        if self.updates_used is not None:
            self.updates_used += 1
        return True

    def _display_model(
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

        lines: list[dict[str, Any]] = []
        for order in recent_orders[:6]:
            if not isinstance(order, dict):
                continue
            symbol = str(order.get("symbol") or "--").upper()[:12]
            side = str(order.get("side") or "").upper()[:8]
            status = str(order.get("status") or "UPDATED").replace("_", " ").upper()[:32]
            timestamp = (
                order.get("filled_at")
                or order.get("canceled_at")
                or order.get("submitted_at")
            )
            lines.append(
                {
                    "text": f"ORDER · {side} {symbol} · {status}"[:180],
                    "at": self._unix_timestamp(timestamp),
                    "level": "info",
                }
            )

        for position in positions[:4]:
            if len(lines) >= 10 or not isinstance(position, dict):
                break
            symbol = str(position.get("symbol") or "--").upper()[:12]
            side = str(position.get("side") or "").upper()[:8]
            lines.append(
                {
                    "text": f"POSITION · {side} {symbol}"[:180],
                    "at": int(time.time()),
                    "level": "info",
                }
            )

        for event in public_feed.get("events") or []:
            if len(lines) >= 10 or not isinstance(event, dict):
                break
            label = str(event.get("label") or "RHEN event")[:150]
            kind = str(event.get("kind") or event.get("type") or "system").upper()[:24]
            level = "error" if "error" in kind.lower() else "warn" if "warn" in kind.lower() else "info"
            lines.append(
                {
                    "text": f"{kind} · {label}"[:180],
                    "at": self._unix_timestamp(event.get("at")),
                    "level": level,
                }
            )

        return {
            "system_state": system_state,
            "market_state": "OPEN" if market.get("is_open") else "CLOSED",
            "return_pct": self._optional_float(performance.get("account_return_pct")) or 0.0,
            "open_positions": len(positions),
            "pending_orders": pending_orders,
            "errors_2h": int(telemetry.get("errors_2h") or 0),
            "activity_lines": lines,
        }

    async def _public_feed(self) -> dict[str, Any]:
        response = await self._require_client().get(
            self.public_feed_url,
            headers={"accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        merge_patch: bool = False,
        allow_404: bool = False,
    ) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = (
                "application/merge-patch+json" if merge_patch else "application/json"
            )
        response = await self._require_client().request(
            method,
            f"{self.api_url}{path}",
            headers=headers,
            json=json_body,
        )
        if allow_404 and response.status_code == 404:
            return response
        response.raise_for_status()
        return response

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("PushWard client is not started")
        return self._client

    @staticmethod
    def _fingerprint(value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _unix_timestamp(value: Any) -> int:
        if value is None:
            return int(time.time())
        if isinstance(value, (int, float)):
            return int(value)
        raw = str(value).strip()
        if not raw:
            return int(time.time())
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return int(time.time())
