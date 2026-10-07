"""Bounded observation checkpoints; no broker or strategy mutation authority."""
import json
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


class ExtendedResearchRecorder:
    def __init__(self, engine, sink):
        self.engine = engine
        self.sink = sink
        self.last_key = None
        self.last_error = None
        self.last_confirmed_at = None

    async def capture(self, now=None):
        now = now or datetime.now(timezone.utc)
        snapshot = self.engine.snapshot()
        observed = snapshot.get("scan_observed_at")
        if not observed or not self.sink.enabled:
            return False
        stamp = datetime.fromisoformat(observed)
        if not 0 <= (now - stamp).total_seconds() <= 90:
            return False
        session = (snapshot.get("session") or {}).get("session")
        scan = snapshot.get("scanner") or {}
        if session not in {"premarket", "after_hours", "overnight"} or not scan:
            return False
        bucket = stamp.replace(minute=stamp.minute - stamp.minute % 5, second=0, microsecond=0)
        strategy = snapshot.get("strategy_version_id")
        key = f"extended-research:{strategy}:{session}:{bucket.isoformat()}"
        if key == self.last_key:
            return False
        reasons = Counter(str(row.get("reason") or "unclassified") for row in scan.values() if row.get("action") != "buy")
        payload = {"schema_version": "extended-research-v1", "session_date": stamp.astimezone(NY).date().isoformat(),
            "market_lane": "us_equity_extended", "market_session": session,
            "strategy_version_id": strategy, "observed_at": observed, "checkpoint_minutes": 5,
            "observed_symbols": len(scan),
            "qualified_symbols": sum(row.get("action") == "buy" for row in scan.values()),
            "rejection_reasons": dict(reasons), "samples": [
                {"symbol": symbol, "action": row.get("action"), "reason": row.get("reason"),
                 "decision_reference_price": row.get("reference_price"), "metadata": row.get("metadata") or {}}
                for symbol, row in sorted(scan.items())[:100]],
            "sample_limit": 100, "samples_truncated": len(scan) > 100,
            "analytics_only": True, "live_configuration_changed": False,
            "forward_outcomes": "NOT_MEASURED", "independent_session_count": 0}
        try:
            confirmed = await self.sink.emit_critical(event_type="extended_research_snapshot",
                event_key=key, occurred_at=observed, strategy_version_id=strategy, payload=payload)
            if not confirmed:
                raise RuntimeError("extended_research_write_unconfirmed")
            print(json.dumps({"event": "extended_research_checkpoint_confirmed",
                "session": session, "observed_at": observed,
                "observed_symbols": payload["observed_symbols"],
                "qualified_symbols": payload["qualified_symbols"],
                "rejection_reasons": dict(reasons)}, sort_keys=True), flush=True)
            self.last_key = key
            self.last_confirmed_at = observed
            self.last_error = None
            return True
        except Exception as exc:
            self.last_error = type(exc).__name__ + ": " + str(exc)
            return False
