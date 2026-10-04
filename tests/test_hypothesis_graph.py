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
