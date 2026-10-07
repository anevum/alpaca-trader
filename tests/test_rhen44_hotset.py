from datetime import datetime, timedelta, timezone
import sqlite3

from app.market_fabric.hotset import HotsetSelector
from app.market_fabric.stream_state import MarketStateStore
from app.market_fabric.asset_eligibility import AssetEligibility


NOW = datetime(2026,10,7,20,0,tzinfo=timezone.utc)


def test_hotset_is_bounded_pinned_rate_limited_and_persistent():
    db=sqlite3.connect(":memory:")
    ranked=[f"S{i:03d}" for i in range(100)]
    selector=HotsetSelector(db,capacity=24,pinned=("SPY","QQQ"),min_dwell_seconds=300,max_changes=4)
    current=tuple(["SPY","QQQ"]+[f"OLD{i:02d}" for i in range(22)])
    first,status=selector.propose(ranked,current,NOW)
    assert len(first)==24 and {"SPY","QQQ"} <= set(first)
    assert status["changed"] is True and status["added_count"] <= 4 and status["removed_count"] <= 4
    held,held_status=selector.propose(list(reversed(ranked)),first,NOW+timedelta(seconds=60))
    assert held==first and held_status["reason"]=="DWELL_HOLD"
    restored=HotsetSelector(db,capacity=24,pinned=("SPY","QQQ"),min_dwell_seconds=300,max_changes=4)
    assert restored.restored_symbols==first


def test_hotset_fails_closed_on_undersized_discovery():
    db=sqlite3.connect(":memory:")
    selector=HotsetSelector(db,capacity=24,pinned=("SPY","QQQ"))
    current=tuple(["SPY","QQQ"]+[f"OLD{i:02d}" for i in range(22)])
    proposed,status=selector.propose(["AAPL","NVDA"],current,NOW)
    assert proposed==current
    assert status["active"] is False
    assert status["quality_state"]=="UNAVAILABLE"
    assert status["entry_authority"] is False and status["broker_write_authority"] is False


def test_symbol_rotation_invalidates_subscription_and_asset_attestation():
    store=MarketStateStore(("SPY","QQQ","AAPL"),warm_bars=2)
    store.subscribed={"SPY","QQQ","AAPL"}
    added,removed=store.rotate_symbols(("SPY","QQQ","NVDA"))
    assert added==("NVDA",) and removed==("AAPL",)
    assert store.subscribed=={"SPY","QQQ"}

    db=sqlite3.connect(":memory:")
    assets=AssetEligibility(db,("SPY","QQQ","AAPL"))
    assets.replace([
        {"symbol":"SPY","class":"us_equity","status":"active","tradable":True,"fractionable":True,
         "overnight_tradable":True,"overnight_halted":False},
        {"symbol":"QQQ","class":"us_equity","status":"active","tradable":True,"fractionable":True,
         "overnight_tradable":True,"overnight_halted":False},
        {"symbol":"AAPL","class":"us_equity","status":"active","tradable":True,"fractionable":True,
         "overnight_tradable":True,"overnight_halted":False},
    ],NOW)
    assert assets.rotate_symbols(("SPY","QQQ","NVDA")) is True
    assert assets.fetched_at is None
    assert assets.snapshot("NVDA","REGULAR",NOW)["eligible"] is False
