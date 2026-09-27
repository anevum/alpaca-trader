#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from app.preopen_state.config import get_preopen_settings
from app.preopen_state.dataset import build_dataset, chronological_split
from app.preopen_state.research import fit_logistic, score_rows


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RHEN Pre-Open State historical research")
    p.add_argument("--start", type=parse_date, required=True)
    p.add_argument("--end", type=parse_date, required=True)
    p.add_argument("--checkpoint", default="09:25")
    p.add_argument("--dataset-output", required=True)
    p.add_argument("--target")
    p.add_argument("--features")
    p.add_argument("--model-key")
    p.add_argument("--model-output")
    p.add_argument("--train-fraction", type=float, default=0.60)
    p.add_argument("--validation-fraction", type=float, default=0.20)
    return p


def main() -> int:
    args = parser().parse_args()
    settings = get_preopen_settings()
    rows = asyncio.run(
        build_dataset(
            settings,
            start=args.start,
            end=args.end,
            checkpoint=args.checkpoint,
        )
    )
    Path(args.dataset_output).write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n"
    )

    if not args.target:
        print(json.dumps({"rows": len(rows), "dataset": args.dataset_output}, indent=2))
        return 0

    if not (args.features and args.model_key and args.model_output):
        raise SystemExit(
            "--features, --model-key, and --model-output are required when --target is set"
        )
    train, validation, holdout = chronological_split(
        rows,
        train_fraction=args.train_fraction,
        validation_fraction=args.validation_fraction,
    )
    feature_names = tuple(
        item.strip() for item in args.features.split(",") if item.strip()
    )
    artifact = fit_logistic(
        train,
        feature_names=feature_names,
        target_name=args.target,
        model_key=args.model_key,
        trained_through=str(train[-1]["trade_date"]),
    )
    validation_metrics = score_rows(validation, artifact)
    holdout_metrics = score_rows(holdout, artifact)
    artifact["evaluation"] = {
        "validation": validation_metrics,
        "holdout": holdout_metrics,
        "split": {
            "train_rows": len(train),
            "validation_rows": len(validation),
            "holdout_rows": len(holdout),
            "chronological": True,
        },
    }
    from app.preopen_state.model import artifact_checksum
    artifact["checksum"] = artifact_checksum(artifact)
    Path(args.model_output).write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(artifact["evaluation"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
