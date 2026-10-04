from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from graen.crypto.research_v7 import CONTEXT_UNIVERSE

from .strategy_grammar import (
    StrategyManifest,
    build_manifest,
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
    universe = tuple(CONTEXT_UNIVERSE)
    rows: list[StrategyManifest] = []
    lead_lag_variants = (
        # leader threshold, lag gap, lookback, hold, scan
        (0.0035, 0.0015, 30, 60, 10),
        (0.0050, 0.0020, 60, 120, 15),
        (0.0075, 0.0030, 120, 120, 20),
        (0.0100, 0.0040, 240, 240, 30),
    )
    for index, (leader, gap, lookback, hold, scan) in enumerate(lead_lag_variants, start=1):
        rows.append(
            build_manifest(
                hypothesis_id=f"AUTO-LL-{index:02d}",
                family="cross_asset_diffusion",
                mechanism=(
                    "Large BTC moves may be incorporated into lower-liquidity crypto assets "
                    "with a measurable lag when cross-sectional information diffusion is incomplete."
                ),
                information_source="cross_asset_returns",
                feature="lead_lag_gap",
                transformation="residualize_btc",
                regime="dispersion_bucket",
                trigger="threshold",
                entry="delayed_market" if index > 1 else "market_next_bar",
                exit={60: "time_60m", 120: "time_120m", 240: "time_240m"}[hold],
                parameters={
                    "hold_minutes": hold,
                    "scan_minutes": scan,
                    "lookback_minutes": lookback,
                    "concentration_limit": 0.70,
                    "leader_symbol": "BTC/USD",
                    "leader_threshold": leader,
                    "lag_gap_threshold": gap,
                    "min_target_return": -0.005,
                    "max_target_return": leader,
                    "min_breadth_positive": 3,
                    "require_btc_nonnegative": False,
                },
                symbols=universe,
                timeframe="5m",
                falsification_statement=(
                    "Reject if stressed-cost expectancy is nonpositive, confirmatory evidence "
                    "does not survive online alpha spending, delayed execution fails, or results "
                    "are excessively symbol-concentrated."
                ),
                cost_model="stressed_high",
            )
        )

    breadth_variants = (
        (30, 60, 0.0015, 0.0010),
        (60, 120, 0.0025, 0.0020),
        (120, 240, 0.0035, 0.0030),
    )
    for index, (lookback, hold, gap, market) in enumerate(breadth_variants, start=1):
        rows.append(
            build_manifest(
                hypothesis_id=f"AUTO-BR-{index:02d}",
                family="breadth_laggard_response",
                mechanism=(
                    "When broad crypto participation is positive but individual assets lag the "
                    "cross-sectional move, bounded catch-up may persist after stressed costs."
                ),
                information_source="cross_asset_returns",
                feature="cross_sectional_breadth",
                transformation="rank",
                regime="breadth_bucket",
                trigger="threshold",
                entry="market_next_bar",
                exit={60: "time_60m", 120: "time_120m", 240: "time_240m"}[hold],
                parameters={
                    "hold_minutes": hold,
                    "scan_minutes": 30,
                    "lookback_minutes": lookback,
                    "concentration_limit": 0.55,
                    "lag_gap_threshold": gap,
                    "min_target_return": -0.01,
                    "max_target_return": 0.05,
                    "min_breadth_positive": 4,
                    "market_threshold": market,
                },
                symbols=universe,
                timeframe="5m",
                falsification_statement=(
                    "Reject if broad-market laggard response has nonpositive stressed-cost "
                    "expectancy, fails dependence-aware confirmation, or is concentrated in one symbol."
                ),
                cost_model="stressed_high",
            )
        )
    return tuple(rows)


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
    candidate = next(
        (manifest for manifest in catalog if manifest_hash(manifest) not in prior_hashes),
        None,
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
        "selection_policy": "first_unseen_manifest_in_frozen_catalog",
        "execution_authority": False,
        "live_execution_authorized": False,
    }
