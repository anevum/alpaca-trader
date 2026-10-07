"""Observed prerequisite exposure, not independent research or promotion proof."""
import json

from app.adaptive_policy import fingerprint
from .contracts import utc


def coverage_lineage(*, session_id, feed, symbol, strategy_version, configuration):
    return {"session_id": session_id, "session_day": session_id.split("/")[0],
            "session": session_id.split("/")[-1], "feed": feed, "symbol": symbol,
            "strategy_version": strategy_version, "shadow_configuration_fingerprint": configuration,
            "policy_profile": "BASELINE_LOCKED", "candidate_kind": "SIGNAL_ONLY_COUNTERFACTUAL"}


class CoverageEvidence:
    def __init__(self, db, *, strategy_version, configuration):
        self.db, self.strategy_version, self.configuration = db, strategy_version, configuration
        self.cursors, self.totals = {}, {}
        db.execute("CREATE TABLE IF NOT EXISTS shadow_coverage (id TEXT PRIMARY KEY, day TEXT NOT NULL, body TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS shadow_coverage_candidate (id TEXT PRIMARY KEY, day TEXT NOT NULL, count INTEGER NOT NULL)")

    def lineage(self, store, symbol):
        if store.context is None:
            return None
        return coverage_lineage(session_id=store.context[1], feed=store.context[0], symbol=symbol,
                                strategy_version=self.strategy_version, configuration=self.configuration)

    def advance(self, store, now):
        now = utc(now)
        for symbol in store.symbols:
            prior = self.cursors.get(symbol)
            # Late clocks cannot roll the cursor backwards or count time twice.
            if prior and now < prior["at"]:
                continue
            if prior:
                total = self.totals[prior["id"]]
                elapsed = (now-prior["at"]).total_seconds()
                total["eligible_seconds"] += elapsed if prior["eligible"] else 0
                until = min(now, prior["deadline"]) if prior["deadline"] else prior["at"]
                total["evaluable_seconds"] += max(0, (until-prior["at"]).total_seconds()) if prior["evaluable"] else 0
                total["last_observed_at"] = now.isoformat()
            lineage = self.lineage(store, symbol)
            if lineage is None:
                self.cursors.pop(symbol, None)
                continue
            identity = fingerprint(lineage)
            if identity not in self.totals:
                saved = self.db.execute("SELECT body FROM shadow_coverage WHERE id=?", (identity,)).fetchone()
                self.totals[identity] = json.loads(saved[0]) if saved else {
                    **lineage, "eligible_seconds": 0.0, "evaluable_seconds": 0.0,
                    "first_observed_at": now.isoformat(), "last_observed_at": now.isoformat()}
            row = store.snapshot(symbol, now)
            self.cursors[symbol] = {"id": identity, "at": now, "deadline": store.freshness_deadline(symbol, now),
                "eligible": symbol in store.subscribed and store.connection in {"WARMING", "HEALTHY"},
                "evaluable": row["evaluable"]}

    def candidate(self, body):
        """Called inside the durable distinct-decision transaction, never per tick."""
        lineage = coverage_lineage(session_id=body["session_id"], feed=body["feed"], symbol=body["symbol"],
            strategy_version=body["strategy_version"], configuration=body["shadow_configuration_fingerprint"])
        if lineage["strategy_version"] != self.strategy_version or lineage["shadow_configuration_fingerprint"] != self.configuration:
            raise ValueError("candidate coverage lineage mismatch")
        self.db.execute("INSERT INTO shadow_coverage_candidate VALUES (?,?,1) ON CONFLICT(id) DO UPDATE SET count=count+1",
                        (fingerprint(lineage), lineage["session_day"]))

    def save(self):
        with self.db:
            for identity, total in self.totals.items():
                self.db.execute("INSERT INTO shadow_coverage VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                                (identity, total["session_day"], json.dumps(total, allow_nan=False)))
            for table in ("shadow_coverage", "shadow_coverage_candidate"):
                self.db.execute(f"DELETE FROM {table} WHERE day NOT IN (SELECT DISTINCT day FROM {table} ORDER BY day DESC LIMIT 14)")
                self.db.execute(f"DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY day DESC,id LIMIT 5000)")
        # Runtime memory is bounded to current lineage; durable history is bounded separately.
        active = {p["id"] for p in self.cursors.values()}
        self.totals = {identity: total for identity, total in self.totals.items() if identity in active}

    def summary(self, store, now):
        self.advance(store, now)
        rows = {}
        for symbol in store.symbols:
            cursor = self.cursors.get(symbol)
            if not cursor:
                continue
            total = self.totals[cursor["id"]]
            candidate = self.db.execute("SELECT count FROM shadow_coverage_candidate WHERE id=?", (cursor["id"],)).fetchone()
            candidates = candidate[0] if candidate else 0
            hours = total["evaluable_seconds"]/3600
            rows[symbol] = {**total, "signal_candidates": candidates,
                "evaluable_symbol_hours": hours, "eligible_symbol_hours": total["eligible_seconds"]/3600,
                "signal_candidates_per_evaluable_symbol_hour": candidates/hours if hours else None}
        seconds = sum(r["evaluable_seconds"] for r in rows.values())
        eligible = sum(r["eligible_seconds"] for r in rows.values())
        candidates = sum(r["signal_candidates"] for r in rows.values())
        return {"symbols": rows, "evaluable_symbol_hours": seconds/3600, "eligible_symbol_hours": eligible/3600,
            "session_id": store.context[1] if store.context else None, "feed": store.context[0] if store.context else None,
            "strategy_version": self.strategy_version, "shadow_configuration_fingerprint": self.configuration,
            "evaluable_time_fraction": seconds/eligible if eligible else None, "signal_candidates": candidates,
            "signal_candidates_per_evaluable_symbol_hour": candidates*3600/seconds if seconds else None,
            "observed_at": utc(now).isoformat(), "provenance": "DERIVED", "source": "RHEN/shadow_coverage",
            "methodology_version": "source-deadline-exposure-v1", "entry_authority": False,
            "validation_state": "UNVALIDATED", "independent_evaluation_sessions": None,
            "restart_downtime_included": False, "retention_session_days": 14}
