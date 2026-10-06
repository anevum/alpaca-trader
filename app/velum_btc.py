"""BTC adapter for the existing broker-isolated VELUM replay engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from .btc_discovery_contract import COSTS, DELAYS, candidate_strategy, fingerprint

WARMUP_HOURS = 35 * 24
MAX_RESEARCH_MISSING_HOURS = 1
GAP_POLICY_VERSION = "isolated_provider_gap_segment_reset_v1"


def stamp(row):
    parsed = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("hourly_corpus_timezone_required")
    return parsed.astimezone(timezone.utc)


def metrics(trades: list[dict[str, Any]], drawdown: float | None = None) -> dict[str, Any]:
    returns = [float(row["net_return"]) for row in trades]
    gain = sum(x for x in returns if x > 0)
    loss = -sum(x for x in returns if x < 0)
    equity = peak = 1.0
    dd = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        dd = max(dd, 1 - equity / peak)
    return {"trade_count": len(returns), "independent_days": len({row["entry_at"][:10] for row in trades}),
            "net_expectancy": sum(returns) / len(returns) if returns else 0,
            "profit_factor": gain / loss if loss else (1000.0 if gain else 0),
            "max_drawdown": max(dd, drawdown or 0), "net_return": equity - 1}


def corpus_coverage(rows: list[dict[str, Any]], start: datetime, end: datetime) -> dict[str, Any]:
    lower = start - timedelta(days=35)
    present = {stamp(row) for row in rows if lower <= stamp(row) < end}
    expected = int((end - lower).total_seconds() // 3600)
    missing = [lower + timedelta(hours=i) for i in range(expected) if lower + timedelta(hours=i) not in present]
    return {"expected_bars": expected, "received_bars": len(present), "missing_hours": len(missing),
            "first_missing_at": missing[0].isoformat() if missing else None,
            "last_missing_at": missing[-1].isoformat() if missing else None}


def _selected_hourly(rows: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    lower = start - timedelta(days=35)
    selected = []
    seen = set()
    for row in sorted(rows, key=stamp):
        t = stamp(row)
        if not lower <= t < end:
            continue
        if t in seen or t.minute or t.second or t.microsecond:
            raise ValueError("invalid_or_duplicate_hourly_corpus")
        seen.add(t)
        o, h, l, c = (Decimal(str(row[k])) for k in ("o", "h", "l", "c"))
        if not all(v.is_finite() and v > 0 for v in (o, h, l, c)) or h < max(o, c) or l > min(o, c):
            raise ValueError("invalid_hourly_ohlc")
        selected.append({"t": t.isoformat(), "o": str(o), "h": str(h), "l": str(l), "c": str(c)})
    return selected


def normalized_corpus(rows: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
    lower = start - timedelta(days=35)
    selected = _selected_hourly(rows, start, end)
    expected = int((end - lower).total_seconds() // 3600)
    # Strict helper retained for callers/tests that require fully contiguous data.
    if len(selected) != expected or not selected or stamp(selected[0]) != lower or stamp(selected[-1]) != end - timedelta(hours=1):
        quality = corpus_coverage(rows, start, end)
        raise ValueError(f"incomplete_hourly_corpus:missing={quality['missing_hours']},expected={expected},received={len(selected)},first={quality['first_missing_at']}")
    return selected


def research_corpus(rows: list[dict[str, Any]], start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[datetime]]:
    """Validate research bars while allowing one isolated provider outage.

    Missing prices are never synthesized. A bounded interior gap is carried into
    replay as an explicit segment boundary, which resets strategy warmup and
    prevents positions or delayed entries from crossing unobserved time.
    """
    lower = start - timedelta(days=35)
    selected = _selected_hourly(rows, start, end)
    quality = corpus_coverage(rows, start, end)
    if (not selected or stamp(selected[0]) != lower
            or stamp(selected[-1]) != end - timedelta(hours=1)
            or quality["missing_hours"] > MAX_RESEARCH_MISSING_HOURS):
        expected = int((end - lower).total_seconds() // 3600)
        raise ValueError(f"incomplete_hourly_corpus:missing={quality['missing_hours']},expected={expected},received={len(selected)},first={quality['first_missing_at']}")
    present = {stamp(row) for row in selected}
    expected = int((end - lower).total_seconds() // 3600)
    gaps = [lower + timedelta(hours=i) for i in range(expected)
            if lower + timedelta(hours=i) not in present]
    return selected, gaps


def _segments(corpus: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not corpus:
        return []
    segments: list[list[dict[str, Any]]] = [[corpus[0]]]
    for row in corpus[1:]:
        if stamp(row) == stamp(segments[-1][-1]) + timedelta(hours=1):
            segments[-1].append(row)
        else:
            segments.append([row])
    return segments


def run_direct(engine: Any, rows: list[dict[str, Any]], *, start: datetime, end: datetime,
               candidate: dict[str, Any], prepared_signals: dict[str, bool] | None = None) -> dict[str, Any]:
    strategy = candidate_strategy(candidate)
    corpus, gaps = research_corpus(rows, start, end)
    segments = _segments(corpus)
    gap_hours = [value.isoformat() for value in gaps]
    inputs = fingerprint({"bars": corpus, "provider_gap_hours": gap_hours, "gap_policy": GAP_POLICY_VERSION})

    scorable_times = {
        row["t"]
        for segment in segments
        for index, row in enumerate(segment)
        if index >= WARMUP_HOURS and stamp(row) >= start
    }
    if prepared_signals is None:
        signals: dict[str, bool] = {}
        for segment in segments:
            for index, row in enumerate(segment):
                now = stamp(row)
                if row["t"] not in scorable_times:
                    continue
                # The entry-open candle is never visible to the signal calculation.
                signals[row["t"]] = strategy.evaluate(
                    bars=segment[index - WARMUP_HOURS:index], confirmation_bars={}, symbol="BTC/USD",
                    has_position=False, order_notional=Decimal("1"), now=now).action == "buy"
    else:
        # Test/replay signal injection must obey the same post-gap warmup boundary.
        signals = {key: bool(value) for key, value in prepared_signals.items() if key in scorable_times}

    scenarios = {}
    for name, costs in COSTS.items():
        fee = Decimal(costs["fee_bps"]) / Decimal(10000)
        for delay in DELAYS:
            trades = []
            equity = peak = Decimal("1")
            max_dd = Decimal("0")
            discarded_gap_positions = 0
            discarded_gap_pending_entries = 0

            def fill(reference, side):
                return engine._fill(Decimal(reference), side, Decimal(costs["spread_bps"]), Decimal(costs["slippage_bps"]))

            for segment_index, segment in enumerate(segments):
                position = None
                pending = None

                def close(reference, at, reason):
                    nonlocal equity, position
                    exit_fill = fill(reference, "sell")
                    value = exit_fill * (1 - fee) / (position["fill"] * (1 + fee)) - 1
                    equity *= 1 + value
                    trades.append({"entry_at": position["at"].isoformat(), "exit_at": at.isoformat(),
                                   "entry_price": str(position["fill"]), "exit_price": str(exit_fill),
                                   "net_return": float(value), "exit_reason": reason})
                    position = None

                for index, row in enumerate(segment):
                    now = stamp(row)
                    if now < start:
                        continue
                    if pending is not None and index == pending and position is None:
                        entry_fill = fill(row["o"], "buy")
                        position = {"fill": entry_fill, "at": now, "equity": equity}
                        pending = None
                    if position:
                        stop = position["fill"] * (1 - strategy.hard_stop_pct)
                        target = position["fill"] * (1 + strategy.take_profit_pct)
                        # Mark to liquidation at the worst observed low; stops precede targets.
                        mark = position["equity"] * fill(row["l"], "sell") * (1 - fee) / (position["fill"] * (1 + fee))
                        max_dd = max(max_dd, 1 - mark / peak)
                        if Decimal(row["l"]) <= stop:
                            close(min(Decimal(row["o"]), stop), now + timedelta(hours=1), "stop")
                        elif Decimal(row["h"]) >= target:
                            close(target, now + timedelta(hours=1), "target")
                        elif (now + timedelta(hours=1) - position["at"]).total_seconds() / 60 >= strategy.max_hold_minutes:
                            close(row["c"], now + timedelta(hours=1), "time")
                        peak = max(peak, equity)
                        max_dd = max(max_dd, 1 - equity / peak)
                        continue
                    if pending is None and signals.get(row["t"]):
                        if delay == 0:
                            entry_fill = fill(row["o"], "buy")
                            position = {"fill": entry_fill, "at": now, "equity": equity}
                            # Process this same entry candle immediately and conservatively.
                            stop = entry_fill * (1 - strategy.hard_stop_pct)
                            target = entry_fill * (1 + strategy.take_profit_pct)
                            mark = equity * fill(row["l"], "sell") * (1 - fee) / (entry_fill * (1 + fee))
                            max_dd = max(max_dd, 1 - mark / peak)
                            if Decimal(row["l"]) <= stop:
                                close(min(Decimal(row["o"]), stop), now + timedelta(hours=1), "stop")
                            elif Decimal(row["h"]) >= target:
                                close(target, now + timedelta(hours=1), "target")
                            peak = max(peak, equity)
                            max_dd = max(max_dd, 1 - equity / peak)
                        else:
                            pending = index + delay

                if segment_index == len(segments) - 1:
                    if position:
                        close(segment[-1]["c"], end, "boundary_flatten")
                else:
                    # Unknown provider time is not a tradable candle. Never infer an
                    # exit or delayed fill across it; drop the incomplete episode.
                    discarded_gap_positions += int(position is not None)
                    discarded_gap_pending_entries += int(pending is not None)

            scenarios[f"{name}:delay_{delay}"] = {
                "costs": costs,
                "delay_bars": delay,
                "metrics": metrics(trades, float(max_dd)),
                "trades": trades,
                "gap_discarded_positions": discarded_gap_positions,
                "gap_discarded_pending_entries": discarded_gap_pending_entries,
            }
    return {"dataset_fingerprint": inputs, "candidate_fingerprint": candidate["fingerprint"],
            "start": start.isoformat(), "end": end.isoformat(), "scenarios": scenarios,
            "assumptions": {"signal": "completed hourly bars only", "execution": "next_open_plus_delay",
                            "fees": "per_side", "intrabar": "stop_first_gap_adverse", "warmup_scored": False,
                            "provider_gap_policy": GAP_POLICY_VERSION,
                            "provider_gap_hours": gap_hours,
                            "post_gap_warmup_hours": WARMUP_HOURS}}
