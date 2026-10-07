"""Read-only release truth; configuration cannot grant 4.4 trading authority."""
from __future__ import annotations


def release_status(settings, observer=None):
    configured = {
        "market_stream": settings.rhen_market_stream_enabled,
        "broker_shadow_stream": settings.rhen_broker_stream_shadow_enabled,
        "command_stream": settings.command_live_stream_enabled,
    }
    running = observer is not None
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
        "broker_write_authority": False,
        "adaptive_active_available": False,
        "promotion_eligible": False,
        "promotion_blockers": [
            "canonical_integration_incomplete",
            "exact_configuration_and_broker_baseline_unattested",
            "live_entitlement_and_stream_coverage_unattested",
            "replay_and_shadow_validation_incomplete",
            "runtime_recovery_and_reconciliation_validation_incomplete",
            "command_live_visual_acceptance_incomplete",
            "research_and_holdout_gates_incomplete",
        ],
    }
