from app.research_agent.hypothesis_graph import (
    build_hypothesis_graph,
    repetition_gate,
)


def snapshot():
    return {
        "problems": [
            {
                "problem_id": "p1",
                "problem_key": "key1",
                "title": "Lead lag",
                "domain": "CRYPTO_STRATEGY_RESEARCH",
                "status": "WAITING",
                "created_at": "2026-10-01T00:00:00Z",
                "updated_at": "2026-10-02T00:00:00Z",
                "metadata": {
                    "hypothesis_id": "H-1",
                    "family": "lead_lag",
                    "mechanism": "diffusion",
                    "research_stage": "RESEARCH_IMPLEMENTATION_REQUIRED",
                    "code_promotion": {
                        "phase": "ENGINEERING_REQUIRED",
                        "engineering_requirement": {
                            "requirement_id": "ENG-1",
                            "condition": "ENGINEERING_REQUIRED",
                        },
                    },
                },
            },
            {
                "problem_id": "p2",
                "problem_key": "key2",
                "title": "Old hypothesis",
                "domain": "CRYPTO_STRATEGY_RESEARCH",
                "status": "WAITING",
                "created_at": "2026-09-01T00:00:00Z",
                "updated_at": "2026-09-02T00:00:00Z",
                "metadata": {
                    "hypothesis_id": "H-2",
                    "family": "momentum",
                    "mechanism": "continuation",
                },
            },
        ],
        "runs": [
            {
                "problem_id": "p2",
                "started_at": "2026-09-02T00:00:00Z",
                "completed_at": "2026-09-02T01:00:00Z",
                "result_summary": {
                    "state": "COMPILED_CANDIDATE_REJECTED",
                    "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                    "reasons": ["cost_sensitivity"],
                },
            }
        ],
        "artifacts": [],
    }


def test_graph_preserves_failure_and_engineering_memory():
    graph = build_hypothesis_graph(snapshot())
    assert graph["node_count"] == 2
    by_id = {row["hypothesis_id"]: row for row in graph["nodes"]}
    assert by_id["H-1"]["state"] == "ENGINEERING_REQUIRED"
    assert by_id["H-2"]["state"] == "FALSIFIED"
    assert graph["failure_reason_counts"]["cost_sensitivity"] == 1


def test_repetition_gate_blocks_terminal_rediscovery_without_new_information():
    graph = build_hypothesis_graph(snapshot())
    old = next(row for row in graph["nodes"] if row["hypothesis_id"] == "H-2")
    blocked = repetition_gate(graph, fingerprint=old["fingerprint"])
    assert blocked["allowed"] is False
    assert blocked["prior_exposure_count"] == 1
    allowed = repetition_gate(
        graph,
        fingerprint=old["fingerprint"],
        materially_new_information=True,
    )
    assert allowed["allowed"] is True



def test_autonomous_loop_preserves_each_candidate_as_separate_memory_node():
    graph = build_hypothesis_graph({
        "problems": [{
            "problem_id": "loop-problem",
            "problem_key": "loop-key",
            "title": "GRAEN Autonomous Crypto Research Loop v1",
            "domain": "CRYPTO_STRATEGY_RESEARCH",
            "status": "WAITING",
            "created_at": "2026-10-04T20:00:00Z",
            "updated_at": "2026-10-04T22:00:00Z",
            "metadata": {
                "autonomous_loop_id": "graen-autonomous-operating-loop-v1",
                "research_stage": "CRYPTO_HYPOTHESIS_PLANNER_V1",
            },
        }],
        "runs": [
            {
                "problem_id": "loop-problem",
                "status": "WAITING",
                "started_at": "2026-10-04T20:10:00Z",
                "completed_at": "2026-10-04T20:11:00Z",
                "result_summary": {
                    "state": "STRATEGY_REJECTED_DEVELOPMENT",
                    "decision": "CONTINUE_RESEARCH",
                    "candidate_id": "AUTO-LL-01",
                    "candidate_family": "cross_asset_diffusion",
                    "reasons": ["development_expectancy_nonpositive"],
                },
            },
            {
                "problem_id": "loop-problem",
                "status": "WAITING",
                "started_at": "2026-10-04T21:10:00Z",
                "completed_at": "2026-10-04T21:11:00Z",
                "result_summary": {
                    "state": "STRATEGY_DEVELOPMENT_PASSED",
                    "decision": "CONTINUE_RESEARCH",
                    "candidate_id": "AUTO-LL-02",
                    "candidate_family": "cross_asset_diffusion",
                },
            },
            {
                "problem_id": "loop-problem",
                "status": "WAITING",
                "started_at": "2026-10-04T21:20:00Z",
                "completed_at": "2026-10-04T21:21:00Z",
                "result_summary": {
                    "state": "STRATEGY_VALIDATION_PASSED",
                    "decision": "CONTINUE_RESEARCH",
                    "candidate_id": "AUTO-LL-02",
                    "candidate_family": "cross_asset_diffusion",
                },
            },
        ],
        "artifacts": [],
    })

    by_id = {row["hypothesis_id"]: row for row in graph["nodes"]}
    assert set(by_id) == {"AUTO-LL-01", "AUTO-LL-02"}
    assert by_id["AUTO-LL-01"]["state"] == "FALSIFIED"
    assert by_id["AUTO-LL-01"]["run_count"] == 1
    assert by_id["AUTO-LL-02"]["state"] == "HOLDOUT"
    assert by_id["AUTO-LL-02"]["run_count"] == 2
    assert graph["failure_reason_counts"][
        "development_expectancy_nonpositive"
    ] == 1


def test_direct_planner_engineering_requirement_is_retained_in_graph():
    requirement = {
        "requirement_id": "ENG-GRAEN-HYPOTHESIS-ENGINE",
        "condition": "ENGINEERING_REQUIRED",
        "title": "Extend autonomous crypto hypothesis capability",
    }
    graph = build_hypothesis_graph({
        "problems": [{
            "problem_id": "loop-engineering",
            "problem_key": "loop-engineering-key",
            "title": "GRAEN Autonomous Crypto Research Loop v1",
            "domain": "CRYPTO_STRATEGY_RESEARCH",
            "status": "WAITING",
            "created_at": "2026-10-04T20:00:00Z",
            "updated_at": "2026-10-04T22:00:00Z",
            "metadata": {
                "autonomous_loop_id": "graen-autonomous-operating-loop-v1",
                "research_stage": "CRYPTO_ENGINEERING_REQUIRED",
                "engineering_requirement": requirement,
            },
        }],
        "runs": [],
        "artifacts": [],
    })

    assert graph["node_count"] == 1
    node = graph["nodes"][0]
    assert node["hypothesis_id"] == "graen-autonomous-operating-loop-v1"
    assert node["state"] == "ENGINEERING_REQUIRED"
    assert node["engineering_requirement"]["requirement_id"] == requirement[
        "requirement_id"
    ]



def test_confirmatory_orphan_marks_candidate_falsified_in_memory():
    graph = build_hypothesis_graph({
        "problems": [{
            "problem_id": "loop-orphan",
            "problem_key": "loop-orphan-key",
            "title": "GRAEN Autonomous Crypto Research Loop v1",
            "domain": "CRYPTO_STRATEGY_RESEARCH",
            "status": "WAITING",
            "created_at": "2026-10-04T20:00:00Z",
            "updated_at": "2026-10-04T22:00:00Z",
            "metadata": {
                "autonomous_loop_id": "graen-autonomous-operating-loop-v1",
                "research_stage": "CRYPTO_HYPOTHESIS_PLANNER_V1",
                "falsified_candidate_id": "AUTO-LL-09",
            },
        }],
        "runs": [{
            "problem_id": "loop-orphan",
            "status": "WAITING",
            "started_at": "2026-10-04T21:00:00Z",
            "completed_at": "2026-10-04T21:05:00Z",
            "result_summary": {
                "state": "STRATEGY_DEVELOPMENT_PASSED",
                "decision": "CONTINUE_RESEARCH",
                "candidate_id": "AUTO-LL-09",
                "candidate_family": "cross_asset_diffusion",
            },
        }],
        "artifacts": [],
    })

    node = graph["nodes"][0]
    assert node["hypothesis_id"] == "AUTO-LL-09"
    assert node["state"] == "FALSIFIED"
    assert "sealed_confirmatory_stage_execution_orphaned" in node[
        "failure_reasons"
    ]
