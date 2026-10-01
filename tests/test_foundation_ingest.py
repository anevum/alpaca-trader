from datetime import datetime, timezone

from foundation.ingest.service import (
    EvidenceEvent,
    canonical_payload_hash,
    infer_run,
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
