from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from math import isfinite
from pathlib import Path
from statistics import fmean
from typing import Any, Mapping

from fastapi import FastAPI, Header, HTTPException

from app.alpaca_client import AlpacaClient
from app.config import Settings, get_settings
from app.crypto_layer import CryptoMarketDataClient
from app.persistence import TradingEventSink
from app.research_agent.strategy_runner import RUNNER_VERSION as STRATEGY_RUNNER_VERSION
from graen.crypto.research_v7 import (
    CONTEXT_UNIVERSE,
    CandidateSpec,
    _opportunities_at,
    build_series,
)


UTC = timezone.utc
SERVICE_VERSION = "graen-paper-canary-service-v1"
STATE_SCHEMA_VERSION = "graen.paper-canary-service.state.v1"
DEFAULT_STATE_PATH = "/data/graen-paper-canary-state.json"
TERMINAL_STATES = {"PAPER_PASSED", "PAPER_REJECTED", "RETIRED"}
MIN_PASS_TRADES = 8
MIN_PASS_DAYS = 7
MAX_REVIEW_TRADES = 30
MAX_REVIEW_DAYS = 45
MAX_ALLOWED_SLIPPAGE = Decimal("0.005")
DEFAULT_NOTIONAL = Decimal("1.00")
MAX_NOTIONAL = Decimal("5.00")


def _truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 3600) -> int:
    try:
        return max(minimum, min(maximum, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _env_decimal(name: str, default: Decimal) -> Decimal:
    try:
        value = Decimal(os.getenv(name, str(default)))
    except Exception:
        return default
    return max(Decimal("0.01"), min(MAX_NOTIONAL, value))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _stamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _activation_id(payload: Mapping[str, Any]) -> str:
    material = {
        "shadow_activation_id": str(payload.get("shadow_activation_id") or ""),
        "candidate_methodology": str(payload.get("candidate_methodology") or ""),
        "candidate_spec": payload.get("candidate_spec") or {},
        "shadow_checkpoint": payload.get("shadow_checkpoint") or {},
    }
    return "paper-" + hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()[:24]


def _safe_paper_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "trading_mode": "paper",
            "live_trading": False,
            "bot_armed": False,
            "crypto_execution_enabled": False,
            "scan_only": True,
        }
    )


def _candidate_spec(payload: Mapping[str, Any]) -> CandidateSpec:
    fields = dict(payload)
    fields["targets"] = tuple(str(value) for value in fields.get("targets") or ())
    return CandidateSpec(**fields)


def _latest_common_completed_end(
    bars_by_symbol: Mapping[str, list[dict[str, Any]]],
    *,
    symbols: tuple[str, ...],
    now: datetime,
) -> datetime | None:
    common: set[datetime] | None = None
    for symbol in symbols:
        ends: set[datetime] = set()
        for row in bars_by_symbol.get(symbol, []):
            stamp = _stamp(row.get("t"))
            if stamp is None:
                continue
            end = stamp + timedelta(minutes=5)
            if end <= now:
                ends.add(end)
        if not ends:
            return None
        common = ends if common is None else common & ends
        if not common:
            return None
    return max(common) if common else None


def _profit_factor(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return gains / losses


def _max_drawdown(values: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in values:
        equity *= max(1.0 + value, 1e-12)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


class PaperCanaryService:
    """Serial, paper-account-only execution canary for trusted GRAEN manifests."""

    def __init__(
        self,
        settings: Settings,
        *,
        state_path: str | Path | None = None,
        token: str | None = None,
    ):
        self.settings = _safe_paper_settings(settings)
        self.state_path = Path(
            state_path
            or os.getenv("GRAEN_PAPER_STATE_PATH", DEFAULT_STATE_PATH)
        )
        self.token = str(
            token
            if token is not None
            else os.getenv("GRAEN_PAPER_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.autorun = _truthy("GRAEN_PAPER_AUTORUN", True)
        self.poll_seconds = _env_int("GRAEN_PAPER_POLL_SECONDS", 60, minimum=15, maximum=3600)
        self.order_notional = _env_decimal("GRAEN_PAPER_ORDER_NOTIONAL", DEFAULT_NOTIONAL)
        self.client = AlpacaClient(self.settings)
        self.market_data = CryptoMarketDataClient(self.settings)
        self.event_sink = TradingEventSink(self.settings)
        self.candidates: dict[str, dict[str, Any]] = {}
        self.queue: list[str] = []
        self.active_id: str | None = None
        self.started_at = datetime.now(UTC)
        self.last_cycle_at: datetime | None = None
        self.last_error: str | None = None
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @property
    def execution_authority(self) -> str:
        return "PAPER_ONLY"

    @property
    def live_execution_authority(self) -> bool:
        return False

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.credentials_configured
            and len(self.token) >= 32
            and self.settings.trading_mode == "paper"
            and self.settings.live_trading is False
        )

    def require_token(self, supplied: str | None) -> None:
        if len(self.token) < 32:
            raise HTTPException(status_code=503, detail="paper canary token is not configured")
        if supplied is None or not hmac.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": STATE_SCHEMA_VERSION,
            "saved_at": datetime.now(UTC).isoformat(),
            "active_id": self.active_id,
            "queue": list(self.queue),
            "candidates": self.candidates,
        }
        temp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp.write_text(_canonical(payload) + "\n", encoding="utf-8")
        os.replace(temp, self.state_path)

    def restore(self) -> None:
        if not self.state_path.exists():
            return
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError("unsupported_paper_canary_state_schema")
        candidates = payload.get("candidates") or {}
        if not isinstance(candidates, Mapping):
            raise ValueError("invalid_paper_canary_state")
        self.candidates = {
            str(key): dict(value)
            for key, value in candidates.items()
            if isinstance(value, Mapping)
        }
        self.queue = [
            str(value)
            for value in payload.get("queue") or []
            if str(value) in self.candidates
        ]
        active = str(payload.get("active_id") or "")
        self.active_id = active if active in self.candidates else None
        self._advance_queue()

    def _advance_queue(self) -> None:
        if self.active_id:
            active = self.candidates.get(self.active_id)
            if active and str(active.get("state")) not in TERMINAL_STATES:
                return
            self.active_id = None
        while self.queue:
            candidate_id = self.queue.pop(0)
            row = self.candidates.get(candidate_id)
            if row and str(row.get("state")) == "QUEUED":
                row["state"] = "ACTIVE"
                row["started_at"] = row.get("started_at") or datetime.now(UTC).isoformat()
                self.active_id = candidate_id
                return

    def _checkpoint(self, row: Mapping[str, Any]) -> dict[str, Any]:
        trades = [
            dict(value)
            for value in row.get("trades") or []
            if isinstance(value, Mapping)
        ]
        returns = [
            float(value.get("net_return") or 0.0)
            for value in trades
            if isfinite(float(value.get("net_return") or 0.0))
        ]
        days = {
            str(value.get("exit_at") or "")[:10]
            for value in trades
            if value.get("exit_at")
        }
        expectancy = fmean(returns) if returns else 0.0
        profit_factor = _profit_factor(returns)
        max_drawdown = _max_drawdown(returns)
        worst_slippage = max(
            (
                abs(float(value.get("entry_slippage_pct") or 0.0))
                for value in trades
            ),
            default=0.0,
        )
        errors = int(row.get("operational_error_count") or 0)
        started = _stamp(row.get("started_at")) or datetime.now(UTC)
        elapsed_days = max((datetime.now(UTC) - started).days, 0)

        passed = bool(
            len(trades) >= MIN_PASS_TRADES
            and len(days) >= MIN_PASS_DAYS
            and expectancy > 0
            and (profit_factor is None or profit_factor > 1.0)
            and worst_slippage <= float(MAX_ALLOWED_SLIPPAGE)
            and errors == 0
        )
        review_limit = bool(
            len(trades) >= MAX_REVIEW_TRADES
            or elapsed_days >= MAX_REVIEW_DAYS
        )
        status = (
            "PAPER_PASSED"
            if passed
            else "PAPER_REJECTED"
            if review_limit
            else str(row.get("state") or "QUEUED")
        )
        return {
            "schema_version": "graen.paper-canary.checkpoint.v1",
            "paper_activation_id": row.get("paper_activation_id"),
            "candidate_id": row.get("candidate_id"),
            "candidate_methodology": row.get("candidate_methodology"),
            "status": status,
            "trade_count": len(trades),
            "independent_day_count": len(days),
            "expectancy_per_trade": expectancy,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
            "worst_entry_slippage_pct": worst_slippage,
            "operational_error_count": errors,
            "elapsed_days": elapsed_days,
            "pass_gate": {
                "min_trades": MIN_PASS_TRADES,
                "min_independent_days": MIN_PASS_DAYS,
                "expectancy_positive": True,
                "profit_factor_gt": 1.0,
                "max_entry_slippage_pct": str(MAX_ALLOWED_SLIPPAGE),
                "operational_errors": 0,
            },
            "terminal_rejection_gate": {
                "max_review_trades": MAX_REVIEW_TRADES,
                "max_review_days": MAX_REVIEW_DAYS,
            },
            "paper_execution_authorized": True,
            "live_execution_authorized": False,
            "live_promotion_authorized": False,
            "next_condition": (
                "HUMAN_DECISION_REQUIRED"
                if status == "PAPER_PASSED"
                else "RESEARCHING"
                if status == "PAPER_REJECTED"
                else "OPERATING"
            ),
        }

    def status(self) -> dict[str, Any]:
        rows = []
        for activation_id, row in sorted(self.candidates.items()):
            rows.append({
                "paper_activation_id": activation_id,
                "candidate_id": row.get("candidate_id"),
                "state": row.get("state"),
                "checkpoint": self._checkpoint(row),
            })
        return {
            "ok": bool(
                self.configured
                and self.last_error is None
                and (
                    self.task is not None and not self.task.done()
                    or not self.autorun
                )
            ),
            "system": "GRAEN",
            "service": "paper-canary",
            "service_version": SERVICE_VERSION,
            "configured": self.configured,
            "autorun": self.autorun,
            "active_id": self.active_id,
            "queued_count": len(self.queue),
            "candidate_count": len(self.candidates),
            "candidates": rows,
            "order_notional": str(self.order_notional),
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_error": self.last_error,
            "execution_authority": "PAPER_ONLY",
            "live_execution_authority": False,
            "live_promotion_authorized": False,
        }

    def activate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        methodology = str(payload.get("candidate_methodology") or "")
        spec_payload = payload.get("candidate_spec")
        shadow_checkpoint = payload.get("shadow_checkpoint")
        if methodology != STRATEGY_RUNNER_VERSION:
            raise ValueError("paper_canary_requires_trusted_strategy_runner_methodology")
        if not isinstance(spec_payload, Mapping):
            raise ValueError("paper_canary_candidate_spec_required")
        if not isinstance(shadow_checkpoint, Mapping):
            raise ValueError("paper_canary_shadow_checkpoint_required")
        if str(shadow_checkpoint.get("status") or "").upper() != "READY_FOR_PAPER":
            raise ValueError("paper_canary_requires_ready_for_paper_shadow_checkpoint")
        spec = _candidate_spec(spec_payload)
        activation_id = _activation_id(payload)
        existing = self.candidates.get(activation_id)
        if existing is not None:
            return {
                "activation": dict(existing.get("activation") or {}),
                "duplicate": True,
                "checkpoint": self._checkpoint(existing),
                "live_execution_authority": False,
            }

        activation = {
            "schema_version": "graen.paper-canary.activation.v1",
            "paper_activation_id": activation_id,
            "shadow_activation_id": str(payload.get("shadow_activation_id") or ""),
            "problem_id": str(payload.get("problem_id") or ""),
            "graen_run_id": str(payload.get("graen_run_id") or ""),
            "candidate_methodology": methodology,
            "candidate_id": spec.candidate_id,
            "candidate_spec": spec.to_dict(),
            "shadow_checkpoint": dict(shadow_checkpoint),
            "activated_at": datetime.now(UTC).isoformat(),
            "paper_only": True,
            "live_execution_authorized": False,
        }
        row = {
            "paper_activation_id": activation_id,
            "candidate_id": spec.candidate_id,
            "candidate_methodology": methodology,
            "candidate_spec": spec.to_dict(),
            "activation": activation,
            "state": "QUEUED",
            "started_at": None,
            "last_processed_bar_end": None,
            "open_trade": None,
            "trades": [],
            "opportunity_count": 0,
            "decision_count": 0,
            "operational_error_count": 0,
            "last_error": None,
        }
        self.candidates[activation_id] = row
        self.queue.append(activation_id)
        self._advance_queue()
        self._persist()
        self._emit("graen_paper_canary_activation", {"activation": activation}, activation_id)
        return {
            "activation": activation,
            "duplicate": False,
            "checkpoint": self._checkpoint(row),
            "live_execution_authority": False,
        }

    def _emit(self, event_type: str, payload: Mapping[str, Any], activation_id: str) -> None:
        self.event_sink.emit(
            event_type=event_type,
            event_key=(
                f"graen-paper:{activation_id}:{event_type}:"
                + hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]
            ),
            payload={
                **dict(payload),
                "source_service": SERVICE_VERSION,
                "execution_authority": "PAPER_ONLY",
                "live_execution_authorized": False,
            },
        )

    @staticmethod
    def _qty(notional: Decimal, price: Decimal) -> Decimal:
        if price <= 0:
            return Decimal("0")
        return (notional / price).quantize(Decimal("0.000000001"), rounding=ROUND_DOWN)

    @staticmethod
    def _client_order_id(activation_id: str, side: str, sequence: int) -> str:
        return (
            f"anevum-graen-paper-{activation_id[-12:]}-{side}-{sequence:04d}"
        )[:128]

    async def _refresh_pending_order(self, row: dict[str, Any], now: datetime) -> bool:
        trade = row.get("open_trade")
        if not isinstance(trade, dict):
            return False
        phase = str(trade.get("phase") or "")
        if phase not in {"ENTRY_PENDING", "EXIT_PENDING"}:
            return False
        client_order_id = str(
            trade.get("entry_client_order_id")
            if phase == "ENTRY_PENDING"
            else trade.get("exit_client_order_id")
            or ""
        )
        order = await self.client.order_by_client_order_id(client_order_id)
        if not isinstance(order, Mapping):
            return True
        status = str(order.get("status") or "").casefold()
        if status == "filled":
            price = Decimal(str(order.get("filled_avg_price") or "0"))
            filled_at = _stamp(order.get("filled_at")) or now
            if price <= 0:
                row["operational_error_count"] = int(row.get("operational_error_count") or 0) + 1
                row["last_error"] = "paper_fill_price_unavailable"
                return True
            if phase == "ENTRY_PENDING":
                trade["phase"] = "OPEN"
                trade["entry_price"] = str(price)
                trade["entry_at"] = filled_at.isoformat()
                ref = Decimal(str(trade.get("reference_price") or "0"))
                trade["entry_slippage_pct"] = str(
                    (price - ref) / ref if ref > 0 else Decimal("0")
                )
                return True

            entry_price = Decimal(str(trade.get("entry_price") or "0"))
            exit_return = float(price / entry_price - Decimal("1")) if entry_price > 0 else 0.0
            completed = {
                **trade,
                "phase": "CLOSED",
                "exit_price": str(price),
                "exit_at": filled_at.isoformat(),
                "net_return": exit_return,
            }
            row.setdefault("trades", []).append(completed)
            row["trades"] = list(row["trades"])[-100:]
            row["open_trade"] = None
            return True

        if status in {"rejected", "canceled", "expired"}:
            row["operational_error_count"] = int(row.get("operational_error_count") or 0) + 1
            row["last_error"] = f"paper_order_{status}:{client_order_id}"
            row["open_trade"] = None
            return True
        return True

    async def _cycle_active(self, activation_id: str, row: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        checkpoint = self._checkpoint(row)
        if checkpoint["status"] in {"PAPER_PASSED", "PAPER_REJECTED"}:
            row["state"] = checkpoint["status"]
            self._emit(
                "graen_paper_canary_terminal_checkpoint",
                {"checkpoint": checkpoint},
                activation_id,
            )
            return

        if await self._refresh_pending_order(row, now):
            return

        trade = row.get("open_trade")
        if isinstance(trade, dict) and str(trade.get("phase")) == "OPEN":
            entry_at = _stamp(trade.get("entry_at"))
            hold_minutes = int(trade.get("hold_minutes") or 0)
            if entry_at and now >= entry_at + timedelta(minutes=hold_minutes):
                sequence = len(row.get("trades") or []) + 1
                client_order_id = self._client_order_id(activation_id, "sell", sequence)
                qty = str(trade.get("qty") or "0")
                try:
                    await self.client.submit_crypto_market_sell(
                        symbol=str(trade.get("symbol") or ""),
                        qty=qty,
                        client_order_id=client_order_id,
                    )
                except Exception as exc:
                    row["operational_error_count"] = int(row.get("operational_error_count") or 0) + 1
                    row["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
                    return
                trade["phase"] = "EXIT_PENDING"
                trade["exit_client_order_id"] = client_order_id
            return

        spec = _candidate_spec(row["candidate_spec"])
        lookback_minutes = max(spec.lookback_minutes + 180, 26 * 60)
        bars = await self.market_data.bars_many(
            list(CONTEXT_UNIVERSE),
            timeframe="5Min",
            lookback_minutes=lookback_minutes,
        )
        latest_end = _latest_common_completed_end(
            bars,
            symbols=tuple(CONTEXT_UNIVERSE),
            now=now,
        )
        if latest_end is None:
            return
        last_processed = _stamp(row.get("last_processed_bar_end"))
        if last_processed is not None and latest_end <= last_processed:
            return
        row["last_processed_bar_end"] = latest_end.isoformat()
        if int(latest_end.timestamp() // 60) % max(spec.scan_minutes, 1) != 0:
            return

        series = build_series(
            bars,
            start=latest_end - timedelta(hours=26),
            end=latest_end + timedelta(minutes=spec.hold_minutes + 10),
        )
        opportunities = _opportunities_at(series, spec, latest_end)
        row["decision_count"] = int(row.get("decision_count") or 0) + 1
        if not opportunities:
            return
        opportunity = opportunities[0]
        row["opportunity_count"] = int(row.get("opportunity_count") or 0) + 1

        quotes = await self.market_data.latest_quotes([opportunity.symbol])
        quote = quotes.get(opportunity.symbol, {})
        bid = Decimal(str(quote.get("bp") or "0"))
        ask = Decimal(str(quote.get("ap") or "0"))
        if bid <= 0 or ask <= 0 or ask < bid:
            return
        midpoint = (bid + ask) / Decimal("2")
        spread_pct = (ask - bid) / midpoint if midpoint > 0 else Decimal("999")
        if spread_pct > Decimal("0.01"):
            return
        qty = self._qty(self.order_notional, midpoint)
        if qty <= 0:
            return

        sequence = len(row.get("trades") or []) + 1
        client_order_id = self._client_order_id(activation_id, "buy", sequence)
        try:
            await self.client.submit_crypto_market_buy(
                symbol=opportunity.symbol,
                qty=str(qty),
                client_order_id=client_order_id,
            )
        except Exception as exc:
            row["operational_error_count"] = int(row.get("operational_error_count") or 0) + 1
            row["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
            return

        row["open_trade"] = {
            "phase": "ENTRY_PENDING",
            "symbol": opportunity.symbol,
            "signal_at": opportunity.opportunity_at.isoformat(),
            "hold_minutes": opportunity.hold_minutes,
            "signal": dict(opportunity.signal),
            "reference_price": str(midpoint),
            "quoted_spread_pct": str(spread_pct),
            "qty": str(qty),
            "entry_client_order_id": client_order_id,
        }
        self._emit(
            "graen_paper_canary_order_intent",
            {
                "candidate_id": row.get("candidate_id"),
                "paper_activation_id": activation_id,
                "trade": dict(row["open_trade"]),
            },
            activation_id,
        )

    async def cycle_once(self) -> dict[str, Any]:
        async with self._lock:
            self._advance_queue()
            if self.active_id:
                row = self.candidates[self.active_id]
                try:
                    await self._cycle_active(self.active_id, row)
                    checkpoint = self._checkpoint(row)
                    if checkpoint["status"] in {"PAPER_PASSED", "PAPER_REJECTED"}:
                        row["state"] = checkpoint["status"]
                        self.active_id = None
                        self._advance_queue()
                except Exception as exc:
                    row["operational_error_count"] = int(row.get("operational_error_count") or 0) + 1
                    row["last_error"] = f"{type(exc).__name__}: {exc}"[:1000]
                    self.last_error = row["last_error"]
            self.last_cycle_at = datetime.now(UTC)
            self._persist()
        return self.status()

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                await self.cycle_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass

    async def start(self) -> None:
        self.restore()
        await self.event_sink.start()
        if self.autorun and self.task is None:
            self.task = asyncio.create_task(self.run(), name="graen-paper-canary")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self._persist()
        await self.event_sink.stop()


runtime = PaperCanaryService(get_settings())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="ANEVUM GRAEN Paper Canary", lifespan=lifespan)


@app.get("/health")
async def health():
    return runtime.status()


@app.get("/v1/paper-canary/status")
async def paper_status(x_graen_paper_token: str | None = Header(default=None)):
    runtime.require_token(x_graen_paper_token)
    return runtime.status()


@app.get("/v1/paper-canary/checkpoint/{activation_id}")
async def paper_checkpoint(
    activation_id: str,
    x_graen_paper_token: str | None = Header(default=None),
):
    runtime.require_token(x_graen_paper_token)
    row = runtime.candidates.get(activation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="activation not found")
    return {
        "paper_activation_id": activation_id,
        "checkpoint": runtime._checkpoint(row),
        "candidate": {
            "candidate_id": row.get("candidate_id"),
            "state": row.get("state"),
            "open_trade": row.get("open_trade"),
        },
        "live_execution_authority": False,
    }


@app.post("/v1/paper-canary/activate")
async def paper_activate(
    payload: dict[str, Any],
    x_graen_paper_token: str | None = Header(default=None),
):
    runtime.require_token(x_graen_paper_token)
    try:
        return runtime.activate(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
