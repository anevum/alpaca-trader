from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RuntimeState:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_poll_at: datetime | None = None
    last_error: str | None = None
    funding_ready: bool = False
    last_cash: str | None = None
    last_buying_power: str | None = None

    def mark_poll(self) -> None:
        self.last_poll_at = datetime.now(timezone.utc)


runtime_state = RuntimeState()
