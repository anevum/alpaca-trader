from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings
from .corpus_integrity import (
    build_corpus_integrity_report,
    build_window_integrity,
)
from .edge_corpus import _window_bounds, load_manifest, windows_for_roles
from .market_data import MarketDataClient


PAGINATION_PAGE_LIMIT = 50
PAGE_BAR_LIMIT = 10000


async def _market_calendar(
    market_data: MarketDataClient,
    *,
    start: date,
    end: date,
) -> list[date]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(
            f"{market_data.settings.base_url}/v2/calendar",
            headers=market_data.headers,
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        response.raise_for_status()
        payload = response.json()
    return sorted(
        {
            date.fromisoformat(str(item["date"]))
            for item in payload
            if item.get("date")
        }
    )


async def _fetch_with_pagination_diagnostics(
    market_data: MarketDataClient,
    symbols: list[str],
    *,
    start,
    end,
) -> dict[str, Any]:
    if not market_data.settings.credentials_configured:
        raise RuntimeError("Alpaca credentials are not configured")
    if end <= start:
        raise ValueError("historical bar end must be after start")

    batches = market_data._batches(symbols)
    output: dict[str, list[dict[str, Any]]] = {
        symbol: [] for batch in batches for symbol in batch
    }
    batch_reports: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for batch_index, batch in enumerate(batches):
            params = {
                "symbols": ",".join(batch),
                "timeframe": market_data.settings.bar_timeframe,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "limit": PAGE_BAR_LIMIT,
                "sort": "asc",
                "feed": market_data.settings.data_feed,
            }
            page_token: str | None = None
            pages_fetched = 0
            pagination_complete = False

            for _ in range(PAGINATION_PAGE_LIMIT):
                request_params = dict(params)
                if page_token:
                    request_params["page_token"] = page_token
                response = await client.get(
                    f"{market_data.settings.data_base_url}/v2/stocks/bars",
                    headers=market_data.headers,
                    params=request_params,
                )
                response.raise_for_status()
                data = response.json()
                pages_fetched += 1
                for symbol, bars in (data.get("bars") or {}).items():
                    output.setdefault(symbol.upper(), []).extend(bars or [])
                page_token = data.get("next_page_token")
                if not page_token:
                    pagination_complete = True
                    break

            batch_reports.append(
                {
                    "batch_index": batch_index,
                    "symbols": list(batch),
                    "pages_fetched": pages_fetched,
                    "pagination_complete": pagination_complete,
                    "next_page_token_remaining": bool(page_token),
                    "page_limit": PAGINATION_PAGE_LIMIT,
                    "bar_limit_per_page": PAGE_BAR_LIMIT,
                }
            )

    return {
        "bars": output,
        "pagination": {
            "pagination_complete": all(
                item["pagination_complete"] for item in batch_reports
            ),
            "batch_count": len(batch_reports),
            "batches": batch_reports,
        },
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = get_settings()
    manifest = load_manifest(args.manifest)
    if settings.data_feed != manifest.data_feed:
        raise ValueError(
            f"runtime DATA_FEED={settings.data_feed} does not match "
            f"corpus feed {manifest.data_feed}"
        )
    if settings.bar_timeframe != manifest.timeframe:
        raise ValueError(
            f"runtime BAR_TIMEFRAME={settings.bar_timeframe} does not match "
            f"corpus timeframe {manifest.timeframe}"
        )

    market_data = MarketDataClient(settings)
    windows = list(windows_for_roles(manifest, {"development"}))
    selected = set(args.window or [])
    if selected:
        unknown = selected - {window.window_id for window in windows}
        if unknown:
            raise ValueError(
                f"unknown development window(s): {sorted(unknown)}"
            )
        windows = [window for window in windows if window.window_id in selected]

    symbols = list(
        dict.fromkeys(
            [*manifest.candidate_symbols, *manifest.confirmation_symbols]
        )
    )
    window_reports = []
    for index, window in enumerate(windows, start=1):
        print(
            json.dumps(
                {
                    "event": "corpus_integrity_window_started",
                    "window": window.window_id,
                    "position": index,
                    "total": len(windows),
                }
            ),
            flush=True,
        )
        expected_sessions = await _market_calendar(
            market_data,
            start=window.start,
            end=window.end,
        )
        if not expected_sessions:
            raise RuntimeError(
                f"Alpaca calendar returned no sessions for {window.window_id}"
            )
        start, end = _window_bounds(window)
        fetch = await _fetch_with_pagination_diagnostics(
            market_data,
            symbols,
            start=start,
            end=end,
        )
        report = build_window_integrity(
            manifest,
            window,
            fetch["bars"],
            expected_sessions=expected_sessions,
            pagination=fetch["pagination"],
        )
        window_reports.append(report)
        print(
            json.dumps(
                {
                    "event": "corpus_integrity_window_finished",
                    "window": window.window_id,
                    "pagination_complete": report["summary"]["pagination_complete"],
                    "symbols_with_missing_sessions": report["summary"][
                        "symbols_with_missing_sessions"
                    ],
                    "coverage_reference_bar_count": report[
                        "coverage_reference_bar_count"
                    ],
                }
            ),
            flush=True,
        )

    return build_corpus_integrity_report(manifest, window_reports)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose development-corpus integrity without opening validation, "
            "holdout or quarantine windows."
        )
    )
    parser.add_argument(
        "--manifest",
        default="research/edge-corpus-v1.json",
    )
    parser.add_argument(
        "--window",
        action="append",
        default=[],
        help="Optional development-window id. Repeat to select multiple windows.",
    )
    parser.add_argument(
        "--output",
        default="corpus-integrity-report.json",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    payload = asyncio.run(run(args))
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
