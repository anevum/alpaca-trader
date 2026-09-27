#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence

from app.research_agent.authorization import authorize_freeze, authorize_stage
from app.research_agent.design_checks import validate_design
from app.research_agent.evidence import CanonicalEvidenceReader
from app.research_agent.feasibility import (
    ResearchFeasibilityAdapter,
    evaluate_feasibility,
    feasibility_artifact,
    feasibility_from_dict,
)
from app.research_agent.manifest import freeze_preview
from app.research_agent.models import deterministic_dict
from app.research_agent.proposal import (
    proposal_artifact,
    proposal_from_dict,
    proposal_hash,
)
from app.research_agent.runner import ResearchAgentRunner


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="RHEN Research Agent v1 deterministic foundation",
    )
    value.add_argument(
        "--evidence-file",
        default=os.environ.get("RHEN_RESEARCH_EVIDENCE_FILE"),
        help="Canonical read-only evidence export JSON",
    )
    value.add_argument("--compact", action="store_true")
    subcommands = value.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status")
    for name in ("daily-review", "weekly-review"):
        child = subcommands.add_parser(name)
        child.add_argument(
            "--dry-run",
            action="store_true",
            help="Required in the foundation implementation",
        )
    proposal = subcommands.add_parser("proposal")
    proposal_commands = proposal.add_subparsers(dest="proposal_command", required=True)
    for name in ("validate", "hash"):
        child = proposal_commands.add_parser(name)
        child.add_argument("--proposal", required=True)
    feasibility = subcommands.add_parser("feasibility")
    feasibility_commands = feasibility.add_subparsers(
        dest="feasibility_command", required=True
    )
    evaluate = feasibility_commands.add_parser("evaluate")
    evaluate.add_argument("--proposal", required=True)
    evaluate.add_argument("--fixture", required=True)
    authorization = subcommands.add_parser("authorization")
    authorization_commands = authorization.add_subparsers(
        dest="authorization_command", required=True
    )
    check = authorization_commands.add_parser("check")
    check.add_argument("--decisions", required=True)
    check.add_argument("--request", required=True)
    freeze = subcommands.add_parser("freeze")
    freeze_commands = freeze.add_subparsers(dest="freeze_command", required=True)
    preview = freeze_commands.add_parser("preview")
    preview.add_argument("--proposal", required=True)
    preview.add_argument("--feasibility")
    preview.add_argument("--decisions", required=True)
    return value


def _json_file(path: str) -> object:
    return json.loads(Path(path).read_text())


def _proposal(path: str):
    value = _json_file(path)
    if not isinstance(value, dict):
        raise SystemExit("proposal fixture must contain a JSON object")
    return proposal_from_dict(value)


def _decisions(path: str) -> list[dict]:
    value = _json_file(path)
    if isinstance(value, dict) and isinstance(value.get("research_decisions"), list):
        value = value["research_decisions"]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SystemExit("decisions fixture must contain a JSON array")
    return value


def _review_payload(value) -> dict:
    return {
        "freeze_eligible": value.freeze_eligible,
        "results": [
            {
                "code": item.code,
                "status": item.status.value,
                "message": item.message,
                "path": item.path,
            }
            for item in value.results
        ],
    }


def _foundation_command(args) -> object:
    proposal = _proposal(args.proposal)
    if args.command == "proposal":
        if args.proposal_command == "hash":
            return {
                "proposal_id": proposal.proposal_id,
                "revision": proposal.revision,
                "proposal_hash": proposal_hash(proposal),
            }
        return {
            "proposal": proposal_artifact(proposal),
            "design_review": _review_payload(validate_design(proposal)),
        }
    if args.command == "feasibility":
        fixture = _json_file(args.fixture)
        if not isinstance(fixture, dict):
            raise SystemExit("feasibility fixture must contain a JSON object")
        adapter = ResearchFeasibilityAdapter(lambda **_request: fixture)
        from app.research_agent.feasibility import availability_request

        result = evaluate_feasibility(proposal, adapter.fetch(availability_request(proposal)))
        return feasibility_artifact(result)
    if args.command == "freeze":
        result = (
            feasibility_from_dict(_json_file(args.feasibility))
            if args.feasibility
            else None
        )
        preview = freeze_preview(
            proposal,
            decisions=_decisions(args.decisions),
            feasibility=result,
        )
        return deterministic_dict({
            "persisted": False,
            "stage_opened": False,
            "manifest": preview.manifest,
            "methodology": preview.methodology,
            "prepared_experiment": deterministic_dict(preview.experiment.row),
        })
    raise AssertionError(f"unsupported foundation command: {args.command}")


def _authorization_command(args) -> object:
    request = _json_file(args.request)
    if not isinstance(request, dict):
        raise SystemExit("authorization request must contain a JSON object")
    decisions = _decisions(args.decisions)
    action = request.get("authorized_action")
    if action == "freeze_methodology":
        result = authorize_freeze(
            decisions,
            proposal_id=str(request["proposal_id"]),
            proposal_revision=int(request["proposal_revision"]),
            proposal_hash=str(request["proposal_hash"]),
        )
    elif action == "open_stage":
        result = authorize_stage(
            decisions,
            experiment_id=str(request["experiment_id"]),
            experiment_key=str(request["experiment_key"]),
            stage=str(request["stage"]),
            manifest_hash=str(request["manifest_hash"]),
            source_commit=str(request["source_commit"]),
        )
    else:
        raise SystemExit("unsupported authorization action")
    return {"authorized": True, "authorization": deterministic_dict(result)}


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command in {"proposal", "feasibility", "freeze"}:
        result = _foundation_command(args)
    elif args.command == "authorization":
        result = _authorization_command(args)
    else:
        if not args.evidence_file:
            raise SystemExit(
                "--evidence-file or RHEN_RESEARCH_EVIDENCE_FILE is required; "
                "this foundation configures no database credentials or live service"
            )
        evidence = CanonicalEvidenceReader.from_file(Path(args.evidence_file)).read()
        runner = ResearchAgentRunner(evidence)
        if args.command == "status":
            result = runner.status()
        elif args.command == "daily-review":
            result = runner.daily_review(dry_run=args.dry_run)
        else:
            result = runner.weekly_review(dry_run=args.dry_run)
    print(
        json.dumps(
            result,
            sort_keys=True,
            indent=None if args.compact else 2,
            separators=(",", ":") if args.compact else None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
