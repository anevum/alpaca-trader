from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from foundation.ingest.service import (
    EvidenceEvent,
    canonical_payload_hash,
    infer_run,
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
    asset_class, mode = infer_run(
        event(payload={"market_lane": "crypto", "execution_mode": "shadow"})
    )
    assert asset_class == "crypto"
    assert mode == "shadow"


def test_infer_equity_run():
    asset_class, mode = infer_run(
        event(payload={"market_lane": "us_equity", "trading_mode": "live"})
    )
    assert asset_class == "equity"
    assert mode == "live"


def test_infer_unknown_run_is_explicit():
    asset_class, mode = infer_run(event(payload={}))
    assert asset_class == "unknown"
    assert mode == "shadow_migration"


def test_ingest_token_is_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("FOUNDATION_INGEST_TOKEN", "foundation-secret")
    with pytest.raises(HTTPException) as missing:
        require_foundation_token(None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as wrong:
        require_foundation_token("wrong")
    assert wrong.value.status_code == 401
    require_foundation_token("foundation-secret")


def test_ingest_token_is_optional_on_private_staging(monkeypatch):
    monkeypatch.delenv("FOUNDATION_INGEST_TOKEN", raising=False)
    require_foundation_token(None)
