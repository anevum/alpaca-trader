"""Read-only release truth; configuration cannot grant 4.4 trading authority."""
from __future__ import annotations


def release_status(settings, observer=None):
    configured = {
        "market_stream": settings.rhen_market_stream_enabled,
        "broker_shadow_stream": settings.rhen_broker_stream_shadow_enabled,
        "command_stream": settings.command_live_stream_enabled,
    }
    running = observer is not None
    discovery_size = int(settings.universe_size)
    stream_capacity = int(settings.rhen_market_stream_capacity)
    configured_stream_symbols = len(settings.extended_equity_symbols)
    rotation_required = discovery_size > stream_capacity
    blockers = [
        "exact_configuration_and_broker_baseline_unattested",
        "live_entitlement_and_stream_coverage_unattested",
        "replay_and_shadow_validation_incomplete",
        "runtime_recovery_and_reconciliation_validation_incomplete",
        "command_live_visual_acceptance_incomplete",
        "research_and_holdout_gates_incomplete",
    ]
    hotset = getattr(observer, "hotset_status", {}) if running else {}
    rotation_integrated = bool(hotset.get("integrated"))
    rotation_active = bool(hotset.get("active"))
    if rotation_required and not rotation_integrated:
        blockers.append("discovery_stream_hotset_rotation_incomplete")
    return {
        "provenance": "OPERATIONAL",
        "implementation_version": "rhen-4.4-foundations-v1",
        "implementation_complete": False,
        "champion_behavior": "4.3",
        "state": "SHADOW_ONLY" if running else "IMPLEMENTED_GATED",
        "configured": configured,
        "observer_constructed": running,
        "broker_stream_state": observer.broker.state if running and observer.broker else "DISABLED",
        "command_stream_available": running and configured["command_stream"],
        "universe_contract": {
            "model": "BROAD_DISCOVERY_NARROW_STREAM",
            "discovery_universe_size": discovery_size,
            "stream_capacity": stream_capacity,
            "configured_stream_symbols": configured_stream_symbols,
            "hotset_rotation_required": rotation_required,
            "hotset_rotation_integrated": rotation_integrated,
            "hotset_rotation_active": rotation_active,
            "hotset_quality_state": hotset.get("quality_state","UNAVAILABLE"),
            "hotset_rotation_count": hotset.get("rotation_count",0),
            "current_stream_symbols": len(observer.store.symbols) if running else configured_stream_symbols,
        },
        "broker_write_authority": False,
        "adaptive_active_available": False,
        "promotion_eligible": False,
        "promotion_blockers": blockers,
    }
