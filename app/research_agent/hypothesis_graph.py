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
        requirement = (
            promotion.get("engineering_requirement")
            if isinstance(promotion.get("engineering_requirement"), Mapping)
            else None
        )

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
