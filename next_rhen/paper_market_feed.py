"""ANEVUM V5 F3c - bounded, read-only Alpaca equity market-data ingest.

Only two historical data GET routes are callable, never order/account routes.
No network call is made on import or without explicitly supplied credentials.
The output remains UNATTESTED_IMPORTED even after authenticated HTTPS 200:
neither provider entitlement nor upstream minute coverage is cryptographically
attested, and no paper/live brokerage execution is authorized.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Protocol
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .paper_capture import (
    SNAPSHOT_SCHEMA, _utc, validate_snapshot, PaperCaptureError,
)
from .paper_schedule import validate_slot

HOST = "data.alpaca.markets"
BARS_PATH = "/v2/stocks/bars"
QUOTES_PATH = "/v2/stocks/quotes"
PATHS = {BARS_PATH, QUOTES_PATH}
FEEDS = {"iex", "sip"}
MAX_SYMBOLS = 10
MAX_PAGES_PER_ENDPOINT = 4
MAX_ITEMS = 1200
MAX_BODY_BYTES = 256_000
MAX_RESPONSE_BYTES = 1_000_000
TIMEOUT_SECONDS = 8
PAGE_LIMIT = 300


class MarketFeedError(RuntimeError):
    """Unsafe, unavailable, malformed or incomplete provider retrieval."""


def _json(value: Any) -> bytes:
    try:
        result = json.dumps(value, sort_keys=True, separators=(",", ":"),
                            allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise MarketFeedError("provider response is not canonical JSON") from exc
    if len(result) > MAX_RESPONSE_BYTES:
        raise MarketFeedError("provider evidence exceeds bounded size")
    return result


class PageReader(Protocol):
    def get(self, *, path: str, params: Mapping[str, str]) -> dict[str, Any]: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class AlpacaHTTPSReadOnly:
    """Opt-in HTTPS data transport with fixed host, fixed routes and no redirects.

    Credentials must be injected at runtime from a dedicated, narrowly scoped
    paper-data environment; do NOT reuse Alpaca Connect OAuth app secrets.
    Although this class cannot issue orders, an account key could have broad
    provider permissions. Actual key privileges must be verified separately.
    """

    def __init__(self, *, key_id: str, secret_key: str, opener: Any = None):
        if (not isinstance(key_id, str) or not key_id.strip() or
                not isinstance(secret_key, str) or not secret_key.strip()):
            raise MarketFeedError("separate paper-data API credentials required")
        self._key_id, self._secret = key_id, secret_key
        self._opener = opener if opener is not None else build_opener(_RejectRedirects())
        self.requests = 0

    def get(self, *, path: str, params: Mapping[str, str]) -> dict[str, Any]:
        if path not in PATHS:
            raise MarketFeedError("read-only route is not permitted")
        if not isinstance(params, dict) or set(params) not in (
                {"symbols","timeframe","start","end","feed","limit","sort"},
                {"symbols","timeframe","start","end","feed","limit","sort","page_token"},
                {"symbols","start","end","feed","limit","sort"},
                {"symbols","start","end","feed","limit","sort","page_token"},
        ):
            raise MarketFeedError("unexpected market-data query parameters")
        if ((path == BARS_PATH) != ("timeframe" in params)):
            raise MarketFeedError("timeframe allowed for historical bars only")
        if params.get("feed") not in FEEDS or params.get("sort") != "asc":
            raise MarketFeedError("feed/sort contract mismatch")
        if params.get("timeframe", "1Min") != "1Min" or params.get("limit") != str(PAGE_LIMIT):
            raise MarketFeedError("unexpected timeframe or page limit")
        if any(not isinstance(v, str) or len(v) > 512 for v in params.values()):
            raise MarketFeedError("query parameter exceeds bounds")
        url = "https://" + HOST + path + "?" + urlencode(params)
        req = Request(
            url, method="GET",
            headers={"Accept":"application/json",
                     "APCA-API-KEY-ID":self._key_id,
                     "APCA-API-SECRET-KEY":self._secret},
        )
        self.requests += 1
        if self.requests > 2 * MAX_PAGES_PER_ENDPOINT:
            raise MarketFeedError("market-data request budget exceeded")
        try:
            with self._opener.open(req, timeout=TIMEOUT_SECONDS) as response:
                if getattr(response, "status", None) != 200:
                    raise MarketFeedError("market-data authentication or provider request failed")
                actual_url = response.geturl()
                if not isinstance(actual_url, str) or actual_url != url:
                    raise MarketFeedError("provider redirected or changed the authenticated endpoint")
                body = response.read(MAX_BODY_BYTES + 1)
        except MarketFeedError:
            raise
        except Exception:
            # Do not chain or print provider errors, tokens, URLs or auth headers.
            raise MarketFeedError("market-data HTTPS read failed") from None
        if not isinstance(body, bytes) or len(body) > MAX_BODY_BYTES:
            raise MarketFeedError("market-data response over size cap")
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise MarketFeedError("market-data provider response is not JSON") from exc
        if not isinstance(document, dict):
            raise MarketFeedError("market-data provider response must be an object")
        return document


def _paged(reader: PageReader, *, path: str, params: dict[str, str],
           symbols: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    label = "bars" if path == BARS_PATH else "quotes"
    seen_tokens: set[str] = set()
    pages: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        if len(pages) >= MAX_PAGES_PER_ENDPOINT:
            raise MarketFeedError("market-data pagination truncated at hard page budget")
        query = dict(params)
        if token is not None:
            query["page_token"] = token
        if path not in PATHS:
            raise MarketFeedError("unsupported read endpoint")
        response = reader.get(path=path, params=query)
        if not isinstance(response, dict) or label not in response:
            raise MarketFeedError("provider omitted expected observations section")
        _json(response)
        result = response[label]
        if not isinstance(result, dict) or not set(result).issubset(set(symbols)):
            raise MarketFeedError("provider observations have invalid symbol scope")
        for sym, items in result.items():
            if not isinstance(items, list):
                raise MarketFeedError("provider symbol observations must be arrays")
            for item in items:
                if not isinstance(item, dict):
                    raise MarketFeedError("provider raw market observation malformed")
                rows.append({"symbol":sym,"value":item})
                if len(rows) > MAX_ITEMS:
                    raise MarketFeedError("market-data response total rows exceed budget")
        page_sha = sha256(_json(response)).hexdigest()
        pages.append({"path":path,"query":query,"response":response,
                      "response_sha256":page_sha})
        following = response.get("next_page_token")
        if following is None or following == "":
            return rows, pages
        if (not isinstance(following, str) or len(following) > 512 or
                following in seen_tokens):
            raise MarketFeedError("pagination token cycle or invalid cursor")
        seen_tokens.add(following)
        token = following


def build_paper_snapshot(reader: PageReader, *, slot: dict[str, Any],
                         symbols: list[str], feed: str = "iex"
                         ) -> dict[str, Any]:
    """Read two immutable historical time windows and preserve every response.

    Input is an independently predeclared F3b schedule slot, but its origin
    may still be locally synthetic. No claim of complete SIP/venue/market data
    or independent scheduler authentication is made.
    """
    validate_slot(slot)
    if not isinstance(feed,str) or feed not in FEEDS:
        raise MarketFeedError("equities market-data feed must be explicitly iex or sip")
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= MAX_SYMBOLS or
            any(not isinstance(s,str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,14}",s) for s in symbols) or
            len(set(symbols)) != len(symbols)):
        raise MarketFeedError("invalid or oversized paper research symbol list")
    at = _utc(slot["expected_at"], "scheduled expected_at")
    if at.second or at.microsecond:
        raise MarketFeedError("paper evidence requires minute-aligned planned scan")
    begin_bars = at - timedelta(minutes=3)
    begin_quotes = at - timedelta(minutes=2)
    fmt = lambda dt: dt.isoformat()
    common = {"symbols":",".join(symbols),"end":fmt(at),
              "feed":feed,"limit":str(PAGE_LIMIT),"sort":"asc"}
    bar_rows, bar_pages = _paged(reader,path=BARS_PATH,params={
        **common,"timeframe":"1Min","start":fmt(begin_bars)
    },symbols=symbols)
    quote_rows, quote_pages = _paged(reader,path=QUOTES_PATH,params={
        **common,"start":fmt(begin_quotes)
    },symbols=symbols)
    if len(bar_rows) + len(quote_rows) > MAX_ITEMS:
        raise MarketFeedError("combined market-data rows exceed source budget")
    bars: list[dict[str, Any]] = []
    bar_seen: set[tuple[str,str]] = set()
    for row in bar_rows:
        b, sym = row["value"], row["symbol"]
        if not isinstance(b, dict) or not {"t","o","h","l","c","v"} <= set(b):
            raise MarketFeedError("provider bar missing required fields")
        start = _utc(b["t"], "provider bar start")
        end = start + timedelta(minutes=1)
        if start < begin_bars or end > at:
            raise MarketFeedError("provider returned future, incomplete or out-of-range bar")
        unique = (sym,start.isoformat())
        if unique in bar_seen:
            raise MarketFeedError("duplicate bar across provider pages")
        bar_seen.add(unique)
        volume = b["v"]
        if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
            raise MarketFeedError("provider bar volume invalid")
        bars.append({"symbol":sym,"start":start.isoformat(),"end":end.isoformat(),
                     "open":b["o"],"high":b["h"],"low":b["l"],
                     "close":b["c"],"volume":volume})
    quote_latest: dict[str,dict[str,Any]] = {}
    quote_seen: set[tuple[str,str]] = set()
    for row in quote_rows:
        q,sym = row["value"],row["symbol"]
        if not isinstance(q, dict) or not {"t","bp","ap","bs","as"} <= set(q):
            raise MarketFeedError("provider quote missing required fields")
        when = _utc(q["t"], "provider quote as-of")
        if when < begin_quotes or when > at:
            raise MarketFeedError("future or outside-window provider quote")
        key=(sym,when.isoformat())
        if key in quote_seen:
            raise MarketFeedError("duplicate quote across provider pages")
        quote_seen.add(key)
        for size_name in ("bs","as"):
            if (isinstance(q[size_name],bool) or not isinstance(q[size_name],int)
                    or q[size_name] < 0):
                raise MarketFeedError("provider quote size invalid")
        previous=quote_latest.get(sym)
        if previous is None or when > _utc(previous["observed_at"], "prior quote"):
            quote_latest[sym]={"symbol":sym,"observed_at":when.isoformat(),
                               "bid":q["bp"],"ask":q["ap"],
                               "bid_size":q["bs"],"ask_size":q["as"]}
    snapshot={
        "schema_version":SNAPSHOT_SCHEMA,
        **{k:slot[k] for k in ("workspace_id","run_id","cycle_id",
                                "sequence_no","session_date")},
        "occurred_at":slot["expected_at"],
        "asof_timestamp":slot["expected_at"],
        "source_origin":"UNATTESTED_IMPORTED",
        "provider":"ALPACA_"+feed.upper()+"_HISTORICAL",
        "universe_symbols":list(symbols),
        "bars":bars,"quotes":[quote_latest[s] for s in symbols if s in quote_latest],
    }
    try:
        validate_snapshot(snapshot, slot)
    except PaperCaptureError as exc:
        raise MarketFeedError("provider records violate offline paper contract") from exc
    provenance={
        "schema_version":"anevum.paper-provider-pages.v1",
        "scope":{k:slot[k] for k in ("workspace_id","run_id","cycle_id",
                                    "sequence_no","session_date")},
        "feed":feed,"host":HOST,
        "routes":[BARS_PATH,QUOTES_PATH],
        "raw_pages":bar_pages+quote_pages,
        "page_count":len(bar_pages)+len(quote_pages),
        "bar_records":len(bar_rows),
        "quote_records":len(quote_rows),
        "response_pagination_exhausted":True,
        "missing_bar_symbols":sorted(set(symbols)-{b["symbol"] for b in bars}),
        "missing_quote_symbols":sorted(set(symbols)-set(quote_latest)),
        "snapshot_sha256":sha256(_json(snapshot)).hexdigest(),
        "independently_provider_attested":False,
        "external_schedule_attested":False,
        "data_state":"AWAITING_EVIDENCE",
        "broker_calls":0,
    }
    if len(_json(provenance)) > MAX_RESPONSE_BYTES:
        raise MarketFeedError("combined provider evidence over budget")
    return {"snapshot":snapshot,"provenance":provenance,
            "paper_only":True,"broker_calls":0,"evidence_state":"AWAITING_EVIDENCE"}
