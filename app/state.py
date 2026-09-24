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
    paused: bool = False
    last_signal: dict[str, Any] | None = None
    last_decision: str | None = None
    last_order: dict[str, Any] | None = None

    def mark_poll(self) -> None:
        self.last_poll_at = datetime.now(timezone.utc)

    def mark_strategy(self) -> None:
        self.last_strategy_at = datetime.now(timezone.utc)


runtime_state = RuntimeState()
