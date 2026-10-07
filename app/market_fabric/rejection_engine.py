from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime

CLASSIFICATIONS = {"NOT_EVALUABLE", "EVALUABLE_REJECTED", "CANDIDATE", "ORDER_INTENT_BLOCKED", "ORDER_INTENT_CREATED"}
REASONS = frozenset("FEED_UNAVAILABLE FEED_MISMATCH STREAM_DISCONNECTED STREAM_WARMING SUBSCRIPTION_MISSING STALE_QUOTE STALE_BAR STALE_TRADE NO_BID NO_ASK LOCKED_OR_CROSSED OUT_OF_ORDER_STATE DATA_GAP ASSET_INELIGIBLE OVERNIGHT_NOT_TRADABLE OVERNIGHT_HALTED SESSION_NOT_AUTHORIZED DATA_CAPABILITY_BLOCKED INSUFFICIENT_OBSERVATIONS INSUFFICIENT_ACTIVITY SPREAD_TOO_WIDE LIQUIDITY_TOO_LOW VOLATILITY_TOO_LOW VOLATILITY_TOO_HIGH SIGNAL_BELOW_THRESHOLD MOMENTUM_FAIL RELATIVE_STRENGTH_FAIL REGIME_FAIL CONFIRMATION_FAIL VWAP_EXTENSION_FAIL NET_EDGE_FAIL ALREADY_POSITIONED DUPLICATE_ORDER CORRELATION_CAP SYMBOL_EXPOSURE_CAP GROSS_EXPOSURE_CAP PORTFOLIO_RISK_CAP BUYING_POWER DAILY_LOSS_BREAKER COOLDOWN EXECUTION_NOT_AUTHORIZED POLICY_NOT_PROMOTED BROKER_WRITE_DISABLED ORDER_CONSTRAINT LIMIT_PRICE_INVALID BROKER_REJECTED RECONCILIATION_UNSAFE".split())


@dataclass(frozen=True)
class Evaluation:
    event_id: str
    at: datetime
    session: str
    feed: str
    symbol: str
    classification: str
    reasons: tuple[str, ...] = ()
    eligible: bool = True
    strategy_version: str = "4.3-observation-only"
    policy_profile: str = "BASELINE_LOCKED"

    def __post_init__(self):
        if self.classification not in CLASSIFICATIONS or any(r not in REASONS for r in self.reasons):
            raise ValueError("unknown terminal classification/rejection")
        if self.classification in {"NOT_EVALUABLE", "EVALUABLE_REJECTED", "ORDER_INTENT_BLOCKED"} and not self.reasons:
            raise ValueError("rejected evaluation needs a reason")
        if self.classification in {"CANDIDATE", "ORDER_INTENT_CREATED"} and self.reasons:
            raise ValueError("qualified evaluation cannot carry rejection")


class RejectionEngine:
    def __init__(self, max_events=20000):
        self.events = deque(maxlen=max_events)
        self.ids = set()

    def record(self, event: Evaluation) -> bool:
        if event.event_id in self.ids:
            return False
        if len(self.events) == self.events.maxlen:
            self.ids.remove(self.events[0].event_id)
        self.events.append(event)
        self.ids.add(event.event_id)
        return True

    def summary(self, now, *, seconds=60, session=None, feed=None):
        rows = [e for e in self.events if 0 <= (now-e.at).total_seconds() <= seconds
                and (session is None or e.session == session) and (feed is None or e.feed == feed)]
        eligible = sum(e.eligible for e in rows)
        evaluable = sum(e.eligible and e.classification != "NOT_EVALUABLE" for e in rows)
        candidates = sum(e.classification in {"CANDIDATE", "ORDER_INTENT_CREATED", "ORDER_INTENT_BLOCKED"} for e in rows)
        counts = Counter(e.reasons[0] for e in rows if e.reasons)
        return {"eligible_evaluations": eligible, "evaluable_evaluations": evaluable,
                "evaluable_rate": evaluable/eligible if eligible else None, "candidates": candidates,
                "candidate_rate_per_1000_evaluations": 1000*candidates/evaluable if evaluable else None,
                "order_intents": sum(e.classification == "ORDER_INTENT_CREATED" for e in rows),
                "rejection_counts": dict(counts), "pipeline_diagnostic": "DATA_OR_WARMUP_BLOCKED" if eligible and evaluable/eligible < .5 and not candidates else None,
                "window_seconds": seconds, "retained_events": len(self.events), "retention_capacity": self.events.maxlen,
                "window_may_be_truncated": len(self.events) == self.events.maxlen and bool(rows) and rows[0] == self.events[0]}
