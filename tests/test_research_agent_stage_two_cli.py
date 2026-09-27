import json

from app.research_agent.feasibility import (
    ResearchFeasibilityAdapter,
    availability_request,
    evaluate_feasibility,
    feasibility_artifact,
)
from app.research_agent.proposal import proposal_artifact
from scripts.rhen_research_agent import main


def write_json(path, value):
    path.write_text(json.dumps(value))
    return str(path)


def test_stage_two_cli_is_local_preview_only(
    tmp_path, capsys, proposal, availability_fixture, freeze_decision
):
    proposal_path = write_json(tmp_path / "proposal.json", proposal_artifact(proposal))
    fixture_path = write_json(tmp_path / "availability.json", availability_fixture)
    decisions_path = write_json(tmp_path / "decisions.json", [freeze_decision])
    authorization_request_path = write_json(
        tmp_path / "authorization-request.json",
        freeze_decision["evidence"],
    )

    assert main(["proposal", "validate", "--proposal", proposal_path]) == 0
    assert json.loads(capsys.readouterr().out)["design_review"]["freeze_eligible"] is True
    assert main(["proposal", "hash", "--proposal", proposal_path]) == 0
    assert len(json.loads(capsys.readouterr().out)["proposal_hash"]) == 64
    assert main(
        [
            "authorization",
            "check",
            "--decisions",
            decisions_path,
            "--request",
            authorization_request_path,
        ]
    ) == 0
    assert json.loads(capsys.readouterr().out)["authorized"] is True

    assert main(
        ["feasibility", "evaluate", "--proposal", proposal_path, "--fixture", fixture_path]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"

    adapter = ResearchFeasibilityAdapter(lambda **_request: availability_fixture)
    feasibility = evaluate_feasibility(
        proposal, adapter.fetch(availability_request(proposal))
    )
    feasibility_path = write_json(
        tmp_path / "feasibility.json", feasibility_artifact(feasibility)
    )
    assert main(
        [
            "freeze",
            "preview",
            "--proposal",
            proposal_path,
            "--feasibility",
            feasibility_path,
            "--decisions",
            decisions_path,
        ]
    ) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["persisted"] is False
    assert preview["stage_opened"] is False
    assert preview["prepared_experiment"]["workflow_state"] == "FROZEN"
