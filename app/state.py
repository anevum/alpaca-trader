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

    def begin_cycle(self, correlation_id: str) -> None:
        self.current_correlation_id = correlation_id

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
                # Keep per-symbol scan transitions in operator history only.
                # Canonical decision_cycle persistence already records the
                # bounded candidate evidence once per cycle; durably emitting
                # every changed hold reason duplicates that evidence and can
                # exhaust RHEN Core storage under a broad dynamic universe.
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
