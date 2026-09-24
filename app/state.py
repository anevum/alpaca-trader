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
    paused: bool = False
    entries_enabled: bool = True
    last_signal: dict[str, Any] | None = None
    last_scan: dict[str, Any] = field(default_factory=dict)
    decision_history: list[dict[str, Any]] = field(default_factory=list)
    last_decision: str | None = None
    last_order: dict[str, Any] | None = None

    def mark_poll(self) -> None:
        self.last_poll_at = datetime.now(timezone.utc)

    def mark_strategy(self) -> None:
        self.last_strategy_at = datetime.now(timezone.utc)

    def record_event(
        self,
        *,
        kind: str,
        message: str,
        symbol: str = "",
        action: str = "",
        reason: str = "",
        at: datetime | None = None,
    ) -> None:
        stamp = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        event = {
            "at": stamp.isoformat(),
            "kind": kind,
            "symbol": symbol,
            "action": action,
            "message": message,
            "reason": reason or message,
        }
        self.decision_history.insert(0, event)
        del self.decision_history[200:]

    def record_scan(self, scan: dict[str, Any], at: datetime | None = None) -> None:
        previous = self.last_scan
        for symbol, payload in scan.items():
            old = previous.get(symbol) or {}
            changed = (
                old.get("action") != payload.get("action")
                or old.get("reason") != payload.get("reason")
            )
            if changed:
                self.record_event(
                    kind="scan",
                    symbol=symbol,
                    action=str(payload.get("action") or ""),
                    message=str(payload.get("reason") or ""),
                    reason=str(payload.get("reason") or ""),
                    at=at,
                )
        self.last_scan = scan


runtime_state = RuntimeState()
