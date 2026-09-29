from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

METHODOLOGY_VERSION = "graen-asc-validation-v1"


def _ledgers(counterfactual_lab: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(counterfactual_lab, Mapping):
        return []
    rows = []
    searches = counterfactual_lab.get("rolling_searches")
    if isinstance(searches, Mapping):
        for result in searches.values():
            if not isinstance(result, Mapping):
                continue
            ledger = result.get("search_ledger")
            if isinstance(ledger, Mapping):
                rows.append(ledger)
    return rows


def validate_adaptive_evidence(
    *,
    counterfactual_lab: Mapping[str, Any] | None,
    shadow_validation: Mapping[str, Any] | None,
    freeze_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """GRAEN validation contract for ASC research artifacts.

    Frozen validation is deliberately impossible to infer from performance
    alone. It requires an explicit freeze manifest created before evaluation.
    """

    ledgers = _ledgers(counterfactual_lab)
    selection_controlled = bool(ledgers) and all(
        row.get("grid_frozen_before_evaluation") is True
        and row.get("selection_rule")
        for row in ledgers
    )
    dependence_controlled = bool(ledgers) and all(
        row.get("dependence_method") == "session_level_effects"
        for row in ledgers
    )
    multiple_testing_controlled = bool(ledgers) and all(
        row.get("multiple_testing_method")
        == "bonferroni_exact_session_sign_test"
        for row in ledgers
    )

    shadow = shadow_validation if isinstance(shadow_validation, Mapping) else {}
    walk_forward_passed = bool(
        shadow.get("validation_passed") is True
        and shadow.get("no_lookahead_enforced") is True
        and int(shadow.get("independent_sessions") or 0) >= 10
    )

    manifest = freeze_manifest if isinstance(freeze_manifest, Mapping) else {}
    frozen_validation_passed = bool(
        manifest.get("frozen_before_evaluation") is True
        and manifest.get("validation_window_closed") is True
        and manifest.get("no_posthoc_parameter_changes") is True
        and manifest.get("baseline_fingerprint")
        and manifest.get("baseline_fingerprint")
        == shadow.get("baseline_fingerprint")
        and walk_forward_passed
    )

    reasons = []
    if not selection_controlled:
        reasons.append("SELECTION_BIAS_CONTROL_INCOMPLETE")
    if not dependence_controlled:
        reasons.append("DEPENDENCE_CONTROL_INCOMPLETE")
    if not multiple_testing_controlled:
        reasons.append("MULTIPLE_TESTING_CONTROL_INCOMPLETE")
    if not walk_forward_passed:
        reasons.append("WALK_FORWARD_VALIDATION_NOT_PASSED")
    if not frozen_validation_passed:
        reasons.append("FROZEN_VALIDATION_NOT_PASSED")

    return {
        "methodology_version": METHODOLOGY_VERSION,
        "selection_bias_status": (
            "CONTROLLED" if selection_controlled else "INCOMPLETE"
        ),
        "dependence_status": (
            "CONTROLLED" if dependence_controlled else "INCOMPLETE"
        ),
        "multiple_testing_status": (
            "CONTROLLED" if multiple_testing_controlled else "INCOMPLETE"
        ),
        "walk_forward_passed": walk_forward_passed,
        "frozen_validation_passed": frozen_validation_passed,
        "freeze_manifest_present": bool(manifest),
        "reason_codes": reasons,
        "research_only": True,
        "execution_authority": False,
        "promotion_authorized": False,
    }
