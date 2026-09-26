from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .edge_corpus import CorpusManifest, CorpusWindow, _coverage, manifest_sha256


NY = ZoneInfo("America/New_York")


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _bar_timestamp(bar: dict[str, Any]) -> datetime | None:
    return _timestamp(bar.get("t") or bar.get("timestamp"))


def _iso_utc(stamp: datetime | None) -> str | None:
    if stamp is None:
        return None
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _pagination_by_symbol(pagination: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    for batch in pagination.get("batches") or []:
        item = {
            "batch_index": batch.get("batch_index"),
            "pages_fetched": int(batch.get("pages_fetched") or 0),
            "pagination_complete": bool(batch.get("pagination_complete")),
            "next_page_token_remaining": bool(batch.get("next_page_token_remaining")),
        }
        for symbol in batch.get("symbols") or []:
            mapping[str(symbol).upper()] = item
    return mapping


def build_window_integrity(
    manifest: CorpusManifest,
    window: CorpusWindow,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    expected_sessions: list[date],
    pagination: dict[str, Any],
) -> dict[str, Any]:
    symbols = tuple(
        dict.fromkeys(
            [*manifest.candidate_symbols, *manifest.confirmation_symbols]
        )
    )
    normalized = {
        symbol.upper(): list(bars or [])
        for symbol, bars in bars_by_symbol.items()
    }
    coverage = _coverage(
        normalized,
        candidate_symbols=manifest.candidate_symbols,
        confirmation_symbols=manifest.confirmation_symbols,
    )
    expected = tuple(sorted(dict.fromkeys(expected_sessions)))
    expected_set = set(expected)
    pagination_map = _pagination_by_symbol(pagination)
    records: list[dict[str, Any]] = []

    for symbol in symbols:
        bars = normalized.get(symbol, [])
        timestamps = [
            stamp
            for bar in bars
            if (stamp := _bar_timestamp(bar)) is not None
        ]
        timestamps.sort()
        observed_dates = {stamp.astimezone(NY).date() for stamp in timestamps}
        represented = tuple(sorted(observed_dates & expected_set))
        missing = tuple(sorted(expected_set - observed_dates))
        unexpected = tuple(sorted(observed_dates - expected_set))
        pagination_item = pagination_map.get(symbol, {})

        records.append(
            {
                "window_id": window.window_id,
                "role": window.role,
                "symbol": symbol,
                "symbol_role": (
                    "candidate"
                    if symbol in manifest.candidate_symbols
                    else "confirmation"
                ),
                "bar_count": len(bars),
                "trading_days_represented": len(represented),
                "expected_trading_days": len(expected),
                "first_timestamp": _iso_utc(timestamps[0]) if timestamps else None,
                "last_timestamp": _iso_utc(timestamps[-1]) if timestamps else None,
                "pagination_complete": pagination_item.get("pagination_complete"),
                "pagination_pages_fetched": pagination_item.get("pages_fetched"),
                "pagination_batch_index": pagination_item.get("batch_index"),
                "pagination_next_page_token_remaining": pagination_item.get(
                    "next_page_token_remaining"
                ),
                "missing_sessions": [item.isoformat() for item in missing],
                "missing_session_count": len(missing),
                "unexpected_sessions": [item.isoformat() for item in unexpected],
                "current_coverage_ratio": coverage["coverage_ratio"].get(symbol, 0.0),
                "unparseable_timestamp_count": len(bars) - len(timestamps),
            }
        )

    return {
        "window": {
            "id": window.window_id,
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "role": window.role,
        },
        "expected_sessions": [item.isoformat() for item in expected],
        "expected_trading_days": len(expected),
        "coverage_reference_bar_count": coverage["expected_bar_count_reference"],
        "pagination": pagination,
        "symbols": records,
        "summary": {
            "symbol_count": len(records),
            "pagination_complete": all(
                item.get("pagination_complete") is True for item in records
            ),
            "symbols_with_missing_sessions": sum(
                1 for item in records if item["missing_session_count"] > 0
            ),
            "symbols_with_unparseable_timestamps": sum(
                1 for item in records if item["unparseable_timestamp_count"] > 0
            ),
        },
    }


def build_corpus_integrity_report(
    manifest: CorpusManifest,
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    records = [
        symbol
        for window in windows
        for symbol in window.get("symbols") or []
    ]
    return {
        "status": "diagnostic_only",
        "scope": {
            "role": "development",
            "roles_excluded": ["validation", "holdout", "quarantine"],
            "window_count": len(windows),
            "symbol_count": len(
                tuple(
                    dict.fromkeys(
                        [
                            *manifest.candidate_symbols,
                            *manifest.confirmation_symbols,
                        ]
                    )
                )
            ),
            "record_count": len(records),
        },
        "corpus": {
            "version": manifest.version,
            "manifest_sha256": manifest_sha256(manifest),
            "data_feed": manifest.data_feed,
            "timeframe": manifest.timeframe,
            "session_calendar_source": "alpaca_trading_calendar",
        },
        "summary": {
            "pagination_complete": all(
                item.get("pagination_complete") is True for item in records
            ),
            "records_with_missing_sessions": sum(
                1 for item in records if item.get("missing_session_count", 0) > 0
            ),
            "records_with_unparseable_timestamps": sum(
                1
                for item in records
                if item.get("unparseable_timestamp_count", 0) > 0
            ),
        },
        "windows": windows,
    }
