from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

METHODOLOGY_VERSION = "graen-adaptive-validation-v1"

MIN_HOLDOUT_SESSIONS = 5
MIN_HOLDOUT_CANDIDATES = 50
MIN_HOLDOUT_COVERAGE = Decimal("0.95")


def _d(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def assess_adaptive_validation(
    *,
    counterfactual_lab: Mapping[str, Any],
    shadow_validation: Mapping[str, Any],
    holdout_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess ASC research controls without opening or manufacturing holdout."""

    rolling = counterfactual_lab.get("rolling_searches")
    rolling = rolling if isinstance(rolling, Mapping) else {}

    ledgers = []
    result_rows = []
    for search in rolling.values():
        if not isinstance(search, Mapping):
            continue
        ledger = search.get("search_ledger")
        if isinstance(ledger, Mapping):
            ledgers.append(ledger)
        for row in search.get("results") or []:
            if isinstance(row, Mapping):
                result_rows.append(row)

    selection_bias_ok = bool(ledgers) and all(
        ledger.get("grid_frozen_before_evaluation") is True
        for ledger in ledgers
    )
    dependence_ok = bool(ledgers) and all(
        ledger.get("dependence_method") == "session_level_effects"
        for ledger in ledgers
    )
    multiple_testing_ok = bool(ledgers) and all(
        ledger.get("multiple_testing_method")
        == "bonferroni_exact_session_sign_test"
        for ledger in ledgers
    )
    row_controls_ok = all(
        row.get("selection_bias_status") in {"PASS", "CONTROLLED"}
        and row.get("dependence_status") in {"PASS", "CONTROLLED"}
        for row in result_rows
    ) if result_rows else False

    walk_forward_passed = bool(
        shadow_validation.get("validation_passed") is True
        and shadow_validation.get("no_lookahead_enforced") is True
        and int(shadow_validation.get("independent_sessions") or 0) >= 10
    )

    holdout = holdout_result if isinstance(holdout_result, Mapping) else {}
    holdout_sessions = int(holdout.get("independent_sessions") or 0)
    holdout_candidates = int(holdout.get("complete_candidates") or 0)
    holdout_coverage = _d(holdout.get("forward_coverage"))
    frozen_validation_passed = bool(
        holdout.get("status") == "PASSED"
        and holdout.get("frozen_before_holdout") is True
        and holdout.get("quarantine_accessed") is False
        and holdout_sessions >= MIN_HOLDOUT_SESSIONS
        and holdout_candidates >= MIN_HOLDOUT_CANDIDATES
        and holdout_coverage is not None
        and holdout_coverage >= MIN_HOLDOUT_COVERAGE
    )

    reason_codes = []
    if not selection_bias_ok or not row_controls_ok:
        reason_codes.append("SELECTION_BIAS_CONTROL_INCOMPLETE")
    if not dependence_ok or not row_controls_ok:
        reason_codes.append("DEPENDENCE_CONTROL_INCOMPLETE")
    if not multiple_testing_ok:
        reason_codes.append("MULTIPLE_TESTING_CONTROL_INCOMPLETE")
    if not walk_forward_passed:
        reason_codes.append("WALK_FORWARD_NOT_PASSED")
    if not frozen_validation_passed:
        reason_codes.append("FROZEN_HOLDOUT_NOT_PASSED")

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "selection_bias_status": (
            "CONTROLLED" if selection_bias_ok and row_controls_ok else "WAITING"
        ),
        "dependence_status": (
            "CONTROLLED" if dependence_ok and row_controls_ok else "WAITING"
        ),
        "multiple_testing_status": (
            "CONTROLLED" if multiple_testing_ok else "WAITING"
        ),
        "walk_forward_passed": walk_forward_passed,
        "frozen_validation_passed": frozen_validation_passed,
        "holdout_status": {
            "provided": bool(holdout),
            "status": holdout.get("status"),
            "frozen_before_holdout": holdout.get("frozen_before_holdout"),
            "quarantine_accessed": holdout.get("quarantine_accessed"),
            "independent_sessions": holdout_sessions,
            "complete_candidates": holdout_candidates,
            "forward_coverage": (
                str(holdout_coverage) if holdout_coverage is not None else None
            ),
        },
        "promotion_ready": not reason_codes,
        "reason_codes": reason_codes,
        "holdout_must_remain_untouched_until_frozen": True,
        "research_only": True,
        "execution_authority": False,
        "live_configuration_changed": False,
        "promotion_authorized": False,
    }
