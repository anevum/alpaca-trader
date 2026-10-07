"""Durable hysteresis/dwell state; never a profile release or execution grant."""
import json

from app.adaptive_policy import ORDER, fingerprint
from .contracts import utc


class PolicyStateStore:
    def __init__(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS shadow_policy_state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")

    @staticmethod
    def identity(controller):
        return fingerprint({"library": controller.library.fingerprint,
            "configuration": controller.configuration_fingerprint, "baseline": controller.baseline,
            "hard_limits": {k:str(v) for k,v in controller.hard_limits.items()},
            "dwell": controller.min_dwell_seconds, "confirmations": controller.confirmations})

    def save(self, controller, session_id, now):
        body = {"version": 1, "identity": self.identity(controller), "session_id": session_id,
            "saved_at": utc(now).isoformat(), "profile": controller.profile,
            "since": utc(controller.since).isoformat() if controller.since else None,
            "pending": controller.pending, "confirmed": controller.confirmed,
            "last_observation": utc(controller.last_observation).isoformat() if controller.last_observation else None,
            "entry_authority": False}
        body["checksum"] = fingerprint(body)
        with self.db:
            self.db.execute("INSERT INTO shadow_policy_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                            (json.dumps(body, allow_nan=False, sort_keys=True),))

    def restore(self, controller, session_id, now):
        row = self.db.execute("SELECT body FROM shadow_policy_state WHERE id=1").fetchone()
        if not row:
            return "UNAVAILABLE"
        try:
            body = json.loads(row[0])
            checksum = body.pop("checksum")
            if checksum != fingerprint(body) or body["version"] != 1 or body["entry_authority"] is not False:
                return "REJECTED_INTEGRITY"
            if body["identity"] != self.identity(controller) or body["session_id"] != session_id:
                return "REJECTED_LINEAGE"
            age = (utc(now)-utc(body["saved_at"])).total_seconds()
            if not 0 <= age <= 86400:
                return "REJECTED_STALE"
            profile, pending, confirmed = body["profile"], body["pending"], body["confirmed"]
            if profile not in (*ORDER, "BASELINE_LOCKED") or pending not in (*ORDER, None):
                return "REJECTED_STATE"
            if type(confirmed) is not int or not 0 <= confirmed <= controller.confirmations or (pending is None and confirmed != 0):
                return "REJECTED_STATE"
            since = utc(body["since"]) if body["since"] else None
            observed = utc(body["last_observation"]) if body["last_observation"] else None
            if any(t and t > utc(body["saved_at"]) for t in (since, observed)):
                return "REJECTED_TIME"
            if profile != "BASELINE_LOCKED" and since is None:
                return "REJECTED_STATE"
            # Assign atomically only after all checks; no execution snapshot is restored.
            controller.profile, controller.since = profile, since
            controller.pending, controller.confirmed = pending, confirmed
            controller.last_observation = observed
            return "RESTORED_SHADOW_ONLY"
        except (ValueError, TypeError, KeyError):
            return "REJECTED_STATE"
