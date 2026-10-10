"""ANEVUM V5 F3b: bounded OFFLINE / unverified-input paper scanner capture.

Separate schedule -> immutable local raw market objects -> source-ledger manifest
-> F1 WAL journal, in that order. This prototype neither fetches a live feed
nor opens a brokerage account. No actor may call this broker execution.
Even fully consistent local evidence remains AWAITING_EVIDENCE.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping

from .evidence_journal import DecisionJournal, SCHEMA_VERSION as CYCLE_SCHEMA
from .evidence_vault import LocalImmutableObjectStore, ArchiveIntegrityError
from .paper_schedule import (
    PaperScheduleLedger, PaperScheduleError, PaperScheduleIntegrityError,
    validate_slot,
)
from .source_attestation import (
    SourceScanLedger, SOURCE_SCHEMA, _validate_scope, digest,
    verify_scan_population, SourceIntegrityError, SourceContractError,
)

SNAPSHOT_SCHEMA = "anevum.paper-market-input.v1"
RECORD_SCHEMA = "anevum.paper-market-record.v1"
INDEX_SCHEMA = "anevum.paper-market-index.v1"
STRATEGY_VERSION = "F3B_OFFLINE_BASELINE_V1"
RULE_CONFIG = {"min_body_bps": 15.0, "max_spread_bps": 80.0}
RULE_SHA256 = sha256(b"ANEVUM_F3B_OFFLINE_PAPER_RULESET_V1").hexdigest()
SOURCE_ORIGINS = {"SYNTHETIC_OFFLINE", "UNATTESTED_IMPORTED"}
SNAPSHOT_FIELDS = {
    "schema_version", "workspace_id", "run_id", "cycle_id", "sequence_no",
    "session_date", "occurred_at", "source_origin", "provider",
    "asof_timestamp", "universe_symbols", "bars", "quotes",
}
BAR_FIELDS = {"symbol", "start", "end", "open", "high", "low", "close", "volume"}
QUOTE_FIELDS = {"symbol", "observed_at", "bid", "ask", "bid_size", "ask_size"}
PROVIDER = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,14}$")
HEX = re.compile(r"^[0-9a-f]{64}$")
MAX_SYMBOLS, MAX_BARS, MAX_QUOTES = 100, 6000, 100
MAX_DOC_BYTES = 1_000_000
MAX_ISSUES = 40


class PaperCaptureError(ValueError):
    """An offline market input or scheduler relation is not valid."""


class PaperCaptureIntegrityError(RuntimeError):
    """A market object, schedule or pre-journal capture has been corrupted."""


def _canon(value: Any) -> bytes:
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PaperCaptureError("market object cannot be serialized") from exc
    if len(raw) > MAX_DOC_BYTES:
        raise PaperCaptureError("market object exceeds bounded evidence size")
    return raw


def _utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PaperCaptureError(f"{name} requires timezone")
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperCaptureError(f"{name} invalid timestamp") from exc
    if t.tzinfo is None:
        raise PaperCaptureError(f"{name} requires timezone")
    return t.astimezone(timezone.utc)


def _number(value: Any, name: str, *, positive: bool = True) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PaperCaptureError(f"{name} must be finite numeric")
    if positive and value <= 0:
        raise PaperCaptureError(f"{name} must be positive")
    return float(value)


def _quantity(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PaperCaptureError(f"{name} must be a nonnegative integer")


def _validate_bar(bar: Any, at: datetime, universe: set[str]) -> None:
    if not isinstance(bar, dict) or set(bar) != BAR_FIELDS or bar["symbol"] not in universe:
        raise PaperCaptureError("invalid or out-of-universe market bar")
    start, end = _utc(bar["start"], "bar.start"), _utc(bar["end"], "bar.end")
    if end - start != timedelta(minutes=1) or end > at:
        raise PaperCaptureError("bar must be a completed 1-minute as-of observation")
    prices = {k: _number(bar[k], f"bar.{k}") for k in ("open","high","low","close")}
    if (prices["high"] < max(prices["open"], prices["close"]) or
            prices["low"] > min(prices["open"], prices["close"]) or
            prices["low"] > prices["high"]):
        raise PaperCaptureError("bar violates OHLC bounds")
    _quantity(bar["volume"], "bar.volume")


def _validate_quote(quote: Any, at: datetime, universe: set[str]) -> None:
    if not isinstance(quote, dict) or set(quote) != QUOTE_FIELDS or quote["symbol"] not in universe:
        raise PaperCaptureError("invalid or out-of-universe quote")
    when = _utc(quote["observed_at"], "quote.observed_at")
    if when > at:
        raise PaperCaptureError("future quote cannot be observed")
    bid, ask = _number(quote["bid"], "quote.bid"), _number(quote["ask"], "quote.ask")
    if bid > ask:
        raise PaperCaptureError("quote has crossed bid/ask")
    for key in ("bid_size", "ask_size"):
        _quantity(quote[key], f"quote.{key}")


def validate_snapshot(snapshot: Mapping[str, Any], slot: Mapping[str, Any]) -> None:
    """Reject future-leaking and incomplete/false scope inputs before any writes."""
    validate_slot(slot)
    if not isinstance(snapshot, dict) or set(snapshot) != SNAPSHOT_FIELDS:
        raise PaperCaptureError("snapshot requires exact offline market input fields")
    if snapshot["schema_version"] != SNAPSHOT_SCHEMA:
        raise PaperCaptureError("unsupported market snapshot schema")
    for key in ("workspace_id", "run_id", "cycle_id", "sequence_no", "session_date"):
        if snapshot[key] != slot[key]:
            raise PaperCaptureError(f"snapshot mismatches independently planned {key}")
    if snapshot["source_origin"] not in SOURCE_ORIGINS:
        raise PaperCaptureError("source must be explicitly synthetic or unverified")
    if not isinstance(snapshot["provider"], str) or not PROVIDER.fullmatch(snapshot["provider"]):
        raise PaperCaptureError("invalid market provider identity")
    occurred, asof = _utc(snapshot["occurred_at"], "occurred_at"), _utc(snapshot["asof_timestamp"], "asof")
    expected = _utc(slot["expected_at"], "expected_at")
    if occurred < expected or occurred > expected + timedelta(minutes=5):
        raise PaperCaptureError("market observation outside planned scan window")
    if asof > occurred:
        raise PaperCaptureError("market as-of timestamp is in the future")
    from zoneinfo import ZoneInfo
    if occurred.astimezone(ZoneInfo("America/New_York")).date().isoformat() != slot["session_date"]:
        raise PaperCaptureError("observation outside declared market session")
    symbols, bars, quotes = snapshot["universe_symbols"], snapshot["bars"], snapshot["quotes"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS:
        raise PaperCaptureError("paper universe requires 1-100 symbols")
    if any(not isinstance(s, str) or not SYMBOL.fullmatch(s) for s in symbols):
        raise PaperCaptureError("invalid universe symbol")
    if len(set(symbols)) != len(symbols):
        raise PaperCaptureError("duplicate symbol in paper universe")
    if not isinstance(bars, list) or not isinstance(quotes, list) or len(bars) > MAX_BARS or len(quotes) > MAX_QUOTES:
        raise PaperCaptureError("market bars/quotes exceed bounded arrays")
    universe = set(symbols)
    seen_bars: set[tuple[str, str]] = set()
    for bar in bars:
        _validate_bar(bar, asof, universe)
        key = (bar["symbol"], _utc(bar["end"], "bar.end").isoformat())
        if key in seen_bars:
            raise PaperCaptureError("duplicate completed bar")
        seen_bars.add(key)
    seen_quotes: set[str] = set()
    for quote in quotes:
        _validate_quote(quote, asof, universe)
        if quote["symbol"] in seen_quotes:
            raise PaperCaptureError("duplicate latest quote")
        seen_quotes.add(quote["symbol"])
    _canon(snapshot)


class MarketEvidenceStore:
    """Content-addressed, read-back-verified LOCAL records. Not R2 or a market provider."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.objects = LocalImmutableObjectStore(self.root)

    def _key(self, workspace_id: str, run_id: str, kind: str, h: str) -> str:
        _validate_scope(workspace_id, run_id, "2026-10-09")  # scope only, not a market date claim
        if kind not in {"bar", "quote", "bars_index", "quotes_index", "universe"}:
            raise PaperCaptureError("unknown market object kind")
        if not isinstance(h, str) or not HEX.fullmatch(h):
            raise PaperCaptureError("invalid market object digest")
        return f"private/anevum-v5/{workspace_id}/{run_id}/paper-market/{kind}/{h}.json"

    def put(self, *, workspace_id: str, run_id: str, kind: str, document: dict[str, Any]) -> str:
        raw = _canon(document)
        h = sha256(raw).hexdigest()
        try:
            self.objects.put_once(self._key(workspace_id, run_id, kind, h), raw)
        except ArchiveIntegrityError as exc:
            raise PaperCaptureIntegrityError("market object cannot be sealed") from exc
        return h

    def load(self, *, workspace_id: str, run_id: str, kind: str, digest_sha256: str) -> dict[str, Any]:
        try:
            raw = self.objects.get(self._key(workspace_id, run_id, kind, digest_sha256))
        except ArchiveIntegrityError as exc:
            raise PaperCaptureIntegrityError("market object location invalid") from exc
        if raw is None or sha256(raw).hexdigest() != digest_sha256:
            raise PaperCaptureIntegrityError("market object missing or corrupted")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or _canon(payload) != raw:
                raise PaperCaptureIntegrityError("market object is not canonical")
        except (ValueError, TypeError) as exc:
            raise PaperCaptureIntegrityError("market object invalid JSON") from exc
        return payload


def _evaluate(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Deterministic non-trading baseline. QUALIFIED is NOT a buy signal."""
    now = _utc(snapshot["asof_timestamp"], "asof")
    latest_bars: dict[str, dict[str, Any]] = {}
    for bar in snapshot["bars"]:
        current = latest_bars.get(bar["symbol"])
        if current is None or _utc(bar["end"], "end") > _utc(current["end"], "end"):
            latest_bars[bar["symbol"]] = bar
    quotes = {q["symbol"]: q for q in snapshot["quotes"]}
    output: list[dict[str, Any]] = []
    for symbol in snapshot["universe_symbols"]:
        bar, quote = latest_bars.get(symbol), quotes.get(symbol)
        features: dict[str, Any] = {}
        decision, reason = "UNMEASURABLE", "NO_COMPLETED_BAR"
        if bar is not None:
            features["bar_end"] = bar["end"]
            if now - _utc(bar["end"], "bar.end") > timedelta(minutes=2):
                reason = "STALE_COMPLETED_BAR"
            elif quote is None:
                reason = "NO_QUOTE"
            elif now - _utc(quote["observed_at"], "quote.observed_at") > timedelta(minutes=2):
                reason = "STALE_QUOTE"
            else:
                body_bps = (bar["close"] / bar["open"] - 1) * 10000
                spread_bps = (quote["ask"] - quote["bid"]) / ((quote["ask"] + quote["bid"]) / 2) * 10000
                features.update(body_bps=round(body_bps, 6),
                                spread_bps=round(spread_bps, 6),
                                quote_observed_at=quote["observed_at"])
                qualified = body_bps >= RULE_CONFIG["min_body_bps"] and spread_bps <= RULE_CONFIG["max_spread_bps"]
                decision, reason = (
                    ("QUALIFIED", "PAPER_BASELINE_GATE_MET") if qualified
                    else ("REJECTED", "PAPER_BASELINE_GATE_NOT_MET")
                )
        output.append({"symbol": symbol, "decision": decision, "reason": reason,
                       "observed_at": snapshot["occurred_at"], "features": features})
    return output


def _capture_market_refs(store: MarketEvidenceStore, snap: dict[str, Any]) -> dict[str, Any]:
    scope = {"workspace_id": snap["workspace_id"], "run_id": snap["run_id"]}
    identity = {k: snap[k] for k in ("workspace_id","run_id","cycle_id","sequence_no","session_date")}
    common = {"schema_version": INDEX_SCHEMA, **identity,
              "source_origin": snap["source_origin"], "provider": snap["provider"],
              "asof_timestamp": snap["asof_timestamp"]}
    universe_doc = {**common, "kind": "universe", "symbols": snap["universe_symbols"]}
    universe_hash = store.put(**scope, kind="universe", document=universe_doc)
    refs: dict[str, str] = {}
    for plural, singular, timestamp_field in (
        ("bars", "bar", "end"), ("quotes", "quote", "observed_at"),
    ):
        references = []
        for record in snap[plural]:
            rec_document = {"schema_version": RECORD_SCHEMA,
                            "kind": singular, "provider": snap["provider"],
                            "source_origin": snap["source_origin"], "record": record}
            ref = store.put(**scope, kind=singular, document=rec_document)
            references.append({"symbol": record["symbol"], "sha256": ref,
                               "timestamp": record[timestamp_field]})
        index_doc = {**common, "kind": plural, "items": references}
        refs[plural] = store.put(**scope, kind=plural+"_index", document=index_doc)
    return {"feed": snap["provider"], "data_status": "PARTIAL",
            "asof_timestamp": snap["asof_timestamp"],
            "universe_ref": universe_hash, "bars_ref": refs["bars"],
            "quotes_ref": refs["quotes"]}


def capture_scheduled_paper_scan(
    schedule: PaperScheduleLedger, source: SourceScanLedger,
    journal: DecisionJournal, store: MarketEvidenceStore, *,
    slot: dict[str, Any], snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Write scanner evidence before journal; retry is idempotent after crash.

    Orphan local market records are allowed if a later gate fails; they can
    never authorize an archive ACK. Distinct WAL files prevent circular proof.
    """
    if len({schedule.path, Path(source.path).resolve(), Path(journal.path).resolve()}) != 3:
        raise PaperCaptureError("schedule, scanner and journal must be distinct databases")
    validate_snapshot(snapshot, slot)
    actual = schedule.read_session_verified(workspace_id=slot["workspace_id"],
                  run_id=slot["run_id"], session_date=slot["session_date"])["slots"]
    if not any(planned == slot for planned in actual):
        raise PaperCaptureError("scheduled scan was not independently predeclared")
    candidates = _evaluate(snapshot)
    market_source = _capture_market_refs(store, snapshot)
    cycle = {
        "schema_version": CYCLE_SCHEMA,
        **{key: snapshot[key] for key in ("workspace_id","run_id","cycle_id",
                                          "sequence_no","session_date","occurred_at")},
        "execution_mode": "PAPER_RESEARCH_ONLY",
        "strategy_version": STRATEGY_VERSION,
        "config_sha256": digest(RULE_CONFIG), "code_sha256": RULE_SHA256,
        "market_source": market_source,
        "universe_symbols": list(snapshot["universe_symbols"]),
        "candidates": candidates,
    }
    scan = {
        "schema_version": SOURCE_SCHEMA,
        **{key: cycle[key] for key in ("workspace_id","run_id","cycle_id",
                                       "sequence_no","session_date","occurred_at",
                                       "strategy_version","config_sha256","code_sha256")},
        "scan_origin": ("SYNTHETIC_FIXTURE" if snapshot["source_origin"] == "SYNTHETIC_OFFLINE"
                        else "PRE_JOURNAL_SCANNER"),
        "market_source_sha256": digest(market_source),
        "universe_symbols": list(snapshot["universe_symbols"]),
        "evaluations": [
            {"symbol": c["symbol"], "decision": c["decision"], "reason": c["reason"],
             "observed_at": c["observed_at"], "features_sha256": digest(c["features"])}
            for c in candidates
        ],
    }
    source_ack = source.record_scan(scan)  # durable BEFORE F1 journal; never reverse
    journal_ack = journal.append_cycle(cycle)
    return {
        "paper_only": True, "broker_calls": 0,
        "source_status": source_ack["status"],
        "journal_status": journal_ack["status"],
        "candidate_count": len(candidates),
        "unmeasurable_count": sum(c["decision"] == "UNMEASURABLE" for c in candidates),
        "evidence_state": "AWAITING_EVIDENCE", "research_ready": False,
        "source_hash": source_ack["manifest_sha256"],
        "journal_payload_hash": journal_ack["payload_sha256"],
        "market_source": market_source,
    }


def _verify_local_market(store: MarketEvidenceStore, cycle: Mapping[str, Any]) -> None:
    scope = {"workspace_id":cycle["workspace_id"], "run_id":cycle["run_id"]}
    m = cycle["market_source"]
    if m["data_status"] != "PARTIAL":
        raise PaperCaptureIntegrityError("unsupported claim of source completeness")
    universe = store.load(**scope, kind="universe", digest_sha256=m["universe_ref"])
    if universe.get("symbols") != cycle["universe_symbols"] or universe.get("kind") != "universe":
        raise PaperCaptureIntegrityError("universe archive does not match journal")
    docs: dict[str, list[dict[str, Any]]] = {}
    context = ("workspace_id", "run_id", "cycle_id", "sequence_no", "session_date")
    for key in context:
        if universe.get(key) != cycle[key]:
            raise PaperCaptureIntegrityError("universe archive scope differs")
    for plural, singular, refname in (
        ("bars", "bar", "bars_ref"), ("quotes", "quote", "quotes_ref"),
    ):
        index = store.load(**scope, kind=plural+"_index", digest_sha256=m[refname])
        if index.get("kind") != plural or any(index.get(k) != cycle[k] for k in context):
            raise PaperCaptureIntegrityError("market index mismatch")
        if (index.get("asof_timestamp") != m["asof_timestamp"] or
                index.get("source_origin") != universe.get("source_origin") or
                index.get("provider") != universe.get("provider")):
            raise PaperCaptureIntegrityError("market index provider/time mismatch")
        if not isinstance(index.get("items"), list):
            raise PaperCaptureIntegrityError("market index lacks records")
        reconstructed = []
        for ref in index["items"]:
            if not isinstance(ref, dict) or set(ref) != {"symbol", "timestamp", "sha256"}:
                raise PaperCaptureIntegrityError("malformed raw market record reference")
            value = store.load(**scope, kind=singular, digest_sha256=ref["sha256"])
            if (value.get("schema_version") != RECORD_SCHEMA or value.get("kind") != singular or
                    value.get("provider") != index["provider"] or
                    value.get("source_origin") != index["source_origin"]):
                raise PaperCaptureIntegrityError("market record origin/kind mismatch")
            raw = value.get("record")
            ts = "end" if plural == "bars" else "observed_at"
            if not isinstance(raw, dict) or raw.get("symbol") != ref["symbol"] or raw.get(ts) != ref["timestamp"]:
                raise PaperCaptureIntegrityError("raw market record reference mismatches payload")
            reconstructed.append(raw)
        docs[plural] = reconstructed
    if (universe.get("asof_timestamp") != m["asof_timestamp"] or
            universe.get("provider") != m["feed"]):
        raise PaperCaptureIntegrityError("universe market provenance mismatch")
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        **{k: cycle[k] for k in context},
        "occurred_at": cycle["occurred_at"], "source_origin": universe["source_origin"],
        "provider": universe["provider"], "asof_timestamp": universe["asof_timestamp"],
        "universe_symbols": universe["symbols"],
        "bars": docs["bars"], "quotes": docs["quotes"],
    }
    # Unlike validate_snapshot, recorded replay does not depend on the
    # original scheduler object. Replay as-of checks are still required.
    mock_slot = {
        "schema_version": "anevum.paper-schedule.v1",
        **{k: cycle[k] for k in context},
        "expected_at": cycle["occurred_at"],
        "execution_mode": "PAPER_RESEARCH_ONLY", "planner_origin": "SYNTHETIC_PLAN",
    }
    validate_snapshot(snapshot, mock_slot)
    if _evaluate(snapshot) != cycle["candidates"]:
        raise PaperCaptureIntegrityError("candidate evidence differs from restored raw market objects")


def verify_paper_session(
    schedule: PaperScheduleLedger, source: SourceScanLedger, journal: DecisionJournal,
    store: MarketEvidenceStore, *, workspace_id: str, run_id: str,
    session_date: str,
) -> dict[str, Any]:
    """Local completeness test; never emits PASS or offhost/market attestation."""
    _validate_scope(workspace_id, run_id, session_date)
    issues: list[str] = []
    if len({schedule.path, Path(source.path).resolve(), Path(journal.path).resolve()}) != 3:
        raise PaperCaptureError("schedule, scanner and journal must be distinct databases")
    try:
        planned = schedule.read_session_verified(
            workspace_id=workspace_id, run_id=run_id, session_date=session_date)
        slots = planned["slots"]
    except (PaperScheduleError, PaperScheduleIntegrityError, sqlite3.DatabaseError,
            SourceContractError, ValueError, TypeError):
        slots = []
        issues.append("SCHEDULER_LEDGER_INTEGRITY_BLOCKED")
    if not slots:
        issues.append("SCHEDULER_NO_PLANNED_SLOTS")
    f3a = verify_scan_population(source, journal, workspace_id=workspace_id,
                                run_id=run_id, session_date=session_date,
                                scheduler_expected_cycles=len(slots))
    if f3a["evidence_state"] == "BLOCKED":
        issues.append("SCANNER_JOURNAL_RECONCILIATION_BLOCKED")
    try:
        exported = journal.export_session(workspace_id=workspace_id, run_id=run_id,
                                          session_date=session_date)
        cycles = exported["private_cycles"]
    except (RuntimeError, ValueError, sqlite3.DatabaseError):
        cycles = []
        issues.append("JOURNAL_SESSION_UNAVAILABLE")
    by_seq = {c["sequence_no"]: c for c in cycles}
    for slot in slots:
        row = by_seq.get(slot["sequence_no"])
        if row is None:
            issues.append(f"SCHEDULED_SCAN_NOT_CAPTURED:{slot['sequence_no']}")
        elif (row["cycle_id"] != slot["cycle_id"] or
              _utc(row["occurred_at"], "journal occurred_at") <
              _utc(slot["expected_at"], "schedule expected_at") or
              _utc(row["occurred_at"], "journal occurred_at") >
              _utc(slot["expected_at"], "schedule expected_at") + timedelta(minutes=5)):
            issues.append(f"SCHEDULED_SCAN_ID_OR_TIME_MISMATCH:{slot['sequence_no']}")
    source_restores = 0
    for row in cycles:
        try:
            _verify_local_market(store, row)
            source_restores += 1
        except (PaperCaptureError, PaperCaptureIntegrityError, ArchiveIntegrityError,
                ValueError, KeyError, TypeError):
            issues.append(f"MARKET_SOURCE_LOCAL_RESTORE_BLOCKED:{row['sequence_no']}")
    codes = sorted(set(issues))
    blocked = bool(codes)
    return {
        "schema_version": "anevum.paper-capture-audit.v1",
        "scope": {"workspace_id":workspace_id, "run_id":run_id,
                  "session_date":session_date},
        "planned_slots": len(slots), "journal_cycles": len(cycles),
        "market_cycles_locally_restored": source_restores,
        "locally_consistent": not blocked and bool(slots),
        "upstream_schedule_independently_attested": False,
        "provider_market_data_independently_attested": False,
        "raw_market_objects_restored_from_remote": False,
        "offhost_wal_ack_verified": False,
        "research_ready": False, "alpha_validated": False,
        "broker_write_authority": False, "broker_calls": 0,
        "f3a_evidence_state": f3a["evidence_state"],
        "f3a_issue_codes": f3a["issue_codes"],
        "issue_codes": codes[:MAX_ISSUES], "issues_total": len(codes),
        "evidence_state": "BLOCKED" if blocked else "AWAITING_EVIDENCE",
    }
