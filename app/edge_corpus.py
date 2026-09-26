from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .market_data import MarketDataClient


NY = ZoneInfo("America/New_York")
ALLOWED_ROLES = {"development", "validation", "holdout", "quarantine"}
REQUIRED_SESSION_COVERAGE = 1.0
CACHE_SCHEMA_VERSION = 2
DEFAULT_RESEARCH_COST_SCENARIOS = (("base", "5", "2"), ("moderate", "8", "3"), ("stress", "12", "5"))


@dataclass(frozen=True)
class CorpusWindow:
    window_id: str
    start: date
    end: date
    role: str

    @property
    def calendar_days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class CorpusManifest:
    version: str
    created_at: str
    data_feed: str
    timeframe: str
    candidate_symbols: tuple[str, ...]
    confirmation_symbols: tuple[str, ...]
    windows: tuple[CorpusWindow, ...]
    notes: tuple[str, ...]
    research_horizon_minutes: int = 15
    research_event_cooldown_minutes: int = 15
    research_stop_pct: str = "0.0035"
    research_target_pct: str = "0.005"
    research_entry_start: str = "09:31"
    research_entry_cutoff: str = "15:30"
    research_cost_scenarios: tuple[tuple[str, str, str], ...] = DEFAULT_RESEARCH_COST_SCENARIOS


def _symbols(values: list[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(value).strip().upper()
            for value in values
            if str(value).strip()
        )
    )


def load_manifest(path: str | Path) -> CorpusManifest:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    windows = tuple(
        CorpusWindow(
            window_id=str(item["id"]),
            start=date.fromisoformat(str(item["start"])),
            end=date.fromisoformat(str(item["end"])),
            role=str(item["role"]),
        )
        for item in payload["windows"]
    )
    research = payload.get("research") or {}
    configured_cost_scenarios = research.get("cost_scenarios")
    if configured_cost_scenarios:
        cost_scenarios = tuple(
            (
                str(item["name"]),
                str(item["spread_bps"]),
                str(item["slippage_bps_per_side"]),
            )
            for item in configured_cost_scenarios
        )
    else:
        cost_scenarios = DEFAULT_RESEARCH_COST_SCENARIOS
    manifest = CorpusManifest(
        version=str(payload["version"]),
        created_at=str(payload["created_at"]),
        data_feed=str(payload.get("data_feed") or "iex"),
        timeframe=str(payload.get("timeframe") or "1Min"),
        candidate_symbols=_symbols(payload["candidate_symbols"]),
        confirmation_symbols=_symbols(payload["confirmation_symbols"]),
        windows=windows,
        notes=tuple(str(note) for note in payload.get("notes") or []),
        research_horizon_minutes=int(research.get("horizon_minutes", 15)),
        research_event_cooldown_minutes=int(research.get("event_cooldown_minutes", 15)),
        research_stop_pct=str(research.get("stop_pct", "0.0035")),
        research_target_pct=str(research.get("target_pct", "0.005")),
        research_entry_start=str(research.get("entry_start", "09:31")),
        research_entry_cutoff=str(research.get("entry_cutoff", "15:30")),
        research_cost_scenarios=cost_scenarios,
    )
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: CorpusManifest) -> None:
    if not manifest.version.strip():
        raise ValueError("corpus version is required")
    if not manifest.candidate_symbols:
        raise ValueError("candidate symbol panel cannot be empty")
    if not manifest.confirmation_symbols:
        raise ValueError("confirmation symbol panel cannot be empty")
    if manifest.research_horizon_minutes <= 0:
        raise ValueError("research horizon must be positive")
    if manifest.research_event_cooldown_minutes <= 0:
        raise ValueError("research event cooldown must be positive")
    if float(manifest.research_stop_pct) <= 0 or float(manifest.research_target_pct) <= 0:
        raise ValueError("research stop and target must be positive")
    time.fromisoformat(manifest.research_entry_start)
    time.fromisoformat(manifest.research_entry_cutoff)
    if not manifest.research_cost_scenarios:
        raise ValueError("at least one research cost scenario is required")
    for name, spread, slippage in manifest.research_cost_scenarios:
        if not name or float(spread) < 0 or float(slippage) < 0:
            raise ValueError("invalid research cost scenario")

    ids: set[str] = set()
    ordered = sorted(manifest.windows, key=lambda item: item.start)
    previous_end: date | None = None
    roles = set()

    for window in ordered:
        if not window.window_id:
            raise ValueError("window id cannot be empty")
        if window.window_id in ids:
            raise ValueError(f"duplicate window id: {window.window_id}")
        ids.add(window.window_id)

        if window.role not in ALLOWED_ROLES:
            raise ValueError(f"invalid corpus role: {window.role}")
        roles.add(window.role)

        if window.end < window.start:
            raise ValueError(f"window ends before it starts: {window.window_id}")
        if window.calendar_days > 21:
            raise ValueError(
                f"window exceeds 21-calendar-day research fetch limit: "
                f"{window.window_id}"
            )
        if previous_end is not None and window.start <= previous_end:
            raise ValueError("corpus windows must not overlap")
        previous_end = window.end

    required_roles = {"development", "validation", "holdout"}
    if not required_roles <= roles:
        missing = sorted(required_roles - roles)
        raise ValueError(f"corpus missing required roles: {missing}")

    role_order = {
        "development": 0,
        "validation": 1,
        "holdout": 2,
        "quarantine": 3,
    }
    non_quarantine = [
        window for window in ordered if window.role != "quarantine"
    ]
    observed = [role_order[window.role] for window in non_quarantine]
    if observed != sorted(observed):
        raise ValueError(
            "development, validation and holdout windows must be chronological"
        )


def manifest_payload(manifest: CorpusManifest) -> dict[str, Any]:
    return {
        "version": manifest.version,
        "created_at": manifest.created_at,
        "data_feed": manifest.data_feed,
        "timeframe": manifest.timeframe,
        "candidate_symbols": list(manifest.candidate_symbols),
        "confirmation_symbols": list(manifest.confirmation_symbols),
        "research": {
            "horizon_minutes": manifest.research_horizon_minutes,
            "event_cooldown_minutes": manifest.research_event_cooldown_minutes,
            "stop_pct": manifest.research_stop_pct,
            "target_pct": manifest.research_target_pct,
            "entry_start": manifest.research_entry_start,
            "entry_cutoff": manifest.research_entry_cutoff,
            "cost_scenarios": [
                {"name": name, "spread_bps": spread, "slippage_bps_per_side": slippage}
                for name, spread, slippage in manifest.research_cost_scenarios
            ],
        },
        "windows": [
            {
                "id": window.window_id,
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "role": window.role,
            }
            for window in manifest.windows
        ],
        "notes": list(manifest.notes),
    }


def manifest_sha256(manifest: CorpusManifest) -> str:
    encoded = json.dumps(
        manifest_payload(manifest),
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_key(manifest: CorpusManifest, window: CorpusWindow) -> str:
    symbol_hash = hashlib.sha256(
        ",".join(
            [*manifest.candidate_symbols, *manifest.confirmation_symbols]
        ).encode("utf-8")
    ).hexdigest()[:12]
    return (
        f"{window.window_id}-integrity-v{CACHE_SCHEMA_VERSION}-"
        f"{manifest.data_feed}-{manifest.timeframe}-{symbol_hash}.json.gz"
    )


def window_cache_path(
    cache_dir: str | Path,
    manifest: CorpusManifest,
    window: CorpusWindow,
) -> Path:
    return Path(cache_dir) / manifest.version / _cache_key(manifest, window)


def _window_bounds(window: CorpusWindow) -> tuple[datetime, datetime]:
    start = datetime.combine(
        window.start,
        time(0, 0),
        tzinfo=NY,
    ).astimezone(timezone.utc)
    end = datetime.combine(
        window.end + timedelta(days=1),
        time(0, 0),
        tzinfo=NY,
    ).astimezone(timezone.utc)
    return start, end


def _bar_timestamp(bar: dict[str, Any]) -> datetime | None:
    value = bar.get("t") or bar.get("timestamp")
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(NY)


def _regular_session_dates(
    bars: list[dict[str, Any]],
) -> set[date]:
    dates: set[date] = set()
    for bar in bars:
        stamp = _bar_timestamp(bar)
        if stamp is None:
            continue
        local_time = stamp.timetz().replace(tzinfo=None)
        if time(9, 30) <= local_time < time(16, 0):
            dates.add(stamp.date())
    return dates


def _coverage(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    candidate_symbols: tuple[str, ...],
    confirmation_symbols: tuple[str, ...],
    expected_sessions: tuple[date, ...],
) -> dict[str, Any]:
    all_symbols = list(
        dict.fromkeys([*candidate_symbols, *confirmation_symbols])
    )
    counts = {
        symbol: len(bars_by_symbol.get(symbol, []))
        for symbol in all_symbols
    }

    nonzero = [value for value in counts.values() if value > 0]
    density_reference = max(nonzero) if nonzero else 0
    density_ratios = {
        symbol: (
            round(count / density_reference, 6)
            if density_reference > 0
            else 0.0
        )
        for symbol, count in counts.items()
    }

    expected = tuple(sorted(dict.fromkeys(expected_sessions)))
    expected_set = set(expected)
    observed_sessions = {
        symbol: _regular_session_dates(bars_by_symbol.get(symbol, []))
        for symbol in all_symbols
    }
    represented_sessions = {
        symbol: observed_sessions[symbol] & expected_set
        for symbol in all_symbols
    }
    missing_sessions = {
        symbol: sorted(expected_set - observed_sessions[symbol])
        for symbol in all_symbols
    }
    session_counts = {
        symbol: len(represented_sessions[symbol])
        for symbol in all_symbols
    }
    session_ratios = {
        symbol: (
            round(session_counts[symbol] / len(expected), 6)
            if expected
            else 0.0
        )
        for symbol in all_symbols
    }

    eligible = [
        symbol
        for symbol in candidate_symbols
        if session_ratios.get(symbol, 0.0) >= REQUIRED_SESSION_COVERAGE
    ]
    missing_confirmations = [
        symbol
        for symbol in confirmation_symbols
        if session_ratios.get(symbol, 0.0) < REQUIRED_SESSION_COVERAGE
    ]

    return {
        "coverage_rule": "all_expected_regular_trading_sessions_present",
        "expected_session_count": len(expected),
        "expected_sessions": [item.isoformat() for item in expected],
        "session_counts": session_counts,
        "coverage_ratio": session_ratios,
        "missing_sessions": {
            symbol: [item.isoformat() for item in values]
            for symbol, values in missing_sessions.items()
        },
        "required_session_coverage": REQUIRED_SESSION_COVERAGE,
        "bar_counts": counts,
        "iex_bar_density_reference_count": density_reference,
        "iex_bar_density_ratio": density_ratios,
        "eligible_candidate_symbols": eligible,
        "eligible_candidate_count": len(eligible),
        "candidate_count": len(candidate_symbols),
        "missing_or_incomplete_confirmations": missing_confirmations,
    }


def _cache_payload(
    manifest: CorpusManifest,
    window: CorpusWindow,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    expected_sessions: tuple[date, ...],
    fetch_integrity: dict[str, Any],
) -> dict[str, Any]:
    normalized = {
        symbol.upper(): list(bars or [])
        for symbol, bars in bars_by_symbol.items()
    }
    coverage = _coverage(
        normalized,
        candidate_symbols=manifest.candidate_symbols,
        confirmation_symbols=manifest.confirmation_symbols,
        expected_sessions=expected_sessions,
    )
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "manifest_version": manifest.version,
        "manifest_sha256": manifest_sha256(manifest),
        "window": {
            "id": window.window_id,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "role": window.role,
        },
        "data_feed": manifest.data_feed,
        "timeframe": manifest.timeframe,
        "fetch_integrity": fetch_integrity,
        "coverage": coverage,
        "bars": normalized,
    }


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))


def _read_cache(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def validate_cached_window(
    payload: dict[str, Any],
    manifest: CorpusManifest,
    window: CorpusWindow,
) -> None:
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION:
        raise ValueError("cached corpus schema version does not match")
    if payload.get("manifest_sha256") != manifest_sha256(manifest):
        raise ValueError("cached corpus manifest hash does not match")
    cached = payload.get("window") or {}
    if cached.get("id") != window.window_id:
        raise ValueError("cached corpus window id does not match")
    if cached.get("role") != window.role:
        raise ValueError("cached corpus role does not match")

    fetch_integrity = payload.get("fetch_integrity") or {}
    if fetch_integrity.get("pagination_complete") is not True:
        raise ValueError("cached corpus pagination is incomplete")

    coverage = payload.get("coverage") or {}
    if coverage.get("expected_session_count", 0) <= 0:
        raise ValueError("cached corpus has no expected trading sessions")
    if coverage.get("missing_or_incomplete_confirmations"):
        raise ValueError(
            "cached corpus window has incomplete confirmation data: "
            f"{coverage['missing_or_incomplete_confirmations']}"
        )


async def fetch_or_load_window(
    market_data: MarketDataClient,
    manifest: CorpusManifest,
    window: CorpusWindow,
    *,
    cache_dir: str | Path = ".edge_corpus",
    refresh: bool = False,
) -> dict[str, Any]:
    expected_sessions = tuple(
        await market_data.market_calendar(
            start=window.start,
            end=window.end,
        )
    )
    if not expected_sessions:
        raise RuntimeError(
            f"Alpaca calendar returned no sessions for {window.window_id}"
        )

    path = window_cache_path(cache_dir, manifest, window)
    if path.exists() and not refresh:
        payload = _read_cache(path)
        validate_cached_window(payload, manifest, window)
        payload["cache"] = {
            "path": str(path),
            "hit": True,
        }
        return payload

    start, end = _window_bounds(window)
    symbols = list(
        dict.fromkeys(
            [*manifest.candidate_symbols, *manifest.confirmation_symbols]
        )
    )
    fetch = await market_data.historical_bars_many_with_metadata(
        symbols,
        start=start,
        end=end,
    )
    payload = _cache_payload(
        manifest,
        window,
        fetch["bars"],
        expected_sessions=expected_sessions,
        fetch_integrity=fetch["pagination"],
    )
    validate_cached_window(payload, manifest, window)
    _write_cache(path, payload)
    payload["cache"] = {
        "path": str(path),
        "hit": False,
    }
    return payload


def windows_for_roles(
    manifest: CorpusManifest,
    roles: set[str],
) -> tuple[CorpusWindow, ...]:
    unknown = roles - ALLOWED_ROLES
    if unknown:
        raise ValueError(f"unknown corpus roles: {sorted(unknown)}")
    return tuple(
        window
        for window in manifest.windows
        if window.role in roles
    )
