from app.research_agent.nostra_session import derive_nostra_regime_timeline


def candidate(cycle, observed_at, idx, *, return_5m, vwap_edge):
    return {
        "candidate_id": f"{cycle}-{idx}",
        "scan_cycle_id": cycle,
        "session": observed_at[:10],
        "observed_at": observed_at,
        "symbol": f"T{idx}",
        "qualified": return_5m > 0,
        "scan_cycle": {
            "scan_cycle_id": cycle,
            "cycle_key": f"cycle-{cycle}",
        } if idx == 0 else {},
        "features": {
            "vwap_edge_pct": vwap_edge,
            "regime_confirmations": {
                "SPY": {"window_return_pct": "0.002"},
                "QQQ": {"window_return_pct": "0.003"},
                "IWM": {"window_return_pct": "0.001"},
            },
            "ads002_v2_raw_features": {
                "return_5m": return_5m,
                "fast_slow_spread_pct": "0.001",
                "vwap_edge_pct": vwap_edge,
                "relative_volume_ratio": "1.2",
                "realized_vol_5m": "0.002",
                "spread_bps": "5",
            },
        },
    }


def test_nostra_timeline_uses_last_cycle_in_each_five_minute_bucket():
    rows = []
    for idx in range(4):
        rows.append(
            candidate(
                "1",
                "2026-09-29T10:01:20-04:00",
                idx,
                return_5m=0.002,
                vwap_edge=0.001,
            )
        )
        rows.append(
            candidate(
                "2",
                "2026-09-29T10:04:40-04:00",
                idx,
                return_5m=0.003,
                vwap_edge=0.002,
            )
        )
        rows.append(
            candidate(
                "3",
                "2026-09-29T10:06:10-04:00",
                idx,
                return_5m=-0.001,
                vwap_edge=-0.0005,
            )
        )
    result = derive_nostra_regime_timeline(rows)
    assert result["observations"] == 2
    assert result["timeline"][0]["cycle_key"] == "cycle-2"
    assert result["timeline"][1]["cycle_key"] == "cycle-3"
    assert result["latest"]["execution_authority"] is False


def test_nostra_timeline_never_fabricates_15m_benchmark_returns():
    rows = [
        candidate(
            "1",
            "2026-09-29T10:01:20-04:00",
            1,
            return_5m=0.002,
            vwap_edge=0.001,
        )
    ]
    result = derive_nostra_regime_timeline(rows)
    state = result["timeline"][0]["market_state"]
    assert state["benchmark_return_15m"] is None
    assert result["benchmark_15m_available"] is False


def test_nostra_timeline_is_read_only():
    result = derive_nostra_regime_timeline([])
    assert result["read_only"] is True
    assert result["execution_authority"] is False
    assert result["live_configuration_changed"] is False
