"""Append-only operator authorization registry for ASC-008 profile releases.

Only the execution process has the HMAC key (ADMIN_TOKEN). Pure research/control
children have ADMIN_TOKEN stripped by the unified supervisor, so they can neither
mint nor alter a valid authorization. Records are authorization evidence only:
they never change execution configuration, policy mode, risk, or broker authority.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

UTC=timezone.utc
SCHEMA_VERSION="asc008-profile-approval-registry-v1"
AUTHORIZED_ACTION="authorize_policy_profile_release"


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()


def _stamp(value: datetime | None=None) -> str:
    current=(value or datetime.now(UTC))
    if current.tzinfo is None:
        current=current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _required(payload: dict[str, Any], key: str, *, max_length=512) -> str:
    value=str(payload.get(key) or "").strip()
    if not value or len(value)>max_length:
        raise ValueError(f"invalid_{key}")
    return value


def _fingerprint(value: str, key: str) -> str:
    text=_required({key:value},key,max_length=128)
    if not text.startswith("sha256:") or len(text)!=71:
        raise ValueError(f"invalid_{key}")
    if any(c not in "0123456789abcdef" for c in text[7:]):
        raise ValueError(f"invalid_{key}")
    return text


class ProfileReleaseApprovalRegistry:
    def __init__(self,path: str,secret: str):
        self.path=str(path)
        self.secret=str(secret or "")
        if not self.secret:
            raise ValueError("approval_registry_secret_unavailable")
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute("""create table if not exists profile_release_approval_events(
                decision_id text primary key,
                release_fingerprint text not null,
                status text not null,
                record_json text not null,
                signature text not null,
                decided_at text not null
            )""")
            db.execute("""create index if not exists profile_release_approval_release_time
                on profile_release_approval_events(release_fingerprint,decided_at desc)""")

    def connect(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        return db

    def _signature(self,record: dict[str,Any]) -> str:
        return hmac.new(self.secret.encode(),_canonical(record),hashlib.sha256).hexdigest()

    def _normalize_authorization(self,payload: dict[str,Any],*,authorized_by: str,now=None):
        release_fingerprint=_fingerprint(
            _required(payload,"release_fingerprint",max_length=128),"release_fingerprint")
        config=_fingerprint(
            _required(payload,"configuration_fingerprint",max_length=128),"configuration_fingerprint")
        library=_fingerprint(
            _required(payload,"policy_library_fingerprint",max_length=128),"policy_library_fingerprint")
        authorized_at=_stamp(now)
        return {
            "decision_id":str(uuid4()),
            "decision_key":f"ASC008:{release_fingerprint}",
            "status":"final",
            "decided_at":authorized_at,
            "decision_type":"research_authorization",
            "superseded_by_decision_id":None,
            "evidence":{
                "authorized_action":AUTHORIZED_ACTION,
                "release_id":_required(payload,"release_id"),
                "release_fingerprint":release_fingerprint,
                "profile_id":_required(payload,"profile_id",max_length=100),
                "profile_version":_required(payload,"profile_version",max_length=100),
                "source_strategy_version":_required(payload,"source_strategy_version",max_length=200),
                "target_strategy_version":_required(payload,"target_strategy_version",max_length=200),
                "configuration_fingerprint":config,
                "policy_library_fingerprint":library,
                "authorization_reference":_required(payload,"authorization_reference"),
                "authorized_by":str(authorized_by or "command-admin")[:320],
                "authorized_at":authorized_at,
                "revoked":False,
                "execution_authority":False,
                "broker_write_authority":False,
                "active_mode_authorized":False,
                "automatic_application_authorized":False,
            },
        }

    def _append(self,record: dict[str,Any]):
        signature=self._signature(record)
        release_fingerprint=str(record["evidence"]["release_fingerprint"])
        with self.connect() as db:
            db.execute("""insert into profile_release_approval_events(
                decision_id,release_fingerprint,status,record_json,signature,decided_at
            ) values(?,?,?,?,?,?)""",(
                record["decision_id"],release_fingerprint,record["status"],
                json.dumps(record,sort_keys=True,separators=(",",":")),
                signature,record["decided_at"],
            ))
            db.commit()
        return record

    def authorize(self,payload: dict[str,Any],*,authorized_by: str,now=None):
        candidate=self._normalize_authorization(payload,authorized_by=authorized_by,now=now)
        current=self.current()
        exact=next((row for row in current["research_decisions"]
                    if row.get("evidence",{}).get("release_fingerprint")
                    ==candidate["evidence"]["release_fingerprint"]),None)
        if exact and exact.get("evidence",{}).get("revoked") is not True:
            if exact.get("evidence") != candidate.get("evidence"):
                raise ValueError("release_fingerprint_binding_conflict")
            return {**exact,"duplicate":True}
        return {**self._append(candidate),"duplicate":False}

    def revoke(self,release_fingerprint: str,*,revoked_by: str,now=None):
        release_fingerprint=_fingerprint(release_fingerprint,"release_fingerprint")
        current=self.current()
        existing=next((row for row in current["research_decisions"]
                       if row.get("evidence",{}).get("release_fingerprint")==release_fingerprint),None)
        if existing is None:
            raise KeyError("profile_release_authorization_not_found")
        if existing.get("evidence",{}).get("revoked") is True:
            return {**existing,"duplicate":True}
        decided_at=_stamp(now)
        evidence=dict(existing["evidence"])
        evidence.update({
            "revoked":True,
            "revoked_by":str(revoked_by or "command-admin")[:320],
            "revoked_at":decided_at,
            "execution_authority":False,
            "broker_write_authority":False,
            "active_mode_authorized":False,
            "automatic_application_authorized":False,
        })
        record={
            "decision_id":str(uuid4()),
            "decision_key":existing["decision_key"],
            "status":"final",
            "decided_at":decided_at,
            "decision_type":"research_authorization",
            "superseded_by_decision_id":None,
            "evidence":evidence,
        }
        return {**self._append(record),"duplicate":False}

    def current(self):
        rows=[]
        invalid=0
        with self.connect() as db:
            raw=db.execute("""select * from profile_release_approval_events
                order by decided_at desc,rowid desc""").fetchall()
        seen=set()
        for row in raw:
            release=str(row["release_fingerprint"])
            if release in seen:
                continue
            seen.add(release)
            try:
                record=json.loads(row["record_json"])
            except json.JSONDecodeError:
                invalid+=1
                continue
            if not isinstance(record,dict) or not hmac.compare_digest(
                str(row["signature"]),self._signature(record)):
                invalid+=1
                continue
            if str(record.get("decision_id") or "")!=str(row["decision_id"]):
                invalid+=1
                continue
            rows.append(record)
        return {
            "ok":True,
            "schema_version":SCHEMA_VERSION,
            "research_decisions":rows,
            "record_count":len(rows),
            "invalid_record_count":invalid,
            "execution_authority":False,
            "broker_orders_possible":False,
            "active_mode_authorized":False,
            "automatic_application_authorized":False,
        }
