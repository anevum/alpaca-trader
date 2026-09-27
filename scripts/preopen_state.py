#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.preopen_state.catalog import source_catalog
from app.preopen_state.config import get_preopen_settings
from app.preopen_state.engine import PreOpenStateEngine
from app.preopen_state.persistence import PreOpenEventSink
from app.preopen_state.research import fit_logistic, score_rows


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="RHEN Pre-Open State v1 research CLI")
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog")

    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--checkpoint", default="09:25")
    snapshot.add_argument("--emit", action="store_true")

    train = commands.add_parser("train")
    train.add_argument("--input", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--target", required=True)
    train.add_argument("--features", required=True)
    train.add_argument("--model-key", required=True)
    train.add_argument("--trained-through", required=True)
    train.add_argument("--evaluation")

    return value


async def snapshot_command(checkpoint: str, emit: bool) -> dict:
    settings = get_preopen_settings()
    engine = PreOpenStateEngine(settings)
    payload = await engine.snapshot(checkpoint=checkpoint)
    if emit:
        result = await PreOpenEventSink(settings).emit(
            event_type="preopen_state_snapshot",
            event_key=payload["snapshot_key"],
            payload=payload,
            occurred_at=payload["observed_at"],
        )
        payload["persistence"] = result
    return payload


def main() -> int:
    args = parser().parse_args()
    if args.command == "catalog":
        print(json.dumps([item.to_dict() for item in source_catalog()], indent=2))
        return 0
    if args.command == "snapshot":
        print(json.dumps(asyncio.run(snapshot_command(args.checkpoint, args.emit)), indent=2))
        return 0

    rows = json.loads(Path(args.input).read_text())
    if not isinstance(rows, list):
        raise SystemExit("training input must be a JSON array")
    features = tuple(item.strip() for item in args.features.split(",") if item.strip())
    artifact = fit_logistic(
        rows,
        feature_names=features,
        target_name=args.target,
        model_key=args.model_key,
        trained_through=args.trained_through,
    )
    Path(args.output).write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    if args.evaluation:
        evaluation_rows = json.loads(Path(args.evaluation).read_text())
        print(json.dumps(score_rows(evaluation_rows, artifact), indent=2))
    else:
        print(json.dumps({"written": args.output, "status": artifact["status"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
