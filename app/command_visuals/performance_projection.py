"""Account sample diagnostics, explicitly not flow-adjusted strategy returns."""
import json
from zoneinfo import ZoneInfo

from app.adaptive_policy import fingerprint
from app.market_fabric.contracts import number, utc


class AccountPerformance:
    def __init__(self, db):
        self.db = db
        db.execute("CREATE TABLE IF NOT EXISTS shadow_account_baseline (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")

    def observe(self, snapshot, now):
        now = utc(now)
        account = snapshot["account"]
        if not account.get("id") or not account.get("currency") or account.get("equity") is None:
            return {"quality_state":"UNAVAILABLE","reason":"ACCOUNT_IDENTITY_OR_EQUITY_MISSING","points":{}}
        identity = fingerprint({"account":account["id"],"currency":account["currency"]})
        day = now.astimezone(ZoneInfo("America/New_York")).date().isoformat()
        equity = number(account["equity"],positive=True)
        old = self.db.execute("SELECT body FROM shadow_account_baseline WHERE id=1").fetchone()
        state = json.loads(old[0]) if old else None
        if state and (state.get("identity") != identity or state.get("day") != day):
            state = None
        if state and utc(state["last_at"]) > now:
            raise ValueError("out-of-order account performance sample")
        if not state:
            state = {"identity":identity,"day":day,"baseline":equity,"peak":equity,"baseline_at":now.isoformat(),"samples":0}
        state.update(peak=max(state["peak"],equity),last_at=now.isoformat(),samples=state["samples"]+1)
        with self.db:
            self.db.execute("INSERT INTO shadow_account_baseline VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body", (json.dumps(state,allow_nan=False),))
        gross = sum(abs(number(p["market_value"])) for p in snapshot["positions"])
        values = {"normalized_equity":equity/state["baseline"]*100,
                  "sampled_drawdown_pct":(equity/state["peak"]-1)*100,
                  "gross_exposure_pct":gross/equity*100}
        points = {key:{"timestamp":now.isoformat(),"value":value,"source":"ALPACA/REST_RECONCILIATION",
            "provenance":"DERIVED","quality_state":"LIVE","methodology_version":"account-observation-diagnostics-v1"} for key,value in values.items()}
        return {"points":points,"quality_state":"LIVE","baseline_at":state["baseline_at"],"session_day":day,
            "samples":state["samples"],"cash_flow_adjustment":"UNAVAILABLE","strategy_performance_state":"UNAVAILABLE",
            "scope":"BROKER_ACCOUNT_SAMPLES","provenance":"DERIVED","source":"ALPACA/REST_RECONCILIATION",
            "methodology_version":"account-observation-diagnostics-v1","entry_authority":False}
