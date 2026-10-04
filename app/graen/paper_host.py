from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from app.alpaca_client import AlpacaClient
from app.config import Settings
from app.crypto_layer import CryptoMarketDataClient
from app.persistence import TradingEventSink
from app.research_agent.strategy_runner import (
    RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
)
from graen.crypto.candidate_shadow import CandidateForwardShadow


UTC = timezone.utc
PAPER_HOST_VERSION = "graen.paper-canary-host.v1"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _decimal_env(name: str, default: str) -> Decimal:
    try:
        return Decimal(str(os.getenv(name, default)))
    except Exception:
        return Decimal(default)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _d(value: Any) -> Decimal:
    try:
        result = Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


class PaperActivationRequest(BaseModel):
    problem_id: str
    graen_run_id: str
    campaign_id: str
    candidate_methodology: str
    candidate_spec: dict[str, Any]
    shadow_checkpoint: dict[str, Any]
    shadow_activation_id: str = ""


class GraenPaperCanaryHost:
    """Generic paper-only execution adapter for frozen GRAEN candidates.

    It intentionally reuses the deterministic forward-shadow signal clock, then
    routes only those entry/exit events to an Alpaca paper account. It cannot
    authorize or submit live-account orders.
    """

    def __init__(
        self,
        settings: Settings,
        client: AlpacaClient,
        event_sink: TradingEventSink,
        *,
        token: str | None = None,
        state_path: str | Path | None = None,
        poll_seconds: int | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.market_data = CryptoMarketDataClient(settings)
        self.event_sink = event_sink
        self.shadow = CandidateForwardShadow(settings)
        self.token = str(
            token
            if token is not None
            else os.getenv("GRAEN_PAPER_TOKEN", "")
        ).strip()
        root = str(
            os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "") or "/data"
        ).strip()
        self.state_path = Path(
            state_path
            if state_path is not None
            else os.getenv(
                "GRAEN_PAPER_STATE_PATH",
                str(Path(root) / "graen-paper-canary-state.json"),
            )
        )
        raw_poll = (
            poll_seconds
            if poll_seconds is not None
            else os.getenv("GRAEN_PAPER_POLL_SECONDS", "60")
        )
        try:
            self.poll_seconds = max(15, min(int(raw_poll), 3600))
        except (TypeError, ValueError):
            self.poll_seconds = 60

        self.enabled = _truthy(os.getenv("GRAEN_PAPER_ENABLED"))
        self.acknowledged = (
            str(
                os.getenv(
                    "I_ACKNOWLEDGE_GRAEN_AUTONOMOUS_PAPER",
                    "NO",
                )
            ).strip().upper()
            == "YES"
        )
        self.order_notional = _decimal_env(
            "GRAEN_PAPER_ORDER_NOTIONAL", "1.00"
        )
        self.max_order_notional = _decimal_env(
            "GRAEN_PAPER_MAX_ORDER_NOTIONAL", "1.00"
        )
        self.max_total_notional = _decimal_env(
            "GRAEN_PAPER_MAX_TOTAL_POSITION_NOTIONAL", "2.00"
        )
        self.max_positions = _int_env(
            "GRAEN_PAPER_MAX_CONCURRENT_POSITIONS", 1, 1, 10
        )
        self.max_entries_24h = _int_env(
            "GRAEN_PAPER_MAX_ENTRIES_24H", 4, 1, 100
        )
        self.stop_pct = _decimal_env("GRAEN_PAPER_STOP_PCT", "0.05")
        self.stop_limit_buffer_pct = _decimal_env(
            "GRAEN_PAPER_STOP_LIMIT_BUFFER_PCT", "0.01"
        )
        self.max_spread_pct = _decimal_env(
            "GRAEN_PAPER_MAX_SPREAD_PCT", "0.005"
        )
        self.max_quote_age_seconds = _int_env(
            "GRAEN_PAPER_MAX_QUOTE_AGE_SECONDS", 30, 1, 300
        )
        self.max_fill_slippage_pct = _decimal_env(
            "GRAEN_PAPER_MAX_FILL_SLIPPAGE_PCT", "0.01"
        )
        self.min_round_trips = _int_env(
            "GRAEN_PAPER_MIN_ROUND_TRIPS", 20, 5, 200
        )
        self.min_independent_days = _int_env(
            "GRAEN_PAPER_MIN_INDEPENDENT_DAYS", 5, 2, 60
        )
        self.max_review_round_trips = _int_env(
            "GRAEN_PAPER_MAX_REVIEW_ROUND_TRIPS", 40, 10, 400
        )
        self.max_review_days = _int_env(
            "GRAEN_PAPER_MAX_REVIEW_DAYS", 45, 7, 180
        )

        self.activation: dict[str, Any] | None = None
        self.rounds: dict[str, dict[str, Any]] = {}
        self.completed: dict[str, dict[str, Any]] = {}
        self.circuit_open_reason: str | None = None
        self.ambiguous_submission_count = 0
        self.execution_error_count = 0
        self.lock = asyncio.Lock()
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_cycle_at: datetime | None = None
        self.last_persisted_at: datetime | None = None
        self.last_error: str | None = None

        self._validate_static_limits()

    def _validate_static_limits(self) -> None:
        if self.order_notional <= 0:
            raise ValueError("GRAEN_PAPER_ORDER_NOTIONAL must be positive")
        if (
            self.max_order_notional <= 0
            or self.order_notional > self.max_order_notional
        ):
            raise ValueError("paper order notional exceeds fixed maximum")
        if self.max_total_notional < self.max_order_notional:
            raise ValueError(
                "paper total-position limit cannot be below order limit"
            )
        if not Decimal("0") < self.stop_pct < Decimal("0.20"):
            raise ValueError("GRAEN_PAPER_STOP_PCT must be between 0 and 0.20")
        if not Decimal("0") <= self.stop_limit_buffer_pct < Decimal("0.10"):
            raise ValueError(
                "GRAEN_PAPER_STOP_LIMIT_BUFFER_PCT must be below 0.10"
            )
        if not Decimal("0") < self.max_spread_pct < Decimal("0.10"):
            raise ValueError(
                "GRAEN_PAPER_MAX_SPREAD_PCT must be between 0 and 0.10"
            )

    @property
    def auth_configured(self) -> bool:
        return len(self.token) >= 32

    @property
    def paper_execution_authorized(self) -> bool:
        return bool(
            self.enabled
            and self.acknowledged
            and self.settings.trading_mode == "paper"
            and self.settings.execution_enabled
            and self.settings.bot_armed
            and not self.settings.live_trading
            and not self.settings.live_execution_authorized
            and not self.settings.crypto_execution_enabled
            and self.settings.credentials_configured
        )

    @property
    def live_execution_authorized(self) -> bool:
        return False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def require_token(self, supplied: str | None) -> None:
        if not self.auth_configured:
            raise HTTPException(
                status_code=503,
                detail="GRAEN_PAPER_TOKEN is not configured",
            )
        if supplied is None or not hmac.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @staticmethod
    def _safe(value: str) -> str:
        return "".join(
            ch.lower() if ch.isalnum() else "-"
            for ch in str(value)
        ).strip("-")[:36]

    @staticmethod
    def _activation_id(payload: Mapping[str, Any]) -> str:
        material = {
            "problem_id": payload.get("problem_id"),
            # graen_run_id is a transient worker attempt identity. The paper
            # activation must survive a crash/reclaim between remote activation
            # and local metadata persistence.
            "campaign_id": payload.get("campaign_id"),
            "candidate_methodology": payload.get(
                "candidate_methodology"
            ),
            "candidate_spec": payload.get("candidate_spec"),
            "shadow_checkpoint": payload.get("shadow_checkpoint"),
            "shadow_activation_id": payload.get("shadow_activation_id"),
        }
        digest = hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return "paper-" + digest[:32]

    def _client_order_id(
        self,
        *,
        symbol: str,
        kind: str,
        round_id: str,
    ) -> str:
        activation = str(
            (self.activation or {}).get("activation_id") or "inactive"
        )
        prefix = hashlib.sha256(activation.encode()).hexdigest()[:10]
        return (
            "anevum-graen-paper-"
            + prefix
            + "-"
            + self._safe(symbol)
            + "-"
            + self._safe(kind)
            + "-"
            + self._safe(round_id)[:18]
        )[:128]

    @staticmethod
    def _qty(notional: Decimal, price: Decimal) -> Decimal:
        if price <= 0:
            return Decimal("0")
        return (notional / price).quantize(
            Decimal("0.000000001"),
            rounding=ROUND_DOWN,
        )

    def _snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": "graen.paper-canary-state.v1",
            "host_version": PAPER_HOST_VERSION,
            "activation": self.activation,
            "shadow": self.shadow.snapshot(),
            "rounds": self.rounds,
            "completed": self.completed,
            "circuit_open_reason": self.circuit_open_reason,
            "ambiguous_submission_count": self.ambiguous_submission_count,
            "execution_error_count": self.execution_error_count,
            "live_execution_authorized": False,
        }

    def _atomic_write(self, payload: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.state_path)
        self.last_persisted_at = datetime.now(UTC)

    async def persist(self) -> None:
        await asyncio.to_thread(self._atomic_write, self._snapshot())

    def _restore_sync(self) -> None:
        if not self.state_path.exists():
            return
        with self.state_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, Mapping):
            raise ValueError("paper canary state must be an object")
        self.activation = (
            dict(payload.get("activation"))
            if isinstance(payload.get("activation"), Mapping)
            else None
        )
        shadow = payload.get("shadow")
        if isinstance(shadow, Mapping):
            self.shadow.restore(shadow)
        rounds = payload.get("rounds")
        self.rounds = (
            {
                str(key): dict(value)
                for key, value in rounds.items()
                if isinstance(value, Mapping)
            }
            if isinstance(rounds, Mapping)
            else {}
        )
        completed = payload.get("completed")
        self.completed = (
            {
                str(key): dict(value)
                for key, value in completed.items()
                if isinstance(value, Mapping)
            }
            if isinstance(completed, Mapping)
            else {}
        )
        self.circuit_open_reason = (
            str(payload.get("circuit_open_reason"))
            if payload.get("circuit_open_reason")
            else None
        )
        self.ambiguous_submission_count = int(
            payload.get("ambiguous_submission_count") or 0
        )
        self.execution_error_count = int(
            payload.get("execution_error_count") or 0
        )

    async def restore(self) -> None:
        try:
            await asyncio.to_thread(self._restore_sync)
            self.last_error = None
        except Exception as exc:
            self.activation = None
            self.rounds = {}
            self.circuit_open_reason = "restart_state_invalid"
            self.last_error = (
                "restore_failed:" + type(exc).__name__ + ":" + str(exc)
            )[:1000]

    async def _clean_paper_account_required(self) -> None:
        positions, open_orders = await asyncio.gather(
            self.client.positions(),
            self.client.open_orders(),
        )
        crypto_positions = [
            row
            for row in positions
            if "/" in str(row.get("symbol") or "")
            and _d(row.get("qty")) > 0
        ]
        crypto_orders = [
            row
            for row in open_orders
            if "/" in str(row.get("symbol") or "")
        ]
        if crypto_positions or crypto_orders:
            raise ValueError(
                "paper activation requires a clean dedicated crypto paper account"
            )

    async def activate(
        self,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.paper_execution_authorized:
            raise ValueError(
                "paper adapter is not authorized; require dedicated paper "
                "mode, GRAEN_PAPER_ENABLED=true, acknowledgement, execution "
                "enabled, bot armed, and crypto live lane disabled"
            )
        methodology = str(payload.get("candidate_methodology") or "")
        if methodology != STRATEGY_RUNNER_VERSION:
            raise ValueError(
                "paper adapter only accepts strategy-grammar candidates"
            )
        checkpoint = payload.get("shadow_checkpoint")
        if (
            not isinstance(checkpoint, Mapping)
            or str(checkpoint.get("status") or "").upper()
            != "READY_FOR_PAPER"
        ):
            raise ValueError("paper activation requires READY_FOR_PAPER evidence")
        candidate_spec = payload.get("candidate_spec")
        if not isinstance(candidate_spec, Mapping) or not candidate_spec:
            raise ValueError("candidate_spec is required")

        activation_id = self._activation_id(payload)
        async with self.lock:
            previous = str(
                (self.activation or {}).get("activation_id") or ""
            )
            if previous == activation_id:
                return {
                    "activation": dict(self.activation or {}),
                    "duplicate": True,
                    "paper_execution_authority": True,
                    "live_execution_authority": False,
                }
            completed = self.completed.get(activation_id)
            if completed is not None:
                return {
                    "activation": dict(completed.get("activation") or {}),
                    "duplicate": True,
                    "checkpoint": dict(completed.get("checkpoint") or {}),
                    "paper_execution_authority": True,
                    "live_execution_authority": False,
                }
            if self.activation is not None:
                raise ValueError(
                    "paper adapter already owns another candidate; complete "
                    "or retire it before activating a new one"
                )
            await self._clean_paper_account_required()
            now = datetime.now(UTC)
            activation = {
                "schema_version": "graen.paper-canary.activation.v1",
                "activation_id": activation_id,
                "problem_id": str(payload.get("problem_id") or ""),
                "graen_run_id": str(payload.get("graen_run_id") or ""),
                "campaign_id": str(payload.get("campaign_id") or ""),
                "candidate_methodology": methodology,
                "candidate_spec": dict(candidate_spec),
                "shadow_checkpoint": dict(checkpoint),
                "shadow_activation_id": str(
                    payload.get("shadow_activation_id") or ""
                ),
                "activated_at": now.isoformat(),
                "execution_class": "AUTONOMOUS_PAPER",
                "paper_only": True,
                "live_execution_authorized": False,
                "max_review_days": self.max_review_days,
            "promotion_authorized": False,
            }
            shadow_activation = {
                "schema_version": "graen.candidate_shadow.activation.v1",
                "activation_id": activation_id,
                "problem_id": activation["problem_id"],
                "graen_run_id": activation["graen_run_id"],
                "campaign_id": activation["campaign_id"],
                "epoch_index": 0,
                "generation": 1,
                "candidate_methodology": methodology,
                "candidate_id": str(
                    candidate_spec.get("candidate_id") or ""
                ),
                "candidate_spec": dict(candidate_spec),
                "velum_artifact_id": "",
                "evidence_phase": "PAPER_CANARY",
                "activated_at": now.isoformat(),
                "research_only": False,
                "promotion_authorized": False,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            self.shadow.activate(shadow_activation)
            self.activation = activation
            self.rounds = {}
            self.circuit_open_reason = None
            self.ambiguous_submission_count = 0
            self.execution_error_count = 0
            await self.persist()
            self._emit(
                "graen_paper_candidate_activated",
                {
                    **activation,
                    "live_execution_authorized": False,
                },
                event_key=activation_id + ":activated",
            )
            return {
                "activation": dict(activation),
                "duplicate": False,
                "paper_execution_authority": True,
                "live_execution_authority": False,
            }

    def _emit(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        event_key: str,
        symbol: str = "",
        occurred_at: str | None = None,
    ) -> None:
        self.event_sink.emit(
            event_type=event_type,
            event_key=("graen:paper:" + event_key)[:200],
            symbol=symbol,
            occurred_at=occurred_at,
            correlation_id=str(
                (self.activation or {}).get("activation_id") or ""
            ),
            payload={
                **dict(payload),
                "execution_class": "AUTONOMOUS_PAPER",
                "paper_only": True,
                "live_execution_authorized": False,
            },
        )

    async def _critical_intent(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        event_key: str,
        symbol: str,
    ) -> bool:
        return await self.event_sink.emit_critical(
            event_type=event_type,
            event_key=("graen:paper:" + event_key)[:200],
            symbol=symbol,
            correlation_id=str(
                (self.activation or {}).get("activation_id") or ""
            ),
            payload={
                **dict(payload),
                "execution_class": "AUTONOMOUS_PAPER",
                "paper_only": True,
                "live_execution_authorized": False,
            },
        )

    async def _quote(
        self,
        symbol: str,
    ) -> tuple[Decimal, Decimal, float | None, dict[str, Any]]:
        quotes = await self.market_data.latest_quotes([symbol])
        quote = quotes.get(symbol, {})
        bid = _d(quote.get("bp"))
        ask = _d(quote.get("ap"))
        midpoint = (
            (bid + ask) / Decimal("2")
            if bid > 0 and ask >= bid
            else Decimal("0")
        )
        spread = (
            (ask - bid) / midpoint
            if midpoint > 0
            else Decimal("999")
        )
        stamp = _stamp(quote.get("t"))
        age = (
            max((datetime.now(UTC) - stamp).total_seconds(), 0.0)
            if stamp is not None else None
        )
        return midpoint, spread, age, {
            "bid": str(bid),
            "ask": str(ask),
            "midpoint": str(midpoint),
            "spread_pct": str(spread),
            "quote_age_seconds": age,
            "quote_timestamp": quote.get("t"),
        }

    def _round_id(self, event: Mapping[str, Any]) -> str:
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        material = {
            "activation_id": (self.activation or {}).get("activation_id"),
            "symbol": event.get("symbol") or payload.get("symbol"),
            "signal_at": payload.get("signal_at"),
        }
        return hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()[:24]

    def _entries_24h(self) -> int:
        floor = datetime.now(UTC) - timedelta(hours=24)
        return sum(
            1
            for row in self.rounds.values()
            if (_stamp(row.get("opened_at")) or datetime.min.replace(tzinfo=UTC))
            >= floor
        )

    def _open_rounds(self) -> list[dict[str, Any]]:
        return [
            row
            for row in self.rounds.values()
            if str(row.get("status") or "") not in {
                "CLOSED", "REJECTED", "FAILED"
            }
        ]

    async def _recover_order(
        self,
        client_order_id: str,
    ) -> dict[str, Any] | None:
        for attempt in range(3):
            try:
                row = await self.client.order_by_client_order_id(
                    client_order_id
                )
            except Exception:
                row = None
            if row is not None:
                return row
            if attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))
        return None

    async def _handle_entry(
        self,
        event: Mapping[str, Any],
    ) -> None:
        if self.circuit_open_reason:
            return
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        symbol = str(event.get("symbol") or payload.get("symbol") or "").upper()
        if not symbol or "/" not in symbol:
            self.circuit_open_reason = "invalid_candidate_symbol"
            return
        round_id = self._round_id(event)
        if round_id in self.rounds:
            return
        if len(self._open_rounds()) >= self.max_positions:
            return
        if self._entries_24h() >= self.max_entries_24h:
            return
        projected = (
            Decimal(len(self._open_rounds())) * self.order_notional
            + self.order_notional
        )
        if projected > self.max_total_notional:
            return

        midpoint, spread, quote_age, quality = await self._quote(symbol)
        if (
            midpoint <= 0
            or quote_age is None
            or quote_age > self.max_quote_age_seconds
            or spread > self.max_spread_pct
        ):
            self._emit(
                "graen_paper_entry_rejected_market_quality",
                {
                    "round_id": round_id,
                    "market_quality": quality,
                },
                event_key=round_id + ":quality-reject",
                symbol=symbol,
            )
            return
        qty = self._qty(self.order_notional, midpoint)
        if qty <= 0:
            return

        buy_client_id = self._client_order_id(
            symbol=symbol,
            kind="buy",
            round_id=round_id,
        )
        stop_client_id = self._client_order_id(
            symbol=symbol,
            kind="stop",
            round_id=round_id,
        )
        row = {
            "round_id": round_id,
            "symbol": symbol,
            "signal_at": payload.get("signal_at"),
            "reference_price": str(midpoint),
            "order_notional": str(self.order_notional),
            "qty": str(qty),
            "buy_client_order_id": buy_client_id,
            "stop_client_order_id": stop_client_id,
            "sell_client_order_id": None,
            "opened_at": datetime.now(UTC).isoformat(),
            "status": "ENTRY_INTENT",
            "market_quality": quality,
            "buy_order": None,
            "stop_order": None,
            "sell_order": None,
            "closed_evidence": None,
        }
        self.rounds[round_id] = row
        await self.persist()

        persisted = await self._critical_intent(
            "graen_paper_entry_intent",
            row,
            event_key=round_id + ":entry-intent",
            symbol=symbol,
        )
        if not persisted:
            row["status"] = "FAILED"
            row["failure_reason"] = "durable_entry_intent_unavailable"
            self.circuit_open_reason = "durable_entry_intent_unavailable"
            await self.persist()
            return

        existing = await self._recover_order(buy_client_id)
        if existing is None:
            try:
                existing = await self.client.submit_crypto_market_buy(
                    symbol=symbol,
                    qty=str(qty),
                    client_order_id=buy_client_id,
                )
            except Exception as exc:
                existing = await self._recover_order(buy_client_id)
                if existing is None:
                    self.ambiguous_submission_count += 1
                    self.circuit_open_reason = "ambiguous_paper_buy_submission"
                    row["status"] = "FAILED"
                    row["failure_reason"] = (
                        type(exc).__name__ + ": " + str(exc)
                    )[:500]
                    await self.persist()
                    return

        row["buy_order"] = dict(existing)
        row["status"] = "OPENING"
        self.event_sink.record_broker_order(
            existing,
            correlation_id=str(
                (self.activation or {}).get("activation_id") or ""
            ),
        )

        stop = (
            midpoint * (Decimal("1") - self.stop_pct)
        ).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
        limit = (
            stop * (Decimal("1") - self.stop_limit_buffer_pct)
        ).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)
        try:
            stop_order = await self.client.submit_crypto_stop_limit_sell(
                symbol=symbol,
                qty=str(qty),
                stop_price=str(stop),
                limit_price=str(limit),
                client_order_id=stop_client_id,
            )
            row["stop_order"] = dict(stop_order)
            row["status"] = "OPEN"
            self.event_sink.record_broker_order(
                stop_order,
                correlation_id=str(
                    (self.activation or {}).get("activation_id") or ""
                ),
            )
        except Exception as exc:
            self.execution_error_count += 1
            self.circuit_open_reason = "protective_stop_unavailable"
            row["status"] = "PROTECTION_FAILED"
            row["failure_reason"] = (
                type(exc).__name__ + ": " + str(exc)
            )[:500]
            # A paper position without protection is flattened immediately.
            try:
                sell_id = self._client_order_id(
                    symbol=symbol,
                    kind="failsafe-sell",
                    round_id=round_id,
                )
                row["sell_client_order_id"] = sell_id
                sell = await self.client.submit_crypto_market_sell(
                    symbol=symbol,
                    qty=str(qty),
                    client_order_id=sell_id,
                )
                row["sell_order"] = dict(sell)
                self.event_sink.record_broker_order(
                    sell,
                    correlation_id=str(
                        (self.activation or {}).get("activation_id") or ""
                    ),
                )
            except Exception:
                self.ambiguous_submission_count += 1
        await self.persist()

    async def _handle_exit(
        self,
        event: Mapping[str, Any],
    ) -> None:
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        symbol = str(event.get("symbol") or payload.get("symbol") or "").upper()
        round_id = self._round_id(event)
        row = self.rounds.get(round_id)
        if row is None:
            # Find an open round for the same symbol when the shadow event
            # payload has changed shape but the candidate identity is stable.
            row = next(
                (
                    candidate
                    for candidate in self._open_rounds()
                    if str(candidate.get("symbol") or "") == symbol
                ),
                None,
            )
        if row is None or row.get("closed_evidence"):
            return

        positions, open_orders = await asyncio.gather(
            self.client.positions(),
            self.client.open_orders(),
        )
        position = next(
            (
                item
                for item in positions
                if str(item.get("symbol") or "").upper() == symbol
                and _d(item.get("qty")) > 0
            ),
            None,
        )
        if position is None:
            await self._reconcile_round(row, open_orders=open_orders)
            return

        stop_id = str(row.get("stop_client_order_id") or "")
        for order in open_orders:
            if (
                stop_id
                and str(order.get("client_order_id") or "") == stop_id
                and order.get("id")
            ):
                try:
                    await self.client.cancel_order(str(order["id"]))
                except Exception:
                    pass

        qty = _d(position.get("qty"))
        if qty <= 0:
            return
        sell_client_id = str(
            row.get("sell_client_order_id") or ""
        ) or self._client_order_id(
            symbol=symbol,
            kind="sell",
            round_id=str(row.get("round_id") or round_id),
        )
        row["sell_client_order_id"] = sell_client_id
        persisted = await self._critical_intent(
            "graen_paper_exit_intent",
            {
                "round_id": row.get("round_id"),
                "symbol": symbol,
                "qty": str(qty),
                "sell_client_order_id": sell_client_id,
            },
            event_key=str(row.get("round_id")) + ":exit-intent",
            symbol=symbol,
        )
        if not persisted:
            self.circuit_open_reason = "durable_exit_intent_unavailable"
            await self.persist()
            return

        sell = await self._recover_order(sell_client_id)
        if sell is None:
            try:
                sell = await self.client.submit_crypto_market_sell(
                    symbol=symbol,
                    qty=str(qty),
                    client_order_id=sell_client_id,
                )
            except Exception as exc:
                sell = await self._recover_order(sell_client_id)
                if sell is None:
                    self.ambiguous_submission_count += 1
                    self.circuit_open_reason = "ambiguous_paper_sell_submission"
                    row["failure_reason"] = (
                        type(exc).__name__ + ": " + str(exc)
                    )[:500]
                    await self.persist()
                    return
        row["sell_order"] = dict(sell)
        row["status"] = "CLOSING"
        self.event_sink.record_broker_order(
            sell,
            correlation_id=str(
                (self.activation or {}).get("activation_id") or ""
            ),
        )
        await self.persist()

    async def _reconcile_round(
        self,
        row: dict[str, Any],
        *,
        open_orders: list[dict[str, Any]] | None = None,
    ) -> None:
        if row.get("closed_evidence"):
            return
        client_ids = [
            str(row.get("buy_client_order_id") or ""),
            str(row.get("sell_client_order_id") or ""),
            str(row.get("stop_client_order_id") or ""),
        ]
        observed: dict[str, dict[str, Any]] = {}
        for client_id in client_ids:
            if not client_id:
                continue
            order = await self._recover_order(client_id)
            if order is not None:
                observed[client_id] = dict(order)

        buy = observed.get(str(row.get("buy_client_order_id") or ""))
        sell = observed.get(str(row.get("sell_client_order_id") or ""))
        stop = observed.get(str(row.get("stop_client_order_id") or ""))
        if buy is not None:
            row["buy_order"] = buy
        if sell is not None:
            row["sell_order"] = sell
        if stop is not None:
            row["stop_order"] = stop

        exit_order = next(
            (
                order
                for order in (sell, stop)
                if isinstance(order, Mapping)
                and str(order.get("status") or "").lower() == "filled"
            ),
            None,
        )
        if (
            not isinstance(buy, Mapping)
            or str(buy.get("status") or "").lower() != "filled"
            or exit_order is None
        ):
            return

        entry_fill = _d(buy.get("filled_avg_price"))
        exit_fill = _d(exit_order.get("filled_avg_price"))
        if entry_fill <= 0 or exit_fill <= 0:
            return
        net_return = exit_fill / entry_fill - Decimal("1")
        reference = _d(row.get("reference_price"))
        slippage = (
            abs(entry_fill - reference) / reference
            if reference > 0 else Decimal("0")
        )
        closed_at = (
            exit_order.get("filled_at")
            or exit_order.get("updated_at")
            or datetime.now(UTC).isoformat()
        )
        row["closed_evidence"] = {
            "entry_fill": str(entry_fill),
            "exit_fill": str(exit_fill),
            "net_return": str(net_return),
            "entry_slippage_pct": str(slippage),
            "closed_at": str(closed_at),
            "exit_client_order_id": exit_order.get("client_order_id"),
        }
        row["status"] = "CLOSED"
        self._emit(
            "graen_paper_round_trip_closed",
            {
                "round_id": row.get("round_id"),
                "symbol": row.get("symbol"),
                **row["closed_evidence"],
            },
            event_key=str(row.get("round_id")) + ":closed",
            symbol=str(row.get("symbol") or ""),
            occurred_at=str(closed_at),
        )

    async def _reconcile(self) -> None:
        open_orders = await self.client.open_orders()
        for row in self.rounds.values():
            await self._reconcile_round(
                row,
                open_orders=open_orders,
            )

    async def cycle_once(self) -> list[dict[str, Any]]:
        async with self.lock:
            if self.activation is None:
                return []
            if not self.paper_execution_authorized:
                self.circuit_open_reason = "paper_authority_removed"
                await self.persist()
                return []
            events = await self.shadow.cycle()
            for event in events:
                event_type = str(event.get("event_type") or "")
                if event_type == "graen_candidate_shadow_entry":
                    await self._handle_entry(event)
                elif event_type == "graen_candidate_shadow_exit":
                    await self._handle_exit(event)
            await self._reconcile()
            self.last_cycle_at = datetime.now(UTC)
            checkpoint = self.checkpoint()
            if (
                self.activation is not None
                and checkpoint.get("status") in {"PAPER_PASSED", "PAPER_REJECTED"}
                and not self._open_rounds()
            ):
                activation_id = str(
                    self.activation.get("activation_id") or ""
                )
                if activation_id:
                    self.completed[activation_id] = {
                        "activation": dict(self.activation),
                        "checkpoint": dict(checkpoint),
                        "completed_at": datetime.now(UTC).isoformat(),
                    }
                    self._emit(
                        "graen_paper_terminal_checkpoint",
                        {
                            "activation_id": activation_id,
                            "checkpoint": checkpoint,
                        },
                        event_key=activation_id + ":terminal",
                    )
                self.activation = None
                self.rounds = {}
                self.shadow = CandidateForwardShadow(self.settings)
                self.circuit_open_reason = None
                self.ambiguous_submission_count = 0
                self.execution_error_count = 0
            await self.persist()
            self.last_error = None
            return events

    def checkpoint(self) -> dict[str, Any]:
        evidence = [
            dict(row["closed_evidence"])
            for row in self.rounds.values()
            if isinstance(row.get("closed_evidence"), Mapping)
        ]
        returns = [float(_d(row.get("net_return"))) for row in evidence]
        slippage = [
            float(_d(row.get("entry_slippage_pct")))
            for row in evidence
        ]
        wins = sum(value for value in returns if value > 0)
        losses = abs(sum(value for value in returns if value < 0))
        profit_factor = (
            wins / losses
            if losses > 0
            else 999.0 if wins > 0 else 0.0
        )
        expectancy = fmean(returns) if returns else 0.0
        mean_slippage = fmean(slippage) if slippage else 0.0
        day_count = len(
            {
                str(row.get("closed_at") or "")[:10]
                for row in evidence
                if row.get("closed_at")
            }
        )
        enough = (
            len(evidence) >= self.min_round_trips
            and day_count >= self.min_independent_days
        )
        open_round_count = len(self._open_rounds())
        activation_started = _stamp(
            (self.activation or {}).get("activated_at")
        ) or datetime.now(UTC)
        elapsed_days = max(
            (datetime.now(UTC) - activation_started).days,
            0,
        )
        gates = {
            "minimum_round_trips": len(evidence) >= self.min_round_trips,
            "minimum_independent_days": day_count >= self.min_independent_days,
            "positive_expectancy": expectancy > 0,
            "profit_factor_gt_one": profit_factor > 1.0,
            "fill_slippage_within_limit": (
                mean_slippage <= float(self.max_fill_slippage_pct)
            ),
            "no_ambiguous_submissions": (
                self.ambiguous_submission_count == 0
            ),
            "no_execution_errors": self.execution_error_count == 0,
            "circuit_closed": self.circuit_open_reason is None,
            "no_open_rounds": open_round_count == 0,
        }
        ready = enough and all(gates.values())
        limit_reached = (
            len(evidence) >= self.max_review_round_trips
            or elapsed_days >= self.max_review_days
        ) and open_round_count == 0
        status = (
            "PAPER_PASSED"
            if ready
            else "PAPER_REJECTED"
            if limit_reached
            else "PAPER_COLLECTING"
        )
        return {
            "schema_version": "graen.paper-canary-checkpoint.v1",
            "status": status,
            "round_trip_count": len(evidence),
            "independent_day_count": day_count,
            "elapsed_days": elapsed_days,
            "expectancy_per_round_trip": expectancy,
            "profit_factor": profit_factor,
            "mean_entry_slippage_pct": mean_slippage,
            "gates": gates,
            "circuit_open_reason": self.circuit_open_reason,
            "ambiguous_submission_count": self.ambiguous_submission_count,
            "execution_error_count": self.execution_error_count,
            "promotion_authorized": False,
            "live_execution_authorized": False,
            "live_broker_orders_possible": False,
        }

    async def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.cycle_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.execution_error_count += 1
                self.last_error = (
                    type(exc).__name__ + ": " + str(exc)
                )[:1000]
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.poll_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        if self.task is not None:
            return
        self.stop_event.clear()
        await self.restore()
        self.task = asyncio.create_task(
            self._run(),
            name="graen-autonomous-paper-canary",
        )

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        if self.activation is not None:
            try:
                async with self.lock:
                    await self.persist()
            except Exception as exc:
                self.last_error = (
                    "persist_failed:" + type(exc).__name__ + ":" + str(exc)
                )[:1000]

    def checkpoint_for_activation(
        self,
        activation_id: str,
    ) -> dict[str, Any] | None:
        activation_id = str(activation_id or "").strip()
        if not activation_id:
            return None
        if (
            self.activation is not None
            and str(self.activation.get("activation_id") or "")
            == activation_id
        ):
            return {
                "paper_activation_id": activation_id,
                "completed": False,
                "checkpoint": self.checkpoint(),
                "candidate": {
                    "candidate_id": str(
                        (self.activation.get("candidate_spec") or {}).get(
                            "candidate_id"
                        )
                        or ""
                    ),
                    "state": "ACTIVE",
                },
                "live_execution_authorized": False,
            }
        completed = self.completed.get(activation_id)
        if completed is not None:
            return {
                "paper_activation_id": activation_id,
                "completed": True,
                "checkpoint": dict(completed.get("checkpoint") or {}),
                "candidate": {
                    "candidate_id": str(
                        (
                            (completed.get("activation") or {}).get(
                                "candidate_spec"
                            )
                            or {}
                        ).get("candidate_id")
                        or ""
                    ),
                    "state": str(
                        (completed.get("checkpoint") or {}).get("status")
                        or ""
                    ),
                },
                "live_execution_authorized": False,
            }
        return None

    def status(self) -> dict[str, Any]:
        checkpoint = self.checkpoint()
        latest_terminal = (
            max(
                self.completed.values(),
                key=lambda row: str(row.get("completed_at") or ""),
            )
            if self.completed
            else None
        )
        return {
            "ok": self.last_error is None,
            "system": "RHEN",
            "service": "graen-paper-canary",
            "host_version": PAPER_HOST_VERSION,
            "running": self.running,
            "enabled": self.enabled,
            "acknowledged": self.acknowledged,
            "auth_configured": self.auth_configured,
            "paper_execution_authorized": self.paper_execution_authorized,
            "live_execution_authorized": False,
            "live_broker_orders_possible": False,
            "active": self.activation is not None,
            "completed_count": len(self.completed),
            "latest_terminal": (
                {
                    "activation_id": str(
                        (latest_terminal.get("activation") or {}).get(
                            "activation_id"
                        )
                        or ""
                    ),
                    "candidate_id": str(
                        (
                            (latest_terminal.get("activation") or {}).get(
                                "candidate_spec"
                            )
                            or {}
                        ).get("candidate_id")
                        or ""
                    ),
                    "status": str(
                        (latest_terminal.get("checkpoint") or {}).get(
                            "status"
                        )
                        or ""
                    ),
                    "completed_at": latest_terminal.get("completed_at"),
                }
                if latest_terminal is not None
                else None
            ),
            "candidate_id": (
                str(
                    ((self.activation or {}).get("candidate_spec") or {}).get(
                        "candidate_id"
                    )
                    or ""
                )
                or None
            ),
            "activation_id": (
                (self.activation or {}).get("activation_id")
                if self.activation else None
            ),
            "current_activity": (
                "Paper execution evidence collection"
                if self.activation is not None
                else "Awaiting READY_FOR_PAPER candidate"
            ),
            "checkpoint": checkpoint,
            "checkpoint_status": checkpoint["status"],
            "last_cycle_at": (
                self.last_cycle_at.isoformat()
                if self.last_cycle_at else None
            ),
            "last_persisted_at": (
                self.last_persisted_at.isoformat()
                if self.last_persisted_at else None
            ),
            "last_error": self.last_error,
            "state_path": str(self.state_path),
            "risk_envelope": {
                "order_notional": str(self.order_notional),
                "max_order_notional": str(self.max_order_notional),
                "max_total_position_notional": str(
                    self.max_total_notional
                ),
                "max_concurrent_positions": self.max_positions,
                "max_entries_24h": self.max_entries_24h,
                "catastrophe_stop_pct": str(self.stop_pct),
            },
        }


def create_paper_router(host: GraenPaperCanaryHost) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/graen-paper/health")
    async def graen_paper_health() -> dict[str, Any]:
        state = host.status()
        return {
            "ok": state["ok"],
            "system": state["system"],
            "service": state["service"],
            "running": state["running"],
            "enabled": state["enabled"],
            "paper_execution_authorized": state[
                "paper_execution_authorized"
            ],
            "live_execution_authorized": False,
            "active": state["active"],
            "checkpoint_status": state["checkpoint_status"],
        }

    @router.get("/v1/graen-paper/status")
    async def graen_paper_status(
        x_graen_paper_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        host.require_token(x_graen_paper_token)
        return host.status()

    @router.get("/v1/graen-paper/checkpoint/{activation_id}")
    async def graen_paper_checkpoint(
        activation_id: str,
        x_graen_paper_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        host.require_token(x_graen_paper_token)
        checkpoint = host.checkpoint_for_activation(activation_id)
        if checkpoint is None:
            raise HTTPException(status_code=404, detail="activation not found")
        return checkpoint

    @router.post("/v1/graen-paper/activate")
    async def graen_paper_activate(
        request: PaperActivationRequest,
        x_graen_paper_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        host.require_token(x_graen_paper_token)
        try:
            return await host.activate(request.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
