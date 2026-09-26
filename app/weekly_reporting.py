from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from typing import Any

REPORT_VERSION = "rhen-weekly-v1"
ZERO = Decimal("0")
HUNDRED = Decimal("100")

LOCKED_EDGE_V1_FAMILIES = (
    "controlled continuation",
    "pullback reclaim",
    "compression breakout",
    "relative-strength impulse",
    "opening-breakout retest",
)


def d(value: Any, default: Decimal = ZERO) -> Decimal:
    if value in (None, ""):
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


def _mean(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, ZERO) / Decimal(len(values))


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return Decimal(str(median(values)))


def _distribution(values: list[Decimal]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "median": None,
            "mean": None,
            "maximum": None,
        }
    return {
        "count": len(values),
        "minimum": min(values),
        "median": _median(values),
        "mean": _mean(values),
        "maximum": max(values),
    }


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize(item) for item in value]
    return value


def _session(payload: dict[str, Any]) -> str | None:
    value = payload.get("session")
    return str(value) if value else None


def _daily_records(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    for item in inputs.get("daily_reports") or []:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or not _session(payload):
            continue
        records.append(item)
    records.sort(key=lambda item: str(item["payload"]["session"]))
    return records


def _daily_trades(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for record in records:
        payload = record["payload"]
        session = str(payload["session"])
        for trade in payload.get("trades") or []:
            if isinstance(trade, dict):
                trades.append({**trade, "session": session})
    return trades


def _daily_metric(records: list[dict[str, Any]], key: str) -> list[tuple[str, Decimal]]:
    values: list[tuple[str, Decimal]] = []
    for record in records:
        payload = record["payload"]
        metrics = payload.get("metrics") or {}
        values.append((str(payload["session"]), d(metrics.get(key))))
    return values


def _calendar_session_strings(calendar: list[dict[str, Any]]) -> list[str]:
    output = []
    for item in calendar:
        raw = item.get("date")
        if raw is None:
            continue
        output.append(raw.isoformat() if isinstance(raw, date) else str(raw))
    return sorted(set(output))


def _shortened_sessions(calendar: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for item in calendar:
        raw_date = item.get("date")
        if raw_date is None:
            continue
        close = str(item.get("close") or "")
        normalized = close[:5]
        if normalized and normalized != "16:00":
            output.append(
                {
                    "session": raw_date.isoformat() if isinstance(raw_date, date) else str(raw_date),
                    "open": item.get("open"),
                    "close": item.get("close"),
                }
            )
    return output


def evidence_strength(
    *,
    supporting_sessions: int,
    contradicting_sessions: int,
    available_sessions: int,
    sample_count: int,
    operationally_confirmed: bool = False,
) -> str:
    if operationally_confirmed and sample_count > 0:
        return "operationally confirmed"
    if available_sessions <= 1:
        return "observed once" if sample_count > 0 else "insufficient sample"
    if supporting_sessions >= 2 and contradicting_sessions == 0:
        if supporting_sessions == available_sessions:
            return "persistent across most available sessions"
        return "repeated across multiple sessions"
    if supporting_sessions > 0 and contradicting_sessions > 0:
        return "contradicted by other sessions"
    if sample_count > 0:
        return "insufficient sample"
    return "insufficient sample"


def _performance(
    records: list[dict[str, Any]],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    trades = _daily_trades(records)
    pnls = [d(trade.get("realized_pnl")) for trade in trades]
    wins = [pnl for pnl in pnls if pnl > ZERO]
    losses = [pnl for pnl in pnls if pnl < ZERO]
    flats = [pnl for pnl in pnls if pnl == ZERO]
    gross_profit = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)
    realized = sum((value for _, value in _daily_metric(records, "realized_pnl")), ZERO)
    completed = sum(int(d((record["payload"].get("metrics") or {}).get("trade_count"))) for record in records)
    session_pnl = _daily_metric(records, "realized_pnl")

    equity_rows = list(inputs.get("account_equity_by_session") or [])
    equity_rows.sort(key=lambda row: str(row.get("session") or ""))
    starting_equity = d(equity_rows[0].get("starting_equity")) if equity_rows else None
    ending_equity = d(equity_rows[-1].get("ending_equity")) if equity_rows else None
    weekly_return = None
    if starting_equity is not None and ending_equity is not None and starting_equity > ZERO:
        weekly_return = (ending_equity - starting_equity) / starting_equity

    order_count = sum(int(row.get("orders") or 0) for row in inputs.get("orders_by_session") or [])
    fill_count = sum(int(row.get("fills") or 0) for row in inputs.get("fills_by_session") or [])
    entry_count = sum(int(row.get("entry_fills") or 0) for row in inputs.get("fills_by_session") or [])
    candidate_count = sum(int(row.get("evaluated") or 0) for row in inputs.get("candidate_by_session") or [])
    rejected_count = sum(int(row.get("rejected") or 0) for row in inputs.get("candidate_by_session") or [])
    qualified_count = sum(int(row.get("qualified") or 0) for row in inputs.get("candidate_by_session") or [])
    incident_count = len(inputs.get("incidents") or [])

    by_strategy: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for position in inputs.get("positions") or []:
        version = str(position.get("strategy_version_id") or "unknown")
        grouped[version].append(position)
    for version, positions in grouped.items():
        closed = [row for row in positions if str(row.get("status") or "") == "closed"]
        values = [d(row.get("realized_pnl")) for row in closed if row.get("realized_pnl") is not None]
        positives = [value for value in values if value > ZERO]
        negatives = [value for value in values if value < ZERO]
        by_strategy[version] = {
            "closed_positions": len(closed),
            "realized_pnl": sum(values, ZERO),
            "wins": len(positives),
            "losses": len(negatives),
            "flat": sum(1 for value in values if value == ZERO),
            "expectancy": _mean(values),
        }

    best = max(session_pnl, key=lambda item: item[1]) if session_pnl else None
    worst = min(session_pnl, key=lambda item: item[1]) if session_pnl else None
    max_drawdown = (inputs.get("account_weekly_drawdown") or {}).get("max_drawdown_pct")

    return {
        "starting_equity": starting_equity,
        "ending_equity": ending_equity,
        "weekly_realized_pnl": realized,
        "weekly_return": weekly_return,
        "weekly_return_basis": "canonical account equity snapshots" if weekly_return is not None else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > ZERO else None,
        "completed_trades": completed,
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(flats),
        "win_rate": (Decimal(len(wins)) / Decimal(len(trades))) if trades else None,
        "average_winner": _mean(wins),
        "average_loser": _mean(losses),
        "expectancy": (realized / Decimal(completed)) if completed else None,
        "best_session": {"session": best[0], "realized_pnl": best[1]} if best else None,
        "worst_session": {"session": worst[0], "realized_pnl": worst[1]} if worst else None,
        "maximum_weekly_drawdown": max_drawdown,
        "cumulative_equity_path": equity_rows,
        "order_count": order_count,
        "fill_count": fill_count,
        "entry_count": entry_count,
        "evaluated_candidates": candidate_count,
        "rejected_candidates": rejected_count,
        "qualified_candidates": qualified_count,
        "incident_count": incident_count,
        "daily_dispersion": [
            {"session": session, "realized_pnl": value}
            for session, value in session_pnl
        ],
        "by_strategy_version": by_strategy,
    }


def _trade_quality(records: list[dict[str, Any]], inputs: dict[str, Any]) -> dict[str, Any]:
    trades = _daily_trades(records)
    winners = [trade for trade in trades if d(trade.get("realized_pnl")) > ZERO]
    losers = [trade for trade in trades if d(trade.get("realized_pnl")) < ZERO]
    mfes = [d(trade.get("mfe_pct")) for trade in trades if trade.get("mfe_pct") is not None]
    maes = [d(trade.get("mae_pct")) for trade in trades if trade.get("mae_pct") is not None]
    winner_mfe = [d(trade.get("mfe_pct")) for trade in winners if trade.get("mfe_pct") is not None]
    winner_mae = [d(trade.get("mae_pct")) for trade in winners if trade.get("mae_pct") is not None]
    loser_mfe = [d(trade.get("mfe_pct")) for trade in losers if trade.get("mfe_pct") is not None]
    loser_mae = [d(trade.get("mae_pct")) for trade in losers if trade.get("mae_pct") is not None]
    holds = [d(trade.get("hold_minutes")) for trade in trades if trade.get("hold_minutes") is not None]

    losses_first_favorable = [
        trade for trade in losers
        if trade.get("mfe_pct") is not None and d(trade.get("mfe_pct")) > ZERO
    ]
    uncaptured_winners = []
    material_giveback_winners = []
    for trade in winners:
        if trade.get("mfe_pct") is None or trade.get("return_pct") is None:
            continue
        gap = d(trade.get("mfe_pct")) - d(trade.get("return_pct"))
        if gap > ZERO:
            uncaptured_winners.append(gap)
            if gap > abs(d(trade.get("return_pct"))):
                material_giveback_winners.append(gap)

    exits: dict[str, dict[str, Any]] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade.get("exit_reason") or "unknown")].append(trade)
    for reason, values in grouped.items():
        pnls = [d(row.get("realized_pnl")) for row in values]
        exits[reason] = {
            "count": len(values),
            "realized_pnl": sum(pnls, ZERO),
            "wins": sum(1 for pnl in pnls if pnl > ZERO),
            "losses": sum(1 for pnl in pnls if pnl < ZERO),
            "average_mfe_pct": _mean([d(row.get("mfe_pct")) for row in values if row.get("mfe_pct") is not None]),
            "average_mae_pct": _mean([d(row.get("mae_pct")) for row in values if row.get("mae_pct") is not None]),
        }

    per_session = []
    for record in records:
        payload = record["payload"]
        session = str(payload["session"])
        session_trades = [
            {**trade, "session": session}
            for trade in payload.get("trades") or []
            if isinstance(trade, dict)
        ]
        per_session.append(
            {
                "session": session,
                "trades": len(session_trades),
                "mfe": _distribution([d(t.get("mfe_pct")) for t in session_trades if t.get("mfe_pct") is not None]),
                "mae": _distribution([d(t.get("mae_pct")) for t in session_trades if t.get("mae_pct") is not None]),
            }
        )

    canonical_positions = list(inputs.get("positions") or [])
    target_touched = sum(1 for row in canonical_positions if row.get("target_touched") is True)
    stop_touched = sum(1 for row in canonical_positions if row.get("stop_touched") is True)

    execution = inputs.get("canonical_period_summary") or {}
    execution_quality = execution.get("execution_quality") or {}

    return {
        "source": "canonical daily report trades; canonical positions used only for deeper post-trade fields",
        "mfe_distribution": _distribution(mfes),
        "mae_distribution": _distribution(maes),
        "winner_mfe_distribution": _distribution(winner_mfe),
        "winner_mae_distribution": _distribution(winner_mae),
        "loser_mfe_distribution": _distribution(loser_mfe),
        "loser_mae_distribution": _distribution(loser_mae),
        "time_in_trade_minutes": _distribution(holds),
        "losses_that_moved_favorably_first": len(losses_first_favorable),
        "winners_exited_before_available_mfe": len(uncaptured_winners),
        "winners_where_uncaptured_mfe_exceeded_realized_gain": len(material_giveback_winners),
        "uncaptured_winner_mfe_gap_distribution": _distribution(uncaptured_winners),
        "target_touched_positions": target_touched,
        "stop_touched_positions": stop_touched,
        "exit_reason_breakdown": exits,
        "execution_shortfall": execution_quality,
        "by_session": per_session,
    }


def _candidate_analysis(inputs: dict[str, Any]) -> dict[str, Any]:
    daily = list(inputs.get("candidate_by_session") or [])
    evaluated = sum(int(row.get("evaluated") or 0) for row in daily)
    rejected = sum(int(row.get("rejected") or 0) for row in daily)
    qualified = sum(int(row.get("qualified") or 0) for row in daily)
    signals = sum(int(row.get("signals") or 0) for row in daily)
    partial_backfill = sum(int(row.get("partial_backfill") or 0) for row in daily)

    reason_totals: Counter[str] = Counter()
    reason_by_day: dict[str, dict[str, int]] = defaultdict(dict)
    for row in inputs.get("rejection_reasons") or []:
        reason = str(row.get("reason") or "unspecified")
        count = int(row.get("count") or 0)
        session = str(row.get("session") or "")
        reason_totals[reason] += count
        if session:
            reason_by_day[session][reason] = count

    gates = []
    for row in inputs.get("gate_rates") or []:
        total = int(row.get("total") or 0)
        passed = int(row.get("passed") or 0)
        gates.append(
            {
                **row,
                "pass_rate": (Decimal(passed) / Decimal(total)) if total else None,
            }
        )

    forward = list(inputs.get("forward_outcomes") or [])
    complete_forward = sum(int(row.get("complete") or 0) for row in forward)

    population = [
        {
            "session": row.get("session"),
            "evaluated": int(row.get("evaluated") or 0),
            "qualified": int(row.get("qualified") or 0),
            "rejected": int(row.get("rejected") or 0),
            "signals": int(row.get("signals") or 0),
        }
        for row in daily
    ]

    return {
        "evaluated_candidates": evaluated,
        "rejected_candidates": rejected,
        "qualified_candidates": qualified,
        "signals": signals,
        "partial_backfill_candidates": partial_backfill,
        "rejection_reason_frequencies": dict(reason_totals),
        "rejection_reason_frequencies_by_day": dict(reason_by_day),
        "gate_pass_fail_rates": gates,
        "candidate_population_by_session": population,
        "population_shift_assessment": (
            "requires at least two sessions"
            if len(population) < 2
            else "compare the per-session counts directly; no cross-session conclusion is inferred automatically"
        ),
        "forward_outcomes": forward,
        "forward_outcomes_complete": complete_forward,
        "post_event_only": True,
    }


def _operational_health(inputs: dict[str, Any]) -> dict[str, Any]:
    incidents = list(inputs.get("incidents") or [])
    runtime = list(inputs.get("operational_by_session") or [])
    duplicates = dict(inputs.get("duplicate_checks") or {})
    return {
        "availability_measurable": False,
        "availability_note": "runtime start/stop events are retained, but complete session-level uptime coverage is not reconstructable for all historical sessions",
        "runtime_by_session": runtime,
        "incidents": incidents,
        "incident_count": len(incidents),
        "failed_report_generations": sum(int(row.get("report_failures") or 0) for row in runtime),
        "reconciliation_failures": sum(int(row.get("reconciliation_failures") or 0) for row in runtime),
        "runtime_errors": sum(int(row.get("runtime_errors") or 0) for row in runtime),
        "execution_failures": sum(int(row.get("execution_failures") or 0) for row in runtime),
        "stale_market_data_events": sum(int(row.get("stale_market_data_events") or 0) for row in runtime),
        "duplicate_telemetry": duplicates,
    }


def _observation(
    observation_id: str,
    statement: str,
    *,
    supporting_sessions: int,
    contradicting_sessions: int,
    available_sessions: int,
    sample_count: int,
    magnitude: Any = None,
    symbols: int | None = None,
    market_conditions_stable: bool | None = None,
    likely_type: str,
    operationally_confirmed: bool = False,
) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "statement": statement,
        "evidence_strength": evidence_strength(
            supporting_sessions=supporting_sessions,
            contradicting_sessions=contradicting_sessions,
            available_sessions=available_sessions,
            sample_count=sample_count,
            operationally_confirmed=operationally_confirmed,
        ),
        "supporting_sessions": supporting_sessions,
        "contradicting_sessions": contradicting_sessions,
        "sample_count": sample_count,
        "magnitude": magnitude,
        "stable_across_symbols": symbols is not None and symbols > 1,
        "symbol_count": symbols,
        "stable_across_market_conditions": market_conditions_stable,
        "likely_type": likely_type,
    }


def _findings(
    records: list[dict[str, Any]],
    performance: dict[str, Any],
    trade_quality: dict[str, Any],
    candidates: dict[str, Any],
    operations: dict[str, Any],
    completeness: str,
    missing_sessions: list[str],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    available = len(records)
    session_pnl = _daily_metric(records, "realized_pnl")
    negative_sessions = sum(1 for _, value in session_pnl if value < ZERO)
    positive_sessions = sum(1 for _, value in session_pnl if value > ZERO)
    trades = _daily_trades(records)
    symbols = len({str(row.get("symbol") or "") for row in trades if row.get("symbol")})

    observations = [
        _observation(
            "weekly-pnl-direction",
            "Observed daily-report performance was net negative.",
            supporting_sessions=negative_sessions,
            contradicting_sessions=positive_sessions,
            available_sessions=available,
            sample_count=int(performance.get("completed_trades") or 0),
            magnitude=performance.get("weekly_realized_pnl"),
            symbols=symbols,
            market_conditions_stable=None,
            likely_type="strategy-related",
        )
    ]

    thesis = trade_quality.get("exit_reason_breakdown", {}).get("thesis_failure") or {}
    if int(thesis.get("count") or 0):
        supporting = 0
        contradicting = 0
        for record in records:
            rows = [
                row for row in record["payload"].get("trades") or []
                if isinstance(row, dict) and str(row.get("exit_reason") or "") == "thesis_failure"
            ]
            if not rows:
                continue
            pnl = sum((d(row.get("realized_pnl")) for row in rows), ZERO)
            if pnl < ZERO:
                supporting += 1
            else:
                contradicting += 1
        observations.append(
            _observation(
                "thesis-failure-negative",
                "Thesis-failure exits were associated with negative realized P&L in the observed sessions.",
                supporting_sessions=supporting,
                contradicting_sessions=contradicting,
                available_sessions=available,
                sample_count=int(thesis.get("count") or 0),
                magnitude=thesis.get("realized_pnl"),
                symbols=len({
                    str(row.get("symbol") or "")
                    for row in trades
                    if str(row.get("exit_reason") or "") == "thesis_failure"
                }),
                market_conditions_stable=None,
                likely_type="strategy-related",
            )
        )

    incident_sessions = {
        str(row.get("session") or "")
        for row in operations.get("incidents") or []
        if row.get("session")
    }
    if operations.get("incident_count"):
        observations.append(
            _observation(
                "reconciliation-incidents",
                "Broker/canonical reconciliation mismatches occurred and were durably recorded.",
                supporting_sessions=len(incident_sessions),
                contradicting_sessions=0,
                available_sessions=available,
                sample_count=int(operations.get("incident_count") or 0),
                magnitude=int(operations.get("incident_count") or 0),
                symbols=None,
                market_conditions_stable=None,
                likely_type="operational",
                operationally_confirmed=True,
            )
        )

    repeated = [
        item for item in observations
        if item["evidence_strength"] in {
            "repeated across multiple sessions",
            "persistent across most available sessions",
            "operationally confirmed",
        }
    ]
    contradictory = [
        item for item in observations
        if item["evidence_strength"] == "contradicted by other sessions"
    ]

    limitations = []
    if completeness != "COMPLETE":
        limitations.append(
            {
                "type": "daily_report_coverage",
                "message": f"Weekly report is {completeness}; missing canonical daily reports for {len(missing_sessions)} expected sessions.",
                "missing_sessions": missing_sessions,
            }
        )
    if int(candidates.get("partial_backfill_candidates") or 0):
        limitations.append(
            {
                "type": "candidate_backfill",
                "message": "Historical candidate telemetry is a qualified-signal backfill; rejected opportunities for that history cannot be reconstructed.",
            }
        )
    if not int(candidates.get("forward_outcomes_complete") or 0):
        limitations.append(
            {
                "type": "forward_outcomes",
                "message": "No complete rejected/qualified candidate forward-outcome sample is available for this period.",
            }
        )

    questions: list[dict[str, Any]] = []
    thesis_count = int(thesis.get("count") or 0)
    if thesis_count >= 3:
        questions.append(
            {
                "research_question_id": "RQ-THESIS-EXIT-ENTRY-QUALITY",
                "status": "READY_FOR_RESEARCH" if available >= 2 else "MONITOR",
                "evidence_summary": {
                    "sessions_observed": available,
                    "thesis_exit_count": thesis_count,
                    "thesis_exit_realized_pnl": thesis.get("realized_pnl"),
                    "evidence_strength": next(
                        (item["evidence_strength"] for item in observations if item["observation_id"] == "thesis-failure-negative"),
                        "insufficient sample",
                    ),
                },
                "sample_size": thesis_count,
                "question": "Are thesis-failure exits primarily identifying weak entries that were already deteriorating before entry, rather than merely correcting exits late?",
                "why_it_matters": "If the same pre-entry structure recurs across sessions, it is a research problem about entry quality; if it does not, it may be one-session noise.",
                "required_data": [
                    "pre-entry canonical candidate features",
                    "MFE/MAE",
                    "exit reason",
                    "session-level market context",
                    "multiple independent sessions",
                ],
                "linked_experiment": None,
            }
        )

    if int(operations.get("incident_count") or 0) >= 2:
        questions.append(
            {
                "research_question_id": "RQ-RECONCILIATION-RECURRENCE",
                "status": "MONITOR",
                "evidence_summary": {
                    "incident_count": operations.get("incident_count"),
                    "sessions_affected": len(incident_sessions),
                    "all_recorded_as_operational": True,
                },
                "sample_size": int(operations.get("incident_count") or 0),
                "question": "Do broker/canonical reconciliation mismatches recur in later sessions after the repairs already deployed?",
                "why_it_matters": "Repeated recurrence would be an infrastructure defect; absence of recurrence would support treating the September 25 incidents as repaired historical failures.",
                "required_data": [
                    "future reconciliation incidents",
                    "runtime/deployment provenance",
                    "affected symbols",
                    "resolved snapshots",
                ],
                "linked_experiment": None,
            }
        )

    decisions = [
        {
            "decision_key": "continue-live-strategy-unchanged",
            "evidence": {
                "completeness": completeness,
                "included_sessions": available,
                "completed_trades": performance.get("completed_trades"),
                "weekly_realized_pnl": performance.get("weekly_realized_pnl"),
            },
            "interpretation": "Weekly evidence is descriptive and does not meet a promotion or retuning gate.",
            "decision": "Continue observing the current live strategy unchanged while accumulating canonical cross-session evidence.",
            "scope": "weekly operating state",
            "production_behavior_changed": False,
        }
    ]
    if operations.get("incident_count"):
        decisions.append(
            {
                "decision_key": "monitor-reconciliation-recurrence",
                "evidence": {
                    "incident_count": operations.get("incident_count"),
                    "sessions_affected": len(incident_sessions),
                },
                "interpretation": "The incidents are operational evidence and should not be conflated with trading-strategy performance.",
                "decision": "Monitor for recurrence; do not alter strategy parameters in response to these incidents.",
                "scope": "operational reliability",
                "production_behavior_changed": False,
            }
        )

    findings = {
        "confirmed_facts": [
            {
                "fact": "Canonical daily reports included",
                "value": available,
            },
            {
                "fact": "Expected trading sessions",
                "value": available + len(missing_sessions),
            },
            {
                "fact": "Completed trades in included daily reports",
                "value": performance.get("completed_trades"),
            },
            {
                "fact": "Weekly realized P&L from included daily reports",
                "value": performance.get("weekly_realized_pnl"),
            },
        ],
        "repeated_patterns": repeated,
        "contradictory_evidence": contradictory,
        "operational_defects": [
            {
                "incident_type": row.get("incident_type"),
                "severity": row.get("severity"),
                "session": row.get("session"),
                "message": row.get("message"),
                "resolved_at": row.get("resolved_at"),
            }
            for row in operations.get("incidents") or []
        ],
        "data_quality_limitations": limitations,
        "research_hypotheses": [
            {
                "research_question_id": row["research_question_id"],
                "question": row["question"],
                "status": row["status"],
            }
            for row in questions
        ],
        "questions_requiring_more_data": [
            "Cross-session persistence cannot be established from a single canonical daily report."
            if available < 2 else None,
            "Rejected-candidate counterfactual quality requires completed forward outcomes."
            if not int(candidates.get("forward_outcomes_complete") or 0) else None,
        ],
        "locked_decisions_not_open_for_retuning": [
            {
                "family": family,
                "status": "permanently rejected under edge-corpus-v1",
            }
            for family in LOCKED_EDGE_V1_FAMILIES
        ] + [
            {
                "research_direction": "Residual Downshock Rebound v2.1",
                "status": "selected next direction; not implemented or run by this report",
            }
        ],
    }
    findings["questions_requiring_more_data"] = [
        item for item in findings["questions_requiring_more_data"] if item
    ]
    return findings, questions, decisions


def build_weekly_report(
    inputs: dict[str, Any],
    calendar: list[dict[str, Any]],
    *,
    period_start: date,
    period_end: date,
    generation_provenance: dict[str, Any],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    records = _daily_records(inputs)
    expected_sessions = _calendar_session_strings(calendar)
    included_sessions = [str(record["payload"]["session"]) for record in records]
    missing_sessions = [session for session in expected_sessions if session not in included_sessions]

    earliest_daily = inputs.get("earliest_daily_session")
    if not records:
        completeness = "INCOMPLETE"
    elif not missing_sessions:
        completeness = "COMPLETE"
    elif earliest_daily and all(session < str(earliest_daily) for session in missing_sessions):
        completeness = "PARTIAL"
    else:
        completeness = "INCOMPLETE"

    source_ids = [str(record.get("event_id") or "") for record in records if record.get("event_id")]
    source_material = {
        "version": REPORT_VERSION,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "daily_report_ids": sorted(source_ids),
        "data_cutoff": inputs.get("data_cutoff"),
    }
    fingerprint = hashlib.sha256(
        json.dumps(source_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report_key = (
        f"{period_start.isoformat()}:{period_end.isoformat()}:"
        f"{REPORT_VERSION}:{fingerprint[:16]}"
    )

    performance = _performance(records, inputs)
    trade_quality = _trade_quality(records, inputs)
    candidates = _candidate_analysis(inputs)
    operations = _operational_health(inputs)
    findings, questions, decisions = _findings(
        records,
        performance,
        trade_quality,
        candidates,
        operations,
        completeness,
        missing_sessions,
    )

    strategy_versions = [
        str(row.get("version_id"))
        for row in inputs.get("strategy_versions") or []
        if row.get("version_id")
    ]
    run_ids = [
        str(row.get("run_id"))
        for row in inputs.get("runs") or []
        if row.get("run_id")
    ]

    warnings = list(inputs.get("warnings") or [])
    if completeness != "COMPLETE":
        warnings.append(
            f"{completeness} weekly evidence: {len(missing_sessions)} expected session(s) lack a canonical daily report."
        )
    if candidates.get("partial_backfill_candidates"):
        warnings.append(
            "Candidate/rejection analysis is limited by historical qualified-signal backfill."
        )
    if not candidates.get("forward_outcomes_complete"):
        warnings.append(
            "Candidate counterfactual analysis is incomplete because forward outcomes are not available."
        )
    if len(strategy_versions) > 1:
        warnings.append(
            "Multiple strategy versions operated during the period; strategy-specific results are kept separate."
        )

    negative_sessions = sum(
        1 for _, value in _daily_metric(records, "realized_pnl") if value < ZERO
    )
    positive_sessions = sum(
        1 for _, value in _daily_metric(records, "realized_pnl") if value > ZERO
    )
    weekly_assessment = {
        "operating_state": (
            "INVESTIGATE_DATA_COMPLETENESS"
            if completeness == "INCOMPLETE"
            else "CONTINUE_UNCHANGED"
        ),
        "research_state": (
            "INSUFFICIENT_CROSS_SESSION_SAMPLE"
            if len(records) < 2
            else (
                "INVESTIGATE"
                if negative_sessions >= positive_sessions
                else "CONTINUE_OBSERVING"
            )
        ),
        "production_behavior_changed": False,
        "promotion_authorized": False,
        "capital_scaling_authorized": False,
    }

    report = {
        "report_type": "weekly",
        "report_version": REPORT_VERSION,
        "report_key": report_key,
        "source_fingerprint": fingerprint,
        "title": f"Canonical weekly review — {period_end.isoformat()}",
        "summary": (
            f"{completeness} week: {len(included_sessions)}/{len(expected_sessions)} "
            f"expected trading sessions have canonical daily reports."
        ),
        "focus": (
            questions[0]["question"]
            if questions
            else "Accumulate additional canonical sessions before elevating a new research question."
        ),
        "classification": {
            "classification": weekly_assessment["research_state"],
            "reason": "Cross-session evidence stability governs weekly interpretation.",
        },
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "week_start": period_start.isoformat(),
        "week_end": period_end.isoformat(),
        "expected_trading_sessions": expected_sessions,
        "included_trading_sessions": included_sessions,
        "missing_trading_sessions": missing_sessions,
        "shortened_sessions": _shortened_sessions(calendar),
        "included_daily_report_ids": source_ids,
        "strategy_versions": strategy_versions,
        "trading_runs": run_ids,
        "runtime_provenance": inputs.get("runtime_instances") or [],
        "generation_provenance": generation_provenance,
        "data_cutoff": inputs.get("data_cutoff"),
        "completeness_state": completeness,
        "generated_at": generated,
        "performance": performance,
        "metrics": performance,
        "cross_day_trade_quality": trade_quality,
        "candidate_analysis": candidates,
        "live_vs_offline_consistency": {
            "status": "not_available" if not inputs.get("live_offline") else "available",
            "daily_evidence": inputs.get("live_offline") or [],
            "interpretation": (
                "No canonical daily live-vs-offline comparison was stored for the included sessions."
                if not inputs.get("live_offline")
                else "Repeated discrepancies should be evaluated across sessions; no production behavior is changed automatically."
            ),
        },
        "operational_health": operations,
        "evidence_stability": findings,
        "research_questions": questions,
        "weekly_decisions": decisions,
        "canonical_period_summary": inputs.get("canonical_period_summary") or {},
        "data_quality_warnings": warnings,
        "warnings": warnings,
        "weekly_assessment": weekly_assessment,
        "research_gate": {
            "live_promotion_requires_separate_decision": True,
            "weekly_observation_is_not_production_logic": True,
            "future_outcomes_are_post_event_analytics_only": True,
            "rejected_edge_v1_families_remain_closed": True,
            "residual_downshock_rebound_v2_1_started": False,
        },
        "live_configuration_changed": False,
        "promotion_authorized": False,
        "capital_scaling_authorized": False,
    }
    return _serialize(report)
