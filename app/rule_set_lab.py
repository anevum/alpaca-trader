"""Bounded research-only entry rule-set tournament.

This laboratory adds trusted, point-in-time entry conditions to the currently
configured rolling momentum strategy. It never constructs broker clients and
does not have live mutation, promotion, or order authority.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .research_agent.strategy_family_registry import build_strategy_family_registry
from .config import Settings, get_settings
from .replay import ReplayEngine
from .strategy import Signal
from .strategy_lab import build_strategy


ALLOWED_INDICATORS = frozenset({"relative_volume", "trend_persistence"})
METHODOLOGY = "rhen-rule-set-lab-v1"


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("entry rule threshold must be numeric") from exc
    if not result.is_finite():
        raise ValueError("entry rule threshold must be finite")
    return result


@dataclass(frozen=True, slots=True)
class EntryRule:
    indicator: str
    threshold: Decimal
    min_bars: int = 8

    def __post_init__(self) -> None:
        if self.indicator not in ALLOWED_INDICATORS:
            raise ValueError(f"untrusted research rule indicator: {self.indicator}")
        if not 3 <= self.min_bars <= 30:
            raise ValueError("entry rule min_bars must be between 3 and 30")
        if not isinstance(self.threshold, Decimal) or not self.threshold.is_finite():
            raise ValueError("entry rule threshold must be a finite Decimal")
        if self.threshold < 0:
            raise ValueError("entry rule threshold must be nonnegative")
        if self.indicator == "trend_persistence" and self.threshold > 1:
            raise ValueError("trend persistence threshold must be at most 1")
        if self.indicator == "trend_persistence" and self.min_bars > 9:
            raise ValueError("trend persistence uses at most nine completed bars")
        if self.indicator == "relative_volume" and self.threshold > 10:
            raise ValueError("relative-volume threshold must be at most 10")


@dataclass(frozen=True, slots=True)
class RuleSetSpec:
    name: str
    hypothesis: str
    entry_rules: tuple[EntryRule, ...]

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.name):
            raise ValueError("rule-set name must be a lowercase stable slug")
        if not self.hypothesis.strip() or len(self.hypothesis) > 600:
            raise ValueError("every challenger needs a bounded hypothesis")
        if not self.entry_rules or len(self.entry_rules) > 3:
            raise ValueError("challenger must have 1-3 entry rules")
        names = [rule.indicator for rule in self.entry_rules]
        if len(names) != len(set(names)):
            raise ValueError("duplicate entry rule indicators are not allowed")

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "hypothesis": self.hypothesis,
            "entry_rules": [
                {
                    "indicator": rule.indicator,
                    "threshold": str(rule.threshold),
                    "min_bars": rule.min_bars,
                }
                for rule in self.entry_rules
            ],
        }


def parse_rule_sets(values: Sequence[Mapping[str, Any]]) -> tuple[RuleSetSpec, ...]:
    if len(values) > 8:
        raise ValueError("bounded rule-set search may test at most 8 challengers")
    specs: list[RuleSetSpec] = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError("rule-set specs must be mappings")
        raw_rules = value.get("entry_rules")
        if not isinstance(raw_rules, list):
            raise ValueError("entry_rules must be a list")
        entries = []
        for row in raw_rules:
            if not isinstance(row, Mapping):
                raise ValueError("entry rule must be an object")
            entries.append(EntryRule(
                indicator=str(row.get("indicator") or ""),
                threshold=_decimal(row.get("threshold")),
                min_bars=int(row.get("min_bars", 8)),
            ))
        specs.append(RuleSetSpec(
            name=str(value.get("name") or "").strip(),
            hypothesis=str(value.get("hypothesis") or "").strip(),
            entry_rules=tuple(entries),
        ))
    names = [row.name for row in specs]
    if len(names) != len(set(names)) or "production" in names:
        raise ValueError("rule-set names must be unique and not 'production'")
    return tuple(specs)


def _indicator_value(rule: EntryRule, bars: list[dict[str, Any]]) -> Decimal | None:
    # The caller supplies only completed bars visible at the evaluation time.
    # Missing/invalid observations fail closed instead of receiving an
    # optimistic synthetic default for the research rule.
    window = bars[-max(rule.min_bars, 21):]
    if len(window) < rule.min_bars:
        return None
    if rule.indicator == "relative_volume":
        history = window[-21:]
        try:
            samples = [_decimal(row.get("v")) for row in history]
        except ValueError:
            return None
        if len(samples) < rule.min_bars or any(v <= 0 for v in samples):
            return None
        reference = sum(samples[:-1], Decimal("0")) / (len(samples) - 1)
        return samples[-1] / reference if reference > 0 else None
    if rule.indicator == "trend_persistence":
        try:
            closes = [_decimal(row.get("c")) for row in window[-9:]]
        except ValueError:
            return None
        if len(closes) < rule.min_bars or any(c <= 0 for c in closes):
            return None
        ups = sum(1 for before, after in zip(closes, closes[1:]) if after > before)
        return Decimal(ups) / Decimal(len(closes) - 1)
    raise ValueError("unrecognized research indicator")


class EntryRuleFilteredStrategy:
    """Add research-only AND gates without mutating production strategy code."""

    def __init__(self, production_strategy: Any, spec: RuleSetSpec):
        self.production_strategy = production_strategy
        self.spec = spec

    def evaluate(
        self,
        *,
        bars: list[dict[str, Any]],
        confirmation_bars: dict[str, list[dict[str, Any]]],
        symbol: str,
        has_position: bool,
        order_notional: Decimal,
        now: Any,
    ) -> Signal:
        signal = self.production_strategy.evaluate(
            bars=bars,
            confirmation_bars=confirmation_bars,
            symbol=symbol,
            has_position=has_position,
            order_notional=order_notional,
            now=now,
        )
        if signal.action != "buy":
            return signal
        diagnostics = []
        for rule in self.spec.entry_rules:
            observed = _indicator_value(rule, bars)
            passed = observed is not None and observed >= rule.threshold
            diagnostics.append({
                "indicator": rule.indicator,
                "threshold": str(rule.threshold),
                "observed": str(observed) if observed is not None else None,
                "min_bars": rule.min_bars,
                "passed": passed,
            })
            if not passed:
                metadata = dict(signal.metadata or {})
                metadata.update({
                    "research_rule_rejected": True,
                    "rule_set": self.spec.name,
                    "rule_diagnostics": diagnostics,
                })
                return Signal(
                    action="hold",
                    symbol=symbol,
                    reason="research-only entry rule rejected or insufficient data",
                    metadata=metadata,
                )
        signal.metadata = dict(signal.metadata or {})
        signal.metadata["research_rule_set"] = self.spec.name
        signal.metadata["research_rule_diagnostics"] = diagnostics
        return signal


def run_rule_set_tournament(
    *,
    settings: Settings,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    rule_sets: Sequence[RuleSetSpec],
    initial_equity: Decimal,
    spread_bps: Decimal,
    slippage_bps: Decimal,
    universe_snapshots: dict[str, list[str]] | None = None,
    min_trades: int = 20,
) -> dict[str, Any]:
    if settings.strategy_name != "rolling_momentum_vwap":
        raise ValueError("entry rule lab is restricted to rolling momentum/VWAP")
    if not 1 <= min_trades <= 10000:
        raise ValueError("min_trades must be positive and bounded")
    if len(rule_sets) > 8 or len({s.name for s in rule_sets}) != len(rule_sets):
        raise ValueError("invalid bounded rule-set slate")
    for spec in rule_sets:
        if not isinstance(spec, RuleSetSpec) or spec.name == "production":
            raise ValueError("all challengers must be validated rule-set specs")

    specs = [
        {"name": "production", "hypothesis": "Frozen production control", "entry_rules": []},
        *[item.canonical() for item in rule_sets],
    ]
    manifest = {
        "methodology": METHODOLOGY,
        "strategy_name": settings.strategy_name,
        "strategy_version_id": getattr(settings, "strategy_version_id", None),
        "frozen_settings": {
            "min_quality_score": str(settings.min_quality_score),
            "stop_pct": str(settings.stop_pct),
            "target_pct": str(settings.target_pct),
            "max_hold_minutes": settings.max_hold_minutes,
            "reentry_cooldown_minutes": settings.reentry_cooldown_minutes,
            "thesis_failure_cycles": settings.thesis_failure_cycles,
            "thesis_exit_enabled": settings.thesis_exit_enabled,
            "profit_protect_enabled": settings.profit_protect_enabled,
        },
        "rules": specs,
        "initial_equity": str(initial_equity),
        "spread_bps": str(spread_bps),
        "slippage_bps_per_side": str(slippage_bps),
        "min_trades": min_trades,
        "data_fingerprint": sha256(
            json.dumps(
                bars_by_symbol, sort_keys=True, separators=(",", ":"), default=str
            ).encode()
        ).hexdigest(),
        "asof_universe_fingerprint": (
            sha256(
                json.dumps(
                    universe_snapshots, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            if universe_snapshots is not None else None
        ),
    }
    fingerprint = sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    results: list[dict[str, Any]] = []
    control_net: Decimal | None = None
    for spec in (None, *rule_sets):
        production = build_strategy(settings)
        strategy = EntryRuleFilteredStrategy(production, spec) if spec else production
        replay = ReplayEngine(settings, strategy)
        result = replay.run(
            bars_by_symbol,
            initial_equity=initial_equity,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            universe_snapshots=universe_snapshots,
        )
        summary = result["summary"]
        net = Decimal(str(summary["net_pnl"]))
        if spec is None:
            control_net = net
        results.append({
            "name": spec.name if spec else "production",
            "spec": spec.canonical() if spec else specs[0],
            "summary": summary,
            "assumptions": result["assumptions"],
            "session_count": result["sessions"],
            "descriptive_min_trade_floor_passed": int(summary["trades"]) >= min_trades,
            "net_pnl_delta_to_control": str(net - control_net),
            "validated_alpha": False,
            "promotion_authorized": False,
        })
    registered_families = build_strategy_family_registry([
        {
            "family_key": f"rhen-rule-{spec.name}",
            "strategy_name": settings.strategy_name,
            "direction": "LONG",
            "asset_class": "US_EQUITY",
            "status": "SPEC_ONLY",
            "parent_family_key": "rhen-long-momentum-v1",
            "hypothesis": spec.hypothesis,
            "research_execution_authority": False,
            "automatic_promotion_authorized": False,
        }
        for spec in rule_sets
    ])
    return {
        "methodology": METHODOLOGY,
        "fingerprint": fingerprint,
        "manifest": manifest,
        "research_family_registry": registered_families,
        "results": results,
        "research_only": True,
        "automatic_promotion_authorized": False,
        "live_strategy_mutation": False,
        "status": "EXPLORATORY_NOT_VALIDATED",
        "warnings": [
            "A favorable paired backtest is not an out-of-sample validation.",
            "Do not promote without frozen holdout, cost stress, and broker-fill parity.",
            "All tested variants count toward the search/multiplicity ledger.",
        ],
    }


def main() -> None:
    cli = argparse.ArgumentParser(description="Offline RHEN rule-set comparison; never places orders.")
    cli.add_argument("--bars-json", required=True, help="JSON mapping of symbol to completed historical bars.")
    cli.add_argument("--rules-json", required=True, help="Frozen JSON array of trusted research challengers.")
    cli.add_argument("--universe-snapshots-json", help="Required for dynamic universe: time-indexed symbols.")
    cli.add_argument("--output", required=True, help="Where to write the immutable research report.")
    cli.add_argument("--initial-equity", default="100")
    cli.add_argument("--spread-bps", default="5")
    cli.add_argument("--slippage-bps", default="2")
    cli.add_argument("--min-trades", type=int, default=20)
    args = cli.parse_args()

    bars = json.loads(Path(args.bars_json).read_text(encoding="utf-8"))
    rules = json.loads(Path(args.rules_json).read_text(encoding="utf-8"))
    universe = (
        json.loads(Path(args.universe_snapshots_json).read_text(encoding="utf-8"))
        if args.universe_snapshots_json else None
    )
    if not isinstance(bars, dict) or not isinstance(rules, list):
        raise ValueError("input JSON must contain a symbol-bar mapping and rule list")
    result = run_rule_set_tournament(
        settings=get_settings(),
        bars_by_symbol=bars,
        rule_sets=parse_rule_sets(rules),
        initial_equity=_decimal(args.initial_equity),
        spread_bps=_decimal(args.spread_bps),
        slippage_bps=_decimal(args.slippage_bps),
        universe_snapshots=universe,
        min_trades=args.min_trades,
    )
    Path(args.output).write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
