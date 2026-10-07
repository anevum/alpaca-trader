"""Durable read-only asset facts. Broker capability never grants release authority."""
import json
from datetime import timedelta

from .contracts import utc


class AssetEligibility:
    METHODOLOGY_VERSION = "explicit-asset-capability-v1"
    MAX_AGE_SECONDS = 600

    def __init__(self, db, symbols):
        self.db, self.symbols = db, tuple(symbols)
        db.execute("CREATE TABLE IF NOT EXISTS shadow_assets (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        self.rows, self.fetched_at = {}, None
        saved = db.execute("SELECT body FROM shadow_assets WHERE id=1").fetchone()
        if saved:
            try:
                body = json.loads(saved[0])
                self.replace(body["assets"], body["fetched_at"], persist=False)
            except (KeyError, ValueError, TypeError, AttributeError):
                self.rows, self.fetched_at = {}, None

    def rotate_symbols(self, symbols):
        next_symbols = tuple(dict.fromkeys(symbols))
        if not next_symbols:
            raise ValueError("asset universe cannot be empty")
        if next_symbols == self.symbols:
            return False
        self.symbols = next_symbols
        self.rows = {symbol:row for symbol,row in self.rows.items() if symbol in next_symbols}
        # A rotated universe is not fully attested until the bounded asset GETs
        # complete. Never carry a prior fetched_at across a changed symbol set.
        self.fetched_at = None
        with self.db:
            self.db.execute("DELETE FROM shadow_assets")
        return True

    def replace(self, assets, fetched_at, *, persist=True):
        if not isinstance(assets, list) or len(assets) > len(self.symbols):
            raise ValueError("invalid bounded asset response")
        stamp, rows = utc(fetched_at), {}
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("symbol") not in self.symbols or asset["symbol"] in rows:
                raise ValueError("asset response identity mismatch")
            row = {key: asset.get(key) for key in ("symbol", "class", "status", "tradable", "fractionable", "overnight_tradable", "overnight_halted")}
            for key in ("tradable", "fractionable", "overnight_tradable", "overnight_halted"):
                if row[key] is not None and type(row[key]) is not bool:
                    raise ValueError("asset capability must be an explicit boolean")
            rows[row["symbol"]] = row
        if persist:
            with self.db:
                self.db.execute("INSERT INTO shadow_assets VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                    (json.dumps({"assets":list(rows.values()),"fetched_at":stamp.isoformat()},allow_nan=False),))
        self.rows, self.fetched_at = rows, stamp

    def snapshot(self, symbol, session, now):
        age = (utc(now)-self.fetched_at).total_seconds() if self.fetched_at else None
        fresh = age is not None and 0 <= age <= self.MAX_AGE_SECONDS
        row = self.rows.get(symbol)
        reasons = []
        if not fresh or row is None:
            reasons.append("ASSET_INELIGIBLE")
        else:
            if row["class"] != "us_equity" or row["status"] != "active" or row["tradable"] is not True:
                reasons.append("ASSET_INELIGIBLE")
            if session == "OVERNIGHT":
                if row["overnight_tradable"] is not True:
                    reasons.append("OVERNIGHT_NOT_TRADABLE")
                if row["overnight_halted"] is not False:
                    reasons.append("OVERNIGHT_HALTED")
        return {"symbol":symbol,"facts":dict(row) if row else None,
            "fetched_at":self.fetched_at.isoformat() if self.fetched_at else None,
            "age_seconds":age,"eligible":not reasons,"rejection_codes":reasons,
            "quality_state":"LIVE" if fresh and row else "UNAVAILABLE",
            "facts_provenance":"OBSERVED","facts_source":"ALPACA/assets",
            "provenance":"DERIVED","source":"RHEN/asset_eligibility",
            "methodology_version":self.METHODOLOGY_VERSION,"entry_authority":False}

    def deadline(self, now):
        if self.fetched_at and self.fetched_at <= utc(now) <= self.fetched_at+timedelta(seconds=self.MAX_AGE_SECONDS):
            return self.fetched_at+timedelta(seconds=self.MAX_AGE_SECONDS)
        return None
