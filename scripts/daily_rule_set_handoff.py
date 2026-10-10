"""Convert one authenticated report export into a persistent *offline* study queue.

Only local files and the isolated SQLite research queue are used. Never
imports brokers or live Core, and never transitions proposals to READY or
authorizes execution. A later human-reviewed data-freeze job owns that gate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Mapping

from app.research_agent.offline_experiment_queue import OfflineExperimentQueue
from app.research_agent.rule_set_agenda import build_rule_set_agenda


MAX_REPORT_BYTES = 5_000_000
SHA256 = re.compile(r"[a-fA-F0-9]{64}")
SESSION = re.compile(r"\d{4}-\d{2}-\d{2}")


def extract_daily_report(export: Mapping[str, Any]) -> dict[str, Any]:
    """Accept the canonical daily body or /v1/research/package JSON."""
    if not isinstance(export, Mapping):
        raise ValueError("daily research input must be an object")
    evidence = export.get("evidence")
    if isinstance(evidence, Mapping):
        daily = evidence.get("latest_daily_report")
        if isinstance(daily, Mapping):
            return dict(daily)
    daily = export.get("latest_daily_report")
    if isinstance(daily, Mapping):
        return dict(daily)
    if export.get("report_type") == "daily":
        return dict(export)
    raise ValueError("input must contain a complete canonical daily report")


def prepare_offline_handoff(export: Mapping[str, Any]) -> dict[str, Any]:
    """Return a sanitized plan; do not write queue or expose live trade data."""
    report = extract_daily_report(export)
    agenda = build_rule_set_agenda(report)
    fingerprint = str(agenda["source_fingerprint"])
    session = str(agenda["session"])
    if not SHA256.fullmatch(fingerprint) or not SESSION.fullmatch(session):
        raise ValueError("immutable daily report identity is missing")
    expected = report.get("rule_set_research_agenda")
    if isinstance(expected, Mapping):
        if expected.get("manifest_sha256") != agenda["manifest_sha256"]:
            raise ValueError("canonical daily report agenda fingerprint mismatch")
    manifest = agenda["frozen_research_manifest"]
    specs = []
    for item in manifest["rule_sets"]:
        spec = {
            "id": f"rset-{session}-{item['name']}-{fingerprint[:12]}",
            "hypothesis": item["hypothesis"],
            "control": {
                "version": agenda["strategy_version_id"],
                "rule_set": "frozen_rolling_momentum_vwap",
                "source_fingerprint": fingerprint,
            },
            "treatment": {
                "ruleset_name": item["name"],
                "entry_rules": item["entry_rules"],
                "live_mutation": False,
            },
            "required_data": {
                "completed_event_time_bars": True,
                "asof_dynamic_universe": True,
                "broker_fills_verified": True,
                "bid_ask_costs": True,
                "same_cohort_as_control": True,
                "development_validation_holdout_separation": True,
                "agenda_manifest_sha256": agenda["manifest_sha256"],
                "cohort_audit_sha256": (
                    report.get("candidate_forward_evidence") or {}
                ).get("cohort_audit", {}).get("cohort_fingerprint"),
            },
            "failure_stop": (
                "Missing event-time evidence, unmatched broker fills, "
                "unreconstructable replay, or insufficient after-cost holdout "
                "performance stops validation; never change live orders."
            ),
            "authority": "OFFLINE_RESEARCH_ONLY",
            "live_change_authorized": False,
            "promotion_authorized": False,
            "execution_authority": False,
            "broker_write_authority": False,
            "automatic_promotion_authorized": False,
        }
        if len(spec["id"]) > 80:
            raise ValueError("offline experiment name exceeds bounded identity")
        specs.append(spec)
    return {
        "session": session,
        "source_fingerprint": fingerprint,
        "manifest_sha256": agenda["manifest_sha256"],
        "agenda_state": agenda["research_state"],
        "blocking_evidence": agenda["blocking_evidence"],
        "proposals": specs,
        "research_only": True,
        "live_changes_authorized": False,
    }


def persist_offline_handoff(
    planned: Mapping[str, Any],
    *,
    db_path: str | Path,
) -> dict[str, Any]:
    """Propose immutable experiments and stop at AWAITING_EVIDENCE.

    The queue has no execution or auto-promotion path. Idempotent reruns
    cannot silently replace an existing experiment source or hypothesis.
    """
    fingerprint = str(planned["source_fingerprint"])
    if not SHA256.fullmatch(fingerprint):
        raise ValueError("offline queue refuses a non-fingerprinted report")
    queue = OfflineExperimentQueue(db_path)
    items = []
    for spec in planned["proposals"]:
        record = queue.propose(spec, source_fingerprint=fingerprint)
        if record["state"] == "PROPOSED":
            record = queue.transition(
                spec["id"],
                "AWAITING_EVIDENCE",
                evidence_fingerprint=fingerprint,
                checks={
                    "agenda_state": planned["agenda_state"],
                    "blocking_evidence": planned["blocking_evidence"],
                    "no_verified_bars_or_broker_fill_fixture": True,
                },
            )
        items.append({"id": record["id"], "state": record["state"]})
    return {
        "session": planned["session"],
        "agenda_state": planned["agenda_state"],
        "blocking_evidence": planned["blocking_evidence"],
        "experiment_queue": items,
        "research_only": True,
        "live_changes_authorized": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freeze canonical RHEN daily rule-set studies in isolated offline research."
    )
    parser.add_argument("--report-json", required=True)
    parser.add_argument("--db", help="Path to an ISOLATED offline SQLite research queue")
    parser.add_argument(
        "--commit", action="store_true",
        help="Persist proposals locally. Without this flag: dry-run only.",
    )
    args = parser.parse_args()
    path = Path(args.report_json)
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        parser.error("canonical report input missing or exceeds 5MB bound")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        planned = prepare_offline_handoff(report)
        if args.commit:
            if not args.db:
                parser.error("--db must be an isolated queue file when using --commit")
            result = persist_offline_handoff(planned, db_path=args.db)
        else:
            result = {
                "session": planned["session"],
                "agenda_state": planned["agenda_state"],
                "blocking_evidence": planned["blocking_evidence"],
                "frozen_study_ids": [s["id"] for s in planned["proposals"]],
                "dry_run": True,
                "research_only": True,
                "live_changes_authorized": False,
            }
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
