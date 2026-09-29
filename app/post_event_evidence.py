from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from .execution import ExecutionEngine
from .opportunity import correlation_checks, score_opportunity
from .risk import validate_buy
from .sizing import calculate_entry_notional
from .state import RuntimeState
from .strategy import RollingMomentumVwapStrategy


NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
FORWARD_METHODOLOGY_VERSION = "candidate-forward-v2"
COMPARISON_METHODOLOGY_VERSION = "live-offline-v1"
FORWARD_HORIZONS_MINUTES = (1, 3, 5, 10, 15, 30, 60)
PREDICTION_BACKFILL_METHODOLOGY_VERSION = "candidate-prediction-backfill-v1"
ADS002_V1_CONFIGURATION_KEYS = (
    "min_momentum_pct",
    "target_pct",
    "max_spread_pct",
    "max_bar_age_seconds",
    "max_vwap_extension_pct",
)


def d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp


def session_boundary(session: date, raw: Any, fallback: time) -> datetime:
    text = str(raw or fallback.strftime("%H:%M"))
    parsed = time.fromisoformat(text)
    return datetime.combine(session, parsed, tzinfo=NY)


def _bar_start(bar: dict[str, Any]) -> datetime | None:
    stamp = parse_timestamp(bar.get("t"))
    return stamp.astimezone(NY) if stamp is not None else None


def _reference_bar_start(candidate: dict[str, Any]) -> datetime | None:
    features = candidate.get("features") or {}
    stamp = parse_timestamp(features.get("bar_time"))
    if stamp is not None:
        return stamp.astimezone(NY)
    return None


def calculate_forward_outcome(
    candidate: dict[str, Any],
    bars: list[dict[str, Any]],
    *,
    horizon_minutes: int,
    session_close: datetime,
    provider: str,
    bar_interval: str,
    methodology_version: str = FORWARD_METHODOLOGY_VERSION,
) -> dict[str, Any]:
    """Measure a candidate strictly after its decision-time reference bar.

    One-minute bars are timestamped at bar start. The reference effective time
    is the close of the decision reference bar. Exact-horizon outcomes require
    the terminal bar whose close lands exactly on the requested horizon. We do
    not bridge a regular-session decision across the session boundary.
    """
    candidate_id = candidate.get("candidate_id")
    reference_price = d(candidate.get("decision_reference_price"))
    if reference_price is None or reference_price <= 0:
        return {
            "candidate_id": candidate_id,
            "horizon_minutes": horizon_minutes,
            "observation_end_at": None,
            "reference_price": None,
            "forward_price": None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": provider,
            "bar_interval": bar_interval,
            "methodology_version": methodology_version,
            "status": "error",
            "details": {
                "reason": "missing_decision_reference_price",
                "analytics_only": True,
            },
        }

    reference_bar_start = _reference_bar_start(candidate)
    if reference_bar_start is None:
        return {
            "candidate_id": candidate_id,
            "horizon_minutes": horizon_minutes,
            "observation_end_at": None,
            "reference_price": str(reference_price),
            "forward_price": None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": provider,
            "bar_interval": bar_interval,
            "methodology_version": methodology_version,
            "status": "insufficient_future_data",
            "details": {
                "reason": "decision_reference_bar_timestamp_unavailable",
                "analytics_only": True,
            },
        }

    reference_effective_at = reference_bar_start + timedelta(minutes=1)
    target_at = reference_effective_at + timedelta(minutes=horizon_minutes)
    local_close = session_close.astimezone(NY)
    if target_at > local_close:
        return {
            "candidate_id": candidate_id,
            "horizon_minutes": horizon_minutes,
            "observation_end_at": local_close.isoformat(),
            "reference_price": str(reference_price),
            "forward_price": None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": provider,
            "bar_interval": bar_interval,
            "methodology_version": methodology_version,
            "status": "insufficient_future_data",
            "details": {
                "reason": "requested_horizon_crosses_regular_session_close",
                "reference_effective_at": reference_effective_at.isoformat(),
                "requested_end_at": target_at.isoformat(),
                "session_close": local_close.isoformat(),
                "analytics_only": True,
            },
        }

    window: list[tuple[datetime, dict[str, Any]]] = []
    terminal: dict[str, Any] | None = None
    terminal_end: datetime | None = None
    for bar in bars:
        start = _bar_start(bar)
        if start is None or start.date() != reference_effective_at.date():
            continue
        end = start + timedelta(minutes=1)
        if end <= reference_effective_at or end > target_at:
            continue
        window.append((end, bar))
        if end == target_at:
            terminal = bar
            terminal_end = end

    if terminal is None or terminal_end is None:
        return {
            "candidate_id": candidate_id,
            "horizon_minutes": horizon_minutes,
            "observation_end_at": (
                max((end for end, _ in window), default=reference_effective_at).isoformat()
            ),
            "reference_price": str(reference_price),
            "forward_price": None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": provider,
            "bar_interval": bar_interval,
            "methodology_version": methodology_version,
            "status": "insufficient_future_data",
            "details": {
                "reason": "exact_horizon_terminal_bar_unavailable",
                "reference_effective_at": reference_effective_at.isoformat(),
                "requested_end_at": target_at.isoformat(),
                "observed_bar_count": len(window),
                "analytics_only": True,
            },
        }

    forward_price = d(terminal.get("c"))
    highs = [d(bar.get("h")) for _, bar in window]
    lows = [d(bar.get("l")) for _, bar in window]
    valid_highs = [value for value in highs if value is not None and value > 0]
    valid_lows = [value for value in lows if value is not None and value > 0]
    if forward_price is None or forward_price <= 0 or not valid_highs or not valid_lows:
        return {
            "candidate_id": candidate_id,
            "horizon_minutes": horizon_minutes,
            "observation_end_at": terminal_end.isoformat(),
            "reference_price": str(reference_price),
            "forward_price": str(forward_price) if forward_price is not None else None,
            "forward_return": None,
            "max_favorable_return": None,
            "max_adverse_return": None,
            "provider": provider,
            "bar_interval": bar_interval,
            "methodology_version": methodology_version,
            "status": "error",
            "details": {
                "reason": "invalid_price_data_in_observation_window",
                "observed_bar_count": len(window),
                "analytics_only": True,
            },
        }

    forward_return = (forward_price / reference_price) - Decimal("1")
    mfe = (max(valid_highs) / reference_price) - Decimal("1")
    mae = (min(valid_lows) / reference_price) - Decimal("1")
    return {
        "candidate_id": candidate_id,
        "horizon_minutes": horizon_minutes,
        "observation_end_at": terminal_end.isoformat(),
        "reference_price": str(reference_price),
        "forward_price": str(forward_price),
        "forward_return": str(forward_return),
        "max_favorable_return": str(mfe),
        "max_adverse_return": str(mae),
        "provider": provider,
        "bar_interval": bar_interval,
        "methodology_version": methodology_version,
        "status": "complete",
        "details": {
            "reference_bar_start": reference_bar_start.isoformat(),
            "reference_effective_at": reference_effective_at.isoformat(),
            "requested_end_at": target_at.isoformat(),
            "observed_bar_count": len(window),
            "analytics_only": True,
        },
    }


def _decimal(value: Any, default: str = "0") -> Decimal:
    parsed = d(value)
    return parsed if parsed is not None else Decimal(default)


def _time_value(value: Any, default: str) -> time:
    return time.fromisoformat(str(value or default))


def build_strategy(configuration: dict[str, Any]) -> RollingMomentumVwapStrategy:
    if str(configuration.get("strategy_name") or "") != "rolling_momentum_vwap":
        raise ValueError("only the active rolling_momentum_vwap strategy is replayable")
    return RollingMomentumVwapStrategy(
        fast_window=int(configuration["fast_window"]),
        slow_window=int(configuration["slow_window"]),
        min_momentum_pct=_decimal(configuration["min_momentum_pct"]),
        min_vwap_edge_pct=_decimal(configuration["min_vwap_edge_pct"]),
        stop_pct=_decimal(configuration["stop_pct"]),
        target_pct=_decimal(configuration["target_pct"]),
        entry_start=_time_value(configuration["entry_start"], "09:31"),
        entry_cutoff=_time_value(configuration["entry_cutoff"], "15:30"),
        confirmation_symbols=tuple(configuration.get("confirmation_symbols") or ()),
        min_confirmations=int(configuration["min_confirmations"]),
        regime_window=int(configuration["regime_window"]),
        regime_min_confirmations=int(configuration["regime_min_confirmations"]),
        regime_min_return_pct=_decimal(configuration["regime_min_return_pct"]),
        max_vwap_extension_pct=_decimal(configuration["max_vwap_extension_pct"]),
        volatility_stop_enabled=bool(configuration["volatility_stop_enabled"]),
        volatility_stop_multiplier=_decimal(configuration["volatility_stop_multiplier"]),
        volatility_stop_lookback_bars=int(configuration["volatility_stop_lookback_bars"]),
        max_dynamic_stop_pct=_decimal(configuration["max_dynamic_stop_pct"]),
    )


def replay_settings(configuration: dict[str, Any], entry_symbols: list[str]) -> SimpleNamespace:
    values = dict(configuration)
    decimal_keys = (
        "order_notional",
        "max_spread_pct",
        "min_quality_score",
        "max_pairwise_correlation",
        "risk_per_trade_pct",
        "max_gross_exposure_pct",
        "min_order_notional",
        "max_order_notional",
        "max_position_notional",
        "max_total_position_notional",
        "max_position_gross_pct",
        "max_portfolio_stop_risk_pct",
        "max_daily_loss",
        "stop_pct",
        "target_pct",
        "min_momentum_pct",
        "min_vwap_edge_pct",
        "regime_min_return_pct",
        "max_vwap_extension_pct",
        "volatility_stop_multiplier",
        "max_dynamic_stop_pct",
    )
    for key in decimal_keys:
        values[key] = _decimal(values.get(key))
    values["allowed_symbols"] = set(
        str(symbol).upper() for symbol in values.get("allowed_symbols", entry_symbols)
    )
    values["scan_symbols"] = tuple(entry_symbols)
    values["execution_authorized"] = bool(values.get("execution_authorized", True))
    return SimpleNamespace(**values)


def truncate_bars(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    decision_at: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Remove every bar that was not fully complete at the live decision time."""
    output: dict[str, list[dict[str, Any]]] = {}
    for symbol, bars in bars_by_symbol.items():
        safe: list[dict[str, Any]] = []
        for bar in bars:
            start = _bar_start(bar)
            if start is None:
                continue
            if start + timedelta(minutes=1) <= decision_at.astimezone(NY):
                safe.append(bar)
        output[symbol.upper()] = safe
    return output


def _live_candidate_result(
    candidate: dict[str, Any],
    execution_result: dict[str, Any],
) -> tuple[str, str]:
    symbol = str(candidate.get("symbol") or "").upper()
    if bool(candidate.get("submitted")) or candidate.get("intent_id"):
        return "submitted", str(candidate.get("final_decision") or "submitted")
    for order in execution_result.get("orders") or []:
        if str(order.get("symbol") or "").upper() == symbol:
            return "submitted", str(execution_result.get("reason") or "submitted")
    for skipped in execution_result.get("skipped") or []:
        if str(skipped.get("symbol") or "").upper() == symbol:
            return "hold", str(skipped.get("reason") or "skipped")
    action = str(candidate.get("action") or "hold").lower()
    if action != "buy":
        return "rejected", str(candidate.get("reason") or "strategy rejected")
    recorded_final = str(candidate.get("final_decision") or "").lower()
    if recorded_final in {"blocked", "hold", "error", "qualified_not_selected"}:
        return recorded_final, str(candidate.get("reason") or recorded_final)
    overall = str(execution_result.get("action") or "").lower()
    if overall in {"hold", "blocked", "error"}:
        return overall, str(execution_result.get("reason") or overall)
    return "qualified", str(candidate.get("reason") or "qualified")


def reconstruct_cycle(
    cycle: dict[str, Any],
    candidates: list[dict[str, Any]],
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Deterministically reconstruct one live cycle from durable pre-decision inputs.

    Strategy evaluation and deterministic gate helpers are imported from the
    production implementation. Broker submission itself is not simulated.
    """
    context = cycle.get("comparison_context") or {}
    configuration = context.get("configuration") or {}
    execution_context = context.get("execution_context") or {}
    execution_result = context.get("execution_result") or {}
    cycle_key = str(cycle.get("cycle_key") or "")
    methodology = COMPARISON_METHODOLOGY_VERSION

    base = {
        "scan_cycle_id": cycle.get("scan_cycle_id"),
        "strategy_version_id": cycle.get("strategy_version_id"),
        "run_id": cycle.get("run_id"),
        "runtime_instance_id": cycle.get("runtime_instance_id"),
        "deployment_id": cycle.get("deployment_id"),
        "methodology_version": methodology,
        "comparison_timestamp": datetime.now(UTC).isoformat(),
    }

    if str(cycle.get("data_status") or "") == "partial_backfill" or not context:
        missing = [
            "original_decision_cycle",
            "full_candidate_population",
            "decision_time_runtime_provenance",
            "decision_time_account_and_risk_state",
        ]
        return [
            {
                **base,
                "candidate_id": candidate.get("candidate_id"),
                "symbol": candidate.get("symbol"),
                "live_result": _live_candidate_result(candidate, execution_result)[0],
                "offline_result": None,
                "match_state": "UNRECONSTRUCTABLE",
                "mismatch_category": "UNRECONSTRUCTABLE",
                "source_data_completeness": "historical_partial_backfill",
                "details": {"missing_inputs": missing, "future_data_used": False},
            }
            for candidate in candidates
        ]

    required_context = (
        "decision_at",
        "entry_symbols",
        "account",
        "positions",
        "recent_orders",
        "open_order_symbols",
        "startup_reconciled",
        "reconciliation_safe",
        "entries_enabled",
        "assets",
    )
    missing_context = [key for key in required_context if key not in execution_context]
    if not configuration or missing_context:
        return [
            {
                **base,
                "candidate_id": candidate.get("candidate_id"),
                "symbol": candidate.get("symbol"),
                "live_result": _live_candidate_result(candidate, execution_result)[0],
                "offline_result": None,
                "match_state": "UNRECONSTRUCTABLE",
                "mismatch_category": "INSUFFICIENT_INPUT",
                "source_data_completeness": "incomplete",
                "details": {
                    "missing_inputs": (
                        (["configuration"] if not configuration else []) + missing_context
                    ),
                    "future_data_used": False,
                },
            }
            for candidate in candidates
        ]

    decision_at = parse_timestamp(execution_context.get("decision_at"))
    if decision_at is None:
        return [
            {
                **base,
                "candidate_id": candidate.get("candidate_id"),
                "symbol": candidate.get("symbol"),
                "live_result": _live_candidate_result(candidate, execution_result)[0],
                "offline_result": None,
                "match_state": "UNRECONSTRUCTABLE",
                "mismatch_category": "STALE_INPUT",
                "source_data_completeness": "invalid_decision_time",
                "details": {"future_data_used": False},
            }
            for candidate in candidates
        ]
    decision_at = decision_at.astimezone(NY)
    entry_symbols = [str(symbol).upper() for symbol in execution_context["entry_symbols"]]
    safe_bars = truncate_bars(bars_by_symbol, decision_at)
    by_symbol = {str(row.get("symbol") or "").upper(): row for row in candidates}

    try:
        strategy = build_strategy(configuration)
        settings = replay_settings(configuration, entry_symbols)
        engine = ExecutionEngine(
            settings,
            client=None,
            market_data=None,
            strategy=strategy,
            state=RuntimeState(),
            ledger=None,
            universe=None,
        )
    except Exception as exc:
        return [
            {
                **base,
                "candidate_id": candidate.get("candidate_id"),
                "symbol": candidate.get("symbol"),
                "live_result": _live_candidate_result(candidate, execution_result)[0],
                "offline_result": None,
                "match_state": "UNRECONSTRUCTABLE",
                "mismatch_category": "CONFIGURATION_MISMATCH",
                "source_data_completeness": "invalid_configuration",
                "details": {"error": f"{type(exc).__name__}: {exc}", "future_data_used": False},
            }
            for candidate in candidates
        ]

    positions = [dict(row) for row in execution_context["positions"]]
    recent_orders = [dict(row) for row in execution_context["recent_orders"]]
    open_order_symbols = set(str(x).upper() for x in execution_context["open_order_symbols"])
    account = dict(execution_context["account"])
    confirmations = {
        symbol: safe_bars.get(symbol, [])
        for symbol in tuple(configuration.get("confirmation_symbols") or ())
    }

    offline_signals: dict[str, Any] = {}
    stage_reasons: dict[str, str] = {}
    qualified: list[Any] = []
    strategy_mismatch: set[str] = set()
    gate_mismatch: set[str] = set()

    for symbol in entry_symbols:
        candidate = by_symbol.get(symbol)
        if candidate is None:
            continue
        has_position = any(
            str(position.get("symbol") or "").upper() == symbol
            and _decimal(position.get("qty")) > 0
            for position in positions
        )
        signal = strategy.evaluate(
            bars=safe_bars.get(symbol, []),
            confirmation_bars=confirmations,
            symbol=symbol,
            has_position=has_position,
            order_notional=settings.order_notional,
            now=decision_at,
        )
        live_strategy = ((candidate.get("features") or {}).get("strategy_evaluation") or {})
        if live_strategy and str(live_strategy.get("action") or "") != signal.action:
            strategy_mismatch.add(symbol)

        if signal.action == "buy":
            quote = candidate.get("quote") or {}
            raw_quote = {
                "bp": quote.get("bid"),
                "ap": quote.get("ask"),
            }
            allowed, reason, quality = engine._market_quality(
                signal,
                safe_bars.get(symbol, []),
                raw_quote,
                confirmations,
                decision_at,
            )
            signal.metadata = dict(signal.metadata or {})
            signal.metadata["market_quality"] = quality
            if allowed:
                ranking = score_opportunity(
                    settings,
                    signal,
                    safe_bars.get(symbol, []),
                    quality,
                )
                signal.metadata["quality_score"] = ranking["score"]
                signal.metadata["quality_components"] = ranking["components"]
                signal.metadata["relative_volume_ratio"] = ranking["relative_volume_ratio"]
                signal.metadata["trend_persistence"] = ranking["trend_persistence"]
                if Decimal(str(ranking["score"])) < settings.min_quality_score:
                    signal.action = "hold"
                    signal.reason = (
                        f"quality score {ranking['score']:.2f} below "
                        f"MIN_QUALITY_SCORE {settings.min_quality_score}"
                    )
                else:
                    qualified.append(signal)
            else:
                signal.action = "hold"
                signal.reason = reason
        offline_signals[symbol] = signal
        stage_reasons[symbol] = str(signal.reason or "")
        live_scan_action = str(candidate.get("action") or "hold").lower()
        if signal.action != live_scan_action:
            gate_mismatch.add(symbol)

    ranked = sorted(qualified, key=engine._signal_rank, reverse=True)

    loss_ok, loss_reason, _ = engine._loss_streak_gate(recent_orders, decision_at)
    global_block: str | None = None
    if not loss_ok:
        global_block = loss_reason
    elif not bool(execution_context["startup_reconciled"]):
        global_block = "qualified entries blocked until startup reconciliation completes"
    elif not bool(execution_context["reconciliation_safe"]):
        global_block = "qualified entries blocked by broker/canonical reconciliation"
    elif not bool(execution_context["entries_enabled"]):
        global_block = "qualified entries blocked because new entries are disabled"

    planned: set[str] = set()
    final_reasons: dict[str, str] = dict(stage_reasons)
    if global_block is None:
        entry_count = engine._entry_orders_today(
            recent_orders,
            now=decision_at,
        )
        if settings.portfolio_limit_mode == "risk":
            cycle_limit = (
                int(settings.max_new_entries_per_cycle)
                if int(settings.max_new_entries_per_cycle) > 0
                else None
            )
        else:
            cycle_limit = min(
                int(settings.max_new_entries_per_cycle),
                int(settings.max_concurrent_positions),
            )
        simulated_positions = [dict(row) for row in positions]
        simulated_account = dict(account)
        simulated_cash = _decimal(account.get("cash"))
        assets = execution_context["assets"]

        for signal in ranked:
            if cycle_limit is not None and len(planned) >= cycle_limit:
                final_reasons.setdefault(signal.symbol.upper(), "cycle entry limit reached")
                break
            symbol = signal.symbol.upper()
            if symbol in open_order_symbols:
                final_reasons[symbol] = "open order already exists for symbol"
                continue
            exposure_symbols = [
                str(position.get("symbol") or "").upper()
                for position in simulated_positions
                if _decimal(position.get("qty")) > 0
            ]
            corr_ok, corr_reason, _ = correlation_checks(
                settings,
                symbol,
                exposure_symbols,
                safe_bars,
            )
            if not corr_ok:
                final_reasons[symbol] = corr_reason
                continue
            if int(settings.reentry_cooldown_minutes) > 0:
                latest_exit = engine._latest_bot_exit_today(
                    recent_orders,
                    symbol,
                    now=decision_at,
                )
                if latest_exit is not None:
                    minutes_since_exit = (decision_at - latest_exit).total_seconds() / 60
                    if minutes_since_exit < int(settings.reentry_cooldown_minutes):
                        final_reasons[symbol] = "same-symbol cooldown active"
                        continue
            simulated_account["cash"] = str(simulated_cash)
            effective_stop = _decimal(
                (signal.metadata or {}).get("effective_stop_pct"),
                str(settings.stop_pct),
            )
            notional = calculate_entry_notional(
                settings,
                simulated_account,
                simulated_positions,
                stop_pct_override=effective_stop,
            )
            if notional <= 0:
                final_reasons[symbol] = "capital allocator produced no eligible notional"
                continue
            risk = validate_buy(
                settings,
                symbol,
                notional,
                simulated_account,
                simulated_positions,
                entry_count + len(planned),
                entry_symbols=set(entry_symbols),
                stop_pct_override=effective_stop,
            )
            if not risk.allowed:
                final_reasons[symbol] = risk.reason
                continue
            asset = assets.get(symbol)
            if not isinstance(asset, dict):
                final_reasons[symbol] = "asset eligibility input unavailable"
                continue
            if (
                str(asset.get("status") or "").lower() != "active"
                or not bool(asset.get("tradable"))
                or not bool(asset.get("fractionable"))
            ):
                final_reasons[symbol] = "asset is not active, tradable, and fractionable"
                continue
            planned.add(symbol)
            final_reasons[symbol] = "eligible for broker submission"
            simulated_cash -= notional
            simulated_positions.append(
                {
                    "symbol": symbol,
                    "qty": "1",
                    "market_value": str(notional),
                    "risk_stop_pct": str(effective_stop),
                }
            )
    else:
        for signal in ranked:
            final_reasons[signal.symbol.upper()] = global_block

    results: list[dict[str, Any]] = []
    submitted = {
        str(order.get("symbol") or "").upper()
        for order in execution_result.get("orders") or []
        if order.get("symbol")
    }
    if not submitted:
        submitted = {
            str(row.get("symbol") or "").upper()
            for row in candidates
            if bool(row.get("submitted")) or row.get("intent_id")
        }
    for candidate in candidates:
        symbol = str(candidate.get("symbol") or "").upper()
        live_result, live_reason = _live_candidate_result(candidate, execution_result)
        offline_signal = offline_signals.get(symbol)
        if offline_signal is None:
            offline_result = "candidate_missing_offline"
            category = "CANDIDATE_MISSING_OFFLINE"
            match_state = "MISMATCH"
        elif symbol in strategy_mismatch:
            offline_result = offline_signal.action
            category = "SIGNAL_MISMATCH"
            match_state = "MISMATCH"
        elif symbol in gate_mismatch:
            offline_result = offline_signal.action
            category = "GATE_MISMATCH"
            match_state = "MISMATCH"
        else:
            offline_result = (
                "eligible_to_submit"
                if symbol in planned
                else ("rejected" if offline_signal.action != "buy" else "hold")
            )
            live_selected = symbol in submitted
            if (symbol in planned) == live_selected:
                category = "MATCH"
                match_state = "MATCH"
            else:
                category = "ACTION_MISMATCH"
                match_state = "MISMATCH"

        results.append(
            {
                **base,
                "candidate_id": candidate.get("candidate_id"),
                "symbol": symbol,
                "live_result": live_result,
                "offline_result": offline_result,
                "match_state": match_state,
                "mismatch_category": category,
                "source_data_completeness": "complete",
                "details": {
                    "live_reason": live_reason,
                    "offline_reason": final_reasons.get(symbol),
                    "decision_at": decision_at.isoformat(),
                    "future_data_used": False,
                    "bars_truncated_at_decision": True,
                },
            }
        )
    return results


@dataclass
class PostEventRunSummary:
    session: str
    candidates: int = 0
    prediction_backfill_events: int = 0
    outcome_events: int = 0
    comparison_events: int = 0
    complete_outcomes: int = 0
    incomplete_outcomes: int = 0
    error_outcomes: int = 0


class PostEventEvidenceRunner:
    """Post-close analytics runner. It has no broker order or strategy-state writes."""

    def __init__(
        self,
        *,
        settings: Any,
        market_data: Any,
        event_sink: Any,
        evidence_reader: Any,
    ) -> None:
        self.settings = settings
        self.market_data = market_data
        self.event_sink = event_sink
        self.evidence_reader = evidence_reader

    async def _apply_backpressure(self) -> None:
        """Drain analytics telemetry before the bounded queue can overflow."""
        queue = getattr(self.event_sink, "queue", None)
        if queue is None:
            return
        maxsize = int(getattr(queue, "maxsize", 0) or 0)
        threshold = max(1, maxsize // 2) if maxsize > 0 else 500
        if queue.qsize() >= threshold:
            await queue.join()

    @staticmethod
    def _forward_outcome_eligible(candidate: dict[str, Any]) -> bool:
        """Gate only whether a candidate can be identified for measurement.

        Prediction completeness is intentionally not part of this decision.
        Invalid prices, timestamps, or future data are persisted as explicit
        per-horizon error/insufficient states by calculate_forward_outcome().
        """
        identity = candidate.get("candidate_id") or candidate.get("candidate_key")
        symbol = str(candidate.get("symbol") or "").strip()
        return identity not in {None, ""} and bool(symbol)

    @staticmethod
    def _ads002_research_eligibility(
        candidate: dict[str, Any],
        outcome: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep model-validation eligibility downstream of outcome measurement."""
        reasons: list[str] = []
        score = candidate.get("ads002_score")
        if not isinstance(score, dict):
            reasons.append("ADS002_PREDICTION_MISSING")
        else:
            if score.get("methodology_version") != "ads-shadow-v1":
                reasons.append("ADS002_METHODOLOGY_MISMATCH")
            completeness = score.get("source_completeness")
            if not (
                isinstance(completeness, dict)
                and completeness.get("pretrade_complete") is True
            ):
                reasons.append("ADS002_PRETRADE_INCOMPLETE")
        if outcome.get("status") != "complete":
            reasons.append("REQUIRED_HORIZON_INCOMPLETE")
        return {
            "eligible": not reasons,
            "reason_codes": reasons,
            "analytics_only": True,
        }

    @staticmethod
    def _configuration_value_equal(left: Any, right: Any) -> bool:
        left_decimal = d(left)
        right_decimal = d(right)
        if left_decimal is not None and right_decimal is not None:
            return left_decimal == right_decimal
        return str(left) == str(right)

    @classmethod
    def _frozen_ads002_configurations(
        cls,
        candidates: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Find immutable decision-cycle configurations already persisted in-session."""
        by_strategy: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            strategy_version = str(candidate.get("strategy_version_id") or "")
            if not strategy_version or strategy_version in by_strategy:
                continue
            payload = candidate.get("decision_cycle_payload")
            if not isinstance(payload, dict):
                continue
            comparison = payload.get("comparison_context")
            if not isinstance(comparison, dict):
                continue
            configuration = comparison.get("configuration")
            if not isinstance(configuration, dict):
                continue
            if all(key in configuration for key in ADS002_V1_CONFIGURATION_KEYS):
                by_strategy[strategy_version] = dict(configuration)
        return by_strategy

    def _reconstruct_ads002_v1(
        self,
        candidate: dict[str, Any],
        *,
        frozen_configurations: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Reconstruct v1 only when frozen inputs and frozen scoring config agree."""
        existing = candidate.get("ads002_score")
        if isinstance(existing, dict):
            return existing, None

        builder = getattr(self.event_sink, "_ads002_shadow_candidate_safe", None)
        current_configuration = getattr(
            self.event_sink,
            "_comparison_configuration",
            None,
        )
        if not callable(builder) or not callable(current_configuration):
            return None, "ADS002_CANONICAL_SCORER_UNAVAILABLE"

        candidate_strategy = str(candidate.get("strategy_version_id") or "")
        active_strategy = str(getattr(self.settings, "strategy_version_id", "") or "")
        if not candidate_strategy or candidate_strategy != active_strategy:
            return None, "STRATEGY_VERSION_MISMATCH"

        frozen = frozen_configurations.get(candidate_strategy)
        if not isinstance(frozen, dict):
            return None, "FROZEN_SCORING_CONFIGURATION_UNAVAILABLE"

        current = current_configuration()
        mismatched = [
            key
            for key in ADS002_V1_CONFIGURATION_KEYS
            if key not in current
            or not self._configuration_value_equal(frozen.get(key), current.get(key))
        ]
        if mismatched:
            return None, "FROZEN_SCORING_CONFIGURATION_MISMATCH:" + ",".join(mismatched)

        features = candidate.get("features")
        if not isinstance(features, dict):
            return None, "FROZEN_CANDIDATE_FEATURES_UNAVAILABLE"

        score = builder(
            symbol=str(candidate.get("symbol") or ""),
            metadata=dict(features),
        )
        if not isinstance(score, dict):
            return None, "ADS002_RECONSTRUCTION_FAILED"
        return score, None

    async def _backfill_missing_ads002_v1(
        self,
        candidates: list[dict[str, Any]],
        *,
        computed_at: str,
        summary: PostEventRunSummary,
    ) -> None:
        """Append derived v1 evidence without rewriting historical raw evidence."""
        frozen_configurations = self._frozen_ads002_configurations(candidates)
        for candidate in candidates:
            if isinstance(candidate.get("ads002_score"), dict):
                continue

            score, failure_reason = self._reconstruct_ads002_v1(
                candidate,
                frozen_configurations=frozen_configurations,
            )
            if score is None:
                continue

            candidate["ads002_score"] = score
            candidate_id = candidate.get("candidate_id")
            symbol = str(candidate.get("symbol") or "")
            payload = {
                "candidate_id": candidate_id,
                "candidate_key": candidate.get("candidate_key"),
                "symbol": symbol,
                "strategy_version_id": candidate.get("strategy_version_id"),
                "observed_at": candidate.get("observed_at"),
                "ads002": score,
                "ads002_v2_reconstruction": {
                    "status": "UNRECONSTRUCTABLE",
                    "reason": "FULL_DECISION_CYCLE_CROSS_SECTION_NOT_PERSISTED",
                    "raw_features_preserved": bool(
                        (candidate.get("features") or {}).get(
                            "ads002_v2_raw_features"
                        )
                    ),
                },
                "reconstruction": {
                    "status": "RECONSTRUCTED",
                    "methodology_version": PREDICTION_BACKFILL_METHODOLOGY_VERSION,
                    "basis": (
                        "immutable_candidate_features+"
                        "frozen_session_scoring_configuration"
                    ),
                    "failure_reason": failure_reason,
                    "analytics_only": True,
                    "raw_evidence_rewritten": False,
                },
                "computed_at": computed_at,
            }
            self.event_sink.emit(
                event_type="candidate_prediction_backfill",
                event_key=(
                    f"candidate-prediction-backfill:{candidate_id}:"
                    f"ads-shadow-v1:{PREDICTION_BACKFILL_METHODOLOGY_VERSION}"
                ),
                occurred_at=computed_at,
                symbol=symbol,
                payload=payload,
            )
            await self._apply_backpressure()
            summary.prediction_backfill_events += 1

    @staticmethod
    def _cycle_payload(candidate: dict[str, Any]) -> dict[str, Any]:
        scan = dict(candidate.get("scan_cycle") or {})
        source = candidate.get("decision_cycle_payload")
        if isinstance(source, dict):
            scan["comparison_context"] = source.get("comparison_context") or {}
            scan["cycle_key"] = source.get("cycle_key") or scan.get("cycle_key")
        return scan

    async def run_session(self, session: date) -> PostEventRunSummary:
        source = await self.evidence_reader(evidence_session=session.isoformat())
        candidates = [
            dict(row)
            for row in (source.get("candidates") or [])
            if isinstance(row, dict)
        ]
        summary = PostEventRunSummary(
            session=session.isoformat(),
            candidates=len(candidates),
        )
        if not candidates:
            return summary

        calendar = await self.market_data.market_calendar_details(
            start=session,
            end=session,
        )
        if not calendar:
            raise RuntimeError(f"{session.isoformat()} is not a trading session")
        detail = calendar[0]
        session_open = session_boundary(session, detail.get("open"), time(9, 30))
        session_close = session_boundary(session, detail.get("close"), time(16, 0))

        symbols: set[str] = {
            str(row.get("symbol") or "").upper()
            for row in candidates
            if row.get("symbol")
        }
        for row in candidates:
            payload = row.get("decision_cycle_payload")
            if not isinstance(payload, dict):
                continue
            comparison = payload.get("comparison_context") or {}
            config = comparison.get("configuration") or {}
            context = comparison.get("execution_context") or {}
            symbols.update(str(x).upper() for x in (config.get("confirmation_symbols") or []))
            symbols.update(str(x).upper() for x in (context.get("entry_symbols") or []))

        bars_by_symbol: dict[str, list[dict[str, Any]]]
        bar_error: str | None = None
        try:
            bars_by_symbol = await self.market_data.historical_bars_many(
                sorted(symbols),
                start=session_open,
                end=session_close + timedelta(minutes=1),
            )
        except Exception as exc:
            bars_by_symbol = {}
            bar_error = f"{type(exc).__name__}: {exc}"

        computed_at = datetime.now(UTC).isoformat()
        await self._backfill_missing_ads002_v1(
            candidates,
            computed_at=computed_at,
            summary=summary,
        )
        outcome_candidates = [
            candidate
            for candidate in candidates
            if self._forward_outcome_eligible(candidate)
        ]
        for candidate in outcome_candidates:
            provider = str(
                candidate.get("data_feed")
                or (candidate.get("scan_cycle") or {}).get("data_feed")
                or getattr(self.settings, "data_feed", "unknown")
            )
            interval = str(
                candidate.get("bar_interval")
                or (candidate.get("scan_cycle") or {}).get("bar_interval")
                or getattr(self.settings, "bar_timeframe", "1Min")
            )
            for horizon in FORWARD_HORIZONS_MINUTES:
                if bar_error:
                    outcome = {
                        "candidate_id": candidate.get("candidate_id"),
                        "horizon_minutes": horizon,
                        "observation_end_at": None,
                        "reference_price": candidate.get("decision_reference_price"),
                        "forward_price": None,
                        "forward_return": None,
                        "max_favorable_return": None,
                        "max_adverse_return": None,
                        "provider": provider,
                        "bar_interval": interval,
                        "methodology_version": FORWARD_METHODOLOGY_VERSION,
                        "status": "error",
                        "details": {
                            "reason": "historical_market_data_fetch_failed",
                            "error": bar_error,
                            "analytics_only": True,
                        },
                    }
                else:
                    outcome = calculate_forward_outcome(
                        candidate,
                        bars_by_symbol.get(str(candidate.get("symbol") or "").upper(), []),
                        horizon_minutes=horizon,
                        session_close=session_close,
                        provider=provider,
                        bar_interval=interval,
                    )
                outcome["session"] = session.isoformat()
                outcome["computed_at"] = computed_at
                outcome["candidate_key"] = candidate.get("candidate_key")
                outcome["research_eligibility"] = {
                    "ads002_v1": self._ads002_research_eligibility(
                        candidate,
                        outcome,
                    ),
                }
                status = str(outcome["status"])
                candidate_identity = (
                    candidate.get("candidate_id")
                    or candidate.get("candidate_key")
                )
                event_key = (
                    f"candidate-forward:{candidate_identity}:"
                    f"{horizon}:{FORWARD_METHODOLOGY_VERSION}:{status}"
                )
                self.event_sink.emit(
                    event_type="candidate_forward_outcome",
                    event_key=event_key,
                    occurred_at=computed_at,
                    symbol=str(candidate.get("symbol") or ""),
                    payload=outcome,
                )
                await self._apply_backpressure()
                summary.outcome_events += 1
                if status == "complete":
                    summary.complete_outcomes += 1
                elif status == "insufficient_future_data":
                    summary.incomplete_outcomes += 1
                else:
                    summary.error_outcomes += 1

        grouped: dict[Any, list[dict[str, Any]]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.get("scan_cycle_id"), []).append(candidate)

        for rows in grouped.values():
            cycle = self._cycle_payload(rows[0])
            if bar_error:
                comparisons = [
                    {
                        "scan_cycle_id": candidate.get("scan_cycle_id"),
                        "candidate_id": candidate.get("candidate_id"),
                        "strategy_version_id": candidate.get("strategy_version_id"),
                        "run_id": candidate.get("run_id"),
                        "runtime_instance_id": cycle.get("runtime_instance_id"),
                        "deployment_id": cycle.get("deployment_id"),
                        "symbol": candidate.get("symbol"),
                        "live_result": _live_candidate_result(candidate, {})[0],
                        "offline_result": None,
                        "match_state": "UNRECONSTRUCTABLE",
                        "mismatch_category": "MARKET_DATA_MISMATCH",
                        "methodology_version": COMPARISON_METHODOLOGY_VERSION,
                        "source_data_completeness": "market_data_unavailable",
                        "comparison_timestamp": computed_at,
                        "details": {
                            "error": bar_error,
                            "future_data_used": False,
                        },
                    }
                    for candidate in rows
                ]
            else:
                comparisons = reconstruct_cycle(cycle, rows, bars_by_symbol)

            for comparison in comparisons:
                candidate_id = comparison.get("candidate_id")
                key = (
                    f"{comparison.get('scan_cycle_id')}:{candidate_id}:"
                    f"{COMPARISON_METHODOLOGY_VERSION}"
                )
                comparison["comparison_key"] = key
                comparison["session"] = session.isoformat()
                comparison["analytics_only"] = True
                category = str(comparison.get("mismatch_category") or "UNKNOWN")
                state = str(comparison.get("match_state") or "UNKNOWN")
                self.event_sink.emit(
                    event_type="live_offline_comparison",
                    event_key=f"live-offline:{key}:{state}:{category}",
                    occurred_at=computed_at,
                    symbol=str(comparison.get("symbol") or ""),
                    payload=comparison,
                )
                await self._apply_backpressure()
                summary.comparison_events += 1

        queue = getattr(self.event_sink, "queue", None)
        if queue is not None:
            await queue.join()
        return summary
