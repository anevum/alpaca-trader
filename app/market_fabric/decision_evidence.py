"""Bounded durable shadow decisions, never canonical orders or promotion evidence."""
import json
from collections import Counter


class DecisionEvidence:
    def __init__(self, db, *, retain=2000, retain_candidates=1000):
        self.db = db
        self.retain, self.retain_candidates = retain, retain_candidates
        db.execute("CREATE TABLE IF NOT EXISTS shadow_decision (id TEXT PRIMARY KEY, at TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS shadow_candidate (id TEXT PRIMARY KEY, at TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS shadow_session (day TEXT, session TEXT, symbol TEXT, body TEXT NOT NULL, PRIMARY KEY(day,session,symbol))")
        db.execute("CREATE TABLE IF NOT EXISTS shadow_seen (id TEXT PRIMARY KEY, at TEXT NOT NULL)")

    def record(self, identity, body, *, coverage=None):
        # A decision has one terminal classification; repeated quote updates are not
        # independent candidates. Persist source and derived lineage before publishing.
        encoded = json.dumps(body, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 16384:
            raise ValueError("shadow decision evidence exceeds bounded payload")
        if body.get("entry_authority") is not False or not body.get("observed_at"):
            raise ValueError("shadow evidence cannot carry execution authority")
        at = body["observed_at"]
        with self.db:
            added = self.db.execute("INSERT OR IGNORE INTO shadow_seen VALUES (?,?)", (identity, at)).rowcount
            if not added:
                return False
            self.db.execute("INSERT INTO shadow_decision VALUES (?,?,?)", (identity, at, encoded))
            candidate = body["classification"] == "CANDIDATE"
            if candidate:
                self.db.execute("INSERT OR IGNORE INTO shadow_candidate VALUES (?,?,?)", (identity, at, encoded))
                if coverage is not None:
                    coverage.candidate(body)
            day, session, symbol = body["session_day"], body["session"], body["symbol"]
            old = self.db.execute("SELECT body FROM shadow_session WHERE day=? AND session=? AND symbol=?", (day, session, symbol)).fetchone()
            totals = json.loads(old[0]) if old else {"decisions": 0, "evaluable_decisions": 0, "candidates": 0, "rejection_counts": {}}
            totals["decisions"] += 1
            totals["evaluable_decisions"] += int(body["classification"] != "NOT_EVALUABLE")
            totals["candidates"] += int(candidate)
            counts = Counter(totals["rejection_counts"])
            if body["reasons"]:
                counts[body["reasons"][0]] += 1
            totals["rejection_counts"] = dict(counts)
            totals["last_observed_at"] = at
            self.db.execute("INSERT INTO shadow_session VALUES (?,?,?,?) ON CONFLICT(day,session,symbol) DO UPDATE SET body=excluded.body", (day, session, symbol, json.dumps(totals)))
            for table, retain in (("shadow_decision", self.retain), ("shadow_candidate", self.retain_candidates)):
                self.db.execute(f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY at DESC,id DESC LIMIT ?)", (retain,))
            self.db.execute("DELETE FROM shadow_session WHERE day NOT IN (SELECT DISTINCT day FROM shadow_session ORDER BY day DESC LIMIT 14)")
            self.db.execute("DELETE FROM shadow_seen WHERE id NOT IN (SELECT id FROM shadow_seen ORDER BY at DESC,id DESC LIMIT 40000)")
        return True

    def summary(self, day, session):
        rows = self.db.execute("SELECT symbol,body FROM shadow_session WHERE day=? AND session=? ORDER BY symbol", (day, session)).fetchall()
        return {"session_day": day, "session": session, "symbols": {s: json.loads(b) for s,b in rows},
                "methodology_version": "shadow-distinct-decision-v1", "provenance": "DERIVED",
                "source": "RHEN/shadow_decision", "entry_authority": False,
                "retention": {"decisions": self.retain, "candidates": self.retain_candidates, "session_days": 14, "dedup_identities": 40000},
                "independent_evaluation_sessions": None, "validation_state": "UNVALIDATED"}
