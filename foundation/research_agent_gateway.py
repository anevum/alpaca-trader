from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb


UTC = timezone.utc
ALLOWED_RUN_STATUS = {"COMPLETED", "NOOP", "BLOCKED", "FAILED"}
LEDGER_VERSION = "math001-search-ledger-v1"
FORBIDDEN_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "private_reasoning",
    "reasoning_trace",
}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        normalized = value
        if normalized.tzinfo is None:
            normalized = normalized.replace(tzinfo=UTC)
        return normalized.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def _obj(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _array(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _contains_forbidden_reasoning(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_forbidden_reasoning(v) for v in value)
    if not isinstance(value, dict):
        return False
    for key, item in value.items():
        if str(key).strip().lower() in FORBIDDEN_REASONING_KEYS:
            return True
        if _contains_forbidden_reasoning(item):
            return True
    return False


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _row_dict(cur: psycopg.Cursor[Any], row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    cols = [column.name for column in cur.description]
    return _serialize(dict(zip(cols, row)))


def _latest_strategy(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    cur.execute(
        """
        select run_id,strategy_version_id,asset_class,mode,started_at,ended_at,
               source_commit,configuration,status
        from rhen.strategy_runs
        order by started_at desc,created_at desc
        limit 1
        """
    )
    run = _row_dict(cur, cur.fetchone())
    if not run:
        raise RuntimeError("current_strategy_missing")

    version = str(run.get("strategy_version_id") or "").strip()
    if not version:
        raise RuntimeError("current_strategy_missing")

    cur.execute(
        """
        select payload
        from rhen.events
        where event_type='runtime_start'
          and (strategy_version_id=%s or %s='unknown')
        order by occurred_at desc,event_id desc
        limit 1
        """,
        (version, version),
    )
    row = cur.fetchone()
    runtime = _obj(row[0] if row else {})
    config = _obj(run.get("configuration"))
    name = str(
        runtime.get("strategy_name")
        or runtime.get("strategy")
        or config.get("strategy_name")
        or config.get("strategy")
        or version
    ).strip()

    return {
        "version_id": version,
        "strategy_version_id": version,
        "strategy_name": name,
        "status": run.get("status"),
        "git_commit": run.get("source_commit"),
        "activated_at": run.get("started_at"),
        "asset_class": run.get("asset_class"),
        "mode": run.get("mode"),
        "run_id": run.get("run_id"),
        "identity_fallback_to_version": name == version,
    }


def _latest_report(
    cur: psycopg.Cursor[Any],
    event_type: str,
) -> tuple[dict[str, Any] | None, datetime | None]:
    cur.execute(
        """
        select payload,coalesce(
            nullif(payload->>'generated_at','')::timestamptz,
            occurred_at
        ) as evidence_at
        from rhen.events
        where event_type=%s
        order by evidence_at desc,ingested_at desc
        limit 1
        """,
        (event_type,),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return _serialize(_obj(row[0])), row[1]


def _questions(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    cur.execute(
        """
        select distinct on (research_question_id)
            question_record_id,research_question_id,created_on,question,why_it_matters,
            status,evidence_summary,sample_size,required_data,source_weekly_report_id,
            source_agent_run_id,linked_experiment_id,category,priority_score,
            evidence_cutoff,next_action,last_reviewed_at,created_at
        from rhen.research_questions
        order by research_question_id,created_at desc
        """
    )
    cols=[c.name for c in cur.description]
    return [_serialize(dict(zip(cols,row))) for row in cur.fetchall()]


def _experiments(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    cur.execute(
        """
        select distinct on (experiment_key)
            experiment_id,experiment_key,payload,created_at
        from rhen.research_experiments
        order by experiment_key,created_at desc
        """
    )
    result=[]
    for experiment_id,key,payload,created_at in cur.fetchall():
        result.append(_serialize({
            "experiment_id":experiment_id,
            "experiment_key":key,
            **_obj(payload),
            "created_at":created_at,
        }))
    return result


def _decisions(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    cur.execute(
        """
        select distinct on (decision_key)
            decision_id,decision_key,status,decided_at,payload
        from rhen.research_decisions
        where lower(status) in ('final','superseded')
        order by decision_key,decided_at desc
        """
    )
    return [
        _serialize({
            "decision_id":row[0],
            "decision_key":row[1],
            "status":row[2],
            "decided_at":row[3],
            **_obj(row[4]),
        })
        for row in cur.fetchall()
    ]


def _agent_runs(cur: psycopg.Cursor[Any]) -> list[dict[str, Any]]:
    cur.execute(
        """
        select run_id,run_key,agent_version,source_commit,trigger,trigger_reference,
               started_at,completed_at,evidence_cutoff,input_artifacts,input_fingerprint,
               proposed_actions,actions_taken,tools_invoked,output_artifact,
               approval_required,authorization_reference,status,error_summary,
               rationale_summary,llm_usage,operator_identity,created_at
        from rhen.research_agent_runs
        where trigger <> 'foundation_contract_probe'
        order by started_at desc
        limit 50
        """
    )
    cols=[c.name for c in cur.description]
    return [_serialize(dict(zip(cols,row))) for row in cur.fetchall()]


def _search_ledger(cur: psycopg.Cursor[Any]) -> dict[str, Any]:
    cur.execute(
        """
        select payload,recorded_at
        from rhen.research_search_ledgers
        where coalesce(source_commit,'') <> 'foundation-contract-probe'
        order by recorded_at desc,ledger_id desc
        limit 75
        """
    )
    ledgers=[(_obj(row[0]),row[1]) for row in cur.fetchall()]
    hypotheses=[]
    events=[]
    multiplicity=[]
    dependence=[]
    for payload,_ in ledgers:
        hypotheses.extend(_array(payload.get("hypotheses")))
        events.extend(_array(payload.get("events")))
        if isinstance(payload.get("multiplicity_plan"),dict):
            multiplicity.append(dict(payload["multiplicity_plan"]))
        if isinstance(payload.get("dependence_plan"),dict):
            dependence.append(dict(payload["dependence_plan"]))

    hypotheses=sorted(
        (row for row in hypotheses if isinstance(row,dict)),
        key=lambda row:str(row.get("created_at") or ""),
        reverse=True,
    )[:75]
    events=sorted(
        (row for row in events if isinstance(row,dict)),
        key=lambda row:str(row.get("event_at") or ""),
        reverse=True,
    )[:100]
    multiplicity=multiplicity[:50]
    dependence=dependence[:50]
    last_at=ledgers[0][1] if ledgers else None
    exposure={
        "ledger_version":LEDGER_VERSION,
        "proposal_count":len(ledgers),
        "hypothesis_count":sum(len(_array(item[0].get("hypotheses"))) for item in ledgers),
        "event_count":sum(len(_array(item[0].get("events"))) for item in ledgers),
        "last_recorded_at":_serialize(last_at),
        "production_authority":False,
        "protected_stage_authority":False,
    }
    return {
        "exposure":exposure,
        "recent_hypotheses":_serialize(hypotheses),
        "recent_events":_serialize(events),
        "multiplicity_state":{
            "plan_count":len(multiplicity),
            "policy_status":"STUDY_ONLY_UNFROZEN",
            "production_authority":False,
        },
        "recent_multiplicity_plans":_serialize(multiplicity),
        "dependence_state":{
            "plan_count":len(dependence),
            "policy_status":"STUDY_ONLY_UNFROZEN",
            "production_authority":False,
        },
        "recent_dependence_plans":_serialize(dependence),
    }


def read_evidence(database_url: str) -> dict[str, Any]:
    with psycopg.connect(database_url,connect_timeout=5) as conn:
        with conn.cursor() as cur:
            strategy=_latest_strategy(cur)
            daily,daily_at=_latest_report(cur,"research_daily_report")
            weekly,weekly_at=_latest_report(cur,"research_weekly_report")
            candidates=[stamp for stamp in (daily_at,weekly_at) if stamp is not None]
            cutoff=max(candidates).astimezone(UTC).isoformat() if candidates else None
            return {
                "current_strategy":strategy,
                "latest_daily_report":daily,
                "latest_weekly_report":weekly,
                "research_questions":_questions(cur),
                "experiments":_experiments(cur),
                "research_decisions":_decisions(cur),
                "agent_runs":_agent_runs(cur),
                "search_ledger":_search_ledger(cur),
                "evidence_cutoff":cutoff,
            }


def _validate_run(run: dict[str, Any]) -> None:
    required=(
        "run_id","run_key","agent_version","source_commit","trigger",
        "started_at","completed_at","input_fingerprint","status",
    )
    if any(not str(run.get(key) or "").strip() for key in required):
        raise ValueError("invalid_run_record")
    if str(run.get("status")) not in ALLOWED_RUN_STATUS:
        raise ValueError("invalid_run_status")
    if _contains_forbidden_reasoning(run):
        raise ValueError("hidden_reasoning_forbidden")


def record_run(
    database_url: str,
    run: dict[str, Any],
) -> dict[str, Any]:
    _validate_run(run)
    with psycopg.connect(database_url,connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into rhen.research_agent_runs (
                    run_id,run_key,agent_version,source_commit,trigger,trigger_reference,
                    started_at,completed_at,evidence_cutoff,input_artifacts,
                    input_fingerprint,proposed_actions,actions_taken,tools_invoked,
                    output_artifact,approval_required,authorization_reference,status,
                    error_summary,rationale_summary,llm_usage,operator_identity
                ) values (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
                )
                on conflict (run_key) do nothing
                returning run_id
                """,
                (
                    UUID(str(run["run_id"])),str(run["run_key"]),str(run["agent_version"]),
                    str(run["source_commit"]),str(run["trigger"]),
                    str(run.get("trigger_reference")) if run.get("trigger_reference") is not None else None,
                    run["started_at"],run["completed_at"],run.get("evidence_cutoff"),
                    Jsonb(_array(run.get("input_artifacts"))),str(run["input_fingerprint"]),
                    Jsonb(_array(run.get("proposed_actions"))),
                    Jsonb(_array(run.get("actions_taken"))),
                    Jsonb(_array(run.get("tools_invoked"))),
                    Jsonb(run.get("output_artifact")) if run.get("output_artifact") is not None else None,
                    bool(run.get("approval_required",False)),
                    str(run.get("authorization_reference")) if run.get("authorization_reference") is not None else None,
                    str(run["status"]),
                    Jsonb(_obj(run.get("error_summary"))) if run.get("error_summary") is not None else None,
                    Jsonb(_obj(run.get("rationale_summary"))) if run.get("rationale_summary") is not None else None,
                    Jsonb(_obj(run.get("llm_usage"))),
                    str(run.get("operator_identity")) if run.get("operator_identity") is not None else None,
                ),
            )
            row=cur.fetchone()
            conn.commit()
    return (
        {"inserted":True,"duplicate":False,"run_id":str(row[0])}
        if row else {"inserted":False,"duplicate":True}
    )


def record_search_ledger(
    database_url: str,
    ledger: dict[str, Any],
) -> dict[str, Any]:
    if _contains_forbidden_reasoning(ledger):
        raise ValueError("hidden_reasoning_forbidden")
    if ledger.get("ledger_version")!=LEDGER_VERSION:
        raise ValueError("invalid_search_ledger_version")
    if bool(ledger.get("production_authority")) or bool(ledger.get("protected_stage_authority")):
        raise ValueError("search_ledger_authority_forbidden")
    proposal_id=str(ledger.get("proposal_id") or "").strip()
    try:
        revision=int(ledger.get("proposal_revision"))
    except (TypeError,ValueError) as exc:
        raise ValueError("invalid_search_ledger") from exc
    proposal_hash=str(ledger.get("proposal_hash") or "").strip()
    if not proposal_id or revision < 1 or len(proposal_hash)!=64:
        raise ValueError("invalid_search_ledger")
    digest=_canonical_hash(ledger)
    family=_obj(ledger.get("family"))
    family_id=family.get("family_id")
    run_id=ledger.get("source_agent_run_id")
    with psycopg.connect(database_url,connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into rhen.research_search_ledgers (
                    ledger_version,proposal_id,proposal_revision,proposal_hash,
                    source_agent_run_id,source_commit,family_id,candidate_variant_count,
                    search_generation,production_authority,protected_stage_authority,
                    ledger_hash,payload
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,false,false,%s,%s)
                on conflict (proposal_id,proposal_revision) do nothing
                returning ledger_id
                """,
                (
                    LEDGER_VERSION,proposal_id,revision,proposal_hash,
                    UUID(str(run_id)) if run_id else None,
                    str(ledger.get("source_commit") or "") or None,
                    UUID(str(family_id)) if family_id else None,
                    int(ledger.get("candidate_variant_count") or 0),
                    int(ledger.get("search_generation") or 0),
                    digest,Jsonb(ledger),
                ),
            )
            row=cur.fetchone()
            conn.commit()
    return {
        "inserted":row is not None,
        "duplicate":row is None,
        "ledger_hash":digest,
        "proposal_id":proposal_id,
        "proposal_revision":revision,
        "multiplicity_plan":_obj(ledger.get("multiplicity_plan")),
        "dependence_plan":_obj(ledger.get("dependence_plan")),
    }
