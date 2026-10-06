"""Paper-only observer/assignment adapter; no broker or live-authority API."""
from __future__ import annotations

import os
from typing import Any
import httpx

from .btc_discovery_contract import LIVE_ID, resolve_strategy, valid_assignment


class BtcPaperSelection:
    def __init__(self, settings):
        self.settings = settings
        self.assignment = None
        self.error = None
        self.entries_allowed = False
        self.pending_candidate_id = None

    @property
    def enabled(self):
        return (getattr(self.settings, "trading_mode", None) == "paper" and self.settings.crypto_execution_mode == "btc_direct_paper"
                and os.getenv("BTC_DISCOVERY_PAPER_SELECTION", "false").lower() == "true")

    async def sync(self, engine, positions, orders, recent_orders, *, previous_error=None):
        if not self.enabled:
            return
        self.entries_allowed = False
        token = self.settings.trading_ingest_token
        base = os.getenv("BTC_DISCOVERY_CORE_URL", "http://alpaca-trader.railway.internal:8080/v1/btc-discovery").rstrip("/")
        headers = {"x-anevum-scheduler-token": token}
        try:
            if not token:
                raise ValueError("btc_paper_core_token_missing")
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(base + "/assignment", headers=headers)
                response.raise_for_status()
                payload = response.json()
                if payload.get("ok") is not True or payload.get("live_authority") is not False:
                    raise ValueError("invalid_btc_assignment_envelope")
                assignment = valid_assignment(payload.get("assignment"))
                if assignment is None:
                    if self.assignment or engine.strategy.strategy_version_id != LIVE_ID:
                        raise ValueError("durable_paper_assignment_disappeared")
                    self.entries_allowed = True  # Preserve the already authorized baseline canary.
                    self.error = None
                    return
                runtime = assignment.get("paper_runtime") or {}
                same_activation = runtime.get("candidate_id") == assignment["candidate_id"]
                flat = not engine._crypto_positions(positions)
                crypto_orders = [x for x in orders if str(x.get("symbol") or "").replace("/", "").upper() == "BTCUSD"]
                if not same_activation and (not flat or crypto_orders):
                    active = valid_assignment(payload.get("active_assignment"))
                    if active and runtime.get("candidate_id") == active["candidate_id"]:
                        engine.strategy = resolve_strategy("paper", active)
                        self.settings.crypto_strategy_version_id = engine.strategy.strategy_version_id
                        self.settings.crypto_strategy_family = engine.strategy.strategy_family
                        self.assignment = active
                    elif self.assignment is not None:
                        engine.strategy = resolve_strategy("paper", self.assignment)
                    self.pending_candidate_id = assignment["candidate_id"]
                    self.error = "PENDING_STRATEGY_CHANGE"
                    # Current baseline remains authorized until a safe boundary exists.
                    self.entries_allowed = False
                    return
                if same_activation and runtime.get("run_id") != self.settings.trading_run_id:
                    raise ValueError("paper_run_identity_changed")
                selected = resolve_strategy("paper", assignment)
                observation = {"candidate_id": assignment["candidate_id"], "trading_mode": "paper",
                    "run_id": self.settings.trading_run_id, "activate": True, "flat": flat,
                    "open_orders": len(crypto_orders), "orders": recent_orders,
                    "error": previous_error or engine.state.crypto_last_error}
                acknowledgment = await client.post(base + "/paper-observation", headers=headers, json=observation)
                acknowledgment.raise_for_status()
                ack = acknowledgment.json()
                if ack.get("ok") is not True or ack.get("candidate_id") != selected.strategy_version_id or ack.get("live_authority") is not False:
                    raise ValueError("paper_activation_not_durable")
                # Mutate only the crypto identity. Equity settings, run ownership and
                # broker authorization remain the existing canary's settings.
                engine.strategy = selected
                self.settings.crypto_strategy_version_id = selected.strategy_version_id
                self.settings.crypto_strategy_family = selected.strategy_family
                self.settings.crypto_stats_start_at_raw = ack["paper_runtime"]["activated_at"]
                self.assignment = assignment
                self.pending_candidate_id = None
                self.entries_allowed = assignment.get("entries_allowed") is True
                self.error = None
        except Exception as exc:
            self.error = "paper_assignment_unavailable:" + type(exc).__name__
            # Existing-position protection continues with the last verified strategy;
            # new entries never fall back to another research candidate.

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "candidate_id": (self.assignment or {}).get("candidate_id"),
                "status": "PENDING_STRATEGY_CHANGE" if self.pending_candidate_id else "ACTIVE" if self.assignment else "BASELINE",
                "pending_candidate_id": self.pending_candidate_id,
                "stage": (self.assignment or {}).get("stage"), "entries_allowed": self.entries_allowed,
                "error": self.error, "live_authority": False}
