"""ANEVUM V5 F3d: external-witness CONTRACT for paper schedule and source receipts.

No credential creation, no network, no production runtime, no Alpaca Connect.
Objects are write-once through injected independent storage abstractions.
A caller must custody/pin returned SHA256 values OUTSIDE the source and
scheduler stores. Local or mock stores do NOT constitute independent witnesses.
An internally agreeing result never upgrades market-source trust or trading.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .evidence_vault import ArchiveIntegrityError, ImmutableObjectStore
from .paper_schedule import PaperScheduleLedger, validate_slot
from .paper_source_archive import restore_provider_snapshot, PaperRemoteIntegrityError
from .source_attestation import _validate_scope, ID_PATTERN

PLAN_SCHEMA = "anevum.paper-witness-plan.v1"
SOURCE_SCHEMA = "anevum.paper-witness-source.v1"
MAX_WITNESS_SCANS = 10
MAX_WITNESS_BYTES = 40_000
HEX = re.compile(r"^[a-f0-9]{64}$")
SCOPE_FIELDS = ("workspace_id", "run_id", "session_date")
PAPER_MODE = "PAPER_RESEARCH_ONLY"


class PaperWitnessError(RuntimeError):
    """An external paper witness was absent, contradictory or corrupt."""


def _canon(data: Any) -> bytes:
    try:
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"),
                         allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise PaperWitnessError("witness cannot be canonicalized") from exc
    if len(raw) > MAX_WITNESS_BYTES:
        raise PaperWitnessError("witness exceeds bounded maximum size")
    return raw


def _sha(data: Any) -> str:
    return sha256(_canon(data)).hexdigest()


def _scope(workspace_id: str, run_id: str, session_date: str) -> dict[str, str]:
    try:
        _validate_scope(workspace_id, run_id, session_date)
    except (ValueError, TypeError) as exc:
        raise PaperWitnessError("invalid witness workspace, run or session") from exc
    return {"workspace_id": workspace_id, "run_id": run_id,
            "session_date": session_date}


def _prefix(scope: Mapping[str, str]) -> str:
    return ("private/anevum-v5/" + scope["workspace_id"] + "/" +
            scope["run_id"] + "/paper-witness/" + scope["session_date"] + "/")


def _anchor(key: str, value: bytes) -> dict[str, str]:
    return {"key": key, "sha256": sha256(value).hexdigest()}


def _pin_type(anchor: Any, prefix: str) -> tuple[str, str]:
    if (not isinstance(anchor, dict) or set(anchor) != {"key", "sha256"}
            or not isinstance(anchor["key"], str)
            or not isinstance(anchor["sha256"], str)
            or not HEX.fullmatch(anchor["sha256"])
            or not anchor["key"].startswith(prefix)):
        raise PaperWitnessError("witness requires an out-of-band scoped key and SHA256")
    return anchor["key"], anchor["sha256"]


def _store_once(store: ImmutableObjectStore, key: str, raw: bytes) -> dict[str, str]:
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_WITNESS_BYTES:
        raise PaperWitnessError("invalid witness object size")
    try:
        store.put_once(key, raw)
        present = store.get(key)
    except Exception:
        raise PaperWitnessError("witness write or readback failed") from None
    if present != raw:
        raise PaperWitnessError("witness remote readback mismatch")
    return _anchor(key, raw)


def _load(store: ImmutableObjectStore, anchor: Any, prefix: str) -> dict[str, Any]:
    key, expected = _pin_type(anchor, prefix)
    try:
        raw = store.get(key)
    except Exception:
        raise PaperWitnessError("witness read failed") from None
    if (not isinstance(raw, bytes) or not raw or len(raw) > MAX_WITNESS_BYTES
            or sha256(raw).hexdigest() != expected):
        raise PaperWitnessError("external witness absent or changed")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PaperWitnessError("witness JSON corrupted") from exc
    if not isinstance(value, dict) or _canon(value) != raw:
        raise PaperWitnessError("witness has noncanonical content")
    return value


def _read_plan(witness: ImmutableObjectStore, anchor: Any,
               scope: dict[str, str]) -> dict[str, Any]:
    prefix = _prefix(scope)
    key, pinned = _pin_type(anchor, prefix + "plan-")
    if key != prefix + "plan-" + pinned + ".json":
        raise PaperWitnessError("plan anchor key/digest not matched")
    plan = _load(witness, anchor, prefix + "plan-")
    if (set(plan) != {"schema_version", "scope", "slot_count",
                      "slots", "source_run_chain_sha256", "paper_only",
                      "origin_independently_attested", "receipt_custody_proven"}
            or plan["schema_version"] != PLAN_SCHEMA or plan["scope"] != scope
            or plan["paper_only"] is not True
            or plan["origin_independently_attested"] is not False
            or plan["receipt_custody_proven"] is not False):
        raise PaperWitnessError("witness schedule schema or scope invalid")
    slots = plan["slots"]
    n = plan["slot_count"]
    if (not isinstance(slots, list) or not isinstance(n, int)
            or isinstance(n, bool) or not 1 <= n <= MAX_WITNESS_SCANS
            or len(slots) != n
            or not isinstance(plan["source_run_chain_sha256"], str)
            or not HEX.fullmatch(plan["source_run_chain_sha256"])):
        raise PaperWitnessError("unbounded, missing or malformed schedule declaration")
    seen: set[tuple[int, str]] = set()
    previous = None
    for slot in slots:
        try:
            validate_slot(slot)
        except (TypeError, ValueError) as exc:
            raise PaperWitnessError("anchored paper slot contract invalid") from exc
        if any(slot[k] != scope[k] for k in SCOPE_FIELDS):
            raise PaperWitnessError("anchored schedule mixes workspace/run/session")
        pair = (slot["sequence_no"], slot["cycle_id"])
        if (pair in seen or
                (previous is not None and slot["sequence_no"] != previous + 1)):
            raise PaperWitnessError("anchored schedule has duplicate or missing ordered slot")
        previous = slot["sequence_no"]
        seen.add(pair)
    return plan


def publish_schedule_witness(
    witness: ImmutableObjectStore, planner: PaperScheduleLedger, *,
    workspace_id: str, run_id: str, session_date: str,
) -> dict[str, str]:
    """Seal an ordered local schedule declaration in an injected witness store.

    Application MUST invoke before scanner intake and separately retain this
    returned anchor. This function cannot prove origin, invocation timing,
    transport ownership, independence or immutability of external pin custody.
    """
    scope = _scope(workspace_id, run_id, session_date)
    try:
        read = planner.read_session_verified(**scope)
    except Exception:
        raise PaperWitnessError("schedule source cannot be verified") from None
    slots = read["slots"]
    if not 1 <= len(slots) <= MAX_WITNESS_SCANS:
        raise PaperWitnessError("planned witness cycles outside safety budget")
    plan = {
        "schema_version": PLAN_SCHEMA, "scope": scope, "slot_count": len(slots),
        "slots": slots, "source_run_chain_sha256": read["chain_sha256"],
        "paper_only": True, "origin_independently_attested": False,
        "receipt_custody_proven": False,
    }
    raw = _canon(plan)
    key = _prefix(scope) + "plan-" + sha256(raw).hexdigest() + ".json"
    answer = _store_once(witness, key, raw)
    _read_plan(witness, answer, scope)
    return answer


def publish_source_witness(
    witness: ImmutableObjectStore, provider_store: ImmutableObjectStore, *,
    plan_anchor: dict[str, str], source_receipt: dict[str, Any],
    workspace_id: str, run_id: str, session_date: str, cycle_id: str,
) -> dict[str, str]:
    """After a provider-page archive exists, bind exact receipt to plan slot."""
    if witness is provider_store:
        raise PaperWitnessError("source and witness store must be separate objects")
    scope = _scope(workspace_id, run_id, session_date)
    plan = _read_plan(witness, plan_anchor, scope)
    if (not isinstance(cycle_id, str) or not ID_PATTERN.fullmatch(cycle_id)
            or not isinstance(source_receipt, dict)
            or set(source_receipt) != {"receipt_key", "receipt_sha256"}
            or not isinstance(source_receipt["receipt_key"], str)
            or not isinstance(source_receipt["receipt_sha256"], str)
            or not HEX.fullmatch(source_receipt["receipt_sha256"])):
        raise PaperWitnessError("invalid source receipt scope or hash")
    matching = [slot for slot in plan["slots"] if slot["cycle_id"] == cycle_id]
    if len(matching) != 1:
        raise PaperWitnessError("source receipt not in witnessed schedule")
    slot = matching[0]
    try:
        restored = restore_provider_snapshot(
            provider_store, receipt_key=source_receipt["receipt_key"],
            pinned_receipt_sha256=source_receipt["receipt_sha256"],
            workspace_id=workspace_id, run_id=run_id,
            cycle_id=cycle_id, session_date=session_date)
    except (PaperRemoteIntegrityError, ValueError, TypeError, KeyError):
        raise PaperWitnessError("provider source did not independently replay") from None
    if restored["slot"] != slot:
        raise PaperWitnessError("provider source slot differs from predeclared plan")
    manifest = {
        "schema_version": SOURCE_SCHEMA, "scope": scope,
        "cycle_id": cycle_id, "sequence_no": slot["sequence_no"],
        "plan_anchor_sha256": plan_anchor["sha256"],
        "source_receipt_key": source_receipt["receipt_key"],
        "source_receipt_sha256": source_receipt["receipt_sha256"],
        "snapshot_sha256": _sha(restored["snapshot"]),
        "paper_only": True, "source_origin_verified": False,
        "external_custody_independently_proven": False,
    }
    raw = _canon(manifest)
    key = (_prefix(scope) + "source-" + str(slot["sequence_no"]).zfill(6)
           + "-" + sha256(raw).hexdigest() + ".json")
    return _store_once(witness, key, raw)


def restore_witnessed_session(
    witness: ImmutableObjectStore, provider_store: ImmutableObjectStore, *,
    plan_anchor: dict[str, str], source_anchors: list[dict[str, str]],
    workspace_id: str, run_id: str, session_date: str,
) -> dict[str, Any]:
    """Audit without any local scheduler, scanner, journal or market files.

    A matching result is still AWAITING_EVIDENCE. Caller must have an
    independent channel to trust SHA256 pins and witness store custody.
    """
    if witness is provider_store:
        raise PaperWitnessError("source and witness store must be separated")
    scope = _scope(workspace_id, run_id, session_date)
    plan = _read_plan(witness, plan_anchor, scope)
    if (not isinstance(source_anchors, list) or
            len(source_anchors) != plan["slot_count"]):
        raise PaperWitnessError("witnessed source receipts omit a scheduled scan")
    prefix = _prefix(scope)
    seen: dict[int, dict[str, Any]] = {}
    for ref in source_anchors:
        key, h = _pin_type(ref, prefix + "source-")
        source = _load(witness, ref, prefix + "source-")
        if (not isinstance(source.get("sequence_no"), int)
                or isinstance(source.get("sequence_no"), bool)
                or not isinstance(source.get("cycle_id"), str)
                or key != prefix + "source-" + str(source["sequence_no"]).zfill(6)
                   + "-" + h + ".json"):
            raise PaperWitnessError("witness source index is invalid")
        if (set(source) != {"schema_version", "scope", "cycle_id", "sequence_no",
                            "plan_anchor_sha256", "source_receipt_key",
                            "source_receipt_sha256", "snapshot_sha256", "paper_only",
                            "source_origin_verified", "external_custody_independently_proven"}
                or source["schema_version"] != SOURCE_SCHEMA
                or source["scope"] != scope
                or source["plan_anchor_sha256"] != plan_anchor["sha256"]
                or source["paper_only"] is not True
                or source["source_origin_verified"] is not False
                or source["external_custody_independently_proven"] is not False
                or not isinstance(source["snapshot_sha256"], str)
                or not HEX.fullmatch(source["snapshot_sha256"])):
            raise PaperWitnessError("untrusted or contradictory source witness")
        seq = source["sequence_no"]
        if seq in seen:
            raise PaperWitnessError("duplicate witness source sequence")
        seen[seq] = source
    for slot in plan["slots"]:
        seq = slot["sequence_no"]
        source = seen.get(seq)
        if source is None or source["cycle_id"] != slot["cycle_id"]:
            raise PaperWitnessError("scheduled scan missing matching source witness")
        try:
            restored = restore_provider_snapshot(
                provider_store,
                receipt_key=source["source_receipt_key"],
                pinned_receipt_sha256=source["source_receipt_sha256"],
                workspace_id=workspace_id, run_id=run_id,
                cycle_id=slot["cycle_id"], session_date=session_date)
        except (PaperRemoteIntegrityError, ValueError, KeyError, TypeError):
            raise PaperWitnessError("anchored source cannot be restored independently") from None
        if restored["slot"] != slot or _sha(restored["snapshot"]) != source["snapshot_sha256"]:
            raise PaperWitnessError("restored source does not equal witnessed schedule")
    return {
        "schema_version": "anevum.paper-witness-reconciliation.v1",
        "scope": scope, "scheduled_cycles": len(plan["slots"]),
        "remote_source_cycles_restored": len(seen),
        "external_plan_witness_hash_checked": True,
        "external_source_witness_hashes_checked": True,
        "remote_provider_pages_replayed": True,
        "local_sqlite_wal_required": False,
        "external_sha256_pins_independently_custodied": False,
        "witness_provider_independently_controlled": False,
        "market_feed_origin_independently_attested": False,
        "market_minute_completeness_proven": False,
        "f1_journal_remote_archive_proven": False,
        "research_ready": False, "alpha_validated": False,
        "broker_write_authority": False, "broker_calls": 0,
        "evidence_state": "AWAITING_EVIDENCE",
    }


def audit_witnessed_session(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Fail closed to BLOCKED, never fabricate a positive proof on exception."""
    try:
        return restore_witnessed_session(*args, **kwargs)
    except (PaperWitnessError, PaperRemoteIntegrityError, ValueError, TypeError, KeyError):
        return {"evidence_state": "BLOCKED",
                "issue_codes": ["WITNESS_SOURCE_OR_SCHEDULE_MISSING_OR_CHANGED"],
                "research_ready": False, "broker_write_authority": False,
                "broker_calls": 0}
