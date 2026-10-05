from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


GRAPH_SCHEMA_VERSION = "graen.hypothesis-graph.v1"
UTC = timezone.utc
TERMINAL_FAILURE_STATES = {
    "NO_DEVELOPMENT_SURVIVOR",
    "COMPILED_CANDIDATE_REJECTED",
    "VALIDATION_FAILED",
    "HOLDOUT_FAILED",
    "CORPUS_FAIL",
    "PERFORMANCE_FAIL",
    "INTEGRITY_ABORT",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def hypothesis_identity(problem: Mapping[str, Any]) -> str:
    metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
    explicit = (
        metadata.get("candidate_id")
        or metadata.get("hypothesis_id")
        or metadata.get("campaign_id")
        or metadata.get("autonomous_loop_id")
        or problem.get("problem_key")
        or problem.get("problem_id")
    )
    return _text(explicit) or "UNKNOWN"


def build_hypothesis_graph(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Project durable GRAEN problems/runs into permanent hypothesis memory."""
    problems = [
        row for row in (snapshot.get("problems") or [])
        if isinstance(row, Mapping)
    ]
    runs = [
        row for row in (snapshot.get("runs") or [])
        if isinstance(row, Mapping)
    ]
    artifacts = [
        row for row in (snapshot.get("artifacts") or [])
        if isinstance(row, Mapping)
    ]

    runs_by_problem: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for run in runs:
        runs_by_problem[_text(run.get("problem_id"))].append(run)

    artifacts_by_problem: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        artifacts_by_problem[_text(artifact.get("problem_id"))].append(artifact)

    nodes: list[dict[str, Any]] = []
    failure_reasons: dict[str, int] = defaultdict(int)
    family_counts: dict[str, int] = defaultdict(int)

    for problem in problems:
        problem_id = _text(problem.get("problem_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        problem_runs = sorted(
            runs_by_problem.get(problem_id, []),
            key=lambda row: _stamp(row.get("started_at")) or datetime.min.replace(tzinfo=UTC),
        )
        latest = problem_runs[-1] if problem_runs else {}
        summary = latest.get("result_summary") if isinstance(latest.get("result_summary"), Mapping) else {}
        promotion = metadata.get("code_promotion") if isinstance(metadata.get("code_promotion"), Mapping) else {}
        direct_requirement = (
            metadata.get("engineering_requirement")
            if isinstance(metadata.get("engineering_requirement"), Mapping)
            else None
        )
        requirement = direct_requirement or (
            promotion.get("engineering_requirement")
            if isinstance(promotion.get("engineering_requirement"), Mapping)
            else None
        )

        candidate_runs: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for candidate_run in problem_runs:
            candidate_summary = (
                candidate_run.get("result_summary")
                if isinstance(candidate_run.get("result_summary"), Mapping)
                else {}
            )
            candidate_id = _text(candidate_summary.get("candidate_id"))
            if candidate_id:
                candidate_runs[candidate_id].append(candidate_run)

        for candidate_id, grouped_runs in sorted(candidate_runs.items()):
            candidate_latest = grouped_runs[-1]
            candidate_summary = (
                candidate_latest.get("result_summary")
                if isinstance(candidate_latest.get("result_summary"), Mapping)
                else {}
            )
            candidate_state = _text(
                candidate_summary.get("state")
                or candidate_summary.get("status")
            )
            candidate_decision = _text(candidate_summary.get("decision"))
            candidate_next_stage = _text(
                candidate_summary.get("next_research_stage")
            )
            upper_state = candidate_state.upper()
            upper_stage = candidate_next_stage.upper()
            invalidated_candidate = _text(
                metadata.get("falsified_candidate_id")
            )
            if invalidated_candidate == candidate_id:
                candidate_memory_state = "FALSIFIED"
                if (
                    "sealed_confirmatory_stage_execution_orphaned"
                    not in candidate_reasons
                ):
                    candidate_reasons.append(
                        "sealed_confirmatory_stage_execution_orphaned"
                    )
            elif (
                "REJECT" in upper_state
                or "FAIL" in upper_state
                or "NO_DEVELOPMENT_SURVIVOR" in upper_state
                or candidate_decision.upper()
                in {"REJECT", "REJECTED", "NEEDS_NEW_HYPOTHESIS_ENGINE"}
            ):
                candidate_memory_state = "FALSIFIED"
            elif "PAPER_VALIDATED" in upper_state or "PAPER_PASS" in upper_state:
                candidate_memory_state = "PAPER_VALIDATED"
            elif upper_state in {
                "PAPER_ADAPTER_REQUIRED",
                "PAPER_AUTHORIZATION_REQUIRED",
            }:
                candidate_memory_state = "ENGINEERING_REQUIRED"
            elif (
                "PAPER_CANARY" in upper_state
                or "READY_FOR_PAPER" in upper_state
                or "PAPER" in upper_stage
            ):
                candidate_memory_state = "PAPER"
            elif "FORWARD_SHADOW" in upper_state or "SHADOW" in upper_stage:
                candidate_memory_state = "SHADOW"
            elif (
                "HOLDOUT_PASSED" in upper_state
                or "READY_FOR_VELUM" in upper_state
                or "VELUM" in upper_stage
            ):
                candidate_memory_state = "VELUM"
            elif "VALIDATION_PASSED" in upper_state or "HOLDOUT" in upper_stage:
                candidate_memory_state = "HOLDOUT"
            elif "DEVELOPMENT_PASSED" in upper_state or "VALIDATION" in upper_stage:
                candidate_memory_state = "VALIDATING"
            elif (
                "MANIFEST_FROZEN" in upper_state
                or "DEVELOPMENT" in upper_stage
                or str(candidate_latest.get("status") or "").upper() == "RUNNING"
            ):
                candidate_memory_state = "ACTIVE"
            else:
                candidate_memory_state = "COMPLETED"

            candidate_reasons: list[str] = []
            for key in ("reasons", "reason_codes", "gate_reason_codes"):
                value = candidate_summary.get(key)
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                    candidate_reasons.extend(
                        _text(v) for v in value if _text(v)
                    )
            if candidate_summary.get("error"):
                candidate_reasons.append(
                    _text(candidate_summary.get("error"))
                )
            for reason in candidate_reasons:
                failure_reasons[reason] += 1

            candidate_family = _text(
                candidate_summary.get("candidate_family")
                or metadata.get("candidate_family")
                or metadata.get("family")
            )
            if candidate_family:
                family_counts[candidate_family] += 1
            candidate_mechanism = _text(
                candidate_summary.get("mechanism")
                or metadata.get("mechanism")
            )
            manifest_hash_value = _text(
                candidate_summary.get("manifest_hash")
            )
            candidate_datasets = sorted({
                _text((row.get("input_snapshot") or {}).get("dataset_id"))
                for row in grouped_runs
                if isinstance(row.get("input_snapshot"), Mapping)
                and _text((row.get("input_snapshot") or {}).get("dataset_id"))
            })
            candidate_node = {
                "hypothesis_id": candidate_id,
                "problem_id": problem_id,
                "title": problem.get("title"),
                "family": candidate_family or None,
                "mechanism": candidate_mechanism or None,
                "state": candidate_memory_state,
                "research_stage": candidate_next_stage or None,
                "run_count": len(grouped_runs),
                "dataset_ids": candidate_datasets,
                "parents": [],
                "latest_result_state": candidate_state or None,
                "latest_decision": candidate_decision or None,
                "failure_reasons": candidate_reasons,
                "engineering_requirement": None,
                "created_at": (
                    grouped_runs[0].get("created_at")
                    or grouped_runs[0].get("started_at")
                    or problem.get("created_at")
                ),
                "updated_at": (
                    candidate_latest.get("completed_at")
                    or candidate_latest.get("started_at")
                    or problem.get("updated_at")
                ),
                "last_run_at": (
                    candidate_latest.get("completed_at")
                    or candidate_latest.get("started_at")
                ),
                "artifact_count": 0,
            }
            candidate_node["fingerprint"] = _hash({
                "hypothesis_id": candidate_id,
                "family": candidate_family,
                "mechanism": candidate_mechanism,
                "manifest_hash": manifest_hash_value,
            })
            nodes.append(candidate_node)

        # Candidate-tagged runs are the permanent hypothesis memory for the
        # self-driving loop. Keep an additional control node only when the
        # problem itself carries a current engineering requirement.
        if candidate_runs and requirement is None:
            continue

        hypothesis_id = hypothesis_identity(problem)
        family = _text(
            metadata.get("family")
            or metadata.get("candidate_family")
            or summary.get("candidate_family")
            or problem.get("domain")
        )
        mechanism = _text(metadata.get("mechanism") or summary.get("mechanism"))
        stage = _text(metadata.get("research_stage"))
        result_state = _text(summary.get("state") or summary.get("status"))
        decision = _text(summary.get("decision"))
        status = _text(problem.get("status")).upper()

        if requirement:
            memory_state = "ENGINEERING_REQUIRED"
        elif result_state in TERMINAL_FAILURE_STATES or decision in {
            "NEEDS_NEW_HYPOTHESIS_ENGINE",
            "REJECT",
            "REJECTED",
        }:
            memory_state = "FALSIFIED"
        elif "HOLDOUT" in stage:
            memory_state = "HOLDOUT"
        elif "VALIDATION" in stage:
            memory_state = "VALIDATING"
        elif status in {"RUNNING", "QUEUED", "WAITING"}:
            memory_state = "ACTIVE"
        elif status in {"SUCCEEDED"}:
            memory_state = "COMPLETED"
        elif status in {"FAILED", "CANCELLED", "BLOCKED"}:
            memory_state = "BLOCKED"
        else:
            memory_state = status or "UNKNOWN"

        reasons = []
        for key in ("reasons", "reason_codes", "gate_reason_codes"):
            value = summary.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                reasons.extend(_text(v) for v in value if _text(v))
        if summary.get("error"):
            reasons.append(_text(summary.get("error")))
        for reason in reasons:
            failure_reasons[reason] += 1
        if family:
            family_counts[family] += 1

        source_hypotheses = metadata.get("source_hypotheses")
        parents = (
            [_text(v) for v in source_hypotheses if _text(v)]
            if isinstance(source_hypotheses, Sequence) and not isinstance(source_hypotheses, (str, bytes))
            else []
        )
        exposure = len(problem_runs)
        datasets = sorted({
            _text((row.get("input_snapshot") or {}).get("dataset_id"))
            for row in problem_runs
            if isinstance(row.get("input_snapshot"), Mapping)
            and _text((row.get("input_snapshot") or {}).get("dataset_id"))
        })

        node = {
            "hypothesis_id": hypothesis_id,
            "problem_id": problem_id,
            "title": problem.get("title"),
            "family": family or None,
            "mechanism": mechanism or None,
            "state": memory_state,
            "research_stage": stage or None,
            "run_count": exposure,
            "dataset_ids": datasets,
            "parents": parents,
            "latest_result_state": result_state or None,
            "latest_decision": decision or None,
            "failure_reasons": reasons,
            "engineering_requirement": requirement,
            "created_at": problem.get("created_at"),
            "updated_at": problem.get("updated_at"),
            "last_run_at": latest.get("completed_at") or latest.get("started_at"),
            "artifact_count": len(artifacts_by_problem.get(problem_id, [])),
        }
        node["fingerprint"] = _hash({
            "hypothesis_id": hypothesis_id,
            "family": family,
            "mechanism": mechanism,
            "parents": parents,
            "research_stage": stage,
        })
        nodes.append(node)

    nodes.sort(
        key=lambda row: _stamp(row.get("updated_at")) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    counts: dict[str, int] = defaultdict(int)
    for node in nodes:
        counts[_text(node.get("state"))] += 1

    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "node_count": len(nodes),
        "state_counts": dict(sorted(counts.items())),
        "family_counts": dict(sorted(family_counts.items())),
        "failure_reason_counts": dict(
            sorted(failure_reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
        "nodes": nodes,
        "graph_hash": _hash([
            {
                "hypothesis_id": row["hypothesis_id"],
                "fingerprint": row["fingerprint"],
                "state": row["state"],
                "latest_result_state": row["latest_result_state"],
            }
            for row in nodes
        ]),
    }


def prior_exposure(
    graph: Mapping[str, Any],
    *,
    hypothesis_id: str | None = None,
    fingerprint: str | None = None,
) -> list[dict[str, Any]]:
    rows = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        if hypothesis_id and _text(node.get("hypothesis_id")) == _text(hypothesis_id):
            rows.append(dict(node))
        elif fingerprint and _text(node.get("fingerprint")) == _text(fingerprint):
            rows.append(dict(node))
    return rows


def repetition_gate(
    graph: Mapping[str, Any],
    *,
    fingerprint: str,
    materially_new_information: bool = False,
) -> dict[str, Any]:
    matches = prior_exposure(graph, fingerprint=fingerprint)
    terminal = [
        row for row in matches
        if row.get("state") in {"FALSIFIED", "COMPLETED", "HOLDOUT", "VALIDATING"}
    ]
    blocked = bool(terminal) and not materially_new_information
    return {
        "allowed": not blocked,
        "reason": (
            "materially_new_information"
            if terminal and materially_new_information
            else "previously_exposed_hypothesis"
            if blocked
            else "no_terminal_prior_exposure"
        ),
        "prior_exposure_count": len(matches),
        "terminal_prior_count": len(terminal),
        "prior_hypotheses": [row.get("hypothesis_id") for row in terminal],
    }
