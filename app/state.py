from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RuntimeState:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_poll_at: datetime | None = None
    last_strategy_at: datetime | None = None
    last_error: str | None = None
    funding_ready: bool = False
    last_cash: str | None = None
    last_buying_power: str | None = None
    last_equity: str | None = None
    last_equity_reference: str | None = None
    last_day_pnl: str | None = None
    last_risk_reference_equity: str | None = None
    last_cash_flow_accounting: dict[str, Any] | None = None
    last_cash_flow_error: str | None = None
    paused: bool = False
    entries_enabled: bool = True
    startup_reconciled: bool = False
    reconciliation_safe: bool = False
    last_reconciliation: dict[str, Any] | None = None
    last_signal: dict[str, Any] | None = None
    last_scan: dict[str, Any] = field(default_factory=dict)
    last_completed_scan: dict[str, Any] = field(default_factory=dict)
    decision_history: list[dict[str, Any]] = field(default_factory=list)
    last_decision: str | None = None
    last_order: dict[str, Any] | None = None
    current_correlation_id: str | None = None
    universe_active_symbols: list[str] = field(default_factory=list)
    universe_candidate_count: int = 0
    universe_eligible_count: int = 0
    universe_updated_at: datetime | None = None
    universe_source: str = "static"
    universe_error: str | None = None
    crypto_last_signal: dict[str, Any] | None = None
    crypto_last_scan: dict[str, Any] = field(default_factory=dict)
    crypto_last_completed_scan: dict[str, Any] = field(default_factory=dict)
    crypto_last_decision: str | None = None
    crypto_universe_active_symbols: list[str] = field(default_factory=list)
    crypto_universe_candidate_count: int = 0
    crypto_universe_eligible_count: int = 0
    crypto_universe_updated_at: datetime | None = None
    crypto_universe_source: str = "disabled"
    crypto_universe_error: str | None = None
    crypto_current_correlation_id: str | None = None
    crypto_last_order: dict[str, Any] | None = None
    crypto_last_error: str | None = None
    crypto_last_execution_at: datetime | None = None
    crypto_last_execution_context: dict[str, Any] = field(default_factory=dict)
    crypto_last_scan_at: datetime | None = None
    crypto_last_market_data_at: datetime | None = None
    crypto_scanner_healthy: bool = False
    crypto_execution_healthy: bool = False
    crypto_candidates_generated: int = 0
    crypto_qualified_candidates: int = 0
    crypto_rejection_counts: dict[str, int] = field(default_factory=dict)
    crypto_active_positions: int = 0
    crypto_aggregate_exposure: str = "0"
    crypto_recent_orders: list[dict[str, Any]] = field(default_factory=list)
    crypto_protective_status: dict[str, Any] = field(default_factory=dict)
    crypto_forward_evidence_state: dict[str, Any] = field(default_factory=lambda: {"status": "pending"})
    crypto_replay_state: dict[str, Any] = field(default_factory=lambda: {"status": "unknown"})
    crypto_graen_promotion: dict[str, Any] = field(default_factory=lambda: {"status": "GATED", "promotion_ready": False})
    crypto_graen_evidence: dict[str, Any] = field(default_factory=dict)
    crypto_breaker_state: dict[str, Any] = field(default_factory=dict)
    crypto_exit_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    exit_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_execution_context: dict[str, Any] = field(default_factory=dict)
    event_emitter: Any = field(default=None, repr=False)

    def set_event_emitter(self, emitter: Any) -> None:
        self.event_emitter = emitter

    def set_universe(
        self,
        *,
        symbols: list[str],
        candidate_count: int,
        eligible_count: int,
        source: str,
        at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        self.universe_active_symbols = list(symbols)
        self.universe_candidate_count = candidate_count
        self.universe_eligible_count = eligible_count
        self.universe_source = source
        self.universe_updated_at = at or datetime.now(timezone.utc)
        self.universe_error = error

    def set_crypto_universe(
        self,
        *,
        symbols: list[str],
        candidate_count: int,
        eligible_count: int,
        source: str,
        at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        self.crypto_universe_active_symbols = list(symbols)
        self.crypto_universe_candidate_count = candidate_count
        self.crypto_universe_eligible_count = eligible_count
        self.crypto_universe_source = source
        self.crypto_universe_updated_at = at or datetime.now(timezone.utc)
        self.crypto_universe_error = error

    def record_crypto_scan(
        self,
        scan: dict[str, Any],
        at: datetime | None = None,
    ) -> None:
        previous = self.crypto_last_scan
        for symbol, payload in scan.items():
            old = previous.get(symbol) or {}
            changed = (
                old.get("action") != payload.get("action")
                or old.get("reason") != payload.get("reason")
            )
            if changed:
                self.record_event(
                    kind="crypto_scan",
                    symbol=symbol,
                    action=str(payload.get("action") or ""),
                    message=str(payload.get("reason") or ""),
                    reason=str(payload.get("reason") or ""),
                    at=at,
                    payload={"market": "crypto", "signal": payload},
                    correlation_id=self.crypto_current_correlation_id,
                )
        self.crypto_last_scan = scan
        self.crypto_last_completed_scan = scan
        self.crypto_last_scan_at = at or datetime.now(timezone.utc)
        self.crypto_scanner_healthy = True
        self.crypto_candidates_generated = len(scan)
        self.crypto_qualified_candidates = sum(
            1 for payload in scan.values()
            if str(payload.get("action") or "").lower() == "buy"
        )
        counts: dict[str, int] = {}
        for payload in scan.values():
            if str(payload.get("action") or "").lower() == "buy":
                continue
            reason = str(payload.get("reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
        self.crypto_rejection_counts = counts

    def begin_cycle(self, correlation_id: str) -> None:
        self.current_correlation_id = correlation_id

    def begin_crypto_cycle(self, correlation_id: str) -> None:
        self.crypto_current_correlation_id = correlation_id

    def mark_poll(self) -> None:
        self.last_poll_at = datetime.now(timezone.utc)

    def mark_strategy(self) -> None:
        self.last_strategy_at = datetime.now(timezone.utc)

    def set_reconciliation(
        self,
        result: dict[str, Any],
        *,
        startup: bool = False,
    ) -> None:
        self.last_reconciliation = result
        self.reconciliation_safe = bool(result.get("safe_to_enter"))
        if startup:
            self.startup_reconciled = True

    def record_event(
        self,
        *,
        kind: str,
        message: str,
        symbol: str = "",
        action: str = "",
        reason: str = "",
        at: datetime | None = None,
        payload: dict[str, Any] | None = None,
        emit: bool = True,
        correlation_id: str | None = None,
    ) -> None:
        stamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        event = {
            "at": stamp.isoformat(),
            "kind": kind,
            "symbol": symbol,
            "action": action,
            "message": message,
            "reason": reason or message,
            "correlation_id": correlation_id or self.current_correlation_id,
        }
        self.decision_history.insert(0, event)
        del self.decision_history[200:]
        if emit and self.event_emitter is not None:
            self.event_emitter({**event, "payload": payload or {}})

    def record_scan(self, scan: dict[str, Any], at: datetime | None = None) -> None:
        previous = self.last_scan
        for symbol, payload in scan.items():
            old = previous.get(symbol) or {}
            changed = (
                old.get("action") != payload.get("action")
                or old.get("reason") != payload.get("reason")
            )
            if changed:
                # Per-symbol equity scan transitions are retained in the
                # in-memory operator history, but are not emitted to the
                # durable transport. The canonical decision_cycle event
                # persists the complete scan once per cycle; emitting every
                # changed symbol here duplicates that evidence and can
                # overwhelm the bounded telemetry queue.
                self.record_event(
                    kind="scan",
                    symbol=symbol,
                    action=str(payload.get("action") or ""),
                    message=str(payload.get("reason") or ""),
                    reason=str(payload.get("reason") or ""),
                    at=at,
                    payload={"signal": payload},
                    emit=False,
                )
        self.last_scan = scan
        self.last_completed_scan = scan


runtime_state = RuntimeState()
