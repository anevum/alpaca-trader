"""ANEVUM V5 F3f — offline-by-default, read-only IEX paper-data preflight.

No secret is read in default mode. Explicit --execute is required to call the
fixed historical market-data GET routes, with dedicated staging credentials
ONLY. No live trades, production archive ACK, R2 writes or OAuth app secrets.
The temporary evidence disappears on exit; this is an eligibility probe, NOT
a complete paper session or durable evidence archive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from next_rhen.evidence_journal import DecisionJournal
from next_rhen.paper_capture import (
    MarketEvidenceStore, capture_scheduled_paper_scan, verify_paper_session,
)
from next_rhen.paper_market_feed import (
    AlpacaHTTPSReadOnly, BARS_PATH, QUOTES_PATH, MarketFeedError,
    PAGE_LIMIT, build_paper_snapshot,
)
from next_rhen.paper_schedule import PaperScheduleLedger
from next_rhen.source_attestation import SourceScanLedger

APPROVAL = "READONLY_IEX_PAPER_DATA_ONLY"
MAX_PROBE_SYMBOLS = 2
MAX_PROBE_REQUESTS = 4 + MAX_PROBE_SYMBOLS
MAX_LOOKBACK_DAYS = 14
MIN_DATA_DELAY_MINUTES = 16
DEFAULT_AT = "2026-10-09T14:33:00+00:00"
DEFAULT_SYMBOLS = ("AAPL", "MSFT")


class PaperProbeError(RuntimeError):
    """A bounded, paper-only staging precondition failed."""


def _asof(when: str, *, now: datetime, require_past: bool) -> datetime:
    if not isinstance(when, str):
        raise PaperProbeError("scan time requires an explicit ISO UTC timestamp")
    try:
        t = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        raise PaperProbeError("scan time invalid ISO timestamp") from None
    if t.tzinfo is None or t.utcoffset() != timedelta(0):
        raise PaperProbeError("scan time must explicitly specify UTC")
    t = t.astimezone(timezone.utc)
    if t.second or t.microsecond:
        raise PaperProbeError("scan time must be aligned to a full minute")
    eastern = t.astimezone(ZoneInfo("America/New_York"))
    if (eastern.weekday() >= 5 or
            not (9 <= eastern.hour <= 16) or
            (eastern.hour == 9 and eastern.minute < 45) or
            (eastern.hour == 16 and eastern.minute != 0)):
        raise PaperProbeError("probe requires regular weekday equity-session time")
    if require_past:
        age = now.astimezone(timezone.utc) - t
        if age < timedelta(minutes=MIN_DATA_DELAY_MINUTES):
            raise PaperProbeError("scan time is too recent for conservative data entitlement")
        if age > timedelta(days=MAX_LOOKBACK_DAYS):
            raise PaperProbeError("scan time outside fixed historical probe window")
    return t


def _symbols(value: str) -> list[str]:
    import re
    symbols = value.split(",") if isinstance(value, str) else []
    if (not 1 <= len(symbols) <= MAX_PROBE_SYMBOLS or
            len(set(symbols)) != len(symbols) or
            any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,14}", s) for s in symbols)):
        raise PaperProbeError("probe supports only one or two unique equity symbols")
    return symbols


class BoundedPaperReader:
    """Pre-network route/scope/request checks, even for an injected test fake."""

    def __init__(self, transport: Any, *, symbols: list[str], at: datetime):
        self.transport = transport
        self.symbols = symbols
        self.at = at
        self.calls = 0
        self.routes_seen: list[str] = []

    def get(self, *, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        """Reject widened, shifted, or mutated market queries BEFORE network IO.

        Merely limiting endpoint and symbol is insufficient: a changed start,
        end, pagination, or limit can silently alter evidence coverage.
        The canonical F3e builder owns the full request envelope.
        """
        if path not in (BARS_PATH, QUOTES_PATH):
            raise PaperProbeError("paper probe refuses non-market endpoint")
        if self.calls >= 4 + len(self.symbols):
            raise PaperProbeError("paper staging GET budget exhausted")
        if not isinstance(params, Mapping):
            raise PaperProbeError("paper staging query missing")
        if path == BARS_PATH:
            expected = {
                "symbols": ",".join(self.symbols),
                "start": (self.at - timedelta(minutes=3)).isoformat(),
                "end": (self.at - timedelta(microseconds=1)).isoformat(),
                "feed": "iex", "limit": str(PAGE_LIMIT), "sort": "asc",
                "timeframe": "1Min",
            }
            keys = set(params)
            if keys != set(expected) and keys != set(expected) | {"page_token"}:
                raise PaperProbeError("paper staging bar query scope invalid")
            if "page_token" in params and (
                    not isinstance(params["page_token"], str)
                    or not 1 <= len(params["page_token"]) <= 512):
                raise PaperProbeError("paper staging bar cursor invalid")
        else:
            expected = {
                "symbols": params.get("symbols"),
                "start": (self.at - timedelta(minutes=2)).isoformat(),
                "end": self.at.isoformat(),
                "feed": "iex", "limit": "1", "sort": "desc",
            }
            if params.get("symbols") not in self.symbols or set(params) != set(expected):
                raise PaperProbeError("paper staging quote must be one as-of symbol")
        if any(params.get(field) != value for field, value in expected.items()):
            raise PaperProbeError("paper staging query deviates from predeclared as-of window")
        self.calls += 1
        self.routes_seen.append(path)
        return self.transport.get(path=path, params=dict(params))


class OfflineSyntheticReader:
    """Deterministic fake IEX responses only. No credentials or HTTP transport."""

    def __init__(self, *, at: datetime, symbols: list[str]):
        self.at, self.symbols = at, symbols

    def get(self, *, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        at = self.at
        if path == BARS_PATH:
            if "page_token" in params:
                raise PaperProbeError("synthetic bar feed contains no additional pages")
            rows = {}
            for i, symbol in enumerate(self.symbols):
                base = 100.0 + i * 100
                rows[symbol] = [{
                    "t": (at - timedelta(minutes=1)).isoformat(),
                    "o": base, "h": base + .5, "l": base - .2,
                    "c": base + .3, "v": 100,
                }]
            return {"bars": rows, "next_page_token": None}
        if path == QUOTES_PATH:
            symbol = params["symbols"]
            base = 100.0 + self.symbols.index(symbol) * 100
            return {
                "quotes": {symbol: [{
                    "t": (at - timedelta(seconds=2)).isoformat(),
                    "bp": base + .15, "ap": base + .2,
                    "bs": 10, "as": 12,
                }]},
                # Older ticks exist, but are intentionally not retrieved.
                "next_page_token": "historical-quotes-not-consumed",
            }
        raise PaperProbeError("synthetic reader denies unsupported endpoint")


def run_probe(transport: Any, *, when: datetime, symbols: list[str],
              mode: str) -> dict[str, Any]:
    """Produce an audit, never a research PASS, without durable offsite storage."""
    if mode not in ("OFFLINE_MOCK", "REAL_READONLY_IEX"):
        raise PaperProbeError("unknown staging probe mode")
    if len(symbols) < 1 or len(symbols) > MAX_PROBE_SYMBOLS:
        raise PaperProbeError("paper probe symbol count exceeds limit")
    session = when.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    scope = {
        "workspace_id": "wrk_f3fstage000001",
        "run_id": "paper-f3f-readonly-probe",
        "cycle_id": "paper-probe-0001",
        "sequence_no": 1,
        "session_date": session,
    }
    slot = {
        "schema_version": "anevum.paper-schedule.v1", **scope,
        "expected_at": when.isoformat(), "execution_mode": "PAPER_RESEARCH_ONLY",
        "planner_origin": (
            "SYNTHETIC_PLAN" if mode == "OFFLINE_MOCK"
            else "INJECTED_SCHEDULER_UNVERIFIED"
        ),
    }
    with tempfile.TemporaryDirectory(prefix="anevum-f3f-stage-") as td:
        root = Path(td)
        with (PaperScheduleLedger(root / "planned.sqlite") as planner,
              SourceScanLedger(root / "scanner.sqlite") as source,
              DecisionJournal(root / "decision.sqlite") as journal):
            market = MarketEvidenceStore(root / "market")
            planner.record_slot(slot)
            reader = BoundedPaperReader(transport, symbols=symbols, at=when)
            captured = build_paper_snapshot(
                reader, slot=slot, symbols=list(symbols), feed="iex")
            if (captured["snapshot"]["source_origin"] != "UNATTESTED_IMPORTED" or
                    captured["provenance"]["quote_history_exhausted"] is not False or
                    captured["provenance"]["bar_pagination_exhausted"] is not True or
                    captured["provenance"]["quote_symbols_requested"] != list(symbols)):
                raise PaperProbeError("market source completeness was overstated")
            ack = capture_scheduled_paper_scan(
                planner, source, journal, market,
                slot=slot, snapshot=captured["snapshot"])
            audited = verify_paper_session(
                planner, source, journal, market,
                workspace_id=scope["workspace_id"],
                run_id=scope["run_id"], session_date=scope["session_date"])
            count_bars = len(captured["snapshot"]["bars"])
            count_quotes = len(captured["snapshot"]["quotes"])
            issues = []
            if count_bars < len(symbols):
                issues.append("PROBE_BAR_COVERAGE_INCOMPLETE")
            if count_quotes != len(symbols):
                issues.append("PROBE_QUOTE_COVERAGE_INCOMPLETE")
            if (audited["evidence_state"] != "AWAITING_EVIDENCE" or
                    not audited["locally_consistent"]):
                issues.append("LOCAL_PROBE_REPLAY_FAILED")
            if ack["broker_calls"] != 0 or ack["evidence_state"] != "AWAITING_EVIDENCE":
                issues.append("PROBE_UNSAFE_RESEARCH_CLAIM")
            return {
                "probe_mode": mode,
                "provider_tested": (
                    "OFFLINE_INJECTED_FAKE" if mode == "OFFLINE_MOCK"
                    else "REAL_ALPACA_IEX_GET_ONLY"
                ),
                "claim": "READONLY_LOCAL_ELIGIBILITY_ONLY",
                "requested_symbols": len(symbols),
                "symbols_with_complete_bar": len(
                    {b["symbol"] for b in captured["snapshot"]["bars"]}),
                "symbols_with_sampled_quote": len(
                    {q["symbol"] for q in captured["snapshot"]["quotes"]}),
                "source_pages_fetched": captured["provenance"]["page_count"],
                "get_requests": reader.calls,
                "last_quote_per_symbol_only": True,
                "full_quote_history_retrieved": False,
                "scanner_and_journal_locally_replayable": audited["locally_consistent"],
                "issues": issues,
                "evidence_state": "BLOCKED" if issues else "AWAITING_EVIDENCE",
                "market_provider_independently_attested": False,
                "complete_market_session_proven": False,
                "remote_R2_source_restore_verified": False,
                "production_archive_ack": False,
                "broker_calls": 0,
                "live_trading_authorized": False,
                "offhost_raw_evidence_retained": False,
            }


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true",
                        help="Explicit opt-in isolated IEX GET-only acceptance")
    parser.add_argument("--scan-at", default=DEFAULT_AT,
                        help="Completed UTC 1-minute bar boundary (ISO with Z/+00:00)")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                        help="One or two US equity symbols, comma separated")
    args = parser.parse_args(argv)
    mode = "REAL_READONLY_IEX" if args.execute else "OFFLINE_MOCK"
    try:
        when = _asof(args.scan_at, now=now or datetime.now(timezone.utc),
                     require_past=args.execute)
        symbols = _symbols(args.symbols)
        if args.execute:
            if os.getenv("ANEVUM_F3F_PAPER_DATA_APPROVED") != APPROVAL:
                raise PaperProbeError("dedicated read-only staging approval missing")
            key_id = os.getenv("ANEVUM_F3F_PAPER_DATA_KEY_ID", "")
            key_secret = os.getenv("ANEVUM_F3F_PAPER_DATA_SECRET_KEY", "")
            if not key_id or not key_secret:
                raise PaperProbeError("isolated paper market-data credentials not configured")
            transport = AlpacaHTTPSReadOnly(
                key_id=key_id, secret_key=key_secret)
        else:
            transport = OfflineSyntheticReader(at=when, symbols=symbols)
        outcome = run_probe(transport, when=when, symbols=symbols, mode=mode)
        print(json.dumps(outcome, sort_keys=True))
        return 0 if outcome["evidence_state"] == "AWAITING_EVIDENCE" else 2
    except (PaperProbeError, MarketFeedError, ValueError, TypeError, RuntimeError):
        # Never print provider exceptions, credential headers, URL tokens,
        # raw quote bodies, or injected environment variable values.
        print(json.dumps({
            "probe_mode": mode, "evidence_state": "BLOCKED",
            "error": "PAPER_SOURCE_PREFLIGHT_BLOCKED",
            "broker_calls": 0, "live_trading_authorized": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
