from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


GRAMMAR_VERSION = "graen.strategy-grammar.v1"

TRUSTED_PRIMITIVES: dict[str, frozenset[str]] = {
    "information_source": frozenset({
        "bar_returns",
        "cross_asset_returns",
        "activity",
        "volatility",
        "calendar",
        "nostra_regime",
    }),
    "feature": frozenset({
        "absolute_return",
        "residual_return",
        "volatility_normalized_return",
        "persistence",
        "acceleration",
        "activity_ratio",
        "cross_sectional_breadth",
        "cross_sectional_dispersion",
        "lead_lag_gap",
    }),
    "transformation": frozenset({
        "identity",
        "zscore",
        "rank",
        "residualize_btc",
        "rolling_normalize",
        "clip",
    }),
    "regime": frozenset({
        "all",
        "btc_direction",
        "volatility_bucket",
        "breadth_bucket",
        "dispersion_bucket",
        "weekday_weekend",
        "utc_session",
        "nostra_state",
    }),
    "trigger": frozenset({
        "threshold",
        "crossing",
        "rank_top",
        "rank_bottom",
        "persistence_confirmed",
        "pullback_reclaim",
        "delayed_confirmation",
    }),
    "entry": frozenset({
        "market_next_bar",
        "delayed_market",
        "pullback_reclaim",
        "controlled_mean_reversion",
        "abstain",
    }),
    "exit": frozenset({
        "time_60m",
        "time_120m",
        "time_240m",
        "signal_decay",
        "regime_exit",
        "protective_stop_target",
    }),
    "sizing": frozenset({
        "research_fixed_unit",
        "paper_fixed_fraction",
    }),
    "execution": frozenset({
        "stressed_market",
        "market",
        "passive_simulated",
        "paper_broker",
    }),
}


class StrategyGrammarError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StrategyManifest:
    hypothesis_id: str
    family: str
    mechanism: str
    information_source: str
    feature: str
    transformation: str
    regime: str
    trigger: str
    entry: str
    exit: str
    sizing: str
    execution: str
    parameters: Mapping[str, Any]
    symbols: tuple[str, ...]
    timeframe: str
    falsification_statement: str
    cost_model: str
    source_hypotheses: tuple[str, ...] = ()
    grammar_version: str = GRAMMAR_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "grammar_version": self.grammar_version,
            "hypothesis_id": self.hypothesis_id,
            "family": self.family,
            "mechanism": self.mechanism,
            "information_source": self.information_source,
            "feature": self.feature,
            "transformation": self.transformation,
            "regime": self.regime,
            "trigger": self.trigger,
            "entry": self.entry,
            "exit": self.exit,
            "sizing": self.sizing,
            "execution": self.execution,
            "parameters": dict(self.parameters),
            "symbols": list(self.symbols),
            "timeframe": self.timeframe,
            "falsification_statement": self.falsification_statement,
            "cost_model": self.cost_model,
            "source_hypotheses": list(self.source_hypotheses),
        }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def manifest_hash(manifest: StrategyManifest) -> str:
    return sha256(canonical_json(manifest.as_dict()).encode("utf-8")).hexdigest()


def missing_primitives(
    manifest: StrategyManifest,
    *,
    trusted: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, str]:
    registry: Mapping[str, Sequence[str]] = trusted or TRUSTED_PRIMITIVES
    result: dict[str, str] = {}
    for field in (
        "information_source",
        "feature",
        "transformation",
        "regime",
        "trigger",
        "entry",
        "exit",
        "sizing",
        "execution",
    ):
        value = str(getattr(manifest, field))
        supported = set(registry.get(field) or ())
        if value not in supported:
            result[field] = value
    return result


def validate_manifest(
    manifest: StrategyManifest,
    *,
    trusted: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    if manifest.grammar_version != GRAMMAR_VERSION:
        raise StrategyGrammarError("unsupported strategy grammar version")
    required = {
        "hypothesis_id": manifest.hypothesis_id,
        "family": manifest.family,
        "mechanism": manifest.mechanism,
        "timeframe": manifest.timeframe,
        "falsification_statement": manifest.falsification_statement,
        "cost_model": manifest.cost_model,
    }
    missing = [key for key, value in required.items() if not str(value or "").strip()]
    if missing:
        raise StrategyGrammarError("missing required strategy fields: " + ",".join(sorted(missing)))
    if not manifest.symbols or any(not str(symbol).strip() for symbol in manifest.symbols):
        raise StrategyGrammarError("strategy manifest requires a nonempty symbol universe")
    if not isinstance(manifest.parameters, Mapping):
        raise StrategyGrammarError("strategy parameters must be a mapping")

    unsupported = missing_primitives(manifest, trusted=trusted)
    return {
        "schema_version": GRAMMAR_VERSION,
        "manifest_hash": manifest_hash(manifest),
        "trusted_compiler_compatible": not unsupported,
        "missing_primitives": unsupported,
        "software_change_required": bool(unsupported),
        "execution_authority": False,
        "live_execution_authorized": False,
    }


def build_manifest(
    *,
    hypothesis_id: str,
    family: str,
    mechanism: str,
    information_source: str,
    feature: str,
    transformation: str = "identity",
    regime: str = "all",
    trigger: str = "threshold",
    entry: str = "market_next_bar",
    exit: str = "time_120m",
    sizing: str = "research_fixed_unit",
    execution: str = "stressed_market",
    parameters: Mapping[str, Any] | None = None,
    symbols: Sequence[str] = ("BTC/USD",),
    timeframe: str = "5m",
    falsification_statement: str,
    cost_model: str = "stressed_high",
    source_hypotheses: Sequence[str] = (),
) -> StrategyManifest:
    manifest = StrategyManifest(
        hypothesis_id=str(hypothesis_id),
        family=str(family),
        mechanism=str(mechanism),
        information_source=str(information_source),
        feature=str(feature),
        transformation=str(transformation),
        regime=str(regime),
        trigger=str(trigger),
        entry=str(entry),
        exit=str(exit),
        sizing=str(sizing),
        execution=str(execution),
        parameters=dict(parameters or {}),
        symbols=tuple(str(symbol) for symbol in symbols),
        timeframe=str(timeframe),
        falsification_statement=str(falsification_statement),
        cost_model=str(cost_model),
        source_hypotheses=tuple(str(value) for value in source_hypotheses),
    )
    validate_manifest(manifest)
    return manifest
