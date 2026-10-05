from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from graen.crypto.research_v7 import CONTEXT_UNIVERSE

from .strategy_grammar import (
    StrategyManifest,
    build_manifest,
    manifest_from_dict,
    manifest_hash,
    validate_manifest,
)


UTC = timezone.utc
PLANNER_VERSION = "graen.hypothesis-planner.v1"
BASE_CONFIRMATORY_ALPHA = 0.05
CORPUS_DAYS = {
    "development": 60,
    "validation": 30,
    "holdout": 30,
}


def _stamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def online_alpha(search_generation: int) -> float:
    """Simple alpha-spending baseline whose infinite sum is <= BASE_CONFIRMATORY_ALPHA."""
    generation = max(1, int(search_generation))
    return BASE_CONFIRMATORY_ALPHA / (generation * (generation + 1))


def _intervals(exposure: Mapping[str, Any]) -> list[tuple[datetime, datetime]]:
    rows: list[tuple[datetime, datetime]] = []
    for item in exposure.get("inspected_intervals") or []:
        if isinstance(item, Mapping):
            start = _stamp(item.get("start"))
            end = _stamp(item.get("end"))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes)) and len(item) == 2:
            start, end = _stamp(item[0]), _stamp(item[1])
        else:
            continue
        if start and end and start < end:
            rows.append((start, end))
    return rows


def select_uninspected_corpus(
    exposure: Mapping[str, Any],
    *,
    now: datetime,
    earliest: datetime = datetime(2024, 1, 1, tzinfo=UTC),
) -> dict[str, Any]:
    if exposure.get("complete") is not True:
        return {
            "available": False,
            "reason": "complete_exposure_ledger_required",
            "next_eligible_at": None,
        }

    now = now.astimezone(UTC)
    total_days = sum(CORPUS_DAYS.values())
    intervals = _intervals(exposure)
    # Keep one complete UTC day between the confirmatory corpus and now.
    end = datetime(now.year, now.month, now.day, tzinfo=UTC) - timedelta(days=1)
    step = timedelta(days=7)

    while end - timedelta(days=total_days) >= earliest:
        start = end - timedelta(days=total_days)
        overlaps = any(start < right and left < end for left, right in intervals)
        if not overlaps:
            validation_start = start + timedelta(days=CORPUS_DAYS["development"])
            holdout_start = validation_start + timedelta(days=CORPUS_DAYS["validation"])
            return {
                "available": True,
                "reason": "untouched_contiguous_corpus_found",
                "development": [start.isoformat(), validation_start.isoformat()],
                "validation": [validation_start.isoformat(), holdout_start.isoformat()],
                "holdout": [holdout_start.isoformat(), end.isoformat()],
                "corpus_start": start.isoformat(),
                "corpus_end": end.isoformat(),
                "exposure_interval_count": len(intervals),
            }
        end -= step

    latest = max((right for _, right in intervals), default=now)
    next_eligible = max(now + timedelta(days=7), latest + timedelta(days=total_days))
    return {
        "available": False,
        "reason": "no_complete_uninspected_120_day_corpus",
        "next_eligible_at": next_eligible.isoformat(),
        "latest_inspected_end": latest.isoformat(),
        "exposure_interval_count": len(intervals),
    }


def _catalog() -> tuple[StrategyManifest, ...]:
    """Bounded trusted hypothesis space.

    This is intentionally a scientific search space, not a performance-tuned
    optimizer. Every manifest is frozen before its corpus is opened and every
    attempted manifest permanently consumes search exposure/online alpha.
    """
    universe = tuple(CONTEXT_UNIVERSE)
    rows: list[StrategyManifest] = []

    # Cross-asset diffusion: 24 structurally separated points chosen by
    # co-prime index strides so adjacent experiments do not merely nudge one
    # threshold. The mechanism remains constant while horizon, latency,
    # breadth and signal magnitude vary.
    leaders = (0.0025, 0.0035, 0.0050, 0.0075, 0.0100)
    gaps = (0.0010, 0.0015, 0.0025, 0.0035)
    lookbacks = (15, 30, 60, 120, 240)
    holds = (60, 120, 240)
    scans = (5, 10, 15, 30)
    breadths = (2, 3, 4)
    for offset in range(24):
        leader = leaders[offset % len(leaders)]
        gap = gaps[(offset * 3) % len(gaps)]
        lookback = lookbacks[(offset * 2) % len(lookbacks)]
        hold = holds[(offset * 2) % len(holds)]
        scan = scans[(offset * 3 + 1) % len(scans)]
        min_breadth = breadths[(offset * 2) % len(breadths)]
        rows.append(
            build_manifest(
                hypothesis_id=f"AUTO-LL-{offset + 1:02d}",
                family="cross_asset_diffusion",
                mechanism=(
                    "Large BTC moves may diffuse into lower-liquidity crypto "
                    "assets with a measurable lag when information incorporation "
                    "is incomplete."
                ),
                information_source="cross_asset_returns",
                feature="lead_lag_gap",
                transformation="residualize_btc",
                regime="dispersion_bucket",
                trigger="threshold",
                entry=(
                    "market_next_bar"
                    if offset % 3 == 0
                    else "delayed_market"
                ),
                exit={
                    60: "time_60m",
                    120: "time_120m",
                    240: "time_240m",
                }[hold],
                parameters={
                    "hold_minutes": hold,
                    "scan_minutes": scan,
                    "lookback_minutes": lookback,
                    "concentration_limit": 0.70,
                    "leader_symbol": "BTC/USD",
                    "leader_threshold": leader,
                    "lag_gap_threshold": gap,
                    "min_target_return": -0.005,
                    "max_target_return": max(leader, 0.0035),
                    "min_breadth_positive": min_breadth,
                    "require_btc_nonnegative": bool(offset % 4 == 3),
                },
                symbols=universe,
                timeframe="5m",
                falsification_statement=(
                    "Reject if stressed-cost expectancy is nonpositive, "
                    "confirmatory evidence fails online alpha spending, delayed "
                    "execution fails, or results are excessively concentrated."
                ),
                cost_model="stressed_high",
            )
        )

    # Breadth-laggard response: full 4 x 2 x 2 structural grid. Scan cadence,
    # breadth and market floor are deterministically tied to the structural
    # point; they are never selected from observed returns.
    breadth_index = 0
    for lookback in (15, 30, 60, 120):
        for hold in (60, 120):
            for gap in (0.0015, 0.0030):
                breadth_index += 1
                scan = {15: 10, 30: 15, 60: 30, 120: 60}[lookback]
                strict = gap >= 0.0030
                rows.append(
                    build_manifest(
                        hypothesis_id=f"AUTO-BR-{breadth_index:02d}",
                        family="breadth_laggard_response",
                        mechanism=(
                            "When broad crypto participation is positive but "
                            "individual assets lag the cross-sectional move, "
                            "bounded catch-up may persist after stressed costs."
                        ),
                        information_source="cross_asset_returns",
                        feature="cross_sectional_breadth",
                        transformation="rank",
                        regime="breadth_bucket",
                        trigger="threshold",
                        entry="market_next_bar",
                        exit={
                            60: "time_60m",
                            120: "time_120m",
                        }[hold],
                        parameters={
                            "hold_minutes": hold,
                            "scan_minutes": scan,
                            "lookback_minutes": lookback,
                            "concentration_limit": 0.55,
                            "lag_gap_threshold": gap,
                            "min_target_return": -0.01,
                            "max_target_return": 0.05,
                            "min_breadth_positive": 4 if strict else 3,
                            "market_threshold": 0.002 if strict else 0.0,
                        },
                        symbols=universe,
                        timeframe="5m",
                        falsification_statement=(
                            "Reject if broad-market laggard response has "
                            "nonpositive stressed-cost expectancy, fails "
                            "dependence-aware confirmation, or is concentrated "
                            "in one symbol."
                        ),
                        cost_model="stressed_high",
                    )
                )
    return tuple(rows)


def _prior_manifests(snapshot: Mapping[str, Any]) -> list[StrategyManifest]:
    manifests: list[StrategyManifest] = []
    seen: set[str] = set()
    for artifact in snapshot.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        if str(artifact.get("artifact_type") or "") != "CRYPTO_STRATEGY_MANIFEST_V1":
            continue
        content = artifact.get("content")
        if not isinstance(content, Mapping):
            continue
        payload = content.get("manifest")
        if not isinstance(payload, Mapping):
            continue
        try:
            manifest = manifest_from_dict(payload)
        except (TypeError, ValueError):
            continue
        digest = manifest_hash(manifest)
        if digest not in seen:
            manifests.append(manifest)
            seen.add(digest)
    return manifests


def _structural_signature(manifest: StrategyManifest) -> tuple[Any, ...]:
    params = dict(manifest.parameters)
    return (
        manifest.family,
        manifest.information_source,
        manifest.feature,
        manifest.transformation,
        manifest.regime,
        manifest.trigger,
        manifest.entry,
        manifest.exit,
        params.get("lookback_minutes"),
        params.get("hold_minutes"),
        params.get("scan_minutes"),
        params.get("leader_threshold"),
        params.get("lag_gap_threshold"),
        params.get("min_breadth_positive"),
        params.get("market_threshold"),
        params.get("require_btc_nonnegative"),
    )


def _structural_distance(left: StrategyManifest, right: StrategyManifest) -> float:
    a = _structural_signature(left)
    b = _structural_signature(right)
    return sum(x != y for x, y in zip(a, b)) / len(a)


def _information_value(
    candidate: StrategyManifest,
    prior: Sequence[StrategyManifest],
) -> dict[str, Any]:
    same_family = [row for row in prior if row.family == candidate.family]
    family_exposure = len(same_family)
    novelty = (
        1.0
        if not prior
        else min(_structural_distance(candidate, row) for row in prior)
    )
    within_family_novelty = (
        1.0
        if not same_family
        else min(
            _structural_distance(candidate, row)
            for row in same_family
        )
    )
    # Coverage reward is intentionally independent of any observed return.
    family_coverage = 1.0 / (1.0 + family_exposure)
    score = (
        0.50 * within_family_novelty
        + 0.30 * novelty
        + 0.20 * family_coverage
    )
    return {
        "score": score,
        "structural_novelty": novelty,
        "within_family_novelty": within_family_novelty,
        "family_exposure": family_exposure,
        "family_coverage": family_coverage,
        "uses_backtest_performance": False,
    }


def _select_next_manifest(
    candidates: Sequence[StrategyManifest],
    *,
    prior_hashes: set[str],
    prior_manifests: Sequence[StrategyManifest],
) -> tuple[StrategyManifest | None, dict[str, Any] | None]:
    unseen = [
        manifest
        for manifest in candidates
        if manifest_hash(manifest) not in prior_hashes
    ]
    if not unseen:
        return None, None
    scored = [
        (manifest, _information_value(manifest, prior_manifests))
        for manifest in unseen
    ]
    scored.sort(
        key=lambda item: (
            -float(item[1]["score"]),
            int(item[1]["family_exposure"]),
            item[0].hypothesis_id,
        )
    )
    return scored[0]


def previously_frozen_manifest_hashes(snapshot: Mapping[str, Any]) -> set[str]:
    hashes: set[str] = set()
    for artifact in snapshot.get("artifacts") or []:
        if not isinstance(artifact, Mapping):
            continue
        if str(artifact.get("artifact_type") or "") not in {
            "CRYPTO_STRATEGY_MANIFEST_V1",
            "CRYPTO_STRATEGY_PLANNER_DECISION",
        }:
            continue
        content = artifact.get("content")
        if not isinstance(content, Mapping):
            continue
        value = str(content.get("manifest_hash") or "")
        if len(value) == 64:
            hashes.add(value)
    return hashes


def plan_next(
    snapshot: Mapping[str, Any],
    exposure: Mapping[str, Any],
    *,
    now: datetime,
) -> dict[str, Any]:
    prior_hashes = previously_frozen_manifest_hashes(snapshot)
    prior_hashes.update(
        str(value)
        for value in exposure.get("strategy_manifest_hashes") or []
        if len(str(value)) == 64
    )
    catalog = _catalog()
    prior_manifests = _prior_manifests(snapshot)
    candidate, information_value = _select_next_manifest(
        catalog,
        prior_hashes=prior_hashes,
        prior_manifests=prior_manifests,
    )
    if candidate is None:
        # The deterministic no-model catalog is finite by design. Re-searching it would
        # turn null results into parameter mining. A model-backed director or a new
        # trusted primitive is now a real capability boundary.
        return {
            "planner_version": PLANNER_VERSION,
            "state": "ENGINEERING_REQUIRED",
            "reason": "trusted_hypothesis_catalog_exhausted",
            "catalog_size": len(catalog),
            "trusted_hypothesis_space_size": len(catalog),
            "prior_manifest_count": len(prior_hashes),
            "capability_required": (
                "Extend the trusted strategy grammar/compiler with a materially new "
                "mechanism or configure the bounded Research Director as an additional "
                "hypothesis source."
            ),
        }

    validation = validate_manifest(candidate)
    if validation["software_change_required"]:
        return {
            "planner_version": PLANNER_VERSION,
            "state": "ENGINEERING_REQUIRED",
            "reason": "hypothesis_requires_new_trusted_compiler",
            "manifest": candidate.as_dict(),
            **validation,
        }

    corpus = select_uninspected_corpus(exposure, now=now)
    if not corpus["available"]:
        return {
            "planner_version": PLANNER_VERSION,
            "state": "WAITING_FOR_UNINSPECTED_CORPUS",
            "reason": corpus["reason"],
            "manifest": candidate.as_dict(),
            "manifest_hash": manifest_hash(candidate),
            "corpus": corpus,
        }

    search_generation = len(prior_hashes) + 1
    return {
        "planner_version": PLANNER_VERSION,
        "state": "READY",
        "reason": "unseen_manifest_and_uninspected_corpus_available",
        "manifest": candidate.as_dict(),
        "manifest_hash": manifest_hash(candidate),
        "compiler_profile": validation["compiler_profile"],
        "corpus": corpus,
        "search_generation": search_generation,
        "validation_alpha": online_alpha(search_generation),
        "selection_policy": (
            "maximin_structural_novelty_without_backtest_performance"
        ),
        "information_value": information_value,
        "trusted_hypothesis_space_size": len(catalog),
        "execution_authority": False,
        "live_execution_authorized": False,
    }
