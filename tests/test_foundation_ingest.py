from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from foundation.ingest.service import (
    EvidenceEvent,
    canonical_payload_hash,
    infer_run,
    reconciliation_result,
    require_foundation_token,
)


def event(**overrides):
    values = {
        "event_key": "run-1:test:1",
        "run_id": "run-1",
        "strategy_version_id": "strategy-1",
        "event_type": "test",
        "occurred_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        "source": "test",
        "payload": {},
    }
    values.update(overrides)
    return EvidenceEvent(**values)


def test_payload_hash_is_canonical():
    left = canonical_payload_hash({"b": 2, "a": 1})
    right = canonical_payload_hash({"a": 1, "b": 2})
    assert left == right
    assert left.startswith("sha256:")


def test_infer_crypto_run():
    assert infer_run(event(payload={"market_lane":"crypto","execution_mode":"shadow"})) == ("crypto","shadow")


def test_infer_equity_run():
    assert infer_run(event(payload={"market_lane":"us_equity","trading_mode":"live"})) == ("equity","live")


def test_ingest_token_accepts_legacy_header_alias(monkeypatch):
    monkeypatch.setenv("FOUNDATION_INGEST_TOKEN", "foundation-secret")
    require_foundation_token("foundation-secret", None)
    require_foundation_token(None, "foundation-secret")
    with pytest.raises(HTTPException):
        require_foundation_token(None, "wrong")


def test_reconciliation_is_safe_only_without_blockers():
    observed = datetime(2026,10,1,tzinfo=timezone.utc)
    safe = reconciliation_result(
        unresolved_intents=[],
        unknown_open_orders=[],
        untracked_positions=[],
        observed_at=observed,
    )
    assert safe["safe_to_enter"] is True
    blocked = reconciliation_result(
        unresolved_intents=[{"client_order_id":"anevum-a"}],
        unknown_open_orders=[],
        untracked_positions=[],
        observed_at=observed,
    )
    assert blocked["safe_to_enter"] is False
    assert blocked["reason"] == "unresolved_intents:1"
