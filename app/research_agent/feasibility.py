from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .models import ExperimentProposal, FeedEntitlement, canonical_json, deterministic_dict
from .proposal import proposal_hash


class FeasibilityError(ValueError):
    pass


class AvailabilityLoader(Protocol):
    def __call__(
        self,
        *,
        provider: str,
        feed: str,
        symbols: tuple[str, ...],
        start: str,
        end: str,
        timeframe: str,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class AvailabilityRequest:
    provider: str
    feed: str
    symbols: tuple[str, ...]
    context_symbols: tuple[str, ...]
    benchmark_symbol: str | None
    start: str
    end: str
    timeframe: str
    expected_sessions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AvailabilitySnapshot:
    provider: str
    feed: str
    entitlement: FeedEntitlement
    timestamps: Mapping[str, tuple[str, ...]]
    pagination_complete: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class SymbolAvailability:
    symbol: str
    expected_sessions: int
    represented_sessions: int
    missing_sessions: tuple[str, ...]
    timestamp_count: int
    expected_timestamp_capacity: int
    timestamp_density: float
    pagination_complete: bool


@dataclass(frozen=True, slots=True)
class FeasibilityResult:
    feasibility_version: str
    proposal_id: str
    proposal_revision: int
    proposal_hash: str
    provider: str
    feed: str
    feed_entitlement: FeedEntitlement
    status: str
    reason_codes: tuple[str, ...]
    symbols: tuple[SymbolAvailability, ...]
    expected_sessions: int
    represented_sessions: int
    missing_sessions: tuple[str, ...]
    pagination_complete: bool
    synchronized_timestamp_count: int
    expected_synchronized_capacity: int
    synchronized_timestamp_ratio: float
    benchmark_available: bool
    context_symbols_available: bool
    expected_observation_capacity: int
    observed_availability_capacity: int
    completeness_ratio: float


FEASIBILITY_VERSION = "rhen-corpus-feasibility-v1"
FORBIDDEN_OUTPUT_KEYS = frozenset(
    {
        "o",
        "h",
        "l",
        "c",
        "v",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "price",
        "signal",
        "signals",
        "residual",
        "residual_shock",
        "forward_return",
        "returns",
        "target",
        "targets",
        "trade_outcome",
        "expectancy",
        "profit_factor",
        "optimal_threshold",
        "parameter_ranking",
        "configuration_selection",
    }
)


def _normalized_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_from_bar(bar: Any) -> str:
    if isinstance(bar, Mapping):
        if "timestamp" in bar:
            return _normalized_timestamp(bar["timestamp"])
        if "t" in bar:
            return _normalized_timestamp(bar["t"])
        raise FeasibilityError("availability bar is missing a timestamp")
    return _normalized_timestamp(bar)


def _entitlement(value: Any) -> FeedEntitlement:
    try:
        return FeedEntitlement(str(value).strip().upper())
    except ValueError:
        return FeedEntitlement.UNKNOWN


class ResearchFeasibilityAdapter:
    """Read-only adapter that strips all market values at the ingestion boundary."""

    def __init__(self, loader: AvailabilityLoader):
        self._loader = loader

    def fetch(self, request: AvailabilityRequest) -> AvailabilitySnapshot:
        raw = self._loader(
            provider=request.provider,
            feed=request.feed,
            symbols=request.symbols,
            start=request.start,
            end=request.end,
            timeframe=request.timeframe,
        )
        if str(raw.get("provider", "")) != request.provider:
            raise FeasibilityError("availability provider does not match the proposal")
        if str(raw.get("feed", "")) != request.feed:
            raise FeasibilityError("availability feed substitution is forbidden")
        bars = raw.get("bars", {})
        if not isinstance(bars, Mapping):
            raise FeasibilityError("availability response bars must be an object")
        timestamps: dict[str, tuple[str, ...]] = {}
        for symbol in request.symbols:
            rows = bars.get(symbol, ())
            if not isinstance(rows, (list, tuple)):
                raise FeasibilityError(f"availability rows for {symbol} must be an array")
            timestamps[symbol] = tuple(
                sorted({_timestamp_from_bar(row) for row in rows})
            )
        pagination_raw = raw.get("pagination_complete", True)
        if isinstance(pagination_raw, Mapping):
            pagination = {
                symbol: pagination_raw.get(symbol) is True for symbol in request.symbols
            }
        else:
            pagination = {
                symbol: pagination_raw is True for symbol in request.symbols
            }
        return AvailabilitySnapshot(
            provider=request.provider,
            feed=request.feed,
            entitlement=_entitlement(raw.get("entitlement", "UNKNOWN")),
            timestamps=timestamps,
            pagination_complete=pagination,
        )


def _minutes_per_interval(interval: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*(?:min|minute|minutes|m)\s*", interval, re.I)
    if not match:
        raise FeasibilityError(f"unsupported feasibility interval: {interval}")
    minutes = int(match.group(1))
    if minutes < 1 or minutes > 390:
        raise FeasibilityError(f"invalid feasibility interval: {interval}")
    return minutes


def availability_request(proposal: ExperimentProposal) -> AvailabilityRequest:
    if not proposal.development_windows:
        raise FeasibilityError("development windows are required for feasibility")
    starts = [str(window.starts_on) for window in proposal.development_windows]
    ends = [str(window.ends_on) for window in proposal.development_windows]
    sessions = tuple(
        sorted(
            {
                str(session)
                for window in proposal.development_windows
                for session in window.expected_sessions
            }
        )
    )
    universe = tuple(symbol.strip().upper() for symbol in proposal.tradable_universe)
    contexts = tuple(
        sorted(
            {
                value.strip().upper()
                for value in proposal.sector_or_context_mapping.values()
                if value.strip()
            }
        )
    )
    benchmark = (
        proposal.market_benchmark.strip().upper()
        if proposal.market_benchmark and proposal.market_benchmark.strip()
        else None
    )
    symbols = tuple(dict.fromkeys((*universe, *contexts, *((benchmark,) if benchmark else ()))))
    return AvailabilityRequest(
        provider=proposal.data_provider,
        feed=proposal.data_feed,
        symbols=symbols,
        context_symbols=contexts,
        benchmark_symbol=benchmark,
        start=min(starts),
        end=max(ends),
        timeframe=proposal.raw_interval,
        expected_sessions=sessions,
    )


def _session(timestamp: str) -> str:
    return _normalized_timestamp(timestamp)[:10]


def evaluate_feasibility(
    proposal: ExperimentProposal,
    snapshot: AvailabilitySnapshot,
) -> FeasibilityResult:
    request = availability_request(proposal)
    if snapshot.provider != request.provider or snapshot.feed != request.feed:
        raise FeasibilityError("availability snapshot does not match proposal source/feed")
    expected_sessions = set(request.expected_sessions)
    bars_per_session = 390 // _minutes_per_interval(proposal.derived_interval)
    expected_per_symbol = len(expected_sessions) * bars_per_session
    rows: list[SymbolAvailability] = []
    timestamp_sets: list[set[str]] = []
    represented_union: set[str] = set()
    for symbol in request.symbols:
        timestamps = set(snapshot.timestamps.get(symbol, ()))
        represented = {_session(item) for item in timestamps}.intersection(expected_sessions)
        represented_union.update(represented)
        timestamp_sets.append(timestamps)
        rows.append(
            SymbolAvailability(
                symbol=symbol,
                expected_sessions=len(expected_sessions),
                represented_sessions=len(represented),
                missing_sessions=tuple(sorted(expected_sessions - represented)),
                timestamp_count=len(timestamps),
                expected_timestamp_capacity=expected_per_symbol,
                timestamp_density=(
                    min(1.0, len(timestamps) / expected_per_symbol)
                    if expected_per_symbol
                    else 0.0
                ),
                pagination_complete=snapshot.pagination_complete.get(symbol, False),
            )
        )
    synchronized = set.intersection(*timestamp_sets) if timestamp_sets else set()
    expected_total = expected_per_symbol * len(request.symbols)
    observed_total = sum(item.timestamp_count for item in rows)
    synchronized_ratio = (
        min(1.0, len(synchronized) / expected_per_symbol)
        if expected_per_symbol
        else 0.0
    )
    completeness_ratio = (
        min(1.0, observed_total / expected_total) if expected_total else 0.0
    )
    floors = proposal.corpus_quality_floors
    minimum_session_ratio = float(floors.get("minimum_session_representation", 1.0))
    minimum_density = float(floors.get("minimum_timestamp_density", 1.0))
    minimum_sync_ratio = float(floors.get("minimum_synchronized_ratio", 1.0))
    minimum_sync_count = int(floors.get("minimum_synchronized_timestamps", 0))
    require_complete_pagination = floors.get("require_complete_pagination", True) is not False

    reasons: list[str] = []
    if snapshot.entitlement is not FeedEntitlement.AVAILABLE:
        reasons.append(f"FEED_{snapshot.entitlement.value}")
    if not expected_sessions:
        reasons.append("EXPECTED_SESSIONS_MISSING")
    for row in rows:
        session_ratio = (
            row.represented_sessions / row.expected_sessions
            if row.expected_sessions
            else 0.0
        )
        if session_ratio < minimum_session_ratio:
            reasons.append(f"MISSING_SESSIONS:{row.symbol}")
        if row.timestamp_density < minimum_density:
            reasons.append(f"TIMESTAMP_DENSITY_LOW:{row.symbol}")
        if require_complete_pagination and not row.pagination_complete:
            reasons.append(f"PAGINATION_INCOMPLETE:{row.symbol}")
    if synchronized_ratio < minimum_sync_ratio or len(synchronized) < minimum_sync_count:
        reasons.append("SYNCHRONIZED_TIMESTAMPS_INSUFFICIENT")
    benchmark_available = bool(
        not request.benchmark_symbol
        or snapshot.timestamps.get(request.benchmark_symbol, ())
    )
    context_available = all(snapshot.timestamps.get(symbol, ()) for symbol in request.context_symbols)
    if proposal.market_benchmark_required and not benchmark_available:
        reasons.append("BENCHMARK_UNAVAILABLE")
    if not context_available:
        reasons.append("CONTEXT_UNAVAILABLE")
    unique_reasons = tuple(dict.fromkeys(reasons))
    return FeasibilityResult(
        feasibility_version=FEASIBILITY_VERSION,
        proposal_id=proposal.proposal_id,
        proposal_revision=proposal.revision,
        proposal_hash=proposal_hash(proposal),
        provider=proposal.data_provider,
        feed=proposal.data_feed,
        feed_entitlement=snapshot.entitlement,
        status="PASS" if not unique_reasons else "FAIL",
        reason_codes=unique_reasons,
        symbols=tuple(rows),
        expected_sessions=len(expected_sessions),
        represented_sessions=len(represented_union),
        missing_sessions=tuple(sorted(expected_sessions - represented_union)),
        pagination_complete=all(item.pagination_complete for item in rows),
        synchronized_timestamp_count=len(synchronized),
        expected_synchronized_capacity=expected_per_symbol,
        synchronized_timestamp_ratio=synchronized_ratio,
        benchmark_available=benchmark_available,
        context_symbols_available=context_available,
        expected_observation_capacity=expected_total,
        observed_availability_capacity=observed_total,
        completeness_ratio=completeness_ratio,
    )


def feasibility_artifact(result: FeasibilityResult) -> dict[str, Any]:
    artifact = deterministic_dict(result)
    assert_availability_only(artifact)
    return artifact


def assert_availability_only(value: Any, path: str = "feasibility") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().casefold()
            if normalized in FORBIDDEN_OUTPUT_KEYS:
                raise FeasibilityError(f"forbidden outcome/market value at {path}.{key}")
            assert_availability_only(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_availability_only(item, f"{path}[{index}]")


def feasibility_bytes(result: FeasibilityResult) -> bytes:
    return (canonical_json(feasibility_artifact(result)) + "\n").encode("utf-8")


def feasibility_from_dict(value: Mapping[str, Any]) -> FeasibilityResult:
    symbols = tuple(
        SymbolAvailability(
            symbol=str(item["symbol"]),
            expected_sessions=int(item["expected_sessions"]),
            represented_sessions=int(item["represented_sessions"]),
            missing_sessions=tuple(str(entry) for entry in item["missing_sessions"]),
            timestamp_count=int(item["timestamp_count"]),
            expected_timestamp_capacity=int(item["expected_timestamp_capacity"]),
            timestamp_density=float(item["timestamp_density"]),
            pagination_complete=item["pagination_complete"] is True,
        )
        for item in value["symbols"]
    )
    result = FeasibilityResult(
        feasibility_version=str(value["feasibility_version"]),
        proposal_id=str(value["proposal_id"]),
        proposal_revision=int(value["proposal_revision"]),
        proposal_hash=str(value["proposal_hash"]),
        provider=str(value["provider"]),
        feed=str(value["feed"]),
        feed_entitlement=_entitlement(value["feed_entitlement"]),
        status=str(value["status"]),
        reason_codes=tuple(str(item) for item in value["reason_codes"]),
        symbols=symbols,
        expected_sessions=int(value["expected_sessions"]),
        represented_sessions=int(value["represented_sessions"]),
        missing_sessions=tuple(str(item) for item in value["missing_sessions"]),
        pagination_complete=value["pagination_complete"] is True,
        synchronized_timestamp_count=int(value["synchronized_timestamp_count"]),
        expected_synchronized_capacity=int(value["expected_synchronized_capacity"]),
        synchronized_timestamp_ratio=float(value["synchronized_timestamp_ratio"]),
        benchmark_available=value["benchmark_available"] is True,
        context_symbols_available=value["context_symbols_available"] is True,
        expected_observation_capacity=int(value["expected_observation_capacity"]),
        observed_availability_capacity=int(value["observed_availability_capacity"]),
        completeness_ratio=float(value["completeness_ratio"]),
    )
    assert_availability_only(deterministic_dict(result))
    return result
