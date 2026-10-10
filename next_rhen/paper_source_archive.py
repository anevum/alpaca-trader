"""ANEVUM V5 F3c: private provider-source snapshot archive and remote-only restore.

Injected ImmutableObjectStore can be a local mock or a separately authorized
R2S3ImmutableStore. This module never creates credentials, network transports,
Cloudflare resources or trading orders. The caller MUST pin an exact receipt
SHA-256 outside the source store; passing an unanchored hash isn't attestation.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .evidence_vault import ImmutableObjectStore, ArchiveIntegrityError
from .paper_market_feed import (
    build_paper_snapshot, _json, MarketFeedError, BARS_PATH, QUOTES_PATH,
    MAX_RESPONSE_BYTES, MAX_TOTAL_PAGES, MAX_PAGES_PER_ENDPOINT,
    MAX_SYMBOLS, QUOTE_SAMPLING_MODE, QUOTE_SAMPLE_LIMIT,
)
from .paper_schedule import validate_slot
from .paper_capture import validate_snapshot, PaperCaptureError
from .source_attestation import _validate_scope, ID_PATTERN

SCHEMA = "anevum.paper-provider-archive.v1"
RECEIPT_SCHEMA = "anevum.paper-provider-archive-receipt.v1"
MAX_REMOTE_OBJECTS = MAX_TOTAL_PAGES + 2
MAX_REMOTE_BYTES = 1_300_000
HEX = re.compile(r"^[0-9a-f]{64}$")


class PaperRemoteIntegrityError(RuntimeError):
    """Private provider-source remote verification failed closed."""


def _prefix(scope: Mapping[str, Any]) -> str:
    _validate_scope(scope["workspace_id"], scope["run_id"], scope["session_date"])
    cid = scope["cycle_id"]
    if (not isinstance(cid,str) or not ID_PATTERN.fullmatch(cid) or cid in {".",".."}):
        raise PaperRemoteIntegrityError("unsafe capture cycle identity")
    return f"private/anevum-v5/{scope['workspace_id']}/{scope['run_id']}/paper-provider/{cid}/"


def _verify_capture(slot: dict[str,Any], captured: dict[str,Any]) -> None:
    validate_slot(slot)
    if not isinstance(captured,dict) or set(captured) != {
            "snapshot","provenance","paper_only","broker_calls","evidence_state"}:
        raise PaperRemoteIntegrityError("invalid capture bundle contract")
    snapshot=captured["snapshot"]
    provenance=captured["provenance"]
    if (captured["paper_only"] is not True or captured["broker_calls"] != 0 or
            captured["evidence_state"] != "AWAITING_EVIDENCE" or
            not isinstance(provenance,dict) or
            provenance.get("schema_version") != "anevum.paper-provider-pages.v1" or
            provenance.get("host") != "data.alpaca.markets" or
            provenance.get("independently_provider_attested") is not False or
            provenance.get("external_schedule_attested") is not False or
            provenance.get("data_state") != "AWAITING_EVIDENCE"):
        raise PaperRemoteIntegrityError("capture improperly asserts authoritative origin")
    try:
        validate_snapshot(snapshot,slot)
    except PaperCaptureError as exc:
        raise PaperRemoteIntegrityError("capture market snapshot invalid") from exc
    scope={k:slot[k] for k in ("workspace_id","run_id","cycle_id",
                              "sequence_no","session_date")}
    if provenance.get("scope") != scope or provenance.get("snapshot_sha256") != sha256(_json(snapshot)).hexdigest():
        raise PaperRemoteIntegrityError("capture and source receipt hashes differ")
    pages=provenance.get("raw_pages")
    if (not isinstance(pages,list) or not 2 <= len(pages) <= MAX_TOTAL_PAGES or
            provenance.get("page_count") != len(pages) or
            provenance.get("response_pagination_exhausted") is not False or
            provenance.get("bar_pagination_exhausted") is not True or
            provenance.get("quote_history_exhausted") is not False or
            provenance.get("quote_sampling_mode") != QUOTE_SAMPLING_MODE or
            provenance.get("quote_symbols_requested") != snapshot["universe_symbols"]):
        raise PaperRemoteIntegrityError("source pages must report honest quote sampling")
    bar_pages = [page for page in pages if isinstance(page,dict) and page.get("path") == BARS_PATH]
    quote_pages = [page for page in pages if isinstance(page,dict) and page.get("path") == QUOTES_PATH]
    if (not 1 <= len(bar_pages) <= MAX_PAGES_PER_ENDPOINT or
            len(quote_pages) != len(snapshot["universe_symbols"]) or
            pages != bar_pages + quote_pages):
        raise PaperRemoteIntegrityError("missing, excess, or reordered source pages")
    for symbol, page in zip(snapshot["universe_symbols"], quote_pages):
        query = page.get("query")
        response = page.get("response")
        if (not isinstance(query,dict) or query.get("symbols") != symbol
                or query.get("sort") != "desc"
                or query.get("limit") != str(QUOTE_SAMPLE_LIMIT)
                or "page_token" in query or not isinstance(response,dict)
                or not isinstance(response.get("quotes"),dict)
                or not set(response["quotes"]).issubset({symbol})
                or not isinstance(response["quotes"].get(symbol,[]),list)
                or len(response["quotes"].get(symbol,[])) > QUOTE_SAMPLE_LIMIT):
            raise PaperRemoteIntegrityError("malformed or starved per-symbol quote page")
    for page in pages:
        if (not isinstance(page,dict) or set(page) != {
                "path","query","response","response_sha256"} or
                page["path"] not in (BARS_PATH,QUOTES_PATH) or
                not isinstance(page["query"],dict) or
                not isinstance(page["response"],dict) or
                page["response_sha256"] != sha256(_json(page["response"])).hexdigest()):
            raise PaperRemoteIntegrityError("corrupt provider page capture")


def seal_provider_snapshot(store: ImmutableObjectStore, *, slot: dict[str,Any],
                           captured: dict[str,Any]) -> dict[str,Any]:
    """Publish staged provider source pages and a self-describing manifest.

    Real store usage is only allowed through a separately approved call path.
    Returns an anchor for a DIFFERENT, external durable trust-anchor store.
    A hash in the same process is not independently trusted by itself.
    """
    _verify_capture(slot,captured)
    scope={k:slot[k] for k in ("workspace_id","run_id","cycle_id",
                              "sequence_no","session_date")}
    prefix=_prefix(scope)
    pages=captured["provenance"]["raw_pages"]
    sources=[]
    data=[]
    for page in pages:
        raw=_json(page)
        h=sha256(raw).hexdigest()
        key=prefix+"pages/"+h+".json"
        sources.append({"key":key,"sha256":h,"bytes":len(raw)})
        data.append((key,raw))
    provenance={k:v for k,v in captured["provenance"].items() if k!="raw_pages"}
    manifest={
        "schema_version":SCHEMA,"scope":scope,"slot":slot,
        "snapshot":captured["snapshot"],"provenance":provenance,
        "raw_pages":sources,
        "transport_result":"OBJECTS_SEALED_PROVIDER_ORIGIN_UNVERIFIED",
        "research_ready":False,"broker_calls":0,
    }
    raw_manifest=_json(manifest)
    sha_manifest=sha256(raw_manifest).hexdigest()
    manifest_key=prefix+"manifest-"+sha_manifest+".json"
    receipt={"schema_version":RECEIPT_SCHEMA,"scope":scope,
             "manifest_key":manifest_key,"manifest_sha256":sha_manifest,
             "page_objects":len(sources),"paper_only":True,
             "evidence_state":"AWAITING_EVIDENCE",
             "independent_feed_authentication_proven":False,
             "broker_calls":0}
    raw_receipt=_json(receipt)
    sha_receipt=sha256(raw_receipt).hexdigest()
    receipt_key=prefix+"receipt-"+sha_receipt+".json"
    data.extend([(manifest_key,raw_manifest),(receipt_key,raw_receipt)])
    attempted=sum(len(value) for _,value in data)
    if len(data)>MAX_REMOTE_OBJECTS or attempted>MAX_REMOTE_BYTES:
        raise PaperRemoteIntegrityError("source archive violates bounded remote quota")
    for key,raw in data:
        try:
            store.put_once(key,raw)
        except ArchiveIntegrityError as exc:
            raise PaperRemoteIntegrityError("source object could not be sealed") from exc
    return {"receipt_key":receipt_key,"receipt_sha256":sha_receipt,
            "manifest_key":manifest_key,"manifest_sha256":sha_manifest,
            "remote_object_count":len(data),"bytes_verified":attempted,
            "independent_anchor_pinned_by_this_module":False,
            "evidence_state":"AWAITING_EVIDENCE",
            "broker_calls":0,"production_archive_state_changed":False}


def _load(store: ImmutableObjectStore, key: str, h: str) -> dict[str,Any]:
    if not isinstance(h,str) or not HEX.fullmatch(h):
        raise PaperRemoteIntegrityError("invalid pinned remote digest")
    try:
        raw=store.get(key)
    except ArchiveIntegrityError as exc:
        raise PaperRemoteIntegrityError("remote source GET unavailable") from exc
    if not isinstance(raw,bytes) or len(raw)>MAX_RESPONSE_BYTES or sha256(raw).hexdigest()!=h:
        raise PaperRemoteIntegrityError("remote source object absent or altered")
    try:
        value=json.loads(raw)
        if not isinstance(value,dict) or _json(value)!=raw:
            raise PaperRemoteIntegrityError("remote source canonical JSON mismatch")
    except (ValueError,TypeError) as exc:
        raise PaperRemoteIntegrityError("remote source object not valid JSON") from exc
    return value


class _Replayer:
    def __init__(self,pages:list[dict[str,Any]]):
        self.pages=pages
        self.offset=0

    def get(self, *, path:str, params:Mapping[str,str]) -> dict[str,Any]:
        if self.offset>=len(self.pages):
            raise PaperRemoteIntegrityError("remote source missing a paginated response")
        page=self.pages[self.offset]
        self.offset+=1
        if page["path"] != path or page["query"] != dict(params):
            raise PaperRemoteIntegrityError("remote source paging/query order changed")
        return page["response"]


def restore_provider_snapshot(
    store: ImmutableObjectStore, *, receipt_key:str, pinned_receipt_sha256:str,
    workspace_id:str, run_id:str, cycle_id:str, session_date:str,
) -> dict[str,Any]:
    """Independently rebuild from remote-only objects plus OUTSIDE pinned hash.

    Caller retains receipt_key + sha256 outside the remote object store,
    not in a different table within the original scanner/journal database.
    """
    scope={"workspace_id":workspace_id,"run_id":run_id,"cycle_id":cycle_id,
           "session_date":session_date}
    prefix=_prefix(scope)
    if (not isinstance(pinned_receipt_sha256,str) or not HEX.fullmatch(pinned_receipt_sha256)
            or not isinstance(receipt_key,str) or
            receipt_key!=prefix+"receipt-"+pinned_receipt_sha256+".json"):
        raise PaperRemoteIntegrityError("receipt key does not match external anchor scope")
    receipt=_load(store,receipt_key,pinned_receipt_sha256)
    receipt_scope=receipt.get("scope")
    if (not isinstance(receipt_scope,dict) or
            any(receipt_scope.get(k)!=v for k,v in scope.items()) or
            isinstance(receipt_scope.get("sequence_no"),bool) or
            not isinstance(receipt_scope.get("sequence_no"),int) or
            receipt_scope["sequence_no"]<1):
        raise PaperRemoteIntegrityError("remote receipt scope or sequence invalid")
    if (receipt.get("schema_version") != RECEIPT_SCHEMA or
            receipt.get("broker_calls") != 0 or
            receipt.get("paper_only") is not True or
            receipt.get("evidence_state") != "AWAITING_EVIDENCE" or
            receipt.get("independent_feed_authentication_proven") is not False):
        raise PaperRemoteIntegrityError("remote receipt contract invalid")
    manifest_key=receipt.get("manifest_key")
    manifest_sha=receipt.get("manifest_sha256")
    if not isinstance(manifest_key,str) or not isinstance(manifest_sha,str) or manifest_key!=prefix+"manifest-"+manifest_sha+".json":
        raise PaperRemoteIntegrityError("manifest key escapes scoped remote receipt")
    manifest=_load(store,manifest_key,manifest_sha)
    if (manifest.get("schema_version")!=SCHEMA or manifest.get("scope")!=receipt_scope or
            manifest.get("broker_calls")!=0 or manifest.get("research_ready") is not False
            or manifest.get("transport_result")!="OBJECTS_SEALED_PROVIDER_ORIGIN_UNVERIFIED"):
        raise PaperRemoteIntegrityError("remote manifest falsely promotes readiness")
    pages=manifest.get("raw_pages")
    if (not isinstance(pages,list) or not 2<=len(pages)<=MAX_TOTAL_PAGES or
            receipt.get("page_objects")!=len(pages)):
        raise PaperRemoteIntegrityError("remote pages missing or over cap")
    restored=[]
    for p in pages:
        if not isinstance(p,dict) or set(p)!={"key","sha256","bytes"}:
            raise PaperRemoteIntegrityError("remote source page reference malformed")
        if (not isinstance(p["key"],str) or p["key"]!=prefix+"pages/"+p["sha256"]+".json"):
            raise PaperRemoteIntegrityError("remote market source key outside scoped run")
        obj=_load(store,p["key"],p["sha256"])
        if len(_json(obj))!=p["bytes"]:
            raise PaperRemoteIntegrityError("remote source page length changed")
        restored.append(obj)
    captured={
        "snapshot":manifest.get("snapshot"),
        "provenance":{**manifest.get("provenance",{}),"raw_pages":restored},
        "paper_only":True,"broker_calls":0,
        "evidence_state":"AWAITING_EVIDENCE",
    }
    if (not isinstance(manifest.get("slot"),dict) or
            any(manifest["slot"].get(k)!=v for k,v in receipt_scope.items())):
        raise PaperRemoteIntegrityError("restored schedule differs from pinned source receipt")
    try:
        _verify_capture(manifest["slot"],captured)
    except (MarketFeedError,PaperCaptureError,KeyError,TypeError,ValueError) as exc:
        raise PaperRemoteIntegrityError("remote capture fails source proof checks") from exc
    replayer=_Replayer(restored)
    fresh=build_paper_snapshot(
        replayer,slot=manifest["slot"],
        symbols=manifest["snapshot"]["universe_symbols"],
        feed=manifest["provenance"]["feed"],
    )
    if (replayer.offset != len(restored) or fresh["snapshot"]!=captured["snapshot"] or
            fresh["provenance"]!=captured["provenance"]):
        raise PaperRemoteIntegrityError("raw remote provider pages cannot reproduce captured market state")
    return {
        "snapshot":captured["snapshot"],
        "slot":manifest["slot"],
        "raw_market_page_count":len(restored),
        "remote_source_replay_verified":True,
        "external_anchor_supplied_not_independently_validated":True,
        "provider_independently_attested":False,
        "scheduler_independently_attested":False,
        "full_market_session_proven":False,
        "broker_calls":0,
        "evidence_state":"AWAITING_EVIDENCE",
    }
