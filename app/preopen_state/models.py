from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class SourceTier(StrEnum):
    DIRECT = "DIRECT"
    PROXY = "PROXY"
    UNAVAILABLE = "UNAVAILABLE"


class DataStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class SignalSource:
    key: str
    label: str
    tier: SourceTier
    symbol: str | None
    measures: str
    provider_requirement: str | None = None
    caveat: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: float | int | str | bool | None
    source_symbol: str | None
    tier: SourceTier
    status: DataStatus
    as_of: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["tier"] = self.tier.value
        value["status"] = self.status.value
        return value
