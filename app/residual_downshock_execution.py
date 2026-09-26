from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import os
import random
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import httpx

from app.residual_downshock_v2_1 import (
    apply_round_trip_cost,
    clustered_bootstrap_mean,
    data_quality_pass,
    expand_parameter_grid,
    fit_residual_model,
    load_manifest,
    median,
    residual,
    robust_z,
)


ET = ZoneInfo("America/New_York")
ALPACA_DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
ALPACA_CALENDAR_URL = "https://api.alpaca.markets/v2/calendar"


class ResearchExecutionError(RuntimeError):
    pass


def assert_persistence_preconditions(experiment: dict[str, Any], existing_result_count: int, manifest_checksum: str) -> None:
    """Fail closed before the one authorized canonical development persistence transaction."""
    expected = {
        "status": "planned",
        "stage_reached": "methodology_frozen",
        "survivor_state": "not_run",
        "sample_count": None,
    }
    mismatches = {key: (experiment.get(key), value) for key, value in expected.items() if experiment.get(key) != value}
    if experiment.get("manifest_hash") != manifest_checksum:
        mismatches["manifest_hash"] = (experiment.get("manifest_hash"), manifest_checksum)
    if existing_result_count != 0:
        mismatches["existing_result_count"] = (existing_result_count, 0)
    if mismatches:
        raise ResearchExecutionError(f"canonical persistence precondition mismatch: {mismatches}")


@dataclass(frozen=True, slots=True)
class RawBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def session(self) -> str:
        return self.timestamp.astimezone(ET).date().isoformat()

    @property
    def minute(self) -> int:
        local = self.timestamp.astimezone(ET)
        return local.hour * 60 + local.minute


def _request_json(client: httpx.Client, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
    last: Exception | None = None
    for attempt in range(6):
        try:
            response = client.get(url, params=params, headers=headers, timeout=60.0)
            if response.status_code == 429 or response.status_code >= 500:
                delay = float(response.headers.get("retry-after", min(2 ** attempt, 20)))
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            if attempt == 5:
                break
            time.sleep(min(2 ** attempt, 20))
    raise ResearchExecutionError(f"Alpaca request failed after retries: {last}")


def fetch_calendar(client: httpx.Client, headers: dict[str, str], start: str, end: str) -> list[str]:
    payload = _request_json(client, ALPACA_CALENDAR_URL, params={"start": start, "end": end}, headers=headers)
    sessions = [str(row["date"]) for row in payload]
    if not sessions or sessions != sorted(set(sessions)):
        raise ResearchExecutionError("Alpaca calendar is empty, duplicated, or unsorted")
    return sessions


def _iso_bound(day: str, *, next_day: bool = False) -> str:
    d = date.fromisoformat(day) + (timedelta(days=1) if next_day else timedelta())
    return datetime.combine(d, dt_time.min, tzinfo=ET).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_bars(
    client: httpx.Client,
    headers: dict[str, str],
    symbols: list[str],
    start: str,
    end: str,
    contract: dict[str, Any],
) -> tuple[dict[str, list[RawBar]], dict[str, bool], int]:
    output: dict[str, list[RawBar]] = {symbol: [] for symbol in symbols}
    pagination = {symbol: True for symbol in symbols}
    page_count = 0
    for offset in range(0, len(symbols), 8):
        group = symbols[offset : offset + 8]
        token: str | None = None
        group_pages = 0
        while True:
            params: dict[str, Any] = {
                "symbols": ",".join(group),
                "timeframe": contract["timeframe"],
                "start": _iso_bound(start),
                "end": _iso_bound(end, next_day=True),
                "limit": contract["page_limit"],
                "adjustment": contract["adjustment"],
                "feed": contract["feed"],
                "sort": contract["sort"],
            }
            if token:
                params["page_token"] = token
            payload = _request_json(client, ALPACA_DATA_URL, params=params, headers=headers)
            group_pages += 1
            page_count += 1
            if group_pages > 10000:
                for symbol in group:
                    pagination[symbol] = False
                raise ResearchExecutionError("historical pagination safety limit exhausted")
            for symbol, rows in (payload.get("bars") or {}).items():
                if symbol not in output:
                    raise ResearchExecutionError(f"unexpected symbol returned by Alpaca: {symbol}")
                for row in rows:
                    stamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
                    local = stamp.astimezone(ET)
                    minute = local.hour * 60 + local.minute
                    if 570 <= minute < 960:
                        output[symbol].append(RawBar(stamp, float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"]), float(row.get("v", 0))))
            token = payload.get("next_page_token")
            if not token:
                break
    for symbol in output:
        output[symbol].sort(key=lambda row: row.timestamp)
    return output, pagination, page_count


def split_raw(bars: dict[str, list[RawBar]]) -> dict[str, dict[str, dict[int, RawBar]]]:
    out: dict[str, dict[str, dict[int, RawBar]]] = {}
    for symbol, rows in bars.items():
        by_session: dict[str, dict[int, RawBar]] = defaultdict(dict)
        for row in rows:
            if row.minute in by_session[row.session]:
                raise ResearchExecutionError(f"duplicate raw minute {symbol} {row.session} {row.minute}")
            by_session[row.session][row.minute] = row
        out[symbol] = dict(by_session)
    return out


def aggregate_session(raw: dict[int, RawBar]) -> dict[int, dict[str, float]]:
    buckets: dict[int, list[RawBar]] = defaultdict(list)
    for minute, bar in raw.items():
        if 570 <= minute < 960:
            buckets[570 + ((minute - 570) // 5 + 1) * 5].append(bar)
    out: dict[int, dict[str, float]] = {}
    for end, rows in sorted(buckets.items()):
        rows.sort(key=lambda row: row.minute)
        out[end] = {
            "open": rows[0].open,
            "high": max(row.high for row in rows),
            "low": min(row.low for row in rows),
            "close": rows[-1].close,
            "volume": sum(row.volume for row in rows),
            "raw_minute_count": len(rows),
        }
    return out


def build_panels(raw: dict[str, dict[str, dict[int, RawBar]]]) -> dict[str, dict[str, dict[int, dict[str, float]]]]:
    return {symbol: {session: aggregate_session(rows) for session, rows in sessions.items()} for symbol, sessions in raw.items()}


def synchronized_session_returns(
    stock: dict[int, dict[str, float]],
    market: dict[int, dict[str, float]],
    sector: dict[int, dict[str, float]],
) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    for end in sorted(set(stock) & set(market) & set(sector)):
        prior = end - 5
        if prior not in stock or prior not in market or prior not in sector:
            continue
        out[end] = {
            "t": float(end),
            "stock_return": stock[end]["close"] / stock[prior]["close"] - 1.0,
            "market_return": market[end]["close"] / market[prior]["close"] - 1.0,
            "sector_return": sector[end]["close"] / sector[prior]["close"] - 1.0,
        }
    return out


def time_bucket(minute: int) -> str | None:
    if 580 <= minute < 660:
        return "early"
    if 660 <= minute < 840:
        return "midday"
    if 840 <= minute <= 900:
        return "late"
    return None


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    location = (len(ordered) - 1) * q
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _profit_factor(values: list[float]) -> float | None:
    gains = sum(value for value in values if value > 0)
    losses = -sum(value for value in values if value < 0)
    if losses == 0:
        return None if gains == 0 else math.inf
    return gains / losses


def _json_number(value: float | None) -> float | str | None:
    if value is None:
        return None
    if math.isinf(value):
        return "Infinity"
    return value


def _cost(event: dict[str, Any], scenario: dict[str, Any], horizon: int = 30) -> float | None:
    exit_price = event["exit_prices"].get(str(horizon))
    if exit_price is None:
        return None
    return apply_round_trip_cost(
        event["entry_price"],
        exit_price,
        full_spread_bps=float(scenario["full_spread_bps"]),
        slippage_bps_per_side=float(scenario["slippage_bps_per_side"]),
    )


def _cooldown(events: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    last: dict[str, int] = {}
    for event in sorted(events, key=lambda e: (e["absolute_minute"], e["symbol"])):
        symbol = event["symbol"]
        point = event["absolute_minute"]
        if symbol in last and point - last[symbol] < minutes:
            continue
        kept.append(event)
        last[symbol] = point
    return kept


def _observation_outcomes(
    symbol: str,
    session: str,
    minute: int,
    raw: dict[str, dict[str, dict[int, RawBar]]],
    panels: dict[str, dict[str, dict[int, dict[str, float]]]],
    synced: dict[int, dict[str, float]],
    model: Any,
    horizons: list[int],
) -> dict[str, Any] | None:
    entry_bar = raw.get(symbol, {}).get(session, {}).get(minute)
    if entry_bar is None:
        return None
    panel = panels[symbol][session]
    exit_prices: dict[str, float | None] = {}
    stock_returns: dict[str, float | None] = {}
    forward_residuals: dict[str, float | None] = {}
    for horizon in horizons:
        endpoint = minute + horizon
        exit_price = panel.get(endpoint, {}).get("close")
        exit_prices[str(horizon)] = exit_price
        stock_returns[str(horizon)] = exit_price / entry_bar.open - 1.0 if exit_price is not None else None
        future = []
        for future_end in range(minute + 5, endpoint + 1, 5):
            if future_end not in synced:
                future = []
                break
            future.append(residual(synced[future_end], model))
        forward_residuals[str(horizon)] = sum(future) if len(future) == horizon // 5 else None
    if stock_returns.get("30") is None or forward_residuals.get("30") is None:
        return None
    raw_path = [bar for m, bar in raw[symbol][session].items() if minute <= m < minute + 30]
    mfe = max((bar.high for bar in raw_path), default=entry_bar.open) / entry_bar.open - 1.0
    mae = min((bar.low for bar in raw_path), default=entry_bar.open) / entry_bar.open - 1.0
    return {
        "entry_price": entry_bar.open,
        "exit_prices": exit_prices,
        "stock_returns": stock_returns,
        "forward_residual_returns": forward_residuals,
        "mfe_30m": mfe,
        "mae_30m": mae,
    }


def build_observations(
    manifest: dict[str, Any],
    raw: dict[str, dict[str, dict[int, RawBar]]],
    panels: dict[str, dict[str, dict[int, dict[str, float]]]],
    calendar: list[str],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, float]]]:
    development = [window for window in manifest["data"]["windows"] if window["role"] == "development"]
    window_for_session = {session: window["id"] for window in development for session in window["expected_sessions"]}
    dev_sessions = sorted(window_for_session)
    session_index = {session: idx for idx, session in enumerate(calendar)}
    mapping = manifest["universe"]["sector_mapping"]
    market_symbol = manifest["universe"]["market_benchmark"]
    horizons = [manifest["horizons"]["primary"], *manifest["horizons"]["secondary"]]
    synced_by_symbol: dict[str, dict[str, dict[int, dict[str, float]]]] = defaultdict(dict)
    for symbol in manifest["universe"]["tradable_symbols"]:
        sector_symbol = mapping[symbol]
        for session in calendar:
            synced_by_symbol[symbol][session] = synchronized_session_returns(
                panels.get(symbol, {}).get(session, {}),
                panels.get(market_symbol, {}).get(session, {}),
                panels.get(sector_symbol, {}).get(session, {}),
            )

    observations: list[dict[str, Any]] = []
    model_ratios: dict[str, dict[str, float]] = defaultdict(dict)
    for symbol in manifest["universe"]["tradable_symbols"]:
        sector_symbol = mapping[symbol]
        for session in dev_sessions:
            idx = session_index.get(session)
            if idx is None:
                model_ratios[symbol][session] = 0.0
                continue
            prior_sessions = calendar[max(0, idx - manifest["residual_model"]["training_sessions"]):idx]
            training: list[dict[str, float]] = []
            distinct = 0
            for prior in prior_sessions:
                rows = list(synced_by_symbol[symbol].get(prior, {}).values())
                if rows:
                    distinct += 1
                    training.extend(rows)
            if distinct < manifest["residual_model"]["minimum_distinct_training_sessions"] or len(training) < manifest["residual_model"]["minimum_synchronized_training_returns"]:
                model_ratios[symbol][session] = 0.0
                continue
            model = fit_residual_model(training, variance_floor=manifest["residual_model"]["factor_variance_floor"])
            if model is None:
                model_ratios[symbol][session] = 0.0
                continue
            history: dict[str, list[float]] = defaultdict(list)
            for prior in prior_sessions:
                for end, row in synced_by_symbol[symbol].get(prior, {}).items():
                    bucket = time_bucket(end)
                    if bucket:
                        history[bucket].append(residual(row, model))
            usable_buckets = {bucket for bucket, values in history.items() if len(values) >= manifest["shock_standardization"]["minimum_bucket_observations"]}
            eligible_total = 65
            eligible_model = 0
            stock_panel = panels.get(symbol, {}).get(session, {})
            market_panel = panels.get(market_symbol, {}).get(session, {})
            synced = synced_by_symbol[symbol].get(session, {})
            market_open = market_panel.get(575, {}).get("open")
            for end in range(580, 901, 5):
                bucket = time_bucket(end)
                if bucket not in usable_buckets or end not in synced:
                    continue
                eligible_model += 1
                row = synced[end]
                resid = residual(row, model)
                z = robust_z(resid, history[bucket], min_scale=manifest["shock_standardization"]["minimum_scale"])
                if z is None:
                    continue
                outcome = _observation_outcomes(symbol, session, end, raw, panels, synced, model, sorted(set(horizons)))
                observations.append({
                    "symbol": symbol,
                    "sector": sector_symbol,
                    "session": session,
                    "window": window_for_session[session],
                    "minute": end,
                    "absolute_minute": date.fromisoformat(session).toordinal() * 1440 + end,
                    "time_bucket": bucket,
                    "residual": resid,
                    "z": z,
                    "market_return": row["market_return"],
                    "sector_return": row["sector_return"],
                    "spy_open_to_t_sign": None if market_open is None or end not in market_panel else (1 if market_panel[end]["close"] > market_open else -1 if market_panel[end]["close"] < market_open else 0),
                    "outcome": outcome,
                })
            model_ratios[symbol][session] = eligible_model / eligible_total
    return observations, model_ratios


def corpus_diagnostics(
    manifest: dict[str, Any],
    raw: dict[str, dict[str, dict[int, RawBar]]],
    panels: dict[str, dict[str, dict[int, dict[str, float]]]],
    pagination: dict[str, bool],
    model_ratios: dict[str, dict[str, float]],
) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    mapping = manifest["universe"]["sector_mapping"]
    market_symbol = manifest["universe"]["market_benchmark"]
    for window in [w for w in manifest["data"]["windows"] if w["role"] == "development"]:
        sessions = window["expected_sessions"]
        for symbol in manifest["universe"]["tradable_symbols"]:
            sector_symbol = mapping[symbol]
            represented = [session for session in sessions if raw.get(symbol, {}).get(session)]
            all_bars = [bar for session in sessions for bar in raw.get(symbol, {}).get(session, {}).values()]
            five_count = sum(len(panels.get(symbol, {}).get(session, {})) for session in sessions)
            partial = [
                session for session in represented
                if len(panels.get(symbol, {}).get(session, {})) < 78
                or 575 not in panels.get(symbol, {}).get(session, {})
                or 960 not in panels.get(symbol, {}).get(session, {})
            ]
            sync_count = 0
            for session in sessions:
                stock = panels.get(symbol, {}).get(session, {})
                market = panels.get(market_symbol, {}).get(session, {})
                sector = panels.get(sector_symbol, {}).get(session, {})
                for end in range(580, 901, 5):
                    if all(t in stock and t in market and t in sector for t in (end - 5, end)):
                        sync_count += 1
            market_count = sum(len(panels.get(market_symbol, {}).get(session, {})) for session in sessions)
            sector_count = sum(len(panels.get(sector_symbol, {}).get(session, {})) for session in sessions)
            report = {
                "window": window["id"],
                "symbol": symbol,
                "sector_benchmark": sector_symbol,
                "expected_sessions": sessions,
                "represented_sessions": represented,
                "expected_session_count": len(sessions),
                "represented_session_count": len(represented),
                "expected_session_representation": len(represented) / len(sessions),
                "first_timestamp": min((bar.timestamp.isoformat() for bar in all_bars), default=None),
                "last_timestamp": max((bar.timestamp.isoformat() for bar in all_bars), default=None),
                "raw_one_minute_bar_count": len(all_bars),
                "five_minute_observation_count": five_count,
                "missing_sessions": sorted(set(sessions) - set(represented)),
                "partial_sessions": partial,
                "pagination_complete": bool(pagination.get(symbol) and pagination.get(market_symbol) and pagination.get(sector_symbol)),
                "synchronization_completeness": sync_count / (len(sessions) * 65),
                "market_benchmark_completeness": market_count / (len(sessions) * 78),
                "sector_benchmark_completeness": sector_count / (len(sessions) * 78),
                "model_availability_ratio": mean(model_ratios[symbol].get(session, 0.0) for session in sessions),
            }
            report["gate_pass"] = data_quality_pass(report, manifest)
            rows.append(report)
    return rows, all(row["gate_pass"] for row in rows)


def sign_flip_p_value(events: list[dict[str, Any]], *, seed: int, resamples: int) -> float | None:
    if not events:
        return None
    by_day: dict[str, list[float]] = defaultdict(list)
    for event in events:
        by_day[event["session"]].append(event["stress_return"])
    days = sorted(by_day)
    observed = mean(value for day in days for value in by_day[day])
    rng = random.Random(seed)
    extreme = 0
    for _ in range(resamples):
        values = []
        for day in days:
            sign = 1 if rng.getrandbits(1) else -1
            values.extend(sign * value for value in by_day[day])
        if mean(values) >= observed:
            extreme += 1
    return (extreme + 1) / (resamples + 1)


def _event_copy(observation: dict[str, Any], scenarios: dict[str, dict[str, Any]]) -> dict[str, Any]:
    outcome = observation["outcome"]
    event = {key: observation[key] for key in ("symbol", "sector", "session", "window", "minute", "absolute_minute", "time_bucket", "residual", "z")}
    event.update(outcome)
    event["cost_returns"] = {name: _cost(event, scenario) for name, scenario in scenarios.items()}
    event["stress_return"] = event["cost_returns"]["STRESS"]
    return event


def _concentration(events: list[dict[str, Any]], field: str) -> tuple[float, str | None, dict[str, int]]:
    counts = Counter(str(event[field]) for event in events)
    if not counts:
        return 0.0, None, {}
    best_count = max(counts.values())
    best = sorted(key for key, value in counts.items() if value == best_count)[0]
    return best_count / len(events), best, dict(sorted(counts.items()))


def evaluate_configuration(
    manifest: dict[str, Any],
    configuration: dict[str, Any],
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    scenarios = {row["name"]: row for row in manifest["costs"]["scenarios"]}
    floor = configuration["absolute_residual_floor"]
    threshold = configuration["residual_z_threshold"]
    down_raw = [o for o in observations if o["residual"] <= -floor and o["z"] <= -threshold]
    positive_raw = [o for o in observations if o["residual"] >= floor and o["z"] >= threshold]
    down_unavailable = sum(o["outcome"] is None for o in down_raw)
    positive_unavailable = sum(o["outcome"] is None for o in positive_raw)
    down = _cooldown([_event_copy(o, scenarios) for o in down_raw if o["outcome"] is not None], manifest["entry"]["cooldown_minutes"])
    positive = _cooldown([_event_copy(o, scenarios) for o in positive_raw if o["outcome"] is not None], manifest["entry"]["cooldown_minutes"])

    neutral = [o for o in observations if o["outcome"] is not None and abs(o["z"]) <= 0.5 and abs(o["residual"]) < 0.001]
    shocks_by_key: dict[tuple[str, str], list[int]] = defaultdict(list)
    for event in down_raw:
        shocks_by_key[(event["symbol"], event["session"])].append(event["minute"])
    neutral_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in neutral:
        if all(abs(candidate["minute"] - shock) >= 60 for shock in shocks_by_key[(candidate["symbol"], candidate["session"])]):
            neutral_by_key[(candidate["symbol"], candidate["session"], candidate["time_bucket"])].append(candidate)
    matched: list[dict[str, Any]] = []
    used: set[tuple[str, str, int]] = set()
    control_seed = manifest["negative_controls"]["seed"]
    for event in down:
        key = (event["symbol"], event["session"], event["time_bucket"])
        candidates = [o for o in neutral_by_key.get(key, []) if (o["symbol"], o["session"], o["minute"]) not in used]
        if not candidates:
            continue
        event_key = f'{event["symbol"]}|{event["session"]}|{event["minute"]}|{configuration["configuration_id"]}'
        choice = min(candidates, key=lambda o: hashlib.sha256(f'{control_seed}|{event_key}|{o["minute"]}'.encode()).hexdigest())
        used.add((choice["symbol"], choice["session"], choice["minute"]))
        matched.append(_event_copy(choice, scenarios))

    stress = [event["stress_return"] for event in down]
    by_window: dict[str, list[float]] = defaultdict(list)
    for event in down:
        by_window[event["window"]].append(event["stress_return"])
    windows = [w["id"] for w in manifest["data"]["windows"] if w["role"] == "development"]
    window_metrics = {
        window: {"event_count": len(by_window[window]), "stress_expectancy": mean(by_window[window]) if by_window[window] else None}
        for window in windows
    }
    bootstrap_events = [{"session": event["session"], "value": event["stress_return"]} for event in down]
    boot = clustered_bootstrap_mean(
        bootstrap_events,
        seed=manifest["uncertainty"]["bootstrap_seed"],
        resamples=manifest["uncertainty"]["bootstrap_resamples"],
    )
    bootstrap_lower = quantile(boot, 0.05)
    p_value = sign_flip_p_value(
        down,
        seed=manifest["uncertainty"]["multiple_testing"]["seed"],
        resamples=manifest["uncertainty"]["multiple_testing"]["resamples"],
    )
    symbol_share, _, by_symbol = _concentration(down, "symbol")
    day_share, _, by_day = _concentration(down, "session")
    sector_share, _, by_sector = _concentration(down, "sector")
    _, best_symbol, _ = _concentration(down, "symbol")
    _, best_sector, _ = _concentration(down, "sector")
    if down:
        sums_symbol = {symbol: sum(e["stress_return"] for e in down if e["symbol"] == symbol) for symbol in by_symbol}
        best_sum = max(sums_symbol.values())
        best_symbol = sorted(symbol for symbol, value in sums_symbol.items() if value == best_sum)[0]
        sums_sector = {sector: sum(e["stress_return"] for e in down if e["sector"] == sector) for sector in by_sector}
        best_sector_sum = max(sums_sector.values())
        best_sector = sorted(sector for sector, value in sums_sector.items() if value == best_sector_sum)[0]
    without_symbol = [e["stress_return"] for e in down if e["symbol"] != best_symbol]
    without_sector = [e["stress_return"] for e in down if e["sector"] != best_sector]
    matched_returns = [event["stress_return"] for event in matched]
    positive_returns = [event["stress_return"] for event in positive]
    negative = [value for value in stress if value < 0]
    positive_values = [value for value in stress if value > 0]
    cvar_count = max(1, math.ceil(len(stress) * 0.05)) if stress else 0
    horizon_metrics: dict[str, Any] = {}
    for horizon in [5, 15, 30, 60]:
        gross = [e["stock_returns"][str(horizon)] for e in down if e["stock_returns"].get(str(horizon)) is not None]
        horizon_metrics[str(horizon)] = {"available": len(gross), "gross_expectancy": mean(gross) if gross else None}
        for name, scenario in scenarios.items():
            values = [_cost(e, scenario, horizon) for e in down]
            values = [v for v in values if v is not None]
            horizon_metrics[str(horizon)][f"{name.lower()}_expectancy"] = mean(values) if values else None

    metrics: dict[str, Any] = {
        "event_count": len(down),
        "unavailable_qualified_event_count": down_unavailable,
        "distinct_event_sessions": len(set(e["session"] for e in down)),
        "events_by_window": {window: len(by_window[window]) for window in windows},
        "events_by_symbol": by_symbol,
        "events_by_sector": by_sector,
        "events_by_day": by_day,
        "events_by_time_of_day": dict(sorted(Counter(e["time_bucket"] for e in down).items())),
        "gross_30m_expectancy": mean(e["stock_returns"]["30"] for e in down) if down else None,
        "gross_residual_30m_mean": mean(e["forward_residual_returns"]["30"] for e in down) if down else None,
        "base_expectancy": mean(e["cost_returns"]["BASE"] for e in down) if down else None,
        "normal_expectancy": mean(e["cost_returns"]["NORMAL"] for e in down) if down else None,
        "stress_expectancy": mean(stress) if stress else None,
        "stress_profit_factor": _json_number(_profit_factor(stress)),
        "win_rate": sum(value > 0 for value in stress) / len(stress) if stress else None,
        "average_positive": mean(positive_values) if positive_values else None,
        "average_negative": mean(negative) if negative else None,
        "median": median(stress) if stress else None,
        "minimum": min(stress) if stress else None,
        "p05": quantile(stress, 0.05),
        "cvar_05": mean(sorted(stress)[:cvar_count]) if stress else None,
        "mfe_30m_mean": mean(e["mfe_30m"] for e in down) if down else None,
        "mae_30m_mean": mean(e["mae_30m"] for e in down) if down else None,
        "window_metrics": window_metrics,
        "worst_window_stress_expectancy": min((v["stress_expectancy"] for v in window_metrics.values() if v["stress_expectancy"] is not None), default=None),
        "positive_stress_windows": sum(v["stress_expectancy"] is not None and v["stress_expectancy"] > 0 for v in window_metrics.values()),
        "windows_meeting_event_floor": sum(v["event_count"] >= manifest["development_gates"]["minimum_events_per_development_window"] for v in window_metrics.values()),
        "bootstrap_lower_95": bootstrap_lower,
        "sign_flip_one_sided_p": p_value,
        "bonferroni_alpha": manifest["uncertainty"]["multiple_testing"]["per_configuration_alpha"],
        "max_symbol_concentration": symbol_share,
        "max_day_concentration": day_share,
        "max_sector_concentration": sector_share,
        "best_symbol_by_stress_sum": best_symbol,
        "leave_best_symbol_out_stress_expectancy": mean(without_symbol) if without_symbol else None,
        "best_sector_by_stress_sum": best_sector,
        "leave_best_sector_out_stress_expectancy": mean(without_sector) if without_sector else None,
        "matched_control_count": len(matched),
        "matched_control_coverage": len(matched) / len(down) if down else 0.0,
        "matched_control_stress_expectancy": mean(matched_returns) if matched_returns else None,
        "matched_control_uplift": mean(stress) - mean(matched_returns) if stress and matched_returns else None,
        "positive_shock_control_count": len(positive),
        "positive_shock_unavailable_count": positive_unavailable,
        "positive_shock_control_stress_expectancy": mean(positive_returns) if positive_returns else None,
        "positive_shock_control_uplift": mean(stress) - mean(positive_returns) if stress and positive_returns else None,
        "secondary_horizons": horizon_metrics,
    }
    g = manifest["development_gates"]
    pf = metrics["stress_profit_factor"]
    pf_numeric = math.inf if pf == "Infinity" else pf
    checks = {
        "minimum_events": metrics["event_count"] >= g["minimum_events"],
        "minimum_distinct_event_sessions": metrics["distinct_event_sessions"] >= g["minimum_distinct_event_sessions"],
        "minimum_development_windows_meeting_event_floor": metrics["windows_meeting_event_floor"] >= g["minimum_development_windows_meeting_event_floor"],
        "minimum_positive_STRESS_expectancy_windows": metrics["positive_stress_windows"] >= g["minimum_positive_STRESS_expectancy_windows"],
        "aggregate_STRESS_expectancy_must_be_positive": metrics["stress_expectancy"] is not None and metrics["stress_expectancy"] > 0,
        "minimum_STRESS_profit_factor": pf_numeric is not None and pf_numeric >= g["minimum_STRESS_profit_factor"],
        "minimum_worst_window_STRESS_expectancy": metrics["worst_window_stress_expectancy"] is not None and metrics["worst_window_stress_expectancy"] >= g["minimum_worst_window_STRESS_expectancy"],
        "STRESS_day_cluster_bootstrap_95pct_lower_bound_must_be_positive": metrics["bootstrap_lower_95"] is not None and metrics["bootstrap_lower_95"] > 0,
        "bonferroni_adjusted_one_sided_p_must_pass": metrics["sign_flip_one_sided_p"] is not None and metrics["sign_flip_one_sided_p"] <= g["per_configuration_alpha"],
        "maximum_symbol_event_share": metrics["max_symbol_concentration"] <= g["maximum_symbol_event_share"],
        "maximum_day_event_share": metrics["max_day_concentration"] <= g["maximum_day_event_share"],
        "maximum_sector_event_share": metrics["max_sector_concentration"] <= g["maximum_sector_event_share"],
        "matched_nonshock_uplift_must_be_positive": metrics["matched_control_uplift"] is not None and metrics["matched_control_uplift"] > 0,
        "minimum_matched_control_coverage": metrics["matched_control_coverage"] >= g["minimum_matched_control_coverage"],
        "positive_shock_control_uplift_must_be_positive": metrics["positive_shock_control_uplift"] is not None and metrics["positive_shock_control_uplift"] > 0,
        "minimum_positive_shock_control_events": metrics["positive_shock_control_count"] >= g["minimum_positive_shock_control_events"],
        "leave_best_symbol_out_STRESS_expectancy_must_be_positive": metrics["leave_best_symbol_out_stress_expectancy"] is not None and metrics["leave_best_symbol_out_stress_expectancy"] > 0,
        "leave_best_sector_out_STRESS_expectancy_must_be_positive": metrics["leave_best_sector_out_stress_expectancy"] is not None and metrics["leave_best_sector_out_stress_expectancy"] > 0,
        "gross_residual_30m_mean_must_be_positive": metrics["gross_residual_30m_mean"] is not None and metrics["gross_residual_30m_mean"] > 0,
        "minimum_STRESS_cvar_5pct": metrics["cvar_05"] is not None and metrics["cvar_05"] >= g["minimum_STRESS_cvar_5pct"],
        "minimum_single_event_STRESS_return": metrics["minimum"] is not None and metrics["minimum"] >= g["minimum_single_event_STRESS_return"],
    }
    return {
        "configuration_id": configuration["configuration_id"],
        "parameters": configuration,
        "metrics": metrics,
        "gate_results": checks,
        "failed_gates": sorted(name for name, passed in checks.items() if not passed),
        "verdict": "PASS" if all(checks.values()) else "REJECT",
    }


def select_survivor(manifest: dict[str, Any], results: list[dict[str, Any]]) -> str | None:
    survivors = [result for result in results if result["verdict"] == "PASS"]
    if not survivors:
        return None
    survivors.sort(key=lambda r: (
        -(r["metrics"]["bootstrap_lower_95"] or -math.inf),
        -(r["metrics"]["worst_window_stress_expectancy"] or -math.inf),
        -(r["metrics"]["stress_expectancy"] or -math.inf),
        -r["metrics"]["event_count"],
        r["configuration_id"],
    ))
    return survivors[0]["configuration_id"]


def run_development(manifest_path: str | Path, *, source_commit: str) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if source_commit == "":
        raise ResearchExecutionError("source commit is required")
    if any(window["access"] != "development" for window in manifest["data"]["windows"] if window["role"] == "development"):
        raise ResearchExecutionError("development window access mismatch")
    if any(window["access"] != "locked" for window in manifest["data"]["windows"] if window["role"] != "development"):
        raise ResearchExecutionError("later-stage window is not locked")
    key = os.environ.get("ALPACA_API_KEY", "")
    secret = os.environ.get("ALPACA_API_SECRET", "")
    if not key or not secret:
        raise ResearchExecutionError("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    development = [w for w in manifest["data"]["windows"] if w["role"] == "development"]
    first_dev = development[0]["start"]
    last_dev = development[-1]["end"]
    with httpx.Client() as client:
        calendar = fetch_calendar(client, headers, "2025-11-01", last_dev)
        first_index = calendar.index(first_dev)
        training_count = manifest["residual_model"]["training_sessions"]
        if first_index < training_count:
            raise ResearchExecutionError("calendar does not contain enough prior training sessions")
        fetch_start = calendar[first_index - training_count]
        allowed_sessions = set(calendar[first_index - training_count : calendar.index(last_dev) + 1])
        frozen_dev_sessions = {session for window in development for session in window["expected_sessions"]}
        if not frozen_dev_sessions.issubset(allowed_sessions):
            raise ResearchExecutionError("Alpaca calendar conflicts with frozen development sessions")
        symbols = sorted(set(manifest["universe"]["tradable_symbols"] + [manifest["universe"]["market_benchmark"]] + manifest["universe"]["sector_benchmarks"]))
        bars, pagination, pages = fetch_bars(client, headers, symbols, fetch_start, last_dev, manifest["data"]["historical_request_contract"])
    raw = split_raw(bars)
    unexpected = sorted({session for sessions in raw.values() for session in sessions if session not in allowed_sessions})
    if unexpected:
        raise ResearchExecutionError(f"data provider returned out-of-scope sessions: {unexpected}")
    panels = build_panels(raw)
    observations, model_ratios = build_observations(manifest, raw, panels, calendar)
    coverage, corpus_pass = corpus_diagnostics(manifest, raw, panels, pagination, model_ratios)
    base_report: dict[str, Any] = {
        "schema_version": "rhen-rdr21-development-report-v1",
        "experiment_key": manifest["experiment"]["experiment_key"],
        "experiment_id": manifest["experiment"]["canonical_experiment_id"],
        "manifest_checksum": manifest["freeze"]["manifest_checksum_sha256"],
        "code_commit": source_commit,
        "stage": "development",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": {"provider": "Alpaca", "feed": "iex", "raw_interval": "1Min", "adjustment": "raw", "fetch_start": fetch_start, "fetch_end": last_dev, "page_count": pages},
        "access_audit": {"development_opened": True, "validation_opened": False, "holdout_opened": False, "quarantine_accessed": False, "latest_performance_date_accessed": last_dev},
        "corpus_integrity": {"verdict": "PASS" if corpus_pass else "FAIL", "coverage": coverage},
        "facts": {"development_windows": [w["id"] for w in development], "tradable_symbol_count": len(manifest["universe"]["tradable_symbols"]), "configuration_count": len(expand_parameter_grid(manifest)), "observation_count": len(observations)},
        "limitations": [manifest["universe"]["survivorship_bias_note"], "Alpaca IEX sparse bins were not forward-filled or interpolated."],
    }
    if not corpus_pass:
        base_report.update({"configurations": [], "selected_configuration_id": None, "verdict": "FAIL", "decision": "DEVELOPMENT CORPUS FAIL; performance evaluation not run", "validation_eligible": False})
        return base_report
    results = [evaluate_configuration(manifest, configuration, observations) for configuration in expand_parameter_grid(manifest)]
    survivor = select_survivor(manifest, results)
    total_events = sum(result["metrics"]["event_count"] for result in results)
    unique_events = len({
        (observation["symbol"], observation["session"], observation["minute"])
        for observation in observations
        if observation["outcome"] is not None
        and any(
            observation["residual"] <= -configuration["absolute_residual_floor"]
            and observation["z"] <= -configuration["residual_z_threshold"]
            for configuration in expand_parameter_grid(manifest)
        )
    })
    base_report.update({
        "configurations": results,
        "total_generated_events_across_configurations": total_events,
        "unique_qualified_observations_before_configuration_cooldowns": unique_events,
        "selected_configuration_id": survivor,
        "verdict": "PASS" if survivor else "FAIL",
        "decision": "DEVELOPMENT PASS; validation eligible but not opened" if survivor else "DEVELOPMENT FAIL; all configurations irreversibly rejected under v2.1",
        "validation_eligible": bool(survivor),
    })
    return base_report


def emit_report_chunks(report: dict[str, Any], size: int = 8000) -> None:
    raw = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.b64encode(gzip.compress(raw, compresslevel=9)).decode()
    chunks = [encoded[i : i + size] for i in range(0, len(encoded), size)]
    digest = hashlib.sha256(raw).hexdigest()
    print(f"RDR21_REPORT_META {len(chunks)} {digest} {len(raw)}", flush=True)
    for index, chunk in enumerate(chunks, 1):
        print(f"RDR21_REPORT_CHUNK {index}/{len(chunks)} {chunk}", flush=True)


def human_report(report: dict[str, Any]) -> str:
    lines = ["# Residual Downshock Rebound v2.1 — Development Report", "", f"Generated: {report['generated_at']}", "", "## Facts", "", f"- Manifest checksum: `{report['manifest_checksum']}`", f"- Code commit: `{report['code_commit']}`", f"- Corpus integrity: **{report['corpus_integrity']['verdict']}**", f"- Development decision: **{report['verdict']}**", "- Validation was not run; holdout was not opened; quarantine was not accessed.", "", "## Gate results", ""]
    if not report.get("configurations"):
        lines.append("Performance evaluation stopped at the corpus-integrity gate.")
    else:
        lines.extend(["| Configuration | Events | STRESS expectancy | Profit factor | Worst window | Bootstrap lower 95% | Decision |", "|---|---:|---:|---:|---:|---:|---|"])
        for result in report["configurations"]:
            metrics = result["metrics"]
            pct = lambda value: "n/a" if value is None else f"{value:.6%}"
            lines.append(f"| {result['configuration_id']} | {metrics['event_count']} | {pct(metrics['stress_expectancy'])} | {metrics['stress_profit_factor']} | {pct(metrics['worst_window_stress_expectancy'])} | {pct(metrics['bootstrap_lower_95'])} | {result['verdict']} |")
    lines.extend(["", "## Robustness results", "", "Leave-best-symbol and leave-best-sector results are recorded per configuration in the machine-readable artifact.", "", "## Negative controls", "", "Matched non-shock and sign-flipped positive-shock comparisons are recorded per configuration in the machine-readable artifact.", "", "## Limitations", ""])
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.extend(["", "## Development decision", "", report["decision"], ""])
    return "\n".join(lines)
