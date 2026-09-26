from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.residual_downshock_v2_1 import load_manifest, stage_allowed
from app.residual_downshock_execution import emit_report_chunks, human_report, run_development


def main() -> None:
    parser = argparse.ArgumentParser(description="RHEN Residual Downshock Rebound v2.1 stage-gated research runner.")
    parser.add_argument("--manifest", default="research/residual-downshock-rebound-v2.1.json")
    parser.add_argument("--stage", choices=["development", "validation", "holdout"], required=True)
    parser.add_argument("--prior-report", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--human-output", default="")
    parser.add_argument("--emit-log-chunks", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(args.manifest)
    prior = None
    if args.prior_report:
        prior = json.loads(Path(args.prior_report).read_text(encoding="utf-8"))

    if not stage_allowed(manifest, args.stage, prior):
        raise SystemExit(f"stage {args.stage} is locked by the frozen manifest")

    if args.execute and args.dry_run:
        raise SystemExit("--execute and --dry-run are mutually exclusive")

    if args.execute:
        if args.stage != "development":
            raise SystemExit("this execution layer is development-only; validation and holdout remain locked")
        report = run_development(args.manifest, source_commit=args.source_commit)
        rendered = json.dumps(report, indent=2, sort_keys=True)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        if args.human_output:
            Path(args.human_output).write_text(human_report(report), encoding="utf-8")
        if args.emit_log_chunks:
            emit_report_chunks(report)
        else:
            print(rendered)
        return

    if not args.dry_run:
        raise SystemExit(
            "Choose --dry-run for a stage-gate inspection or --execute for the authorized development run."
        )

    payload = {
        "experiment": manifest["experiment"]["semantic_id"],
        "manifest_checksum": manifest["freeze"]["manifest_checksum_sha256"],
        "stage": args.stage,
        "stage_access_verified": True,
        "parameter_grid": manifest["parameter_grid"],
        "data_windows": [w for w in manifest["data"]["windows"] if w["role"] == args.stage],
        "real_experiment_run": False,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
