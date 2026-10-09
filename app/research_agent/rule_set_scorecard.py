"""A bounded, offline, *descriptive* paired-session rule-set scorecard.

Consumes results already produced by app.rule_set_lab on distinct frozen
sessions. Uses the same candidate/market cohort for control and challenger
within each session, and guards chronological development/validation/holdout
boundaries. It never validates alpha or authorizes live strategy changes.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from math import comb
import json
import re
from statistics import median
from typing import Any

SCHEMA = "rhen-ruleset-paired-scorecard-v1"
PHASES = ("DEVELOPMENT", "WALK_FORWARD", "HOLDOUT")
MIN_VALIDATION_SESSIONS = 5
MAX_SESSIONS = 90
SHA_RE = re.compile(r"[a-fA-F0-9]{64}")


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError("paired session must have finite numeric P/L")
    try:
        result = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ValueError("paired session has nonnumeric P/L") from exc
    if not result.is_finite():
        raise ValueError("paired session has nonfinite P/L")
    return result


def _signature(value: Any) -> str:
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()).hexdigest()


def _two_sided_sign(deltas: Sequence[Decimal]) -> Decimal | None:
    wins = sum(d > 0 for d in deltas)
    losses = sum(d < 0 for d in deltas)
    trials = wins + losses
    if not trials:
        return None
    tail = sum(comb(trials, i) for i in range(min(wins, losses) + 1))
    return min(Decimal("1"), Decimal(2 * tail) / Decimal(2 ** trials))


def _phase_summary(deltas: list[Decimal], *, comparisons: int) -> dict[str, Any]:
    if not deltas:
        return {
            "independent_sessions": 0, "total_delta": None,
            "mean_delta": None, "median_delta": None,
            "positive_sessions": 0, "negative_sessions": 0,
            "sign_test_p": None, "bonferroni_sign_test_p": None,
            "enough_validation_sessions": False,
        }
    p = _two_sided_sign(deltas)
    return {
        "independent_sessions": len(deltas),
        "total_delta": str(sum(deltas, Decimal("0"))),
        "mean_delta": str(sum(deltas, Decimal("0")) / Decimal(len(deltas))),
        "median_delta": str(median(deltas)),
        "positive_sessions": sum(d > 0 for d in deltas),
        "negative_sessions": sum(d < 0 for d in deltas),
        "sign_test_p": str(p) if p is not None else None,
        "bonferroni_sign_test_p": str(min(Decimal("1"), p * comparisons))
        if p is not None else None,
        "enough_validation_sessions": len(deltas) >= MIN_VALIDATION_SESSIONS,
    }


def paired_rule_set_scorecard(
    sessions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate independently frozen daily tournaments with strict provenance.

    Each item: {session: YYYY-MM-DD, phase: DEVELOPMENT/WALK_FORWARD/HOLDOUT,
    source_fingerprint: 64-hex, tournament: output of run_rule_set_tournament}.
    Results are DESCRIPTIVE_ONLY even if after-cost session deltas look strong.
    """
    if (
        not isinstance(sessions, Sequence)
        or isinstance(sessions, (str, bytes))
        or not 1 <= len(sessions) <= MAX_SESSIONS
    ):
        raise ValueError("require 1-90 session-level tournament packages")

    ordered: list[dict[str, Any]] = []
    seen_days: set[str] = set()
    seen_sources: set[str] = set()
    seen_data: set[str] = set()
    expected_settings: str | None = None
    expected_rules: str | None = None
    expected_costs: str | None = None
    variants: tuple[str, ...] | None = None
    last_phase = -1
    for row in sorted(sessions, key=lambda item: str(item.get("session") or "")):
        if not isinstance(row, Mapping):
            raise ValueError("session row must be an object")
        day = str(row.get("session") or "")
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError("invalid date")
        except ValueError as exc:
            raise ValueError("session must be YYYY-MM-DD") from exc
        if day in seen_days:
            raise ValueError("duplicate independent trading session")
        seen_days.add(day)
        phase = str(row.get("phase") or "").upper()
        if phase not in PHASES:
            raise ValueError("each session requires explicit chronological phase")
        phase_id = PHASES.index(phase)
        if phase_id < last_phase:
            raise ValueError("development/holdout chronology violated")
        last_phase = phase_id
        source = str(row.get("source_fingerprint") or "")
        if not SHA_RE.fullmatch(source) or source in seen_sources:
            raise ValueError("missing or duplicated immutable report fingerprint")
        seen_sources.add(source)
        tournament = row.get("tournament")
        if not isinstance(tournament, Mapping):
            raise ValueError("tournament must be the frozen ruleset lab output")
        if (
            tournament.get("methodology") != "rhen-rule-set-lab-v1"
            or tournament.get("research_only") is not True
            or tournament.get("automatic_promotion_authorized") is not False
        ):
            raise ValueError("untrusted or authorized-to-trade tournament")
        manifest = tournament.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("tournament missing reproducible manifest")
        source_data = str(manifest.get("data_fingerprint") or "")
        if not SHA_RE.fullmatch(source_data) or source_data in seen_data:
            raise ValueError("missing or duplicated independently frozen bar data")
        seen_data.add(source_data)
        settings_hash = _signature(manifest.get("frozen_settings"))
        rules_hash = _signature(manifest.get("rules"))
        costs_hash = _signature({
            "initial_equity": manifest.get("initial_equity"),
            "spread_bps": manifest.get("spread_bps"),
            "slippage_bps_per_side": manifest.get("slippage_bps_per_side"),
        })
        if expected_settings is not None and expected_settings != settings_hash:
            raise ValueError("session control settings drifted")
        if expected_rules is not None and expected_rules != rules_hash:
            raise ValueError("unregistered change to tested rule slate")
        if expected_costs is not None and expected_costs != costs_hash:
            raise ValueError("comparison costs or capital were changed")
        expected_settings = settings_hash
        expected_rules = rules_hash
        expected_costs = costs_hash

        result_rows = tournament.get("results")
        if not isinstance(result_rows, list):
            raise ValueError("tournament missing paired rule-set results")
        names = tuple(str(item.get("name") or "") for item in result_rows)
        if not names or names[0] != "production" or len(set(names)) != len(names):
            raise ValueError("production control must precede distinct challengers")
        if variants is not None and names != variants:
            raise ValueError("some sessions evaluated a different challenger slate")
        variants = names
        control = _decimal((result_rows[0].get("summary") or {}).get("net_pnl"))
        net_by_variant = {}
        for item in result_rows:
            if not isinstance(item, Mapping):
                raise ValueError("tournament result must be an object")
            if item.get("validated_alpha") is not False or item.get("promotion_authorized") is not False:
                raise ValueError("scorecard refuses validated or promotion-authorized claims")
            name = str(item["name"])
            net = _decimal((item.get("summary") or {}).get("net_pnl"))
            published_delta = _decimal(item.get("net_pnl_delta_to_control"))
            if abs(published_delta - (net - control)) > Decimal("0.000000001"):
                raise ValueError("paired P/L delta does not reconcile to control")
            net_by_variant[name] = net - control
        ordered.append({
            "session": day,
            "phase": phase,
            "source_fingerprint": source,
            "data_fingerprint": source_data,
            "paired_net_delta": net_by_variant,
            "market_regime": str(row.get("market_regime") or "UNKNOWN")[:64],
        })

    assert variants is not None
    comparisons = len(variants) - 1
    if not 1 <= comparisons <= 8:
        raise ValueError("must compare 1-8 bounded rule challengers")
    rankings = []
    for name in variants[1:]:
        by_phase = {
            phase: _phase_summary(
                [r["paired_net_delta"][name] for r in ordered if r["phase"] == phase],
                comparisons=comparisons,
            )
            for phase in PHASES
        }
        validation = by_phase["WALK_FORWARD"]
        rankings.append({
            "name": name,
            "by_phase": by_phase,
            "validation_mean_delta": validation["mean_delta"],
            "research_readiness": (
                "REQUIRES_MORE_INDEPENDENT_SESSIONS"
                if any(not by_phase[p]["enough_validation_sessions"]
                       for p in ("WALK_FORWARD", "HOLDOUT"))
                else "REQUIRES_BROKER_PARITY_AND_HUMAN_REVIEW"
            ),
            "validated_alpha": False,
            "eligible_for_live_promotion": False,
        })
    # Rank on the walk-forward split, not the untouched holdout.
    rankings.sort(
        key=lambda item: (
            item["validation_mean_delta"] is not None,
            _decimal(item["validation_mean_delta"])
            if item["validation_mean_delta"] is not None
            else Decimal("-Infinity"),
            item["name"],
        ),
        reverse=True,
    )
    package = {
        "schema_version": SCHEMA,
        "session_count": len(ordered),
        "comparison_count": comparisons,
        "chronological_phases": list(PHASES),
        "source_sessions": [
            {
                "session": row["session"],
                "phase": row["phase"],
                "source_fingerprint": row["source_fingerprint"],
                "data_fingerprint": row["data_fingerprint"],
                "market_regime": row["market_regime"],
            }
            for row in ordered
        ],
        "control": variants[0],
        "leaderboard": rankings,
        "ranking_basis": "WALK_FORWARD_SESSION_MEAN_AFTER_FRICTION",
        "statistical_unit": "INDEPENDENT_TRADING_SESSION_NOT_CANDIDATE",
        "holdout_is_untouched_verified": False,
        "broker_fill_and_stop_parity_verified": False,
        "status": "DESCRIPTIVE_RESEARCH_ONLY_NOT_VALIDATED",
        "live_changes_authorized": False,
        "automatic_promotion_authorized": False,
        "warnings": [
            "Do not select hyperparameters using the holdout: labels alone do not prove it was untouched.",
            "Session-level sign tests and Bonferroni adjustment are exploratory; a positive backtest is not evidence of live executable alpha.",
            "Broker fills, spread, protective stops, as-of universe, and market-regime coverage still need independent audits.",
        ],
    }
    package["scorecard_fingerprint"] = _signature(package)
    return package
