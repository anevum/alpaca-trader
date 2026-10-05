from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Mapping

import httpx
from fastapi import FastAPI, HTTPException

from app.config import Settings, get_settings
from app.market_data import MarketDataClient
from app.graen.service import GraenGateway
from app.graen.research_promotion import ResearchPromotion, engineering_problem_ids
from app.graen.research_director_client import ResearchDirectorClient
from app.research_agent.autonomy import build_engineering_requirement
from app.research_agent.hypothesis_planner import plan_next as plan_next_hypothesis
from app.research_agent.strategy_grammar import manifest_from_dict
from app.research_agent.strategy_runner import (
    RUNNER_VERSION as STRATEGY_RUNNER_VERSION,
    compile_candidate as compile_strategy_candidate,
    evaluate_development as evaluate_strategy_development,
    evaluate_holdout as evaluate_strategy_holdout,
    evaluate_validation as evaluate_strategy_validation,
)
from graen.crypto.research_v7 import (
    CONTEXT_UNIVERSE,
    METHODOLOGY_VERSION as V7_METHODOLOGY_VERSION,
    RESEARCH_BATCH_ID as V7_RESEARCH_BATCH_ID,
    candidate_specs as v7_candidate_specs,
    verify_v7_corpus_contract,
    evaluate_v7_development,
    evaluate_v7_validation,
    evaluate_v7_holdout,
)
from graen.crypto.leadlag_r2 import (
    UNIVERSE as LEADLAG_UNIVERSE,
    METHODOLOGY_VERSION as LEADLAG_METHODOLOGY_VERSION,
    research_specification as leadlag_research_specification,
    evaluate_stage as evaluate_leadlag_stage,
    development_gate as leadlag_development_gate,
    validation_gate as leadlag_validation_gate,
    holdout_gate as leadlag_holdout_gate,
)
from graen.crypto.autonomous_campaign import (
    CAMPAIGN_ID as AUTONOMOUS_CAMPAIGN_ID,
    CONTEXT_UNIVERSE as AUTONOMOUS_UNIVERSE,
    MAX_GENERATIONS_PER_EPOCH,
    METHODOLOGY_PREFIX as AUTONOMOUS_METHODOLOGY_PREFIX,
    epoch_contract as autonomous_epoch_contract,
    evaluate_development as evaluate_autonomous_development,
    evaluate_holdout as evaluate_autonomous_holdout,
    evaluate_validation as evaluate_autonomous_validation,
    methodology_version as autonomous_methodology_version,
    next_epoch_index as autonomous_next_epoch_index,
    prespecification as autonomous_prespecification,
    stage_metadata as autonomous_stage_metadata,
)
from graen.crypto.activity_shock_v9 import (
    FAMILY as V9_FAMILY,
    METHODOLOGY_VERSION as V9_METHODOLOGY_VERSION,
    UNIVERSE as V9_UNIVERSE,
    candidate_specs as v9_candidate_specs,
    evaluate_development as evaluate_v9_development,
    evaluate_holdout as evaluate_v9_holdout,
    evaluate_validation as evaluate_v9_validation,
    verify_stage_corpus as verify_v9_stage_corpus,
)
from graen.crypto.trend_pullback_v10 import (
    FAMILY as V10_FAMILY,
    METHODOLOGY_VERSION as V10_METHODOLOGY_VERSION,
    UNIVERSE as V10_UNIVERSE,
    candidate_specs as v10_candidate_specs,
    evaluate_development as evaluate_v10_development,
    evaluate_holdout as evaluate_v10_holdout,
    evaluate_validation as evaluate_v10_validation,
    verify_stage_corpus as verify_v10_stage_corpus,
)
from graen.crypto.btc_trend_pullback_v11 import (
    CAMPAIGN_ID as V11_CAMPAIGN_ID,
    DEVELOPMENT_END as V11_DEVELOPMENT_END,
    DEVELOPMENT_START as V11_DEVELOPMENT_START,
    FAMILY as V11_FAMILY,
    METHODOLOGY_VERSION as V11_METHODOLOGY_VERSION,
    UNIVERSE as V11_UNIVERSE,
    candidate_specs as v11_candidate_specs,
    evaluate_development as evaluate_v11_development,
    verify_development_corpus as verify_v11_development_corpus,
)
from graen.crypto.btc_mechanisms_v12 import (
    CAMPAIGN_ID as V12_CAMPAIGN_ID,
    DEVELOPMENT_END as V12_DEVELOPMENT_END,
    DEVELOPMENT_START as V12_DEVELOPMENT_START,
    FAMILY as V12_FAMILY,
    METHODOLOGY_VERSION as V12_METHODOLOGY_VERSION,
    UNIVERSE as V12_UNIVERSE,
    candidate_specs as v12_candidate_specs,
    evaluate_development as evaluate_v12_development,
    verify_development_corpus as verify_v12_development_corpus,
)
from graen.crypto.btc_hypotheses_v13 import (
    CAMPAIGN_ID as V13_CAMPAIGN_ID,
    DEVELOPMENT_END as V13_DEVELOPMENT_END,
    DEVELOPMENT_START as V13_DEVELOPMENT_START,
    FAMILY as V13_FAMILY,
    METHODOLOGY_VERSION as V13_METHODOLOGY_VERSION,
    UNIVERSE as V13_UNIVERSE,
    candidate_specs as v13_candidate_specs,
    evaluate_development as evaluate_v13_development,
    hypothesis_registry as v13_hypothesis_registry,
    verify_development_corpus as verify_v13_development_corpus,
)
from graen.crypto.btc_xgb_replication_v14 import (
    ALPACA_SCREEN_END as V14_ALPACA_SCREEN_END,
    ALPACA_SCREEN_START as V14_ALPACA_SCREEN_START,
    CAMPAIGN_ID as V14_CAMPAIGN_ID,
    FAMILY as V14_FAMILY,
    METHODOLOGY_VERSION as V14_METHODOLOGY_VERSION,
    UNIVERSE as V14_UNIVERSE,
    campaign_manifest as v14_campaign_manifest,
    evaluate_alpaca_transfer_screen as evaluate_v14_alpaca_transfer_screen,
)
from graen.crypto.btc_queue_imbalance_v14_r2a import (
    CAMPAIGN_ID as V14_R2A_CAMPAIGN_ID,
    FAMILY as V14_R2A_FAMILY,
    METHODOLOGY_VERSION as V14_R2A_METHODOLOGY_VERSION,
    QUOTE_SCREEN_END as V14_R2A_QUOTE_SCREEN_END,
    QUOTE_SCREEN_START as V14_R2A_QUOTE_SCREEN_START,
    UNIVERSE as V14_R2A_UNIVERSE,
    campaign_manifest as v14_r2a_campaign_manifest,
    evaluate_queue_imbalance_preflight as evaluate_v14_r2a_queue_imbalance,
)
from graen.crypto.btc_passive_scalping_v14_r2b import (
    BAR_SCREEN_END as V14_R2B_BAR_SCREEN_END,
    BAR_SCREEN_START as V14_R2B_BAR_SCREEN_START,
    CAMPAIGN_ID as V14_R2B_CAMPAIGN_ID,
    FAMILY as V14_R2B_FAMILY,
    METHODOLOGY_VERSION as V14_R2B_METHODOLOGY_VERSION,
    UNIVERSE as V14_R2B_UNIVERSE,
    campaign_manifest as v14_r2b_campaign_manifest,
    evaluate_passive_scalping_preflight as evaluate_v14_r2b_passive_scalping,
)
from graen.crypto.cross_sectional_momentum_v14_r2c import (
    BAR_SCREEN_END as V14_R2C_BAR_SCREEN_END,
    BAR_SCREEN_START as V14_R2C_BAR_SCREEN_START,
    CAMPAIGN_ID as V14_R2C_CAMPAIGN_ID,
    FAMILY as V14_R2C_FAMILY,
    METHODOLOGY_VERSION as V14_R2C_METHODOLOGY_VERSION,
    UNIVERSE as V14_R2C_UNIVERSE,
    campaign_manifest as v14_r2c_campaign_manifest,
    evaluate_cross_sectional_momentum_preflight as evaluate_v14_r2c_momentum,
)
from graen.crypto.triangular_arbitrage_v14_r2d import (
    CAMPAIGN_ID as V14_R2D_CAMPAIGN_ID,
    FAMILY as V14_R2D_FAMILY,
    METHODOLOGY_VERSION as V14_R2D_METHODOLOGY_VERSION,
    PAIRS as V14_R2D_PAIRS,
    QUOTE_SCREEN_END as V14_R2D_QUOTE_SCREEN_END,
    QUOTE_SCREEN_START as V14_R2D_QUOTE_SCREEN_START,
    campaign_manifest as v14_r2d_campaign_manifest,
    evaluate_triangular_arbitrage_preflight as evaluate_v14_r2d_triangular_arbitrage,
)
from graen.crypto.btc_4h_trend_v14_r2e import (
    BAR_SCREEN_END as V14_R2E_BAR_SCREEN_END,
    BAR_SCREEN_START as V14_R2E_BAR_SCREEN_START,
    CAMPAIGN_ID as V14_R2E_CAMPAIGN_ID,
    FAMILY as V14_R2E_FAMILY,
    METHODOLOGY_VERSION as V14_R2E_METHODOLOGY_VERSION,
    UNIVERSE as V14_R2E_UNIVERSE,
    campaign_manifest as v14_r2e_campaign_manifest,
    evaluate_btc_4h_trend_preflight as evaluate_v14_r2e_btc_4h_trend,
)
from graen.crypto.btc_slow_momentum_v14_r2f import (
    BAR_SCREEN_END as V14_R2F_BAR_SCREEN_END,
    BAR_SCREEN_START as V14_R2F_BAR_SCREEN_START,
    CAMPAIGN_ID as V14_R2F_CAMPAIGN_ID,
    FAMILY as V14_R2F_FAMILY,
    METHODOLOGY_VERSION as V14_R2F_METHODOLOGY_VERSION,
    UNIVERSE as V14_R2F_UNIVERSE,
    campaign_manifest as v14_r2f_campaign_manifest,
    candidate_spec as v14_r2f_candidate_spec,
    evaluate_btc_slow_momentum_discovery as evaluate_v14_r2f_slow_momentum,
)
from graen.crypto.btc_consensus_trend_v14_r2g import (
    CAMPAIGN_ID as V14_R2G_CAMPAIGN_ID,
    FAMILY as V14_R2G_FAMILY,
    METHODOLOGY_VERSION as V14_R2G_METHODOLOGY_VERSION,
    campaign_manifest as v14_r2g_campaign_manifest,
    candidate_spec as v14_r2g_candidate_spec,
    evaluate_btc_consensus_trend_discovery as evaluate_v14_r2g_consensus_trend,
)
from graen.crypto.btc_4h_consensus_v14_r2h import (
    BAR_SCREEN_END as V14_R2H_BAR_SCREEN_END,
    CAMPAIGN_ID as V14_R2H_CAMPAIGN_ID,
    FAMILY as V14_R2H_FAMILY,
    METHODOLOGY_VERSION as V14_R2H_METHODOLOGY_VERSION,
    campaign_manifest as v14_r2h_campaign_manifest,
    candidate_spec as v14_r2h_candidate_spec,
    evaluate_btc_4h_consensus_transfer as evaluate_v14_r2h_consensus_transfer,
)


UTC = timezone.utc
RUNTIME_VERSION = "graen-research-executor-v1.30.0"
PROBLEM_DOMAIN = "CRYPTO_STRATEGY_RESEARCH"

DEVELOPMENT_START = datetime(2025, 5, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
HOLDOUT_START = datetime(2025, 8, 1, tzinfo=UTC)
HOLDOUT_END = datetime(2025, 9, 1, tzinfo=UTC)

LEADLAG_DEVELOPMENT_START = datetime(2025, 12, 1, tzinfo=UTC)
LEADLAG_VALIDATION_START = datetime(2025, 12, 21, tzinfo=UTC)
LEADLAG_HOLDOUT_START = datetime(2026, 1, 11, tzinfo=UTC)
LEADLAG_HOLDOUT_END = datetime(2026, 1, 31, 16, 0, tzinfo=UTC)
LEADLAG_STAGE_KEY = "CRYPTO_LEADLAG_R2_READY"

AUTONOMOUS_DEVELOPMENT_STAGE = "CRYPTO_AUTONOMOUS_DEVELOPMENT"
AUTONOMOUS_VALIDATION_STAGE = "CRYPTO_AUTONOMOUS_VALIDATION"
AUTONOMOUS_HOLDOUT_STAGE = "CRYPTO_AUTONOMOUS_HOLDOUT"
AUTONOMOUS_VELUM_STAGE = "CRYPTO_AUTONOMOUS_VELUM_REPLAY"
AUTONOMOUS_STAGE_KEYS = {
    AUTONOMOUS_DEVELOPMENT_STAGE,
    AUTONOMOUS_VALIDATION_STAGE,
    AUTONOMOUS_HOLDOUT_STAGE,
    AUTONOMOUS_VELUM_STAGE,
}

V9_CAMPAIGN_ID = "crypto-activity-shock-v9"
V9_DEVELOPMENT_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_DEVELOPMENT"
V9_VALIDATION_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_VALIDATION"
V9_HOLDOUT_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_HOLDOUT"
V9_VELUM_STAGE = "CRYPTO_ACTIVITY_SHOCK_V9_VELUM_REPLAY"
V9_STAGE_KEYS = {
    V9_DEVELOPMENT_STAGE,
    V9_VALIDATION_STAGE,
    V9_HOLDOUT_STAGE,
    V9_VELUM_STAGE,
}
V9_EPOCHS = (
    {
        "name": "PRE2024-A",
        "development_start": datetime(2022, 1, 1, tzinfo=UTC),
        "validation_start": datetime(2022, 7, 1, tzinfo=UTC),
        "holdout_start": datetime(2022, 10, 1, tzinfo=UTC),
        "holdout_end": datetime(2023, 1, 1, tzinfo=UTC),
        "development_availability_probe_disclosed": [
            "2022-01-01T00:00:00+00:00",
            "2022-01-03T00:00:00+00:00",
        ],
    },
    {
        "name": "PRE2024-B",
        "development_start": datetime(2023, 1, 1, tzinfo=UTC),
        "validation_start": datetime(2023, 7, 1, tzinfo=UTC),
        "holdout_start": datetime(2023, 10, 1, tzinfo=UTC),
        "holdout_end": datetime(2024, 1, 1, tzinfo=UTC),
        "development_availability_probe_disclosed": [
            "2023-01-01T00:00:00+00:00",
            "2023-01-05T00:00:00+00:00",
        ],
    },
)


def _v9_epoch_contract(epoch_index: int) -> dict[str, Any]:
    if epoch_index < 0 or epoch_index >= len(V9_EPOCHS):
        raise ValueError("v9_epoch_out_of_range")
    return dict(V9_EPOCHS[epoch_index])


def _v9_next_epoch(epoch_index: int) -> int | None:
    candidate = epoch_index + 1
    return candidate if candidate < len(V9_EPOCHS) else None

V10_CAMPAIGN_ID = "crypto-trend-pullback-v10"
V10_DEVELOPMENT_STAGE = "CRYPTO_TREND_PULLBACK_V10_DEVELOPMENT"
V10_VALIDATION_STAGE = "CRYPTO_TREND_PULLBACK_V10_VALIDATION"
V10_HOLDOUT_STAGE = "CRYPTO_TREND_PULLBACK_V10_HOLDOUT"
V10_VELUM_STAGE = "CRYPTO_TREND_PULLBACK_V10_VELUM_REPLAY"
V10_STAGE_KEYS = {
    V10_DEVELOPMENT_STAGE,
    V10_VALIDATION_STAGE,
    V10_HOLDOUT_STAGE,
    V10_VELUM_STAGE,
}
V10_EPOCHS = V9_EPOCHS

V11_DEVELOPMENT_STAGE = "CRYPTO_BTC_TREND_PULLBACK_V11_DEVELOPMENT"
V11_VELUM_STAGE = "CRYPTO_BTC_TREND_PULLBACK_V11_VELUM_REPLAY"
V11_STAGE_KEYS = {
    V11_DEVELOPMENT_STAGE,
    V11_VELUM_STAGE,
}

V12_DEVELOPMENT_STAGE = "CRYPTO_BTC_MECHANISMS_V12_DEVELOPMENT"
V12_VELUM_STAGE = "CRYPTO_BTC_MECHANISMS_V12_VELUM_REPLAY"
V12_STAGE_KEYS = {
    V12_DEVELOPMENT_STAGE,
    V12_VELUM_STAGE,
}

V13_DEVELOPMENT_STAGE = "CRYPTO_BTC_HYPOTHESES_V13_DEVELOPMENT"
V13_VELUM_STAGE = "CRYPTO_BTC_HYPOTHESES_V13_VELUM_REPLAY"
V13_VALIDATION_STAGE = "CRYPTO_BTC_HYPOTHESES_V13_VALIDATION"
V13_HOLDOUT_STAGE = "CRYPTO_BTC_HYPOTHESES_V13_HOLDOUT"
V13_STAGE_KEYS = {
    V13_DEVELOPMENT_STAGE,
    V13_VELUM_STAGE,
    V13_VALIDATION_STAGE,
    V13_HOLDOUT_STAGE,
}

V14_PREFLIGHT_STAGE = "CRYPTO_BTC_XGB_V14_R1_BROKER_PREFLIGHT"
V14_R2A_STAGE = "CRYPTO_BTC_QUEUE_IMBALANCE_V14_R2A_PREFLIGHT"
V14_R2B_STAGE = "CRYPTO_BTC_PASSIVE_SCALPING_V14_R2B_PREFLIGHT"
V14_R2C_STAGE = "CRYPTO_CROSS_SECTIONAL_MOMENTUM_V14_R2C_PREFLIGHT"
V14_R2D_STAGE = "CRYPTO_TRIANGULAR_ARBITRAGE_V14_R2D_PREFLIGHT"
V14_R2E_STAGE = "CRYPTO_BTC_4H_TREND_V14_R2E_PREFLIGHT"
V14_R2F_STAGE = "CRYPTO_BTC_DAILY_MOMENTUM_V14_R2F_DISCOVERY"
V14_R2G_STAGE = "CRYPTO_BTC_DAILY_CONSENSUS_V14_R2G_DISCOVERY"
V14_R2H_STAGE = "CRYPTO_BTC_4H_CONSENSUS_V14_R2H_TRANSFER"
V14_R2H_VELUM_STAGE = "CRYPTO_BTC_4H_CONSENSUS_V14_R2H_VELUM_REPLAY"
V14_R2H_VELUM_4H_FETCH_FIX_COMMIT = "54f98e493a4ca71f2dbf17c31fbc0cf74c2757c8"
V14_R2H_VELUM_CHUNK_FIX_COMMIT = "6f22220557934839152aeddaf49ac367982defad"
V14_STAGE_KEYS = {
    V14_PREFLIGHT_STAGE,
    V14_R2A_STAGE,
    V14_R2B_STAGE,
    V14_R2C_STAGE,
    V14_R2D_STAGE,
    V14_R2E_STAGE,
    V14_R2F_STAGE,
    V14_R2G_STAGE,
    V14_R2H_STAGE,
    V14_R2H_VELUM_STAGE,
}

RESEARCH_DIRECTOR_STAGE = "CRYPTO_RESEARCH_DIRECTOR_V1"
RESEARCH_DIRECTOR_METHODOLOGY = "graen-research-director-v1"
RESEARCH_DIRECTOR_STAGE_KEYS = {RESEARCH_DIRECTOR_STAGE}

HYPOTHESIS_PLANNER_STAGE = "CRYPTO_HYPOTHESIS_PLANNER_V1"
STRATEGY_DEVELOPMENT_STAGE = "CRYPTO_STRATEGY_MANIFEST_DEVELOPMENT"
STRATEGY_VALIDATION_STAGE = "CRYPTO_STRATEGY_MANIFEST_VALIDATION"
STRATEGY_HOLDOUT_STAGE = "CRYPTO_STRATEGY_MANIFEST_HOLDOUT"
STRATEGY_VELUM_STAGE = "CRYPTO_STRATEGY_MANIFEST_VELUM"
STRATEGY_SHADOW_STAGE = "CRYPTO_STRATEGY_MANIFEST_FORWARD_SHADOW"
STRATEGY_PAPER_STAGE = "CRYPTO_STRATEGY_MANIFEST_PAPER"
WAITING_CORPUS_STAGE = "CRYPTO_WAITING_FOR_UNINSPECTED_CORPUS"
ENGINEERING_REQUIRED_STAGE = "CRYPTO_ENGINEERING_REQUIRED"
AUTONOMOUS_LOOP_ID = "graen-autonomous-operating-loop-v1"
AUTONOMOUS_LOOP_BOOTSTRAP_VERSION = 1
STRATEGY_STAGE_KEYS = {
    HYPOTHESIS_PLANNER_STAGE,
    STRATEGY_DEVELOPMENT_STAGE,
    STRATEGY_VALIDATION_STAGE,
    STRATEGY_HOLDOUT_STAGE,
    STRATEGY_VELUM_STAGE,
    STRATEGY_SHADOW_STAGE,
    STRATEGY_PAPER_STAGE,
}


def _v10_epoch_contract(epoch_index: int) -> dict[str, Any]:
    if epoch_index < 0 or epoch_index >= len(V10_EPOCHS):
        raise ValueError("v10_epoch_out_of_range")
    return dict(V10_EPOCHS[epoch_index])


def _v10_next_epoch(epoch_index: int) -> int | None:
    candidate = epoch_index + 1
    return candidate if candidate < len(V10_EPOCHS) else None

PREVIOUSLY_INSPECTED_RANGES = (
    {
        "id": "graen-crypto-v6",
        "start": "2025-09-01T00:00:00+00:00",
        "end": "2025-12-01T00:00:00+00:00",
    },
    {
        "id": "crypto-v5-fetch",
        "start": "2026-01-31T16:00:00+00:00",
        "end": "2026-06-01T00:00:00+00:00",
    },
    {
        "id": "crypto-v4",
        "start": "2026-06-02T01:53:00+00:00",
        "end": "2026-07-02T01:53:00+00:00",
    },
    {
        "id": "crypto-v3",
        "start": "2026-07-02T01:53:00+00:00",
        "end": "2026-08-01T01:53:00+00:00",
    },
    {
        "id": "crypto-v2.1",
        "start": "2026-08-01T01:53:00+00:00",
        "end": "2026-08-31T01:53:00+00:00",
    },
    {
        "id": "crypto-v1",
        "start": "2026-08-31T01:53:00+00:00",
        "end": "2026-09-30T01:53:00+00:00",
    },
)


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _source_commit() -> str | None:
    return os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GRAEN_RESEARCH_SOURCE_COMMIT")


def _deployment_id() -> str | None:
    return os.getenv("RAILWAY_DEPLOYMENT_ID")


def _research_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "bar_timeframe": "5Min",
            "market_data_batch_size": len(CONTEXT_UNIVERSE),
            "dynamic_universe_enabled": False,
            "execution_enabled": False,
            "live_trading": False,
            "bot_armed": False,
            "scan_only": True,
            "crypto_execution_enabled": False,
        }
    )


def _execution_violations(settings: Settings) -> list[str]:
    violations: list[str] = []
    if bool(settings.execution_enabled):
        violations.append("EXECUTION_ENABLED")
    if bool(settings.live_trading):
        violations.append("LIVE_TRADING")
    if bool(settings.bot_armed):
        violations.append("BOT_ARMED")
    if bool(settings.crypto_execution_enabled):
        violations.append("CRYPTO_EXECUTION_ENABLED")
    return violations


class GraenResearchExecutor:
    def __init__(self) -> None:
        self.worker_id = os.getenv(
            "GRAEN_RESEARCH_WORKER_ID",
            "graen-crypto-research-executor",
        ).strip() or "graen-crypto-research-executor"
        self.interval_seconds = max(
            15,
            int(os.getenv("GRAEN_RESEARCH_TICK_SECONDS", "30")),
        )
        self.autorun = _truthy("GRAEN_RESEARCH_AUTORUN", True)
        self.settings = _research_settings(get_settings())
        self.market_data = MarketDataClient(self.settings)
        self.gateway = GraenGateway(
            os.getenv("GRAEN_GATEWAY_URL", ""),
            os.getenv("GRAEN_GATEWAY_TOKEN", ""),
            timeout_seconds=30.0,
        )
        self.research_promotion = ResearchPromotion(self.gateway)
        self.research_director = ResearchDirectorClient()
        self.research_director_autorun = _truthy(
            "GRAEN_RESEARCH_DIRECTOR_AUTORUN",
            False,
        )
        self.autonomous_loop_bootstrap = _truthy(
            "GRAEN_AUTONOMOUS_LOOP_BOOTSTRAP",
            True,
        )
        self.legacy_campaign_bootstrap = _truthy(
            "GRAEN_LEGACY_CAMPAIGN_BOOTSTRAP",
            False,
        )
        self.callback_base_url = os.getenv("IREN_CALLBACK_BASE_URL", "").strip().rstrip("/")
        self.callback_token = os.getenv("IREN_CALLBACK_TOKEN", "").strip()
        self.velum_base_url = os.getenv("VELUM_SERVICE_URL", "").strip().rstrip("/")
        self.velum_token = (
            os.getenv("VELUM_GRAEN_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.shadow_base_url = os.getenv(
            "RHEN_SHADOW_SERVICE_URL",
            "",
        ).strip().rstrip("/")
        self.shadow_token = (
            os.getenv("GRAEN_SHADOW_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.paper_base_url = os.getenv(
            "GRAEN_PAPER_SERVICE_URL",
            "",
        ).strip().rstrip("/")
        self.paper_token = (
            os.getenv("GRAEN_PAPER_TOKEN", "")
            or os.getenv("GRAEN_GATEWAY_TOKEN", "")
        ).strip()
        self.stop_event = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.started_at = datetime.now(UTC)
        self.last_heartbeat_at: datetime | None = None
        self.last_claim_at: datetime | None = None
        self.last_completion_at: datetime | None = None
        self.active_problem_id: str | None = None
        self.last_error: str | None = None
        self.waiting_dependency_until: datetime | None = None
        self.engineering_required_count: int = 0
        self.last_result: dict[str, Any] | None = None
        self.last_observed_blocked_run_id: str | None = None
        self.last_v12_claim_diagnostic_signature: tuple[Any, ...] | None = None
        self.last_v13_claim_diagnostic_signature: tuple[Any, ...] | None = None
        self.active_methodology_version = V7_METHODOLOGY_VERSION

    @property
    def callback_configured(self) -> bool:
        return bool(self.callback_base_url.startswith("http") and len(self.callback_token) >= 32)

    @property
    def velum_configured(self) -> bool:
        return bool(self.velum_base_url.startswith("http") and len(self.velum_token) >= 32)

    @property
    def shadow_configured(self) -> bool:
        return bool(
            self.shadow_base_url.startswith("http")
            and len(self.shadow_token) >= 32
        )

    @property
    def paper_configured(self) -> bool:
        return bool(
            self.paper_base_url.startswith("http")
            and len(self.paper_token) >= 32
        )

    def health(self) -> dict[str, Any]:
        running = self.task is not None and not self.task.done()
        violations = _execution_violations(self.settings)
        return {
            "ok": bool(
                running
                and self.gateway.configured
                and self.settings.credentials_configured
                and not violations
                and self.last_error is None
            ),
            "system": "GRAEN",
            "service": "graen-research-executor",
            "runtime_version": RUNTIME_VERSION,
            "methodology_version": self.active_methodology_version,
            "supported_methodologies": [
                V7_METHODOLOGY_VERSION,
                LEADLAG_METHODOLOGY_VERSION,
                AUTONOMOUS_METHODOLOGY_PREFIX,
                V9_METHODOLOGY_VERSION,
                V10_METHODOLOGY_VERSION,
                V11_METHODOLOGY_VERSION,
                V12_METHODOLOGY_VERSION,
                V13_METHODOLOGY_VERSION,
                V14_METHODOLOGY_VERSION,
                V14_R2F_METHODOLOGY_VERSION,
                V14_R2G_METHODOLOGY_VERSION,
                V14_R2H_METHODOLOGY_VERSION,
                RESEARCH_DIRECTOR_METHODOLOGY,
            ],
            "running": running,
            "autorun": self.autorun,
            "activity_active": self.active_problem_id is not None,
            "current_activity": (
                "Research problem " + self.active_problem_id
                if self.active_problem_id
                else "Waiting for uninspected research corpus"
                if self.waiting_dependency_until
                and self.waiting_dependency_until > datetime.now(UTC)
                else "Awaiting next autonomous research problem"
            ),
            "waiting_dependency_until": (
                self.waiting_dependency_until.isoformat()
                if self.waiting_dependency_until else None
            ),
            "engineering_required_count": self.engineering_required_count,
            "market_data_credentials_configured": bool(self.settings.credentials_configured),
            "gateway_configured": self.gateway.configured,
            "research_code_promotion_enabled": False,
            "manual_engineering_handoff_enabled": True,
            "runtime_source_mutation_authorized": False,
            "runtime_github_authorization_configured": False,
            "research_director_configured": self.research_director.configured,
            "research_director_autorun": self.research_director_autorun,
            "iren_callback_configured": self.callback_configured,
            "velum_candidate_replay_configured": self.velum_configured,
            "forward_shadow_configured": self.shadow_configured,
            "paper_canary_configured": self.paper_configured,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "crypto_execution_enabled": False,
            "model_execution_enabled": False,
            "isolation_violations": violations,
            "active_problem_id": self.active_problem_id,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "last_claim_at": self.last_claim_at.isoformat() if self.last_claim_at else None,
            "last_completion_at": self.last_completion_at.isoformat() if self.last_completion_at else None,
            "last_error": self.last_error,
            "last_result": self.last_result,
            "research_window": {
                "generic_v7": {
                    "development_start": DEVELOPMENT_START.isoformat(),
                    "validation_start": VALIDATION_START.isoformat(),
                    "holdout_start": HOLDOUT_START.isoformat(),
                    "holdout_end": HOLDOUT_END.isoformat(),
                },
                "leadlag_r2_confirmatory": {
                    "development_start": LEADLAG_DEVELOPMENT_START.isoformat(),
                    "validation_start": LEADLAG_VALIDATION_START.isoformat(),
                    "holdout_start": LEADLAG_HOLDOUT_START.isoformat(),
                    "holdout_end": LEADLAG_HOLDOUT_END.isoformat(),
                },
                "activity_shock_v9": [
                    {
                        "epoch": row["name"],
                        "development_start": row["development_start"].isoformat(),
                        "validation_start": row["validation_start"].isoformat(),
                        "holdout_start": row["holdout_start"].isoformat(),
                        "holdout_end": row["holdout_end"].isoformat(),
                    }
                    for row in V9_EPOCHS
                ],
                "trend_pullback_v10": [
                    {
                        "epoch": row["name"],
                        "development_start": row["development_start"].isoformat(),
                        "validation_start": row["validation_start"].isoformat(),
                        "holdout_start": row["holdout_start"].isoformat(),
                        "holdout_end": row["holdout_end"].isoformat(),
                        "development_previously_inspected": True,
                        "validation_previously_inspected": False,
                        "holdout_previously_inspected": False,
                    }
                    for row in V10_EPOCHS
                ],
            },
            "runtime_provenance": {
                "git_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "started_at": self.started_at.isoformat(),
            },
        }

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="graen-research-executor")

    async def stop(self) -> None:
        self.stop_event.set()
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def _heartbeat(self) -> None:
        await self.gateway.executor_heartbeat(
            worker_id=self.worker_id,
            runtime_version=RUNTIME_VERSION,
            methodology_version=self.active_methodology_version,
            active_problem_id=self.active_problem_id,
            last_error=self.last_error,
        )
        self.last_heartbeat_at = datetime.now(UTC)

    async def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self.autorun:
                    await self.process_once()
                else:
                    await self._heartbeat()
                if self.last_error and self.active_problem_id is None:
                    self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:1000]
                print(
                    "GRAEN_RESEARCH_EXECUTOR_LOOP_ERROR",
                    {
                        "error": self.last_error,
                        "active_problem_id": self.active_problem_id,
                        "runtime_version": RUNTIME_VERSION,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                    flush=True,
                )
                try:
                    await self._heartbeat()
                except Exception:
                    pass
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def _fetch_corpus(self) -> dict[str, list[dict[str, Any]]]:
        fetch_start = DEVELOPMENT_START - timedelta(hours=8)
        fetch_end = HOLDOUT_END + timedelta(hours=3)
        raw: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in CONTEXT_UNIVERSE
        }
        chunk_start = fetch_start
        while chunk_start < fetch_end:
            chunk_end = min(chunk_start + timedelta(days=20), fetch_end)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(CONTEXT_UNIVERSE),
                start=chunk_start,
                end=chunk_end,
            )
            for symbol in CONTEXT_UNIVERSE:
                raw[symbol].extend(chunk.get(symbol, []))
            chunk_start = chunk_end

        clean: dict[str, list[dict[str, Any]]] = {}
        for symbol in CONTEXT_UNIVERSE:
            seen: set[str] = set()
            rows: list[dict[str, Any]] = []
            for bar in raw.get(symbol, []):
                identity = str(bar.get("t") or "")
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                try:
                    stamp = datetime.fromisoformat(identity.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                if fetch_start <= stamp < fetch_end:
                    rows.append(bar)
            clean[symbol] = rows
        return clean

    async def _fetch_stage(
        self,
        symbols: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        warmup_hours: int = 169,
    ) -> dict[str, list[dict[str, Any]]]:
        fetch_start = start - timedelta(hours=warmup_hours)
        # Stage boundaries are strict. Never fetch any bar at or beyond the
        # next sealed stage; provider end timestamps may be inclusive.
        fetch_end = end
        raw: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        chunk_start = fetch_start
        while chunk_start < fetch_end:
            chunk_end = min(chunk_start + timedelta(days=20), fetch_end)
            chunk = await self.market_data.historical_crypto_bars_many(
                list(symbols),
                start=chunk_start,
                end=chunk_end - timedelta(microseconds=1),
            )
            for symbol in symbols:
                raw[symbol].extend(chunk.get(symbol, []))
            chunk_start = chunk_end

        clean: dict[str, list[dict[str, Any]]] = {}
        for symbol in symbols:
            seen: set[str] = set()
            rows: list[dict[str, Any]] = []
            for bar in raw.get(symbol, []):
                identity = str(bar.get("t") or "")
                if not identity or identity in seen:
                    continue
                seen.add(identity)
                try:
                    stamp = datetime.fromisoformat(identity.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                stamp = stamp.astimezone(UTC)
                if fetch_start <= stamp < fetch_end:
                    rows.append(bar)
            clean[symbol] = rows
        return clean


    async def _fetch_v14_r2f_btc_daily(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch the adaptive R2F BTC/USD daily corpus in bounded chunks."""
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")

        rows: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in V14_R2F_UNIVERSE
        }
        chunk_days = 180
        max_pages_per_chunk = 16
        max_rate_limit_retries = 6
        chunk_count = 0
        page_count = 0
        chunk_start = V14_R2F_BAR_SCREEN_START

        async with httpx.AsyncClient(timeout=45.0) as client:
            while chunk_start < V14_R2F_BAR_SCREEN_END:
                chunk_end = min(
                    chunk_start + timedelta(days=chunk_days),
                    V14_R2F_BAR_SCREEN_END,
                )
                params: dict[str, Any] = {
                    "symbols": ",".join(V14_R2F_UNIVERSE),
                    "timeframe": "1Day",
                    "start": chunk_start.isoformat(),
                    "end": (chunk_end - timedelta(microseconds=1)).isoformat(),
                    "limit": 10000,
                    "sort": "asc",
                }
                page_token: str | None = None
                seen_page_tokens: set[str] = set()

                for _ in range(max_pages_per_chunk):
                    request_params = dict(params)
                    if page_token:
                        if page_token in seen_page_tokens:
                            raise RuntimeError(
                                "v14_r2f_chunk_pagination_token_cycle"
                            )
                        seen_page_tokens.add(page_token)
                        request_params["page_token"] = page_token

                    response = None
                    for retry_index in range(max_rate_limit_retries + 1):
                        response = await client.get(
                            f"{self.settings.data_base_url}/v1beta3/crypto/"
                            f"{self.settings.crypto_location}/bars",
                            headers=self.market_data.headers,
                            params=request_params,
                        )
                        if response.status_code != 429:
                            break
                        if retry_index >= max_rate_limit_retries:
                            raise RuntimeError(
                                "v14_r2f_bar_rate_limit_retries_exhausted"
                            )
                        retry_after_raw = response.headers.get("Retry-After")
                        try:
                            retry_after = (
                                float(retry_after_raw) if retry_after_raw else 0.0
                            )
                        except (TypeError, ValueError):
                            retry_after = 0.0
                        await asyncio.sleep(
                            min(max(retry_after, 0.5 * (2 ** retry_index)), 8.0)
                        )

                    if response is None:
                        raise RuntimeError("v14_r2f_bar_response_missing")
                    response.raise_for_status()
                    payload = response.json()
                    payload_bars = payload.get("bars") or {}
                    for symbol in V14_R2F_UNIVERSE:
                        rows[symbol].extend(payload_bars.get(symbol, []) or [])
                    page_count += 1
                    page_token = payload.get("next_page_token")
                    if not page_token:
                        break
                else:
                    raise RuntimeError(
                        "v14_r2f_chunk_pagination_exceeded_safety_limit"
                    )

                chunk_count += 1
                chunk_start = chunk_end

        clean: dict[str, list[dict[str, Any]]] = {}
        for symbol in V14_R2F_UNIVERSE:
            by_stamp: dict[str, dict[str, Any]] = {}
            for row in rows.get(symbol, []):
                stamp = str(row.get("t") or row.get("timestamp") or "")
                if stamp:
                    by_stamp[stamp] = row
            clean[symbol] = [by_stamp[key] for key in sorted(by_stamp)]

        print(
            "GRAEN_V14_R2F_DATA_FETCH",
            {
                "chunks": chunk_count,
                "pages": page_count,
                "raw_rows": {symbol: len(values) for symbol, values in rows.items()},
                "clean_rows": {symbol: len(values) for symbol, values in clean.items()},
                "timeframe": "1Day",
                "execution_authority": False,
            },
            flush=True,
        )
        return clean


    async def _fetch_v14_r2e_btc_4h(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch the frozen Alpaca BTC/USD 4-hour corpus for R2E.

        Alpaca's crypto bar endpoint can paginate far below the requested
        10,000-row limit. Keep each request window bounded so pagination is
        local to a small corpus slice instead of accumulating across 5+ years.
        """
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")

        rows: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in V14_R2E_UNIVERSE
        }
        chunk_days = 60
        max_pages_per_chunk = 16
        max_rate_limit_retries = 6
        chunk_count = 0
        page_count = 0
        chunk_start = V14_R2E_BAR_SCREEN_START

        async with httpx.AsyncClient(timeout=45.0) as client:
            while chunk_start < V14_R2E_BAR_SCREEN_END:
                chunk_end = min(
                    chunk_start + timedelta(days=chunk_days),
                    V14_R2E_BAR_SCREEN_END,
                )
                params: dict[str, Any] = {
                    "symbols": ",".join(V14_R2E_UNIVERSE),
                    "timeframe": "4Hour",
                    "start": chunk_start.isoformat(),
                    # Alpaca end timestamps may be inclusive. Keep adjacent
                    # chunks non-overlapping without moving the frozen boundary.
                    "end": (chunk_end - timedelta(microseconds=1)).isoformat(),
                    "limit": 10000,
                    "sort": "asc",
                }
                page_token: str | None = None
                seen_page_tokens: set[str] = set()

                for _ in range(max_pages_per_chunk):
                    request_params = dict(params)
                    if page_token:
                        if page_token in seen_page_tokens:
                            raise RuntimeError(
                                "v14_r2e_chunk_pagination_token_cycle"
                            )
                        seen_page_tokens.add(page_token)
                        request_params["page_token"] = page_token

                    response = None
                    for retry_index in range(max_rate_limit_retries + 1):
                        response = await client.get(
                            f"{self.settings.data_base_url}/v1beta3/crypto/"
                            f"{self.settings.crypto_location}/bars",
                            headers=self.market_data.headers,
                            params=request_params,
                        )
                        if response.status_code != 429:
                            break
                        if retry_index >= max_rate_limit_retries:
                            raise RuntimeError(
                                "v14_r2e_bar_rate_limit_retries_exhausted"
                            )
                        retry_after_raw = response.headers.get("Retry-After")
                        try:
                            retry_after = (
                                float(retry_after_raw) if retry_after_raw else 0.0
                            )
                        except (TypeError, ValueError):
                            retry_after = 0.0
                        await asyncio.sleep(
                            min(max(retry_after, 0.5 * (2 ** retry_index)), 8.0)
                        )

                    if response is None:
                        raise RuntimeError("v14_r2e_bar_response_missing")
                    response.raise_for_status()
                    payload = response.json()
                    payload_bars = payload.get("bars") or {}
                    for symbol in V14_R2E_UNIVERSE:
                        rows[symbol].extend(payload_bars.get(symbol, []) or [])
                    page_count += 1
                    page_token = payload.get("next_page_token")
                    if not page_token:
                        break
                else:
                    raise RuntimeError(
                        "v14_r2e_chunk_pagination_exceeded_safety_limit"
                    )

                chunk_count += 1
                chunk_start = chunk_end

        # Defensive de-duplication protects against provider boundary semantics
        # while preserving the frozen chronological corpus.
        clean: dict[str, list[dict[str, Any]]] = {}
        for symbol in V14_R2E_UNIVERSE:
            by_stamp: dict[str, dict[str, Any]] = {}
            for row in rows.get(symbol, []):
                stamp = str(row.get("t") or row.get("timestamp") or "")
                if stamp:
                    by_stamp[stamp] = row
            clean[symbol] = [
                by_stamp[key]
                for key in sorted(by_stamp)
            ]

        print(
            "GRAEN_V14_R2E_DATA_FETCH",
            {
                "chunks": chunk_count,
                "pages": page_count,
                "raw_rows": {symbol: len(values) for symbol, values in rows.items()},
                "clean_rows": {symbol: len(values) for symbol, values in clean.items()},
                "timeframe": "4Hour",
                "execution_authority": False,
            },
            flush=True,
        )
        return clean


    async def _fetch_v14_r2d_quotes(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch frozen Alpaca top-of-book quotes for the R2D triangle."""
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")
        params: dict[str, Any] = {
            "symbols": ",".join(V14_R2D_PAIRS),
            "start": V14_R2D_QUOTE_SCREEN_START.isoformat(),
            "end": V14_R2D_QUOTE_SCREEN_END.isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in V14_R2D_PAIRS}
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        page_count = 0
        max_pages = 512
        max_rate_limit_retries = 6
        async with httpx.AsyncClient(timeout=45.0) as client:
            for page_index in range(max_pages):
                request_params = dict(params)
                if page_token:
                    if page_token in seen_page_tokens:
                        raise RuntimeError("v14_r2d_quote_pagination_token_cycle")
                    seen_page_tokens.add(page_token)
                    request_params["page_token"] = page_token
                response = None
                for retry_index in range(max_rate_limit_retries + 1):
                    response = await client.get(
                        f"{self.settings.data_base_url}/v1beta3/crypto/"
                        f"{self.settings.crypto_location}/quotes",
                        headers=self.market_data.headers,
                        params=request_params,
                    )
                    if response.status_code != 429:
                        break
                    if retry_index >= max_rate_limit_retries:
                        raise RuntimeError("v14_r2d_quote_rate_limit_retries_exhausted")
                    retry_after_raw = response.headers.get("Retry-After")
                    try:
                        retry_after = float(retry_after_raw) if retry_after_raw else 0.0
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    await asyncio.sleep(min(max(retry_after, 0.5 * (2 ** retry_index)), 8.0))
                if response is None:
                    raise RuntimeError("v14_r2d_quote_response_missing")
                response.raise_for_status()
                payload = response.json()
                payload_quotes = payload.get("quotes") or {}
                for symbol in V14_R2D_PAIRS:
                    rows[symbol].extend(payload_quotes.get(symbol, []) or [])
                page_count = page_index + 1
                page_token = payload.get("next_page_token")
                if not page_token:
                    break
            else:
                raise RuntimeError("v14_r2d_quote_pagination_exceeded_safety_limit")
        print(
            "GRAEN_V14_R2D_DATA_FETCH",
            {
                "pages": page_count,
                "raw_rows": {symbol: len(values) for symbol, values in rows.items()},
                "execution_authority": False,
            },
            flush=True,
        )
        return rows


    async def _fetch_v14_r2c_daily_crypto(self) -> dict[str, list[dict[str, Any]]]:
        """Fetch the frozen multi-asset Alpaca daily-bar corpus for V14-R2C."""
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")
        params: dict[str, Any] = {
            "symbols": ",".join(V14_R2C_UNIVERSE),
            "timeframe": "1Day",
            "start": V14_R2C_BAR_SCREEN_START.isoformat(),
            "end": V14_R2C_BAR_SCREEN_END.isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        rows: dict[str, list[dict[str, Any]]] = {
            symbol: [] for symbol in V14_R2C_UNIVERSE
        }
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        page_count = 0
        max_pages = 32
        async with httpx.AsyncClient(timeout=45.0) as client:
            for page_index in range(max_pages):
                request_params = dict(params)
                if page_token:
                    if page_token in seen_page_tokens:
                        raise RuntimeError("v14_r2c_bar_pagination_token_cycle")
                    seen_page_tokens.add(page_token)
                    request_params["page_token"] = page_token
                response = await client.get(
                    f"{self.settings.data_base_url}/v1beta3/crypto/"
                    f"{self.settings.crypto_location}/bars",
                    headers=self.market_data.headers,
                    params=request_params,
                )
                response.raise_for_status()
                payload = response.json()
                payload_bars = payload.get("bars") or {}
                for symbol in V14_R2C_UNIVERSE:
                    rows[symbol].extend(payload_bars.get(symbol, []) or [])
                page_count = page_index + 1
                page_token = payload.get("next_page_token")
                if not page_token:
                    break
            else:
                raise RuntimeError("v14_r2c_bar_pagination_exceeded_safety_limit")
        print(
            "GRAEN_V14_R2C_DATA_FETCH",
            {
                "pages": page_count,
                "raw_rows": {symbol: len(values) for symbol, values in rows.items()},
                "execution_authority": False,
            },
            flush=True,
        )
        return rows


    async def _fetch_v14_r2b_minute_btc(self) -> list[dict[str, Any]]:
        """Fetch the frozen Alpaca BTC/USD 1-minute corpus for V14-R2B."""
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")
        params: dict[str, Any] = {
            "symbols": ",".join(V14_R2B_UNIVERSE),
            "timeframe": "1Min",
            "start": V14_R2B_BAR_SCREEN_START.isoformat(),
            "end": V14_R2B_BAR_SCREEN_END.isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        page_count = 0
        max_pages = 256
        async with httpx.AsyncClient(timeout=45.0) as client:
            for page_index in range(max_pages):
                request_params = dict(params)
                if page_token:
                    if page_token in seen_page_tokens:
                        raise RuntimeError("v14_r2b_bar_pagination_token_cycle")
                    seen_page_tokens.add(page_token)
                    request_params["page_token"] = page_token
                response = await client.get(
                    f"{self.settings.data_base_url}/v1beta3/crypto/"
                    f"{self.settings.crypto_location}/bars",
                    headers=self.market_data.headers,
                    params=request_params,
                )
                response.raise_for_status()
                payload = response.json()
                rows.extend((payload.get("bars") or {}).get("BTC/USD", []) or [])
                page_count = page_index + 1
                page_token = payload.get("next_page_token")
                if page_count % 25 == 0:
                    print(
                        "GRAEN_V14_R2B_DATA_FETCH_PROGRESS",
                        {
                            "pages": page_count,
                            "raw_rows": len(rows),
                            "max_pages": max_pages,
                            "execution_authority": False,
                        },
                        flush=True,
                    )
                if not page_token:
                    break
            else:
                raise RuntimeError("v14_r2b_bar_pagination_exceeded_safety_limit")
        print(
            "GRAEN_V14_R2B_DATA_FETCH",
            {
                "pages": page_count,
                "raw_rows": len(rows),
                "max_pages": max_pages,
                "execution_authority": False,
            },
            flush=True,
        )
        return rows


    async def _fetch_v14_r2a_quotes(self) -> list[dict[str, Any]]:
        """Fetch the frozen Alpaca BTC/USD quote corpus for V14-R2A."""
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")
        params: dict[str, Any] = {
            "symbols": ",".join(V14_R2A_UNIVERSE),
            "start": V14_R2A_QUOTE_SCREEN_START.isoformat(),
            "end": V14_R2A_QUOTE_SCREEN_END.isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        page_count = 0
        max_pages = 512
        max_rate_limit_retries = 6
        async with httpx.AsyncClient(timeout=45.0) as client:
            for page_index in range(max_pages):
                request_params = dict(params)
                if page_token:
                    if page_token in seen_page_tokens:
                        raise RuntimeError("v14_r2a_quote_pagination_token_cycle")
                    seen_page_tokens.add(page_token)
                    request_params["page_token"] = page_token
                response = None
                for retry_index in range(max_rate_limit_retries + 1):
                    response = await client.get(
                        f"{self.settings.data_base_url}/v1beta3/crypto/"
                        f"{self.settings.crypto_location}/quotes",
                        headers=self.market_data.headers,
                        params=request_params,
                    )
                    if response.status_code != 429:
                        break
                    if retry_index >= max_rate_limit_retries:
                        raise RuntimeError("v14_r2a_quote_rate_limit_retries_exhausted")
                    retry_after_raw = response.headers.get("Retry-After")
                    try:
                        retry_after = float(retry_after_raw) if retry_after_raw else 0.0
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    await asyncio.sleep(min(max(retry_after, 0.5 * (2 ** retry_index)), 8.0))
                if response is None:
                    raise RuntimeError("v14_r2a_quote_response_missing")
                response.raise_for_status()
                payload = response.json()
                rows.extend((payload.get("quotes") or {}).get("BTC/USD", []) or [])
                page_count = page_index + 1
                page_token = payload.get("next_page_token")
                if page_count % 50 == 0:
                    print(
                        "GRAEN_V14_R2A_DATA_FETCH_PROGRESS",
                        {
                            "pages": page_count,
                            "raw_rows": len(rows),
                            "max_pages": max_pages,
                            "execution_authority": False,
                        },
                        flush=True,
                    )
                if not page_token:
                    break
            else:
                raise RuntimeError("v14_r2a_quote_pagination_exceeded_safety_limit")
        print(
            "GRAEN_V14_R2A_DATA_FETCH",
            {
                "pages": page_count,
                "raw_rows": len(rows),
                "max_pages": max_pages,
                "execution_authority": False,
            },
            flush=True,
        )
        return rows


    async def _fetch_v14_hourly_btc(self) -> list[dict[str, Any]]:
        """Fetch the frozen Alpaca BTC/USD hourly transfer-screen corpus."""
        if not self.settings.credentials_configured:
            raise RuntimeError("market-data credentials are not configured")
        params: dict[str, Any] = {
            "symbols": ",".join(V14_UNIVERSE),
            "timeframe": "1Hour",
            "start": V14_ALPACA_SCREEN_START.isoformat(),
            "end": V14_ALPACA_SCREEN_END.isoformat(),
            "limit": 10000,
            "sort": "asc",
        }
        rows: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        page_count = 0
        max_pages = 768
        max_rate_limit_retries = 6
        async with httpx.AsyncClient(timeout=45.0) as client:
            for page_index in range(max_pages):
                request_params = dict(params)
                if page_token:
                    if page_token in seen_page_tokens:
                        raise RuntimeError("v14_hourly_btc_pagination_token_cycle")
                    seen_page_tokens.add(page_token)
                    request_params["page_token"] = page_token

                response = None
                for retry_index in range(max_rate_limit_retries + 1):
                    response = await client.get(
                        f"{self.settings.data_base_url}/v1beta3/crypto/"
                        f"{self.settings.crypto_location}/bars",
                        headers=self.market_data.headers,
                        params=request_params,
                    )
                    if response.status_code != 429:
                        break
                    if retry_index >= max_rate_limit_retries:
                        raise RuntimeError("v14_hourly_btc_rate_limit_retries_exhausted")
                    retry_after_raw = response.headers.get("Retry-After")
                    try:
                        retry_after = float(retry_after_raw) if retry_after_raw else 0.0
                    except (TypeError, ValueError):
                        retry_after = 0.0
                    delay = min(
                        max(retry_after, 0.5 * (2 ** retry_index)),
                        8.0,
                    )
                    print(
                        "GRAEN_V14_DATA_RATE_LIMIT",
                        {
                            "page": page_index + 1,
                            "retry": retry_index + 1,
                            "delay_seconds": delay,
                            "execution_authority": False,
                        },
                        flush=True,
                    )
                    await asyncio.sleep(delay)

                if response is None:
                    raise RuntimeError("v14_hourly_btc_response_missing")
                response.raise_for_status()
                payload = response.json()
                rows.extend((payload.get("bars") or {}).get("BTC/USD", []) or [])
                page_count = page_index + 1
                page_token = payload.get("next_page_token")

                if page_count % 50 == 0:
                    print(
                        "GRAEN_V14_DATA_FETCH_PROGRESS",
                        {
                            "pages": page_count,
                            "raw_rows": len(rows),
                            "max_pages": max_pages,
                            "execution_authority": False,
                        },
                        flush=True,
                    )
                if not page_token:
                    break
            else:
                raise RuntimeError("v14_hourly_btc_pagination_exceeded_safety_limit")
        print(
            "GRAEN_V14_DATA_FETCH",
            {
                "pages": page_count,
                "raw_rows": len(rows),
                "max_pages": max_pages,
                "rate_limit_retry_cap": max_rate_limit_retries,
                "execution_authority": False,
            },
            flush=True,
        )

        unique: dict[str, dict[str, Any]] = {}
        for row in rows:
            stamp = str(row.get("t") or row.get("timestamp") or "")
            if not stamp:
                continue
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            parsed = parsed.astimezone(UTC)
            if V14_ALPACA_SCREEN_START <= parsed < V14_ALPACA_SCREEN_END:
                unique[parsed.isoformat()] = dict(row)
        return [unique[key] for key in sorted(unique)]

    async def _finalize(
        self,
        *,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
        status: str,
        summary: dict[str, Any],
        next_stage: str | None = None,
        next_metadata: dict[str, Any] | None = None,
        model_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        linked_job_id = (
            str(problem.get("linked_iren_job_id"))
            if problem.get("linked_iren_job_id")
            else None
        )
        await self.gateway.complete_research_problem(
            problem_id=problem_id,
            run_id=run_id,
            worker_id=self.worker_id,
            status=status,
            result_summary=summary,
            model_usage=model_usage or {"invoked": False},
        )

        if (
            status == "WAITING"
            and next_stage is None
            and summary.get("decision") == "CONTINUE_RESEARCH"
            and summary.get("next_action") == "DESIGN_NEXT_FROZEN_RESEARCH_BATCH"
        ):
            next_stage = AUTONOMOUS_DEVELOPMENT_STAGE
            next_metadata = {
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "campaign_epoch": 0,
                "campaign_generation": 1,
                "campaign_origin_methodology": summary.get("methodology_version"),
            }

        if next_stage is not None:
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=next_stage,
                metadata=next_metadata or {},
            )
            summary["continuation_queued"] = bool(queued.get("problem"))
            summary["next_research_stage"] = next_stage
            if next_metadata:
                summary["next_research_metadata"] = next_metadata

        callback_status = "SUCCEEDED" if status == "SUCCEEDED" else "WAITING"
        callback_delivered = await self._callback_iren(
            linked_job_id,
            status=callback_status,
            result={
                "graen_problem_id": problem_id,
                "graen_run_id": run_id,
                **summary,
            },
        )
        summary["iren_callback_delivered"] = callback_delivered
        self.active_problem_id = None
        self.last_completion_at = datetime.now(UTC)
        self.last_result = summary
        self.last_error = None
        print(
            "GRAEN_RESEARCH_RESULT",
            {
                key: summary.get(key)
                for key in (
                    "state", "status", "decision", "campaign_id", "epoch",
                    "epoch_index", "candidate_id", "candidate_family",
                    "validation_opened", "holdout_opened", "holdout_passed",
                    "next_action", "next_research_stage", "continuation_queued",
                    "execution_authority", "broker_orders_possible",
                )
                if key in summary
            },
            flush=True,
        )
        await self._heartbeat()
        return {"claimed": True, "problem_id": problem_id, **summary}

    async def _execute_v7_staged(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_methodology_version = V7_METHODOLOGY_VERSION

        verify_v7_corpus_contract(
            development_start=DEVELOPMENT_START,
            validation_start=VALIDATION_START,
            holdout_start=HOLDOUT_START,
            holdout_end=HOLDOUT_END,
            corpus_provenance_verified=True,
            previously_inspected_ranges=PREVIOUSLY_INSPECTED_RANGES,
        )

        prespec = {
            "schema_version": "graen.crypto_v7.batch_specification.v2",
            "methodology_version": V7_METHODOLOGY_VERSION,
            "research_batch_id": V7_RESEARCH_BATCH_ID,
            "candidate_registry": [row.to_dict() for row in v7_candidate_specs()],
            "candidate_count": len(v7_candidate_specs()),
            "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT"],
            "development": [DEVELOPMENT_START.isoformat(), VALIDATION_START.isoformat()],
            "validation": [VALIDATION_START.isoformat(), HOLDOUT_START.isoformat()],
            "holdout": [HOLDOUT_START.isoformat(), HOLDOUT_END.isoformat()],
            "validation_fetch_requires_development_survivor": True,
            "holdout_fetch_requires_validation_survivor": True,
            "corpus_provenance_verified": True,
            "previously_inspected_ranges": list(PREVIOUSLY_INSPECTED_RANGES),
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "frozen_before_corpus_access": True,
            "authority": {
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "risk_or_sizing_authority": False,
                "production_promotion_authority": False,
            },
        }
        prespec_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_BATCH_SPECIFICATION",
            methodology_version=V7_METHODOLOGY_VERSION,
            content=prespec,
        )

        development_bars = await self._fetch_stage(
            CONTEXT_UNIVERSE,
            start=DEVELOPMENT_START,
            end=VALIDATION_START,
            warmup_hours=8,
        )
        development = evaluate_v7_development(
            bars_by_symbol=development_bars,
            start=DEVELOPMENT_START,
            end=VALIDATION_START,
        )
        development_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_DEVELOPMENT_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content={
                **development,
                "bar_counts": {
                    symbol: len(rows) for symbol, rows in development_bars.items()
                },
                "source_commit": _source_commit(),
            },
        )

        result: dict[str, Any] = {
            "methodology_version": V7_METHODOLOGY_VERSION,
            "research_batch_id": V7_RESEARCH_BATCH_ID,
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "research_only": True,
            "model_invoked": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "production_state_changed": False,
            "prespec_artifact_id": (
                (prespec_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(prespec_artifact.get("artifact"), Mapping) else None
            ),
            "development_artifact_id": (
                (development_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(development_artifact.get("artifact"), Mapping) else None
            ),
            "development": development,
            "validation": {"opened": False},
            "holdout": {"opened": False},
        }

        if not development.get("survivors"):
            result.update({
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
                methodology_version=V7_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "RESEARCH_BATCH_COMPLETE",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": V7_METHODOLOGY_VERSION,
                "research_batch_id": V7_RESEARCH_BATCH_ID,
                "candidate_count": len(v7_candidate_specs()),
                "development_survivors": [],
                "validation_opened": False,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        validation_bars = await self._fetch_stage(
            CONTEXT_UNIVERSE,
            start=VALIDATION_START,
            end=HOLDOUT_START,
            warmup_hours=8,
        )
        validation = evaluate_v7_validation(
            bars_by_symbol=validation_bars,
            development_results=development["results"],
            survivor_ids=development["survivors"],
            start=VALIDATION_START,
            end=HOLDOUT_START,
        )
        validation_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_VALIDATION_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content={
                **validation,
                "bar_counts": {
                    symbol: len(rows) for symbol, rows in validation_bars.items()
                },
                "source_commit": _source_commit(),
            },
        )
        result["validation"] = {
            **validation,
            "artifact_id": (
                (validation_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(validation_artifact.get("artifact"), Mapping) else None
            ),
        }

        selected = validation.get("selected_candidate_id")
        if not selected:
            result.update({
                "status": "NO_VALIDATION_SURVIVOR",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
                methodology_version=V7_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "RESEARCH_BATCH_COMPLETE",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": V7_METHODOLOGY_VERSION,
                "research_batch_id": V7_RESEARCH_BATCH_ID,
                "candidate_count": len(v7_candidate_specs()),
                "development_survivors": development["survivors"],
                "validation_survivors": validation.get("survivors") or [],
                "selected_candidate": None,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        holdout_bars = await self._fetch_stage(
            CONTEXT_UNIVERSE,
            start=HOLDOUT_START,
            end=HOLDOUT_END,
            warmup_hours=8,
        )
        holdout = evaluate_v7_holdout(
            bars_by_symbol=holdout_bars,
            candidate_id=str(selected),
            start=HOLDOUT_START,
            end=HOLDOUT_END,
        )
        holdout_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V7_HOLDOUT_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content={
                **holdout,
                "bar_counts": {
                    symbol: len(rows) for symbol, rows in holdout_bars.items()
                },
                "source_commit": _source_commit(),
            },
        )
        result["holdout"] = {
            **holdout,
            "artifact_id": (
                (holdout_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(holdout_artifact.get("artifact"), Mapping) else None
            ),
        }
        passed = bool(holdout.get("passed"))
        result.update({
            "status": "HOLDOUT_PASS" if passed else "HOLDOUT_FAIL",
            "decision": "PROMOTE_TO_VELUM" if passed else "CONTINUE_RESEARCH",
            "selected_candidate": str(selected),
            "next_action": "VELUM_REPLAY" if passed else "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
        })
        final_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_RESEARCH_BATCH_RESULT",
            methodology_version=V7_METHODOLOGY_VERSION,
            content=result,
        )
        summary = {
            "state": "CANDIDATE_READY_FOR_VELUM" if passed else "RESEARCH_BATCH_COMPLETE",
            "decision": result["decision"],
            "status": result["status"],
            "methodology_version": V7_METHODOLOGY_VERSION,
            "research_batch_id": V7_RESEARCH_BATCH_ID,
            "candidate_count": len(v7_candidate_specs()),
            "development_survivors": development["survivors"],
            "validation_survivors": validation.get("survivors") or [],
            "selected_candidate": str(selected),
            "holdout_opened": True,
            "holdout_passed": passed,
            "artifact_id": (
                (final_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(final_artifact.get("artifact"), Mapping) else None
            ),
            "content_hash": final_artifact.get("content_hash"),
            "model_invoked": False,
            "execution_authority": False,
            "production_state_changed": False,
            "next_action": result["next_action"],
        }
        return await self._finalize(
            problem=problem,
            run=run,
            status="SUCCEEDED" if passed else "WAITING",
            summary=summary,
        )

    async def _execute_leadlag_r2(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_methodology_version = LEADLAG_METHODOLOGY_VERSION

        prespec = {
            **leadlag_research_specification(),
            "corpus": {
                "development": [
                    LEADLAG_DEVELOPMENT_START.isoformat(),
                    LEADLAG_VALIDATION_START.isoformat(),
                ],
                "validation": [
                    LEADLAG_VALIDATION_START.isoformat(),
                    LEADLAG_HOLDOUT_START.isoformat(),
                ],
                "holdout": [
                    LEADLAG_HOLDOUT_START.isoformat(),
                    LEADLAG_HOLDOUT_END.isoformat(),
                ],
                "overlap_allowed": False,
                "holdout_fetch_before_validation_pass": False,
            },
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "frozen_before_corpus_access": True,
        }
        prespec_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="RESEARCH_HYPOTHESIS_SPECIFICATION_REVISION",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content=prespec,
        )

        development_bars = await self._fetch_stage(
            LEADLAG_UNIVERSE,
            start=LEADLAG_DEVELOPMENT_START,
            end=LEADLAG_VALIDATION_START,
        )
        development = evaluate_leadlag_stage(
            development_bars,
            start=LEADLAG_DEVELOPMENT_START,
            end=LEADLAG_VALIDATION_START,
            scenario="high",
            seed=82101,
        )
        development_passed, development_reasons = leadlag_development_gate(development)
        development_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_DEVELOPMENT_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content={
                "stage": "DEVELOPMENT",
                "passed": development_passed,
                "reasons": development_reasons,
                "bar_counts": {symbol: len(rows) for symbol, rows in development_bars.items()},
                "result": development,
                "source_commit": _source_commit(),
            },
        )

        result: dict[str, Any] = {
            "methodology_version": LEADLAG_METHODOLOGY_VERSION,
            "hypothesis_id": "CRYPTO-LEADLAG-001",
            "candidate_id": "CRYPTO-LEADLAG-001-R2",
            "prespec_revision": 2,
            "problem_id": problem_id,
            "graen_run_id": run_id,
            "source_commit": _source_commit(),
            "deployment_id": _deployment_id(),
            "research_only": True,
            "model_invoked": False,
            "execution_authority": False,
            "broker_orders_possible": False,
            "risk_or_sizing_authority": False,
            "production_promotion_authority": False,
            "production_state_changed": False,
            "prespec_artifact_id": (
                (prespec_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(prespec_artifact.get("artifact"), Mapping) else None
            ),
            "development_artifact_id": (
                (development_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(development_artifact.get("artifact"), Mapping) else None
            ),
            "development": {
                "opened": True,
                "passed": development_passed,
                "reasons": development_reasons,
                "result": development,
            },
            "validation": {"opened": False},
            "holdout": {"opened": False},
        }

        if not development_passed:
            result.update({
                "status": "REJECTED_IN_DEVELOPMENT",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_LEADLAG_R2_RESULT",
                methodology_version=LEADLAG_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "CANDIDATE_REJECTED_DEVELOPMENT",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": LEADLAG_METHODOLOGY_VERSION,
                "candidate_id": result["candidate_id"],
                "development_passed": False,
                "validation_opened": False,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        validation_bars = await self._fetch_stage(
            LEADLAG_UNIVERSE,
            start=LEADLAG_VALIDATION_START,
            end=LEADLAG_HOLDOUT_START,
        )
        validation = evaluate_leadlag_stage(
            validation_bars,
            start=LEADLAG_VALIDATION_START,
            end=LEADLAG_HOLDOUT_START,
            scenario="high",
            seed=82201,
        )
        validation_passed, validation_reasons = leadlag_validation_gate(validation)
        validation_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_VALIDATION_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content={
                "stage": "VALIDATION",
                "passed": validation_passed,
                "reasons": validation_reasons,
                "bar_counts": {symbol: len(rows) for symbol, rows in validation_bars.items()},
                "result": validation,
                "source_commit": _source_commit(),
            },
        )
        result["validation"] = {
            "opened": True,
            "passed": validation_passed,
            "reasons": validation_reasons,
            "artifact_id": (
                (validation_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(validation_artifact.get("artifact"), Mapping) else None
            ),
            "result": validation,
        }

        if not validation_passed:
            result.update({
                "status": "REJECTED_IN_VALIDATION",
                "decision": "CONTINUE_RESEARCH",
                "next_action": "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
            })
            final_artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_LEADLAG_R2_RESULT",
                methodology_version=LEADLAG_METHODOLOGY_VERSION,
                content=result,
            )
            summary = {
                "state": "CANDIDATE_REJECTED_VALIDATION",
                "decision": result["decision"],
                "status": result["status"],
                "methodology_version": LEADLAG_METHODOLOGY_VERSION,
                "candidate_id": result["candidate_id"],
                "development_passed": True,
                "validation_passed": False,
                "holdout_opened": False,
                "artifact_id": (
                    (final_artifact.get("artifact") or {}).get("artifact_id")
                    if isinstance(final_artifact.get("artifact"), Mapping) else None
                ),
                "content_hash": final_artifact.get("content_hash"),
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": result["next_action"],
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
            )

        holdout_bars = await self._fetch_stage(
            LEADLAG_UNIVERSE,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
        )
        holdout_high = evaluate_leadlag_stage(
            holdout_bars,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
            scenario="high",
            seed=82301,
        )
        holdout_base = evaluate_leadlag_stage(
            holdout_bars,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
            scenario="base",
            seed=82311,
        )
        holdout_low = evaluate_leadlag_stage(
            holdout_bars,
            start=LEADLAG_HOLDOUT_START,
            end=LEADLAG_HOLDOUT_END,
            scenario="low",
            seed=82321,
        )
        holdout_passed, holdout_reasons = leadlag_holdout_gate(holdout_high)
        holdout_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_HOLDOUT_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content={
                "stage": "HOLDOUT",
                "passed": holdout_passed,
                "reasons": holdout_reasons,
                "bar_counts": {symbol: len(rows) for symbol, rows in holdout_bars.items()},
                "scenarios": {
                    "high": holdout_high,
                    "base": holdout_base,
                    "low": holdout_low,
                },
                "source_commit": _source_commit(),
            },
        )
        result["holdout"] = {
            "opened": True,
            "passed": holdout_passed,
            "reasons": holdout_reasons,
            "artifact_id": (
                (holdout_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(holdout_artifact.get("artifact"), Mapping) else None
            ),
            "scenarios": {
                "high": holdout_high,
                "base": holdout_base,
                "low": holdout_low,
            },
        }
        result.update({
            "status": "HOLDOUT_PASS" if holdout_passed else "HOLDOUT_FAIL",
            "decision": "PROMOTE_TO_VELUM" if holdout_passed else "CONTINUE_RESEARCH",
            "next_action": "VELUM_REPLAY" if holdout_passed else "DESIGN_NEXT_FROZEN_RESEARCH_BATCH",
        })
        final_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_LEADLAG_R2_RESULT",
            methodology_version=LEADLAG_METHODOLOGY_VERSION,
            content=result,
        )
        summary = {
            "state": "CANDIDATE_READY_FOR_VELUM" if holdout_passed else "CANDIDATE_REJECTED_HOLDOUT",
            "decision": result["decision"],
            "status": result["status"],
            "methodology_version": LEADLAG_METHODOLOGY_VERSION,
            "candidate_id": result["candidate_id"],
            "development_passed": True,
            "validation_passed": True,
            "holdout_opened": True,
            "holdout_passed": holdout_passed,
            "artifact_id": (
                (final_artifact.get("artifact") or {}).get("artifact_id")
                if isinstance(final_artifact.get("artifact"), Mapping) else None
            ),
            "content_hash": final_artifact.get("content_hash"),
            "model_invoked": False,
            "execution_authority": False,
            "production_state_changed": False,
            "next_action": result["next_action"],
        }
        return await self._finalize(
            problem=problem,
            run=run,
            status="SUCCEEDED" if holdout_passed else "WAITING",
            summary=summary,
        )

    async def _execute_hypothesis_planner(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_methodology_version = "graen-hypothesis-planner-v1"

        snapshot = await self.gateway.snapshot()
        exposure = await self.gateway.research_exposure_ledger()
        plan = plan_next_hypothesis(snapshot, exposure, now=datetime.now(UTC))

        decision_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_STRATEGY_PLANNER_DECISION",
            methodology_version="graen-hypothesis-planner-v1",
            content={
                **plan,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
            },
        )
        decision_artifact_id = (
            str((decision_artifact.get("artifact") or {}).get("artifact_id"))
            if isinstance(decision_artifact.get("artifact"), Mapping)
            and (decision_artifact.get("artifact") or {}).get("artifact_id")
            else None
        )

        state = str(plan.get("state") or "")
        if state == "ENGINEERING_REQUIRED":
            manifest = plan.get("manifest")
            manifest_hash = str(plan.get("manifest_hash") or "")
            requirement = build_engineering_requirement(
                requirement_id=(
                    "ENG-GRAEN-" + (manifest_hash[:12].upper() if len(manifest_hash) == 64 else "HYPOTHESIS-ENGINE")
                ),
                requested_by="GRAEN",
                title="Extend autonomous crypto hypothesis capability",
                reason=str(plan.get("reason") or "trusted research capability exhausted"),
                capability_required=str(
                    plan.get("capability_required")
                    or "A trusted strategy grammar/compiler capability required by the frozen hypothesis."
                ),
                affected_components=[
                    "app/research_agent/strategy_grammar.py",
                    "app/research_agent/strategy_runner.py",
                    "app/research_agent/hypothesis_planner.py",
                    "app/graen/research_executor_service.py",
                ],
                blocked_research=[
                    str((manifest or {}).get("hypothesis_id") or problem_id)
                    if isinstance(manifest, Mapping)
                    else problem_id
                ],
                acceptance_tests=[
                    "New capability is represented by a frozen strategy manifest, not arbitrary runtime Python.",
                    "Development, validation, holdout, and VELUM remain causally separated.",
                    "Complete search exposure and online multiplicity accounting remain intact.",
                    "Runtime source/Git/merge/deploy authority remains false.",
                    "Existing trading, broker, risk, and credential boundaries remain unchanged.",
                    "After deployment, IREN can restart the planner without manual research-stage intervention.",
                ],
                suggested_paths=[
                    "app/research_agent/strategy_grammar.py",
                    "app/research_agent/strategy_runner.py",
                    "app/research_agent/hypothesis_planner.py",
                    "tests/test_strategy_grammar.py",
                ],
                continuation_policy="Continue every independent research branch; do not repeat exhausted hypotheses.",
                risk="MEDIUM",
                implementation_notes=[
                    "Treat the frozen hypothesis/search history as authoritative.",
                    "Prefer extending data-driven primitives over creating one-off strategy files.",
                    "Do not enable or increase live crypto execution as part of this engineering task.",
                ],
            )
            await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="ENGINEERING_REQUIREMENT",
                methodology_version="graen-hypothesis-planner-v1",
                content=requirement,
            )
            summary = {
                "condition": "ENGINEERING_REQUIRED",
                "state": "ENGINEERING_REQUIRED",
                "decision": "MANUAL_SOFTWARE_REQUIRED",
                "status": "WAITING_FOR_ENGINEERING",
                "planner_artifact_id": decision_artifact_id,
                "engineering_requirement": requirement,
                "manual_chatgpt_workspace_required": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
                "next_action": "OPEN_CHATGPT_CODEX_HANDOFF",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=ENGINEERING_REQUIRED_STAGE,
                next_metadata={
                    "autonomous_loop_id": (
                        metadata.get("autonomous_loop_id")
                        or AUTONOMOUS_LOOP_ID
                    ),
                    "autonomous_continuation": True,
                    "engineering_requirement": requirement,
                    "planner_artifact_id": decision_artifact_id,
                    "engineering_required_source_commit": _source_commit(),
                    "resume_stage": HYPOTHESIS_PLANNER_STAGE,
                },
            )

        if state == "WAITING_FOR_UNINSPECTED_CORPUS":
            corpus = plan.get("corpus") if isinstance(plan.get("corpus"), Mapping) else {}
            next_eligible = str(corpus.get("next_eligible_at") or "")
            parsed = None
            try:
                parsed = datetime.fromisoformat(next_eligible.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                parsed = parsed.astimezone(UTC)
            except ValueError:
                parsed = None
            self.waiting_dependency_until = parsed
            summary = {
                "state": "WAITING_FOR_UNINSPECTED_CORPUS",
                "decision": "WAIT_FOR_NEW_EVIDENCE",
                "status": "WAITING_DATA",
                "planner_artifact_id": decision_artifact_id,
                "candidate_id": (
                    (plan.get("manifest") or {}).get("hypothesis_id")
                    if isinstance(plan.get("manifest"), Mapping)
                    else None
                ),
                "next_eligible_at": next_eligible or None,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
                "next_action": "RESUME_PLANNER_WHEN_CORPUS_ELIGIBLE",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=WAITING_CORPUS_STAGE,
                next_metadata={
                    "autonomous_continuation": True,
                    "resume_stage": HYPOTHESIS_PLANNER_STAGE,
                    "next_eligible_at": next_eligible,
                    "planner_artifact_id": decision_artifact_id,
                },
            )

        if state != "READY":
            raise RuntimeError(f"unsupported_hypothesis_planner_state:{state}")

        manifest = plan.get("manifest")
        if not isinstance(manifest, Mapping):
            raise RuntimeError("hypothesis_planner_missing_manifest")
        self.waiting_dependency_until = None
        manifest_artifact = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_STRATEGY_MANIFEST_V1",
            methodology_version=STRATEGY_RUNNER_VERSION,
            content={
                "manifest": dict(manifest),
                "manifest_hash": plan.get("manifest_hash"),
                "compiler_profile": plan.get("compiler_profile"),
                "corpus": plan.get("corpus"),
                "search_generation": plan.get("search_generation"),
                "validation_alpha": plan.get("validation_alpha"),
                "selection_policy": plan.get("selection_policy"),
                "planner_artifact_id": decision_artifact_id,
                "frozen_before_corpus_access": True,
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
            },
        )
        manifest_artifact_id = (
            str((manifest_artifact.get("artifact") or {}).get("artifact_id"))
            if isinstance(manifest_artifact.get("artifact"), Mapping)
            and (manifest_artifact.get("artifact") or {}).get("artifact_id")
            else None
        )
        frozen = {
            "autonomous_continuation": True,
            "strategy_manifest": dict(manifest),
            "strategy_manifest_hash": str(plan.get("manifest_hash") or ""),
            "strategy_corpus": dict(plan.get("corpus") or {}),
            "strategy_search_generation": int(plan.get("search_generation") or 1),
            "strategy_validation_alpha": float(plan.get("validation_alpha") or 0.0),
            "strategy_manifest_artifact_id": manifest_artifact_id,
            "strategy_planner_artifact_id": decision_artifact_id,
        }
        summary = {
            "state": "STRATEGY_MANIFEST_FROZEN",
            "decision": "CONTINUE_RESEARCH",
            "status": "READY_FOR_DEVELOPMENT",
            "candidate_id": manifest.get("hypothesis_id"),
            "candidate_family": manifest.get("family"),
            "manifest_hash": plan.get("manifest_hash"),
            "manifest_artifact_id": manifest_artifact_id,
            "search_generation": plan.get("search_generation"),
            "validation_alpha": plan.get("validation_alpha"),
            "execution_authority": False,
            "broker_orders_possible": False,
            "production_state_changed": False,
            "next_action": "RUN_DEVELOPMENT",
        }
        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary=summary,
            next_stage=STRATEGY_DEVELOPMENT_STAGE,
            next_metadata=frozen,
        )

    async def _execute_strategy_manifest(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or "")
        manifest_payload = metadata.get("strategy_manifest")
        corpus = metadata.get("strategy_corpus")
        if not isinstance(manifest_payload, Mapping) or not isinstance(corpus, Mapping):
            raise RuntimeError("strategy_manifest_stage_missing_frozen_contract")
        manifest = manifest_from_dict(manifest_payload)
        manifest_hash_value = str(metadata.get("strategy_manifest_hash") or "")
        from app.research_agent.strategy_grammar import manifest_hash as strategy_manifest_hash
        if strategy_manifest_hash(manifest) != manifest_hash_value:
            raise RuntimeError("strategy_manifest_hash_mismatch")

        search_generation = int(metadata.get("strategy_search_generation") or 1)
        alpha = float(metadata.get("strategy_validation_alpha") or 0.0)
        if not 0 < alpha <= 0.05:
            raise RuntimeError("invalid_strategy_confirmatory_alpha")
        self.active_methodology_version = STRATEGY_RUNNER_VERSION

        def bounds(name: str) -> tuple[datetime, datetime]:
            values = corpus.get(name)
            if not isinstance(values, (list, tuple)) or len(values) != 2:
                raise RuntimeError("invalid_strategy_corpus")
            left = datetime.fromisoformat(str(values[0]).replace("Z", "+00:00"))
            right = datetime.fromisoformat(str(values[1]).replace("Z", "+00:00"))
            if left.tzinfo is None or right.tzinfo is None or not left < right:
                raise RuntimeError("invalid_strategy_corpus")
            return left.astimezone(UTC), right.astimezone(UTC)

        async def record(artifact_type: str, payload: Mapping[str, Any]) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=STRATEGY_RUNNER_VERSION,
                content={
                    **dict(payload),
                    "manifest_hash": manifest_hash_value,
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "search_generation": search_generation,
                    "confirmatory_alpha": alpha,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                },
            )
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        continuation_metadata = {
            key: value
            for key, value in metadata.items()
            if key != "research_stage"
        }

        if stage == STRATEGY_DEVELOPMENT_STAGE:
            start, end = bounds("development")
            await record(
                "CRYPTO_STRATEGY_STAGE_OPENED",
                {"stage": "DEVELOPMENT", "start": start.isoformat(), "end": end.isoformat()},
            )
            bars = await self._fetch_stage(
                CONTEXT_UNIVERSE,
                start=start,
                end=end,
                warmup_hours=8,
            )
            result = evaluate_strategy_development(
                bars,
                manifest,
                start=start,
                end=end,
                seed=93000 + search_generation * 100,
            )
            artifact_id = await record("CRYPTO_STRATEGY_DEVELOPMENT_RESULT", result)
            if result.get("passed") is True:
                next_metadata = {
                    **continuation_metadata,
                    "strategy_development_result": result,
                    "strategy_development_artifact_id": artifact_id,
                }
                summary = {
                    "state": "STRATEGY_DEVELOPMENT_PASSED",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "development_artifact_id": artifact_id,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "execution_authority": False,
                    "next_action": "RUN_UNTOUCHED_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_VALIDATION_STAGE,
                    next_metadata=next_metadata,
                )
            summary = {
                "state": "STRATEGY_REJECTED_DEVELOPMENT",
                "decision": "CONTINUE_RESEARCH",
                "status": "DEVELOPMENT_FAIL",
                "candidate_id": manifest.hypothesis_id,
                "candidate_family": manifest.family,
                "reasons": result.get("reasons") or [],
                "validation_opened": False,
                "holdout_opened": False,
                "execution_authority": False,
                "next_action": "GENERATE_NEXT_HYPOTHESIS",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=HYPOTHESIS_PLANNER_STAGE,
                next_metadata={"autonomous_continuation": True},
            )

        if stage == STRATEGY_VALIDATION_STAGE:
            development = metadata.get("strategy_development_result")
            if not isinstance(development, Mapping) or development.get("passed") is not True:
                raise RuntimeError("strategy_validation_requires_passed_development")
            start, end = bounds("validation")
            await record(
                "CRYPTO_STRATEGY_STAGE_OPENED",
                {"stage": "VALIDATION", "start": start.isoformat(), "end": end.isoformat()},
            )
            bars = await self._fetch_stage(
                CONTEXT_UNIVERSE,
                start=start,
                end=end,
                warmup_hours=8,
            )
            result = evaluate_strategy_validation(
                bars,
                manifest,
                start=start,
                end=end,
                development_result=development,
                seed=94000 + search_generation * 100,
                confirmatory_alpha=alpha,
            )
            artifact_id = await record("CRYPTO_STRATEGY_VALIDATION_RESULT", result)
            if result.get("passed") is True:
                next_metadata = {
                    **continuation_metadata,
                    "strategy_validation_result": result,
                    "strategy_validation_artifact_id": artifact_id,
                }
                summary = {
                    "state": "STRATEGY_VALIDATION_PASSED",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "validation_artifact_id": artifact_id,
                    "validation_opened": True,
                    "holdout_opened": False,
                    "execution_authority": False,
                    "next_action": "RUN_UNTOUCHED_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_HOLDOUT_STAGE,
                    next_metadata=next_metadata,
                )
            summary = {
                "state": "STRATEGY_REJECTED_VALIDATION",
                "decision": "CONTINUE_RESEARCH",
                "status": "VALIDATION_FAIL",
                "candidate_id": manifest.hypothesis_id,
                "candidate_family": manifest.family,
                "reasons": result.get("reasons") or [],
                "validation_opened": True,
                "holdout_opened": False,
                "execution_authority": False,
                "next_action": "GENERATE_NEXT_HYPOTHESIS",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=HYPOTHESIS_PLANNER_STAGE,
                next_metadata={"autonomous_continuation": True},
            )

        if stage == STRATEGY_HOLDOUT_STAGE:
            validation = metadata.get("strategy_validation_result")
            if not isinstance(validation, Mapping) or validation.get("passed") is not True:
                raise RuntimeError("strategy_holdout_requires_passed_validation")
            start, end = bounds("holdout")
            await record(
                "CRYPTO_STRATEGY_STAGE_OPENED",
                {"stage": "HOLDOUT", "start": start.isoformat(), "end": end.isoformat()},
            )
            bars = await self._fetch_stage(
                CONTEXT_UNIVERSE,
                start=start,
                end=end,
                warmup_hours=8,
            )
            result = evaluate_strategy_holdout(
                bars,
                manifest,
                start=start,
                end=end,
                seed=95000 + search_generation * 100,
                confirmatory_alpha=alpha,
            )
            artifact_id = await record("CRYPTO_STRATEGY_HOLDOUT_RESULT", result)
            if result.get("passed") is True:
                next_metadata = {
                    **continuation_metadata,
                    "strategy_holdout_result": result,
                    "strategy_holdout_artifact_id": artifact_id,
                }
                summary = {
                    "state": "STRATEGY_HOLDOUT_PASSED",
                    "decision": "PROMOTE_TO_VELUM",
                    "status": "HOLDOUT_PASS",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "holdout_artifact_id": artifact_id,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "execution_authority": False,
                    "next_action": "RUN_VELUM_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_VELUM_STAGE,
                    next_metadata=next_metadata,
                )
            summary = {
                "state": "STRATEGY_REJECTED_HOLDOUT",
                "decision": "CONTINUE_RESEARCH",
                "status": "HOLDOUT_FAIL",
                "candidate_id": manifest.hypothesis_id,
                "candidate_family": manifest.family,
                "reasons": result.get("reasons") or [],
                "holdout_opened": True,
                "holdout_passed": False,
                "execution_authority": False,
                "next_action": "GENERATE_NEXT_HYPOTHESIS",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=HYPOTHESIS_PLANNER_STAGE,
                next_metadata={"autonomous_continuation": True},
            )

        if stage == STRATEGY_VELUM_STAGE:
            holdout = metadata.get("strategy_holdout_result")
            if not isinstance(holdout, Mapping) or holdout.get("passed") is not True:
                raise RuntimeError("strategy_velum_requires_passed_holdout")
            _, holdout_end = bounds("holdout")
            replay_start = holdout_end
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                next_eligible = replay_start + timedelta(days=1)
                self.waiting_dependency_until = next_eligible
                summary = {
                    "state": "VELUM_REPLAY_WAITING_FOR_DATA",
                    "decision": "WAIT_FOR_NEW_EVIDENCE",
                    "status": "WAITING_DATA",
                    "candidate_id": manifest.hypothesis_id,
                    "next_eligible_at": next_eligible.isoformat(),
                    "execution_authority": False,
                    "next_action": "RESUME_VELUM_WHEN_DATA_AVAILABLE",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=WAITING_CORPUS_STAGE,
                    next_metadata={
                        **continuation_metadata,
                        "resume_stage": STRATEGY_VELUM_STAGE,
                        "next_eligible_at": next_eligible.isoformat(),
                    },
                )

            candidate_spec = compile_strategy_candidate(manifest).to_dict()
            velum = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id="strategy-manifest-autonomous-v1",
                epoch_index=search_generation,
                generation=search_generation,
                candidate_methodology=STRATEGY_RUNNER_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=96000 + search_generation * 100,
            )
            artifact_id = await record(
                "CRYPTO_STRATEGY_VELUM_RESULT",
                {
                    "velum_result": velum,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum.get("engineering_gate")
            passed = isinstance(gate, Mapping) and gate.get("passed") is True
            if passed:
                shadow = await self._activate_forward_shadow(
                    problem_id=problem_id,
                    graen_run_id=run_id,
                    campaign_id="strategy-manifest-autonomous-v1",
                    epoch_index=search_generation,
                    generation=search_generation,
                    candidate_methodology=STRATEGY_RUNNER_VERSION,
                    candidate_spec=candidate_spec,
                    velum_artifact_id=artifact_id,
                )
                summary = {
                    "state": "FORWARD_SHADOW_RUNNING",
                    "decision": "COLLECT_FORWARD_EVIDENCE",
                    "status": "SHADOW_ACTIVE",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "velum_artifact_id": artifact_id,
                    "velum_engineering_gate": dict(gate),
                    "shadow_activation": shadow,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "promotion_authorized": False,
                    "next_action": "AWAIT_NATIVE_SHADOW_CHECKPOINT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_SHADOW_STAGE,
                    next_metadata={
                        **continuation_metadata,
                        "strategy_shadow_activation": dict(
                            shadow.get("activation") or {}
                        ),
                        "strategy_shadow_velum_artifact_id": artifact_id,
                    },
                )

            summary = {
                "state": "STRATEGY_REJECTED_VELUM",
                "decision": "CONTINUE_RESEARCH",
                "status": "VELUM_FAIL",
                "candidate_id": manifest.hypothesis_id,
                "candidate_family": manifest.family,
                "velum_artifact_id": artifact_id,
                "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                "execution_authority": False,
                "next_action": "GENERATE_NEXT_HYPOTHESIS",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=HYPOTHESIS_PLANNER_STAGE,
                next_metadata={"autonomous_continuation": True},
            )

        if stage == STRATEGY_SHADOW_STAGE:
            activation = metadata.get("strategy_shadow_activation")
            if not isinstance(activation, Mapping):
                raise RuntimeError("strategy_shadow_requires_activation")
            activation_id = str(activation.get("activation_id") or "")
            status = await self._forward_shadow_status(activation_id)
            candidate_status = status.get("candidate")
            candidate_status = (
                dict(candidate_status)
                if isinstance(candidate_status, Mapping)
                else {}
            )
            observed_candidate_id = str(
                candidate_status.get("candidate_id")
                or (status.get("checkpoint") or {}).get("candidate_id")
                or ""
            )
            if observed_candidate_id != manifest.hypothesis_id:
                raise RuntimeError("forward_shadow_candidate_identity_mismatch")
            checkpoint = status.get("checkpoint")
            checkpoint = dict(checkpoint) if isinstance(checkpoint, Mapping) else {}
            checkpoint_status = str(
                status.get("checkpoint_status")
                or checkpoint.get("status")
                or "COLLECTING"
            ).upper()

            await record(
                "CRYPTO_STRATEGY_FORWARD_SHADOW_CHECKPOINT",
                {
                    "shadow_status": status,
                    "checkpoint": checkpoint,
                    "checkpoint_status": checkpoint_status,
                },
            )

            if checkpoint_status == "SHADOW_REJECTED":
                summary = {
                    "state": "STRATEGY_REJECTED_FORWARD_SHADOW",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "SHADOW_FAIL",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "checkpoint": checkpoint,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": "GENERATE_NEXT_HYPOTHESIS",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=HYPOTHESIS_PLANNER_STAGE,
                    next_metadata={"autonomous_continuation": True},
                )

            if checkpoint_status == "READY_FOR_PAPER":
                summary = {
                    "state": "STRATEGY_READY_FOR_PAPER",
                    "decision": "CONTINUE_TO_PAPER",
                    "status": "SHADOW_PASS",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "checkpoint": checkpoint,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "live_promotion_authorized": False,
                    "next_action": "START_PAPER_CANARY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_PAPER_STAGE,
                    next_metadata={
                        **continuation_metadata,
                        "strategy_shadow_activation": dict(activation),
                        "strategy_shadow_checkpoint": checkpoint,
                    },
                )

            if checkpoint_status not in {"COLLECTING", "QUEUED", ""}:
                raise RuntimeError(
                    "unsupported_forward_shadow_checkpoint:"
                    + checkpoint_status
                )

            summary = {
                "state": "FORWARD_SHADOW_RUNNING",
                "decision": "COLLECT_FORWARD_EVIDENCE",
                "status": "SHADOW_COLLECTING",
                "candidate_id": manifest.hypothesis_id,
                "candidate_family": manifest.family,
                "checkpoint": checkpoint,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
                "next_action": "AWAIT_NATIVE_SHADOW_CHECKPOINT",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=STRATEGY_SHADOW_STAGE,
                next_metadata=continuation_metadata,
            )

        if stage == STRATEGY_PAPER_STAGE:
            shadow_activation = metadata.get("strategy_shadow_activation")
            shadow_checkpoint = metadata.get("strategy_shadow_checkpoint")
            if not isinstance(shadow_activation, Mapping):
                raise RuntimeError("strategy_paper_requires_shadow_activation")
            if not isinstance(shadow_checkpoint, Mapping):
                raise RuntimeError("strategy_paper_requires_shadow_checkpoint")
            candidate_spec = compile_strategy_candidate(manifest).to_dict()

            if not self.paper_configured:
                requirement = build_engineering_requirement(
                    requirement_id=(
                        "ENG-PAPER-"
                        + manifest.hypothesis_id.replace("_", "-").upper()[:48]
                    ),
                    requested_by="GRAEN",
                    title="Deploy generic GRAEN paper-canary adapter",
                    reason=(
                        "A strategy-manifest candidate passed forward shadow, but "
                        "the isolated paper-only adapter is not configured."
                    ),
                    capability_required=(
                        "Configure the canonical app.graen.paper_host in a dedicated "
                        "Alpaca paper-mode RHEN deployment with durable state, "
                        "Foundation telemetry, bounded paper risk, and the shared "
                        "GRAEN paper activation token."
                    ),
                    affected_components=[
                        "GRAEN paper canary",
                        "Railway paper-only service",
                        "Foundation telemetry",
                        "IREN Command",
                    ],
                    blocked_research=[problem_id, manifest.hypothesis_id],
                    acceptance_tests=[
                        "Service hard-gates itself to Alpaca paper endpoint.",
                        "No live execution authority exists.",
                        "Exact strategy-runner candidate activates idempotently.",
                        "Paper checkpoint survives restart.",
                        "GRAEN can poll PAPER_PASSED or PAPER_REJECTED.",
                    ],
                    suggested_paths=[
                        "app/graen/paper_host.py",
                        "app/main.py",
                        "tests/test_graen_paper_host.py",
                    ],
                    continuation_policy=(
                        "Continue all independent research and shadow candidates "
                        "while this candidate waits for paper-adapter deployment."
                    ),
                    risk="LOW",
                )
                summary = {
                    "condition": "ENGINEERING_REQUIRED",
                    "state": "PAPER_ADAPTER_REQUIRED",
                    "decision": "ENGINEERING_REQUIRED",
                    "status": "PAPER_NOT_CONFIGURED",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "engineering_requirement": requirement,
                    "manual_chatgpt_workspace_required": True,
                    "independent_research_continues": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "live_promotion_authorized": False,
                    "production_state_changed": False,
                    "next_action": "DEPLOY_GENERIC_PAPER_CANARY_ADAPTER",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_PAPER_STAGE,
                    next_metadata=continuation_metadata,
                )

            paper_activation = metadata.get("strategy_paper_activation")
            if not isinstance(paper_activation, Mapping):
                activation = await self._activate_paper_canary(
                    problem_id=problem_id,
                    graen_run_id=run_id,
                    campaign_id="strategy-manifest-autonomous-v1",
                    candidate_methodology=STRATEGY_RUNNER_VERSION,
                    candidate_spec=candidate_spec,
                    shadow_activation_id=str(
                        shadow_activation.get("activation_id") or ""
                    ),
                    shadow_checkpoint=shadow_checkpoint,
                )
                if activation.get("busy") is True:
                    summary = {
                        "state": "PAPER_CANARY_WAITING_FOR_SLOT",
                        "decision": "COLLECT_PAPER_EVIDENCE",
                        "status": "PAPER_WAITING",
                        "candidate_id": manifest.hypothesis_id,
                        "candidate_family": manifest.family,
                        "paper_adapter": activation,
                        "execution_authority": "PAPER_ONLY",
                        "live_execution_authority": False,
                        "live_promotion_authorized": False,
                        "next_action": "WAIT_FOR_PAPER_CANARY_SLOT",
                    }
                    return await self._finalize(
                        problem=problem,
                        run=run,
                        status="WAITING",
                        summary=summary,
                        next_stage=STRATEGY_PAPER_STAGE,
                        next_metadata=continuation_metadata,
                    )
                if activation.get("not_authorized") is True:
                    requirement = build_engineering_requirement(
                        requirement_id="ENG-PAPER-CONFIG-V1",
                        requested_by="GRAEN",
                        title="Authorize isolated GRAEN paper-canary runtime",
                        reason=(
                            "The canonical paper adapter is reachable but its "
                            "paper-only execution charter is not fully configured."
                        ),
                        capability_required=(
                            "Configure the dedicated paper service with Alpaca paper "
                            "credentials plus GRAEN_PAPER_ENABLED=true, "
                            "I_ACKNOWLEDGE_GRAEN_AUTONOMOUS_PAPER=YES, bounded paper "
                            "risk variables, execution enabled, bot armed, live false, "
                            "and crypto live execution disabled."
                        ),
                        affected_components=[
                            "GRAEN paper canary",
                            "Railway paper-only service",
                            "Alpaca paper account",
                            "IREN Command",
                        ],
                        blocked_research=[problem_id, manifest.hypothesis_id],
                        acceptance_tests=[
                            "Paper host reports paper_execution_authorized=true.",
                            "Paper host reports live_execution_authorized=false.",
                            "Dedicated crypto paper account is clean at activation.",
                            "Exact READY_FOR_PAPER candidate activates idempotently.",
                            "No live credential or live-risk authority is introduced.",
                        ],
                        suggested_paths=[
                            "app/graen/paper_host.py",
                            "app/main.py",
                            "tests/test_graen_paper_host.py",
                        ],
                        continuation_policy=(
                            "Continue independent research and shadow candidates "
                            "while paper authorization is configured."
                        ),
                        risk="LOW",
                    )
                    summary = {
                        "condition": "ENGINEERING_REQUIRED",
                        "state": "PAPER_AUTHORIZATION_REQUIRED",
                        "decision": "ENGINEERING_REQUIRED",
                        "status": "PAPER_NOT_AUTHORIZED",
                        "candidate_id": manifest.hypothesis_id,
                        "candidate_family": manifest.family,
                        "engineering_requirement": requirement,
                        "manual_chatgpt_workspace_required": True,
                        "independent_research_continues": True,
                        "execution_authority": False,
                        "live_execution_authority": False,
                        "live_promotion_authorized": False,
                        "next_action": "CONFIGURE_PAPER_ONLY_RUNTIME",
                    }
                    return await self._finalize(
                        problem=problem,
                        run=run,
                        status="WAITING",
                        summary=summary,
                        next_stage=STRATEGY_PAPER_STAGE,
                        next_metadata=continuation_metadata,
                    )
                paper_activation = dict(activation.get("activation") or {})
                summary = {
                    "state": "PAPER_CANARY_QUEUED",
                    "decision": "COLLECT_PAPER_EVIDENCE",
                    "status": "PAPER_ACTIVE",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "paper_activation": activation,
                    "execution_authority": "PAPER_ONLY",
                    "live_execution_authority": False,
                    "live_promotion_authorized": False,
                    "next_action": "AWAIT_PAPER_CANARY_CHECKPOINT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=STRATEGY_PAPER_STAGE,
                    next_metadata={
                        **continuation_metadata,
                        "strategy_shadow_activation": dict(shadow_activation),
                        "strategy_shadow_checkpoint": dict(shadow_checkpoint),
                        "strategy_paper_activation": paper_activation,
                    },
                )

            paper_activation_id = str(
                paper_activation.get("activation_id") or ""
            )
            paper_status = await self._paper_canary_status(
                paper_activation_id
            )
            checkpoint = paper_status.get("checkpoint")
            checkpoint = (
                dict(checkpoint)
                if isinstance(checkpoint, Mapping)
                else {}
            )
            checkpoint_status = str(
                checkpoint.get("status") or "QUEUED"
            ).upper()
            await record(
                "CRYPTO_STRATEGY_PAPER_CHECKPOINT",
                {
                    "paper_status": paper_status,
                    "checkpoint": checkpoint,
                    "checkpoint_status": checkpoint_status,
                },
            )

            if checkpoint_status == "PAPER_REJECTED":
                summary = {
                    "state": "STRATEGY_REJECTED_PAPER",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "PAPER_FAIL",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "checkpoint": checkpoint,
                    "execution_authority": "PAPER_ONLY",
                    "live_execution_authority": False,
                    "live_promotion_authorized": False,
                    "next_action": "GENERATE_NEXT_HYPOTHESIS",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=HYPOTHESIS_PLANNER_STAGE,
                    next_metadata={"autonomous_continuation": True},
                )

            if checkpoint_status == "PAPER_PASSED":
                summary = {
                    "condition": "HUMAN_DECISION_REQUIRED",
                    "state": "STRATEGY_PAPER_VALIDATED",
                    "decision": "HUMAN_DECISION_REQUIRED",
                    "status": "PAPER_PASS",
                    "candidate_id": manifest.hypothesis_id,
                    "candidate_family": manifest.family,
                    "checkpoint": checkpoint,
                    "paper_activation": dict(paper_activation),
                    "execution_authority": "PAPER_ONLY",
                    "live_execution_authority": False,
                    "live_promotion_authorized": False,
                    "protected_decision": {
                        "decision_type": "LIVE_RISK_CHARTER",
                        "requested_action": (
                            "Authorize, reject, or archive real-money canary "
                            "promotion for this exact validated candidate."
                        ),
                        "candidate_id": manifest.hypothesis_id,
                        "strategy_manifest_hash": metadata.get(
                            "strategy_manifest_hash"
                        ),
                        "risk_increase_authorized": False,
                    },
                    "next_action": "REVIEW_LIVE_RISK_CHARTER",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=HYPOTHESIS_PLANNER_STAGE,
                    next_metadata={
                        "autonomous_loop_id": (
                            metadata.get("autonomous_loop_id")
                            or AUTONOMOUS_LOOP_ID
                        ),
                        "autonomous_continuation": True,
                        "last_paper_validated_candidate_id": manifest.hypothesis_id,
                        "last_paper_validation_activation_id": str(
                            paper_activation.get("activation_id") or ""
                        ),
                        "protected_live_risk_decision_pending": True,
                    },
                )

            if checkpoint_status not in {"PAPER_COLLECTING", "QUEUED", "ACTIVE"}:
                raise RuntimeError(
                    "unsupported_paper_checkpoint:" + checkpoint_status
                )
            summary = {
                "state": "PAPER_CANARY_RUNNING",
                "decision": "COLLECT_PAPER_EVIDENCE",
                "status": checkpoint_status,
                "candidate_id": manifest.hypothesis_id,
                "candidate_family": manifest.family,
                "checkpoint": checkpoint,
                "execution_authority": "PAPER_ONLY",
                "live_execution_authority": False,
                "live_promotion_authorized": False,
                "next_action": "AWAIT_PAPER_CANARY_CHECKPOINT",
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=STRATEGY_PAPER_STAGE,
                next_metadata=continuation_metadata,
            )

        raise RuntimeError(f"unsupported_strategy_manifest_stage:{stage}")

    async def _execute_autonomous_campaign(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or AUTONOMOUS_DEVELOPMENT_STAGE)
        epoch_index = int(metadata.get("campaign_epoch") or 0)
        generation = int(metadata.get("campaign_generation") or 1)
        contract = autonomous_epoch_contract(epoch_index)
        self.active_methodology_version = autonomous_methodology_version(
            epoch_index,
            generation,
            stage,
        )

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(
            artifact_type: str,
            content: dict[str, Any],
        ) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=self.active_methodology_version,
                content={
                    **content,
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        def next_epoch_transition() -> tuple[str | None, dict[str, Any] | None]:
            next_epoch = autonomous_next_epoch_index(epoch_index)
            if next_epoch is None:
                return None, None
            return (
                AUTONOMOUS_DEVELOPMENT_STAGE,
                autonomous_stage_metadata(
                    epoch_index=next_epoch,
                    generation=1,
                ),
            )

        if stage == AUTONOMOUS_DEVELOPMENT_STAGE:
            prespec = autonomous_prespecification(epoch_index, generation)
            prespec_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                AUTONOMOUS_UNIVERSE,
                start=contract["development_start"],
                end=contract["validation_start"],
                warmup_hours=8,
            )
            development = evaluate_autonomous_development(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
                generation=generation,
            )
            development_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_DEVELOPMENT_RESULT",
                {
                    **development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            selected_result = development.get("selected_development_result")
            if isinstance(selected_spec, Mapping) and isinstance(selected_result, Mapping):
                next_metadata = autonomous_stage_metadata(
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_spec=selected_spec,
                    development_result=selected_result,
                )
                summary = {
                    "state": "AUTONOMOUS_CANDIDATE_FROZEN_FOR_VALIDATION",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": selected_spec.get("candidate_id"),
                    "candidate_family": selected_spec.get("family"),
                    "prespec_artifact_id": prespec_artifact,
                    "development_artifact_id": development_artifact,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "RUN_FRESH_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_VALIDATION_STAGE,
                    next_metadata=next_metadata,
                )

            if generation < MAX_GENERATIONS_PER_EPOCH:
                next_stage = AUTONOMOUS_DEVELOPMENT_STAGE
                next_metadata = autonomous_stage_metadata(
                    epoch_index=epoch_index,
                    generation=generation + 1,
                )
                state = "AUTONOMOUS_GENERATION_REJECTED"
                next_action = "GENERATE_NEXT_DEVELOPMENT_BATCH"
            else:
                next_stage, next_metadata = next_epoch_transition()
                state = (
                    "AUTONOMOUS_EPOCH_REJECTED_DEVELOPMENT"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                )
                next_action = (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                )

            summary = {
                "state": state,
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "prespec_artifact_id": prespec_artifact,
                "development_artifact_id": development_artifact,
                "validation_opened": False,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": next_action,
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        candidate_spec = metadata.get("candidate_spec")
        development_result = metadata.get("development_result")
        if not isinstance(candidate_spec, Mapping) or not isinstance(development_result, Mapping):
            raise RuntimeError("autonomous_campaign_missing_frozen_candidate")

        if stage == AUTONOMOUS_VALIDATION_STAGE:
            bars = await self._fetch_stage(
                AUTONOMOUS_UNIVERSE,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                warmup_hours=8,
            )
            validation = evaluate_autonomous_validation(
                bars,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                candidate_spec=candidate_spec,
                development_result=development_result,
                seed=89000 + epoch_index * 100 + generation,
            )
            validation_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_VALIDATION_RESULT",
                {
                    **validation,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            if validation.get("passed") is True:
                summary = {
                    "state": "AUTONOMOUS_CANDIDATE_FROZEN_FOR_HOLDOUT",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": candidate_spec.get("family"),
                    "validation_artifact_id": validation_artifact,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "OPEN_FRESH_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_HOLDOUT_STAGE,
                    next_metadata=autonomous_stage_metadata(
                        epoch_index=epoch_index,
                        generation=generation,
                        candidate_spec=candidate_spec,
                        development_result=development_result,
                    ),
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "AUTONOMOUS_CANDIDATE_REJECTED_VALIDATION"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VALIDATION_FAIL",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": candidate_spec.get("family"),
                "validation_artifact_id": validation_artifact,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == AUTONOMOUS_HOLDOUT_STAGE:
            bars = await self._fetch_stage(
                AUTONOMOUS_UNIVERSE,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                warmup_hours=8,
            )
            holdout = evaluate_autonomous_holdout(
                bars,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                candidate_spec=candidate_spec,
                seed=90000 + epoch_index * 100 + generation,
            )
            holdout_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_HOLDOUT_RESULT",
                {
                    **holdout,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            if holdout.get("passed") is True:
                next_metadata = autonomous_stage_metadata(
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_spec=candidate_spec,
                    development_result=development_result,
                )
                next_metadata["holdout_result"] = holdout
                next_metadata["holdout_artifact_id"] = holdout_artifact
                summary = {
                    "state": "CANDIDATE_READY_FOR_VELUM",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "HOLDOUT_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": candidate_spec.get("family"),
                    "holdout_artifact_id": holdout_artifact,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "VELUM_CANDIDATE_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_VELUM_STAGE,
                    next_metadata=next_metadata,
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "AUTONOMOUS_CANDIDATE_REJECTED_HOLDOUT"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "HOLDOUT_FAIL",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": candidate_spec.get("family"),
                "holdout_artifact_id": holdout_artifact,
                "holdout_opened": True,
                "holdout_passed": False,
                "model_invoked": False,
                "execution_authority": False,
                "production_state_changed": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == AUTONOMOUS_VELUM_STAGE:
            holdout_result = metadata.get("holdout_result")
            holdout_artifact_id = metadata.get("holdout_artifact_id")
            if not isinstance(candidate_spec, Mapping) or not isinstance(holdout_result, Mapping):
                raise RuntimeError("autonomous_velum_stage_missing_frozen_candidate")
            replay_start = contract["holdout_end"]
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                summary = {
                    "state": "VELUM_REPLAY_WAITING_FOR_DATA",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "WAITING_FOR_REPLAY_WINDOW",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "holdout_artifact_id": holdout_artifact_id,
                    "execution_authority": False,
                    "production_state_changed": False,
                    "next_action": "RETRY_VELUM_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=AUTONOMOUS_VELUM_STAGE,
                    next_metadata=dict(metadata),
                )

            velum_result = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=AUTONOMOUS_CAMPAIGN_ID,
                epoch_index=epoch_index,
                generation=generation,
                candidate_methodology=AUTONOMOUS_METHODOLOGY_PREFIX,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=91000 + epoch_index * 100 + generation,
            )
            velum_artifact = await record_stage(
                "CRYPTO_AUTONOMOUS_VELUM_RESULT",
                {
                    "velum_result": velum_result,
                    "holdout_artifact_id": holdout_artifact_id,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum_result.get("engineering_gate")
            passed = bool(
                isinstance(gate, Mapping)
                and gate.get("passed") is True
            )
            if passed:
                summary = {
                    "state": "CANDIDATE_READY_FOR_FORWARD_SHADOW",
                    "decision": "FORWARD_SHADOW_REQUIRED",
                    "status": "VELUM_PASS",
                    "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                    "epoch": contract["epoch"],
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": candidate_spec.get("family"),
                    "holdout_artifact_id": holdout_artifact_id,
                    "velum_artifact_id": velum_artifact,
                    "velum_engineering_gate": dict(gate),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": "FORWARD_SHADOW_OBSERVATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="SUCCEEDED",
                    summary=summary,
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "AUTONOMOUS_CANDIDATE_REJECTED_VELUM"
                    if next_stage
                    else "AUTONOMOUS_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VELUM_FAIL",
                "campaign_id": AUTONOMOUS_CAMPAIGN_ID,
                "epoch": contract["epoch"],
                "epoch_index": epoch_index,
                "generation": generation,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": candidate_spec.get("family"),
                "holdout_artifact_id": holdout_artifact_id,
                "velum_artifact_id": velum_artifact,
                "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        raise RuntimeError(f"unsupported_autonomous_campaign_stage:{stage}")

    async def _execute_activity_shock_v9(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V9_DEVELOPMENT_STAGE)
        epoch_index = int(metadata.get("v9_epoch_index") or 0)
        generation = 1
        contract = _v9_epoch_contract(epoch_index)
        self.active_methodology_version = V9_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(
            artifact_type: str,
            content: dict[str, Any],
        ) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V9_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        def next_epoch_transition() -> tuple[str | None, dict[str, Any] | None]:
            next_epoch = _v9_next_epoch(epoch_index)
            if next_epoch is None:
                return None, None
            return (
                V9_DEVELOPMENT_STAGE,
                {
                    "v9_campaign_id": V9_CAMPAIGN_ID,
                    "v9_epoch_index": next_epoch,
                    "v9_generation": 1,
                },
            )

        if stage == V9_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.crypto_activity_shock.prespec.v1",
                "campaign_id": V9_CAMPAIGN_ID,
                "methodology_version": V9_METHODOLOGY_VERSION,
                "family": V9_FAMILY,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_registry": [row.to_dict() for row in v9_candidate_specs()],
                "candidate_count": len(v9_candidate_specs()),
                "selection_rule": (
                    "select one development survivor by expectancy_per_trade * sqrt(trade_count); "
                    "validation and holdout remain unopened until prior gate passes"
                ),
                "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY"],
                "development": [
                    contract["development_start"].isoformat(),
                    contract["validation_start"].isoformat(),
                ],
                "validation": [
                    contract["validation_start"].isoformat(),
                    contract["holdout_start"].isoformat(),
                ],
                "holdout": [
                    contract["holdout_start"].isoformat(),
                    contract["holdout_end"].isoformat(),
                ],
                "development_availability_probe_disclosed": list(
                    contract["development_availability_probe_disclosed"]
                ),
                "validation_previously_inspected": False,
                "holdout_previously_inspected": False,
                "frozen_before_stage_corpus_access": True,
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                    "model_execution_enabled": False,
                },
            }
            prespec_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V9_UNIVERSE,
                start=contract["development_start"],
                end=contract["validation_start"],
                warmup_hours=25,
            )
            development_corpus = verify_v9_stage_corpus(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development = evaluate_v9_development(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_DEVELOPMENT_RESULT",
                {
                    **development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": development_corpus,
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            if isinstance(selected_spec, Mapping):
                summary = {
                    "state": "V9_CANDIDATE_FROZEN_FOR_VALIDATION",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": selected_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "prespec_artifact_id": prespec_artifact,
                    "development_artifact_id": development_artifact,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "RUN_FRESH_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V9_VALIDATION_STAGE,
                    next_metadata={
                        "v9_campaign_id": V9_CAMPAIGN_ID,
                        "v9_epoch_index": epoch_index,
                        "v9_generation": 1,
                        "v9_candidate_spec": dict(selected_spec),
                        "v9_development_artifact_id": development_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V9_EPOCH_REJECTED_DEVELOPMENT"
                    if next_stage
                    else "V9_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "campaign_id": V9_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "prespec_artifact_id": prespec_artifact,
                "development_artifact_id": development_artifact,
                "validation_opened": False,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        candidate_spec = metadata.get("v9_candidate_spec")
        if not isinstance(candidate_spec, Mapping):
            raise RuntimeError("v9_missing_frozen_candidate")

        if stage == V9_VALIDATION_STAGE:
            bars = await self._fetch_stage(
                V9_UNIVERSE,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                warmup_hours=25,
            )
            validation_corpus = verify_v9_stage_corpus(
                bars,
                start=contract["validation_start"],
                end=contract["holdout_start"],
            )
            validation = evaluate_v9_validation(
                bars,
                candidate_spec=candidate_spec,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                seed=93000 + epoch_index * 100,
            )
            validation_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_VALIDATION_RESULT",
                {
                    **validation,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": validation_corpus,
                },
            )
            if validation.get("passed") is True:
                summary = {
                    "state": "V9_CANDIDATE_FROZEN_FOR_HOLDOUT",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "validation_artifact_id": validation_artifact,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "OPEN_FRESH_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V9_HOLDOUT_STAGE,
                    next_metadata={
                        "v9_campaign_id": V9_CAMPAIGN_ID,
                        "v9_epoch_index": epoch_index,
                        "v9_generation": 1,
                        "v9_candidate_spec": dict(candidate_spec),
                        "v9_validation_artifact_id": validation_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V9_CANDIDATE_REJECTED_VALIDATION"
                    if next_stage
                    else "V9_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VALIDATION_FAIL",
                "campaign_id": V9_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V9_FAMILY,
                "validation_artifact_id": validation_artifact,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V9_HOLDOUT_STAGE:
            bars = await self._fetch_stage(
                V9_UNIVERSE,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                warmup_hours=25,
            )
            holdout_corpus = verify_v9_stage_corpus(
                bars,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
            )
            holdout = evaluate_v9_holdout(
                bars,
                candidate_spec=candidate_spec,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                seed=94000 + epoch_index * 100,
            )
            holdout_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_HOLDOUT_RESULT",
                {
                    **holdout,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": holdout_corpus,
                },
            )
            if holdout.get("passed") is True:
                summary = {
                    "state": "V9_CANDIDATE_READY_FOR_VELUM",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "HOLDOUT_PASS",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "holdout_artifact_id": holdout_artifact,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "VELUM_CANDIDATE_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V9_VELUM_STAGE,
                    next_metadata={
                        "v9_campaign_id": V9_CAMPAIGN_ID,
                        "v9_epoch_index": epoch_index,
                        "v9_generation": 1,
                        "v9_candidate_spec": dict(candidate_spec),
                        "v9_holdout_result": holdout,
                        "v9_holdout_artifact_id": holdout_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V9_CANDIDATE_REJECTED_HOLDOUT"
                    if next_stage
                    else "V9_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "HOLDOUT_FAIL",
                "campaign_id": V9_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V9_FAMILY,
                "holdout_artifact_id": holdout_artifact,
                "holdout_opened": True,
                "holdout_passed": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V9_VELUM_STAGE:
            holdout_artifact_id = metadata.get("v9_holdout_artifact_id")
            replay_start = contract["holdout_end"]
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V9_VELUM_REPLAY_WAITING_FOR_DATA",
                        "decision": "CONTINUE_RESEARCH",
                        "status": "WAITING_FOR_REPLAY_WINDOW",
                        "campaign_id": V9_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "execution_authority": False,
                        "next_action": "RETRY_VELUM_REPLAY",
                    },
                    next_stage=V9_VELUM_STAGE,
                    next_metadata=dict(metadata),
                )

            velum_result = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V9_CAMPAIGN_ID,
                epoch_index=epoch_index,
                generation=generation,
                candidate_methodology=V9_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=95000 + epoch_index * 100,
            )
            velum_artifact = await record_stage(
                "CRYPTO_ACTIVITY_SHOCK_V9_VELUM_RESULT",
                {
                    "velum_result": velum_result,
                    "holdout_artifact_id": holdout_artifact_id,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum_result.get("engineering_gate")
            passed = bool(isinstance(gate, Mapping) and gate.get("passed") is True)
            if passed:
                shadow = await self._activate_forward_shadow(
                    problem_id=problem_id,
                    graen_run_id=run_id,
                    campaign_id=V9_CAMPAIGN_ID,
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_methodology=V9_METHODOLOGY_VERSION,
                    candidate_spec=candidate_spec,
                    velum_artifact_id=velum_artifact,
                )
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "FORWARD_SHADOW_RUNNING",
                        "decision": "COLLECT_FORWARD_EVIDENCE",
                        "status": "SHADOW_ACTIVE",
                        "campaign_id": V9_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V9_FAMILY,
                        "holdout_artifact_id": holdout_artifact_id,
                        "velum_artifact_id": velum_artifact,
                        "velum_engineering_gate": dict(gate),
                        "shadow_activation": shadow,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "promotion_authorized": False,
                        "production_state_changed": False,
                        "next_action": "AWAIT_NATIVE_SHADOW_CHECKPOINT",
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": (
                        "V9_CANDIDATE_REJECTED_VELUM"
                        if next_stage
                        else "V9_CAMPAIGN_EXHAUSTED"
                    ),
                    "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                    "status": "VELUM_FAIL",
                    "campaign_id": V9_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V9_FAMILY,
                    "holdout_artifact_id": holdout_artifact_id,
                    "velum_artifact_id": velum_artifact,
                    "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": (
                        "START_NEXT_UNTOUCHED_EPOCH"
                        if next_stage
                        else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                    ),
                },
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        raise RuntimeError(f"unsupported_v9_stage:{stage}")

    async def _execute_trend_pullback_v10(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V10_DEVELOPMENT_STAGE)
        epoch_index = int(metadata.get("v10_epoch_index") or 0)
        generation = 1
        contract = _v10_epoch_contract(epoch_index)
        self.active_methodology_version = V10_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(
            artifact_type: str,
            content: dict[str, Any],
        ) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V10_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        def next_epoch_transition() -> tuple[str | None, dict[str, Any] | None]:
            next_epoch = _v10_next_epoch(epoch_index)
            if next_epoch is None:
                return None, None
            return (
                V10_DEVELOPMENT_STAGE,
                {
                    "v10_campaign_id": V10_CAMPAIGN_ID,
                    "v10_epoch_index": next_epoch,
                    "v10_generation": 1,
                },
            )

        if stage == V10_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.crypto_trend_pullback.prespec.v1",
                "campaign_id": V10_CAMPAIGN_ID,
                "methodology_version": V10_METHODOLOGY_VERSION,
                "family": V10_FAMILY,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_registry": [row.to_dict() for row in v10_candidate_specs()],
                "candidate_count": len(v10_candidate_specs()),
                "selection_rule": (
                    "select one development survivor by expectancy_per_trade * sqrt(trade_count); "
                    "validation and holdout remain unopened until prior gate passes"
                ),
                "stage_order": ["DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY"],
                "development": [
                    contract["development_start"].isoformat(),
                    contract["validation_start"].isoformat(),
                ],
                "validation": [
                    contract["validation_start"].isoformat(),
                    contract["holdout_start"].isoformat(),
                ],
                "holdout": [
                    contract["holdout_start"].isoformat(),
                    contract["holdout_end"].isoformat(),
                ],
                "development_availability_probe_disclosed": list(
                    contract["development_availability_probe_disclosed"]
                ),
                "development_previously_inspected": True,
                "development_prior_use": [
                    "graen-crypto-activity-shock-v9",
                ],
                "validation_previously_inspected": False,
                "holdout_previously_inspected": False,
                "frozen_before_stage_corpus_access": True,
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                    "model_execution_enabled": False,
                },
            }
            prespec_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V10_UNIVERSE,
                start=contract["development_start"],
                end=contract["validation_start"],
                warmup_hours=26,
            )
            development_corpus = verify_v10_stage_corpus(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development = evaluate_v10_development(
                bars,
                start=contract["development_start"],
                end=contract["validation_start"],
            )
            development_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_DEVELOPMENT_RESULT",
                {
                    **development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": development_corpus,
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            if isinstance(selected_spec, Mapping):
                summary = {
                    "state": "V10_CANDIDATE_FROZEN_FOR_VALIDATION",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "DEVELOPMENT_PASS",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": selected_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "prespec_artifact_id": prespec_artifact,
                    "development_artifact_id": development_artifact,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "RUN_FRESH_VALIDATION",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V10_VALIDATION_STAGE,
                    next_metadata={
                        "v10_campaign_id": V10_CAMPAIGN_ID,
                        "v10_epoch_index": epoch_index,
                        "v10_generation": 1,
                        "v10_candidate_spec": dict(selected_spec),
                        "v10_development_artifact_id": development_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V10_EPOCH_REJECTED_DEVELOPMENT"
                    if next_stage
                    else "V10_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "NO_DEVELOPMENT_SURVIVOR",
                "campaign_id": V10_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "prespec_artifact_id": prespec_artifact,
                "development_artifact_id": development_artifact,
                "validation_opened": False,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        candidate_spec = metadata.get("v10_candidate_spec")
        if not isinstance(candidate_spec, Mapping):
            raise RuntimeError("v10_missing_frozen_candidate")

        if stage == V10_VALIDATION_STAGE:
            bars = await self._fetch_stage(
                V10_UNIVERSE,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                warmup_hours=26,
            )
            validation_corpus = verify_v10_stage_corpus(
                bars,
                start=contract["validation_start"],
                end=contract["holdout_start"],
            )
            validation = evaluate_v10_validation(
                bars,
                candidate_spec=candidate_spec,
                start=contract["validation_start"],
                end=contract["holdout_start"],
                seed=103000 + epoch_index * 100,
            )
            validation_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_VALIDATION_RESULT",
                {
                    **validation,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": validation_corpus,
                },
            )
            if validation.get("passed") is True:
                summary = {
                    "state": "V10_CANDIDATE_FROZEN_FOR_HOLDOUT",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "VALIDATION_PASS",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "validation_artifact_id": validation_artifact,
                    "holdout_opened": False,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "OPEN_FRESH_HOLDOUT",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V10_HOLDOUT_STAGE,
                    next_metadata={
                        "v10_campaign_id": V10_CAMPAIGN_ID,
                        "v10_epoch_index": epoch_index,
                        "v10_generation": 1,
                        "v10_candidate_spec": dict(candidate_spec),
                        "v10_validation_artifact_id": validation_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V10_CANDIDATE_REJECTED_VALIDATION"
                    if next_stage
                    else "V10_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "VALIDATION_FAIL",
                "campaign_id": V10_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V10_FAMILY,
                "validation_artifact_id": validation_artifact,
                "holdout_opened": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V10_HOLDOUT_STAGE:
            bars = await self._fetch_stage(
                V10_UNIVERSE,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                warmup_hours=26,
            )
            holdout_corpus = verify_v10_stage_corpus(
                bars,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
            )
            holdout = evaluate_v10_holdout(
                bars,
                candidate_spec=candidate_spec,
                start=contract["holdout_start"],
                end=contract["holdout_end"],
                seed=104000 + epoch_index * 100,
            )
            holdout_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_HOLDOUT_RESULT",
                {
                    **holdout,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                    "corpus_gate": holdout_corpus,
                },
            )
            if holdout.get("passed") is True:
                summary = {
                    "state": "V10_CANDIDATE_READY_FOR_VELUM",
                    "decision": "CONTINUE_RESEARCH",
                    "status": "HOLDOUT_PASS",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "holdout_artifact_id": holdout_artifact,
                    "holdout_opened": True,
                    "holdout_passed": True,
                    "model_invoked": False,
                    "execution_authority": False,
                    "next_action": "VELUM_CANDIDATE_REPLAY",
                }
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary=summary,
                    next_stage=V10_VELUM_STAGE,
                    next_metadata={
                        "v10_campaign_id": V10_CAMPAIGN_ID,
                        "v10_epoch_index": epoch_index,
                        "v10_generation": 1,
                        "v10_candidate_spec": dict(candidate_spec),
                        "v10_holdout_result": holdout,
                        "v10_holdout_artifact_id": holdout_artifact,
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            summary = {
                "state": (
                    "V10_CANDIDATE_REJECTED_HOLDOUT"
                    if next_stage
                    else "V10_CAMPAIGN_EXHAUSTED"
                ),
                "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                "status": "HOLDOUT_FAIL",
                "campaign_id": V10_CAMPAIGN_ID,
                "epoch": contract["name"],
                "epoch_index": epoch_index,
                "candidate_id": candidate_spec.get("candidate_id"),
                "candidate_family": V10_FAMILY,
                "holdout_artifact_id": holdout_artifact,
                "holdout_opened": True,
                "holdout_passed": False,
                "model_invoked": False,
                "execution_authority": False,
                "next_action": (
                    "START_NEXT_UNTOUCHED_EPOCH"
                    if next_stage
                    else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                ),
            }
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary=summary,
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        if stage == V10_VELUM_STAGE:
            holdout_artifact_id = metadata.get("v10_holdout_artifact_id")
            replay_start = contract["holdout_end"]
            replay_end = min(
                replay_start + timedelta(days=30),
                datetime.now(UTC) - timedelta(minutes=10),
            )
            if replay_end <= replay_start:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V10_VELUM_REPLAY_WAITING_FOR_DATA",
                        "decision": "CONTINUE_RESEARCH",
                        "status": "WAITING_FOR_REPLAY_WINDOW",
                        "campaign_id": V10_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "execution_authority": False,
                        "next_action": "RETRY_VELUM_REPLAY",
                    },
                    next_stage=V10_VELUM_STAGE,
                    next_metadata=dict(metadata),
                )

            velum_result = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V10_CAMPAIGN_ID,
                epoch_index=epoch_index,
                generation=generation,
                candidate_methodology=V10_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=105000 + epoch_index * 100,
            )
            velum_artifact = await record_stage(
                "CRYPTO_TREND_PULLBACK_V10_VELUM_RESULT",
                {
                    "velum_result": velum_result,
                    "holdout_artifact_id": holdout_artifact_id,
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                },
            )
            gate = velum_result.get("engineering_gate")
            passed = bool(isinstance(gate, Mapping) and gate.get("passed") is True)
            if passed:
                shadow = await self._activate_forward_shadow(
                    problem_id=problem_id,
                    graen_run_id=run_id,
                    campaign_id=V10_CAMPAIGN_ID,
                    epoch_index=epoch_index,
                    generation=generation,
                    candidate_methodology=V10_METHODOLOGY_VERSION,
                    candidate_spec=candidate_spec,
                    velum_artifact_id=velum_artifact,
                )
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "FORWARD_SHADOW_RUNNING",
                        "decision": "COLLECT_FORWARD_EVIDENCE",
                        "status": "SHADOW_ACTIVE",
                        "campaign_id": V10_CAMPAIGN_ID,
                        "epoch": contract["name"],
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V10_FAMILY,
                        "holdout_artifact_id": holdout_artifact_id,
                        "velum_artifact_id": velum_artifact,
                        "velum_engineering_gate": dict(gate),
                        "shadow_activation": shadow,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "promotion_authorized": False,
                        "production_state_changed": False,
                        "next_action": "AWAIT_NATIVE_SHADOW_CHECKPOINT",
                    },
                )

            next_stage, next_metadata = next_epoch_transition()
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": (
                        "V10_CANDIDATE_REJECTED_VELUM"
                        if next_stage
                        else "V10_CAMPAIGN_EXHAUSTED"
                    ),
                    "decision": "CONTINUE_RESEARCH" if next_stage else "NEEDS_NEW_HYPOTHESIS_ENGINE",
                    "status": "VELUM_FAIL",
                    "campaign_id": V10_CAMPAIGN_ID,
                    "epoch": contract["name"],
                    "epoch_index": epoch_index,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V10_FAMILY,
                    "holdout_artifact_id": holdout_artifact_id,
                    "velum_artifact_id": velum_artifact,
                    "velum_engineering_gate": dict(gate) if isinstance(gate, Mapping) else {},
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_state_changed": False,
                    "next_action": (
                        "START_NEXT_UNTOUCHED_EPOCH"
                        if next_stage
                        else "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
                    ),
                },
                next_stage=next_stage,
                next_metadata=next_metadata,
            )

        raise RuntimeError(f"unsupported_v10_stage:{stage}")

    async def _execute_btc_trend_pullback_v11(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V11_DEVELOPMENT_STAGE)
        self.active_methodology_version = V11_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(artifact_type: str, content: dict[str, Any]) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V11_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V11_CAMPAIGN_ID,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        if stage == V11_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.btc_trend_pullback_forward_v11.prespec.v1",
                "campaign_id": V11_CAMPAIGN_ID,
                "methodology_version": V11_METHODOLOGY_VERSION,
                "family": V11_FAMILY,
                "universe": list(V11_UNIVERSE),
                "candidate_registry": [row.to_dict() for row in v11_candidate_specs()],
                "candidate_count": len(v11_candidate_specs()),
                "historical_evidence_role": "DEVELOPMENT_ONLY",
                "historical_development": [
                    V11_DEVELOPMENT_START.isoformat(),
                    V11_DEVELOPMENT_END.isoformat(),
                ],
                "independent_historical_validation_available": False,
                "independent_historical_holdout_available": False,
                "fresh_confirmation_stage": "NATIVE_FORWARD_SHADOW",
                "forward_shadow_ready_gate": {
                    "min_trades": 30,
                    "min_independent_days": 20,
                    "expectancy_positive": True,
                    "profit_factor_min": 1.0,
                    "dependence_p_max": 0.05,
                },
                "selection_rule": (
                    "freeze one BTC-only candidate only if aggregate stressed-cost development "
                    "is positive and at least 5 of 7 fixed temporal folds independently pass "
                    "trade-count, day-count, profit-factor, expectancy, and delayed-entry gates"
                ),
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                },
            }
            prespec_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V11_UNIVERSE,
                start=V11_DEVELOPMENT_START,
                end=V11_DEVELOPMENT_END,
                warmup_hours=26,
            )
            corpus = verify_v11_development_corpus(
                bars,
                start=V11_DEVELOPMENT_START,
                end=V11_DEVELOPMENT_END,
            )
            development = await asyncio.to_thread(
                evaluate_v11_development,
                bars,
            )
            development_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_DEVELOPMENT_RESULT",
                {
                    "prespec_artifact_id": prespec_artifact_id,
                    "corpus": corpus,
                    "development": development,
                    "bar_counts": {
                        symbol: len(rows) for symbol, rows in bars.items()
                    },
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            selected_id = development.get("selected_candidate_id")
            if not isinstance(selected_spec, Mapping) or not selected_id:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V11_NO_DEVELOPMENT_SURVIVOR",
                        "status": "NO_DEVELOPMENT_SURVIVOR",
                        "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                        "campaign_id": V11_CAMPAIGN_ID,
                        "candidate_family": V11_FAMILY,
                        "development_artifact_id": development_artifact_id,
                        "historical_evidence_role": "DEVELOPMENT_ONLY",
                        "fresh_confirmation_opened": False,
                        "next_action": "DESIGN_NEW_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V11_CANDIDATE_FROZEN_FOR_ENGINEERING_REPLAY",
                    "status": "DEVELOPMENT_PASS",
                    "decision": "CONTINUE_RESEARCH",
                    "campaign_id": V11_CAMPAIGN_ID,
                    "candidate_id": str(selected_id),
                    "candidate_family": V11_FAMILY,
                    "candidate_spec": dict(selected_spec),
                    "development_artifact_id": development_artifact_id,
                    "historical_evidence_role": "DEVELOPMENT_ONLY",
                    "fresh_confirmation_opened": False,
                    "next_action": "RUN_VELUM_ENGINEERING_REPLAY",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
                next_stage=V11_VELUM_STAGE,
                next_metadata={
                    "v11_campaign_id": V11_CAMPAIGN_ID,
                    "v11_generation": 1,
                    "v11_candidate_spec": dict(selected_spec),
                    "v11_development_artifact_id": development_artifact_id,
                },
            )

        if stage == V11_VELUM_STAGE:
            candidate_spec = metadata.get("v11_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v11_velum_candidate_spec_missing")
            replay_start = V11_DEVELOPMENT_END - timedelta(days=30)
            replay = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V11_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V11_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=V11_DEVELOPMENT_END,
                seed=119000,
            )
            velum_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_VELUM_RESULT",
                {
                    "candidate_spec": dict(candidate_spec),
                    "replay": replay,
                    "replay_evidence_role": "POST_DEVELOPMENT_ENGINEERING_ONLY",
                    "independent_confirmatory_evidence": False,
                },
            )
            gate = replay.get("engineering_gate") if isinstance(replay.get("engineering_gate"), Mapping) else {}
            if not bool(gate.get("passed")):
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V11_ENGINEERING_REPLAY_REJECTED",
                        "status": "ENGINEERING_REPLAY_FAIL",
                        "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                        "campaign_id": V11_CAMPAIGN_ID,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V11_FAMILY,
                        "velum_artifact_id": velum_artifact_id,
                        "next_action": "DESIGN_NEW_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V11_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V11_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                velum_artifact_id=velum_artifact_id,
            )
            activation_artifact_id = await record_stage(
                "CRYPTO_BTC_TREND_PULLBACK_V11_FORWARD_SHADOW_ACTIVATION",
                {
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": velum_artifact_id,
                    "activation": activation,
                    "fresh_confirmation": True,
                    "promotion_authorized": False,
                },
            )
            return await self._finalize(
                problem=problem,
                run=run,
                status="SUCCEEDED",
                summary={
                    "state": "V11_FORWARD_SHADOW_ACTIVATED",
                    "status": "FORWARD_SHADOW_ACTIVE",
                    "decision": "COLLECT_FRESH_FORWARD_EVIDENCE",
                    "campaign_id": V11_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V11_FAMILY,
                    "velum_artifact_id": velum_artifact_id,
                    "activation_artifact_id": activation_artifact_id,
                    "next_action": "WAIT_FOR_FORWARD_SHADOW_GATE",
                    "promotion_authorized": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            )

        raise RuntimeError(f"unsupported_v11_research_stage:{stage}")

    async def _execute_btc_mechanisms_v12(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V12_DEVELOPMENT_STAGE)
        self.active_methodology_version = V12_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(artifact_type: str, content: dict[str, Any]) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V12_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V12_CAMPAIGN_ID,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                    "production_state_changed": False,
                },
            )
            return artifact_id(response)

        if stage == V12_DEVELOPMENT_STAGE:
            prespec = {
                "schema_version": "graen.btc_mechanism_tournament_v12.prespec.v1",
                "campaign_id": V12_CAMPAIGN_ID,
                "methodology_version": V12_METHODOLOGY_VERSION,
                "family": V12_FAMILY,
                "universe": list(V12_UNIVERSE),
                "candidate_registry": [row.to_dict() for row in v12_candidate_specs()],
                "candidate_count": len(v12_candidate_specs()),
                "mechanisms": sorted({row.mechanism for row in v12_candidate_specs()}),
                "historical_evidence_role": "DEVELOPMENT_ONLY",
                "historical_development": [
                    V12_DEVELOPMENT_START.isoformat(),
                    V12_DEVELOPMENT_END.isoformat(),
                ],
                "adaptive_research_disclosure": (
                    "historical crypto data through v11 has already informed ANEVUM research; "
                    "v12 historical results are hypothesis-generation evidence only"
                ),
                "independent_historical_validation_available": False,
                "independent_historical_holdout_available": False,
                "fresh_confirmation_stage": "NATIVE_FORWARD_SHADOW",
                "forward_shadow_ready_gate": {
                    "min_trades": 30,
                    "min_independent_days": 20,
                    "expectancy_positive": True,
                    "profit_factor_min": 1.0,
                    "dependence_p_max": 0.05,
                },
                "selection_rule": (
                    "freeze at most one candidate across three orthogonal mechanisms only if "
                    "aggregate stressed-cost expectancy and delayed-entry expectancy are positive, "
                    "profit factor exceeds one, and at least 5 of 7 fixed temporal folds pass"
                ),
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                },
            }
            prespec_artifact_id = await record_stage(
                "CRYPTO_BTC_MECHANISMS_V12_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V12_UNIVERSE,
                start=V12_DEVELOPMENT_START,
                end=V12_DEVELOPMENT_END,
                warmup_hours=26,
            )
            corpus = verify_v12_development_corpus(
                bars,
                start=V12_DEVELOPMENT_START,
                end=V12_DEVELOPMENT_END,
            )
            development = await asyncio.to_thread(
                evaluate_v12_development,
                bars,
            )
            development_artifact_id = await record_stage(
                "CRYPTO_BTC_MECHANISMS_V12_DEVELOPMENT_RESULT",
                {
                    "prespec_artifact_id": prespec_artifact_id,
                    "corpus": corpus,
                    "development": development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            selected_id = development.get("selected_candidate_id")

            diagnostics: list[dict[str, Any]] = []
            for candidate_id, row in (development.get("results") or {}).items():
                if not isinstance(row, Mapping):
                    continue
                aggregate = row.get("aggregate") if isinstance(row.get("aggregate"), Mapping) else {}
                primary = aggregate.get("primary") if isinstance(aggregate.get("primary"), Mapping) else {}
                candidate = aggregate.get("candidate") if isinstance(aggregate.get("candidate"), Mapping) else {}
                diagnostics.append({
                    "candidate_id": candidate_id,
                    "mechanism": candidate.get("mechanism"),
                    "selection_score": row.get("selection_score"),
                    "positive_temporal_folds": row.get("positive_temporal_folds"),
                    "reasons": list(row.get("reasons") or []),
                    "trade_count": primary.get("trade_count"),
                    "independent_day_blocks": primary.get("independent_day_blocks"),
                    "expectancy_per_trade": primary.get("expectancy_per_trade"),
                    "profit_factor": primary.get("profit_factor"),
                })
            diagnostics.sort(
                key=lambda row: float(row.get("selection_score") or -1e9),
                reverse=True,
            )
            top_candidates = diagnostics[:3]
            print("GRAEN_V12_DIAGNOSTICS", top_candidates, flush=True)

            if not isinstance(selected_spec, Mapping) or not selected_id:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V12_NO_DEVELOPMENT_SURVIVOR",
                        "status": "NO_DEVELOPMENT_SURVIVOR",
                        "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                        "campaign_id": V12_CAMPAIGN_ID,
                        "candidate_family": V12_FAMILY,
                        "development_artifact_id": development_artifact_id,
                        "top_candidates": top_candidates,
                        "historical_evidence_role": "DEVELOPMENT_ONLY",
                        "fresh_confirmation_opened": False,
                        "next_action": "DESIGN_NEW_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V12_CANDIDATE_FROZEN_FOR_ENGINEERING_REPLAY",
                    "status": "DEVELOPMENT_PASS",
                    "decision": "CONTINUE_RESEARCH",
                    "campaign_id": V12_CAMPAIGN_ID,
                    "candidate_id": str(selected_id),
                    "candidate_family": V12_FAMILY,
                    "candidate_spec": dict(selected_spec),
                    "development_artifact_id": development_artifact_id,
                    "top_candidates": top_candidates,
                    "historical_evidence_role": "DEVELOPMENT_ONLY",
                    "fresh_confirmation_opened": False,
                    "next_action": "RUN_VELUM_ENGINEERING_REPLAY",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
                next_stage=V12_VELUM_STAGE,
                next_metadata={
                    "v12_campaign_id": V12_CAMPAIGN_ID,
                    "v12_generation": 1,
                    "v12_candidate_spec": dict(selected_spec),
                    "v12_development_artifact_id": development_artifact_id,
                },
            )

        if stage == V12_VELUM_STAGE:
            candidate_spec = metadata.get("v12_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v12_velum_candidate_spec_missing")
            replay_start = V12_DEVELOPMENT_END - timedelta(days=30)
            replay = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V12_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V12_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=V12_DEVELOPMENT_END,
                seed=129000,
            )
            velum_artifact_id = await record_stage(
                "CRYPTO_BTC_MECHANISMS_V12_VELUM_RESULT",
                {
                    "candidate_spec": dict(candidate_spec),
                    "replay": replay,
                    "replay_evidence_role": "POST_DEVELOPMENT_ENGINEERING_ONLY",
                    "independent_confirmatory_evidence": False,
                },
            )
            gate = replay.get("engineering_gate") if isinstance(replay.get("engineering_gate"), Mapping) else {}
            if not bool(gate.get("passed")):
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V12_ENGINEERING_REPLAY_REJECTED",
                        "status": "ENGINEERING_REPLAY_FAIL",
                        "decision": "NEEDS_NEW_HYPOTHESIS_ENGINE",
                        "campaign_id": V12_CAMPAIGN_ID,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V12_FAMILY,
                        "velum_artifact_id": velum_artifact_id,
                        "next_action": "DESIGN_NEW_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V12_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V12_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                velum_artifact_id=velum_artifact_id,
            )
            activation_artifact_id = await record_stage(
                "CRYPTO_BTC_MECHANISMS_V12_FORWARD_SHADOW_ACTIVATION",
                {
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": velum_artifact_id,
                    "activation": activation,
                    "fresh_confirmation": True,
                    "promotion_authorized": False,
                },
            )
            return await self._finalize(
                problem=problem,
                run=run,
                status="SUCCEEDED",
                summary={
                    "state": "V12_FORWARD_SHADOW_ACTIVATED",
                    "status": "FORWARD_SHADOW_ACTIVE",
                    "decision": "COLLECT_FRESH_FORWARD_EVIDENCE",
                    "campaign_id": V12_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V12_FAMILY,
                    "velum_artifact_id": velum_artifact_id,
                    "activation_artifact_id": activation_artifact_id,
                    "next_action": "WAIT_FOR_FORWARD_SHADOW_GATE",
                    "promotion_authorized": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            )

        raise RuntimeError(f"unsupported_v12_research_stage:{stage}")

    async def _execute_btc_hypotheses_v13(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V13_DEVELOPMENT_STAGE)
        self.active_methodology_version = V13_METHODOLOGY_VERSION

        def artifact_id(response: Mapping[str, Any]) -> str | None:
            artifact = response.get("artifact")
            return (
                str(artifact.get("artifact_id"))
                if isinstance(artifact, Mapping) and artifact.get("artifact_id")
                else None
            )

        async def record_stage(artifact_type: str, content: dict[str, Any]) -> str | None:
            response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type=artifact_type,
                methodology_version=V13_METHODOLOGY_VERSION,
                content={
                    **content,
                    "campaign_id": V13_CAMPAIGN_ID,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                    "production_state_changed": False,
                    "crypto_execution_enabled": False,
                },
            )
            return artifact_id(response)

        if stage == V13_DEVELOPMENT_STAGE:
            failure_map = [
                {
                    "campaign": "V6",
                    "mechanisms": ["cross_sectional_residual_downshock_reclaim"],
                    "result": "DEVELOPMENT_ONLY_NOT_CONFIRMED",
                    "lesson": "residual-reclaim evidence did not establish a promotable edge",
                },
                {
                    "campaign": "V7",
                    "mechanisms": ["cross_asset_lead_lag_response", "broad_market_laggard_response"],
                    "result": "NO_PROMOTION",
                    "lesson": "market-transmission families did not survive the protected pipeline",
                },
                {
                    "campaign": "V8",
                    "mechanisms": [
                        "adaptive_cross_asset_lead_lag",
                        "adaptive_broad_market_laggard_response",
                        "adaptive_calendar_regime_drift",
                    ],
                    "result": "HYPOTHESIS_ENGINE_EXHAUSTED",
                    "lesson": "adaptive threshold cycling did not justify further tuning of those families",
                },
                {
                    "campaign": "V9",
                    "mechanisms": ["activity_confirmed_momentum_continuation"],
                    "result": "FALSIFIED",
                    "lesson": "activity-confirmed impulse continuation did not survive",
                },
                {
                    "campaign": "V10",
                    "mechanisms": ["activity_confirmed_trend_pullback_recovery"],
                    "result": "MIXED_DEVELOPMENT_CORPUS_EXHAUSTED",
                    "lesson": (
                        "one epoch rejected development; one development candidate froze, but "
                        "validation was never run because SOL stage corpus was incomplete"
                    ),
                },
                {
                    "campaign": "V11",
                    "mechanisms": ["btc_activity_confirmed_trend_pullback_recovery"],
                    "result": "NO_DEVELOPMENT_SURVIVOR",
                    "lesson": "BTC-only trend/pullback did not survive stressed-cost development",
                },
                {
                    "campaign": "V12",
                    "mechanisms": ["compression_breakout", "vol_normalized_trend", "downshock_reclaim"],
                    "result": "NO_DEVELOPMENT_SURVIVOR",
                    "lesson": (
                        "large samples still showed nonpositive stressed-cost/delayed expectancy, "
                        "profit factor at or below one, and inadequate temporal-fold consistency"
                    ),
                },
            ]
            prespec = {
                "schema_version": "graen.btc_hypothesis_tournament_v13.prespec.v1",
                "campaign_id": V13_CAMPAIGN_ID,
                "methodology_version": V13_METHODOLOGY_VERSION,
                "family": V13_FAMILY,
                "universe": list(V13_UNIVERSE),
                "failure_map": failure_map,
                "hypothesis_registry": list(v13_hypothesis_registry()),
                "candidate_registry": [row.to_dict() for row in v13_candidate_specs()],
                "candidate_count": len(v13_candidate_specs()),
                "mechanisms": sorted({row.mechanism for row in v13_candidate_specs()}),
                "historical_evidence_role": "DEVELOPMENT_ONLY",
                "historical_development": [
                    V13_DEVELOPMENT_START.isoformat(),
                    V13_DEVELOPMENT_END.isoformat(),
                ],
                "stage_order": [
                    V13_DEVELOPMENT_STAGE,
                    V13_VELUM_STAGE,
                    V13_VALIDATION_STAGE,
                    V13_HOLDOUT_STAGE,
                ],
                "independent_historical_validation_available": False,
                "independent_historical_holdout_available": False,
                "validation_evidence": "FRESH_FORWARD_ONLY",
                "holdout_evidence": "FRESH_FORWARD_AFTER_VALIDATION_ONLY",
                "selection_rule": (
                    "freeze at most one candidate only if high-cost expectancy and delayed-entry "
                    "expectancy are positive, profit factor exceeds one, at least 5 of 7 fixed "
                    "temporal folds pass, minimum trade/day-block gates pass, and the candidate "
                    "survives Benjamini-Yekutieli multiplicity control"
                ),
                "authority": {
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "risk_or_sizing_authority": False,
                    "production_promotion_authority": False,
                    "crypto_execution_enabled": False,
                },
            }
            prespec_artifact_id = await record_stage(
                "CRYPTO_BTC_HYPOTHESES_V13_PRESPEC",
                prespec,
            )
            bars = await self._fetch_stage(
                V13_UNIVERSE,
                start=V13_DEVELOPMENT_START,
                end=V13_DEVELOPMENT_END,
                warmup_hours=26,
            )
            corpus = verify_v13_development_corpus(
                bars,
                start=V13_DEVELOPMENT_START,
                end=V13_DEVELOPMENT_END,
            )
            development = await asyncio.to_thread(
                evaluate_v13_development,
                bars,
            )
            development_artifact_id = await record_stage(
                "CRYPTO_BTC_HYPOTHESES_V13_DEVELOPMENT_RESULT",
                {
                    "prespec_artifact_id": prespec_artifact_id,
                    "corpus": corpus,
                    "development": development,
                    "bar_counts": {symbol: len(rows) for symbol, rows in bars.items()},
                },
            )
            selected_spec = development.get("selected_candidate_spec")
            selected_id = development.get("selected_candidate_id")

            diagnostics: list[dict[str, Any]] = []
            for candidate_id, row in (development.get("results") or {}).items():
                if not isinstance(row, Mapping):
                    continue
                high = row.get("aggregate_high") if isinstance(row.get("aggregate_high"), Mapping) else {}
                base = row.get("aggregate_base") if isinstance(row.get("aggregate_base"), Mapping) else {}
                primary = high.get("primary") if isinstance(high.get("primary"), Mapping) else {}
                delayed = high.get("one_bar_delay") if isinstance(high.get("one_bar_delay"), Mapping) else {}
                base_primary = base.get("primary") if isinstance(base.get("primary"), Mapping) else {}
                candidate = high.get("candidate") if isinstance(high.get("candidate"), Mapping) else {}
                diagnostics.append({
                    "candidate_id": candidate_id,
                    "mechanism": candidate.get("mechanism"),
                    "parameters": dict(candidate),
                    "trade_count": primary.get("trade_count"),
                    "independent_day_blocks": primary.get("independent_day_blocks"),
                    "expectancy_per_trade": primary.get("expectancy_per_trade"),
                    "profit_factor": primary.get("profit_factor"),
                    "max_drawdown": primary.get("max_drawdown"),
                    "win_loss_distribution": primary.get("win_loss_distribution"),
                    "mfe_mae": primary.get("mfe_mae"),
                    "positive_temporal_folds": row.get("positive_temporal_folds"),
                    "delayed_entry_expectancy": delayed.get("expectancy_per_trade"),
                    "base_cost_expectancy": base_primary.get("expectancy_per_trade"),
                    "high_cost_expectancy": primary.get("expectancy_per_trade"),
                    "dependence_adjusted_p_value": row.get("dependence_adjusted_p_value"),
                    "statistical_survival": row.get("statistical_survival"),
                    "economic_survival": row.get("economic_survival"),
                    "selection_score": row.get("selection_score"),
                    "rejection_reasons": list(row.get("reasons") or []),
                })
            diagnostics.sort(key=lambda row: str(row.get("candidate_id") or ""))
            print("GRAEN_V13_DIAGNOSTICS", diagnostics, flush=True)

            if not isinstance(selected_spec, Mapping) or not selected_id:
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V13_NO_DEVELOPMENT_SURVIVOR",
                        "status": "NO_DEVELOPMENT_SURVIVOR",
                        "decision": "V13_HYPOTHESES_FALSIFIED",
                        "campaign_id": V13_CAMPAIGN_ID,
                        "candidate_family": V13_FAMILY,
                        "development_artifact_id": development_artifact_id,
                        "candidate_diagnostics": diagnostics,
                        "failure_map": failure_map,
                        "validation_opened": False,
                        "holdout_opened": False,
                        "next_action": "DESIGN_NEXT_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )

            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V13_CANDIDATE_FROZEN_FOR_VELUM",
                    "status": "DEVELOPMENT_PASS",
                    "decision": "CONTINUE_RESEARCH",
                    "campaign_id": V13_CAMPAIGN_ID,
                    "candidate_id": str(selected_id),
                    "candidate_family": V13_FAMILY,
                    "candidate_spec": dict(selected_spec),
                    "development_artifact_id": development_artifact_id,
                    "candidate_diagnostics": diagnostics,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "next_action": "RUN_VELUM_ENGINEERING_REPLAY",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
                next_stage=V13_VELUM_STAGE,
                next_metadata={
                    "v13_campaign_id": V13_CAMPAIGN_ID,
                    "v13_candidate_spec": dict(selected_spec),
                    "v13_development_artifact_id": development_artifact_id,
                    "v13_velum_passed": False,
                    "v13_validation_passed": False,
                    "v13_holdout_passed": False,
                },
            )

        if stage == V13_VELUM_STAGE:
            candidate_spec = metadata.get("v13_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v13_velum_candidate_spec_missing")
            replay_start = V13_DEVELOPMENT_END - timedelta(days=60)
            replay = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V13_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V13_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=V13_DEVELOPMENT_END,
                seed=139000,
            )
            velum_artifact_id = await record_stage(
                "CRYPTO_BTC_HYPOTHESES_V13_VELUM_RESULT",
                {
                    "candidate_spec": dict(candidate_spec),
                    "replay": replay,
                    "replay_evidence_role": "POST_DEVELOPMENT_ENGINEERING_ONLY",
                    "independent_confirmatory_evidence": False,
                },
            )
            gate = replay.get("engineering_gate") if isinstance(replay.get("engineering_gate"), Mapping) else {}
            if not bool(gate.get("passed")):
                return await self._finalize(
                    problem=problem,
                    run=run,
                    status="WAITING",
                    summary={
                        "state": "V13_VELUM_REJECTED",
                        "status": "ENGINEERING_REPLAY_FAIL",
                        "decision": "V13_CANDIDATE_REJECTED",
                        "campaign_id": V13_CAMPAIGN_ID,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "candidate_family": V13_FAMILY,
                        "velum_artifact_id": velum_artifact_id,
                        "validation_opened": False,
                        "holdout_opened": False,
                        "next_action": "DESIGN_NEXT_BTC_HYPOTHESIS",
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    },
                )
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V13_VELUM_PASS",
                    "status": "ENGINEERING_REPLAY_PASS",
                    "decision": "CONTINUE_RESEARCH",
                    "campaign_id": V13_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V13_FAMILY,
                    "velum_artifact_id": velum_artifact_id,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "next_action": "OPEN_FRESH_FORWARD_VALIDATION",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
                next_stage=V13_VALIDATION_STAGE,
                next_metadata={
                    "v13_campaign_id": V13_CAMPAIGN_ID,
                    "v13_candidate_spec": dict(candidate_spec),
                    "v13_development_artifact_id": metadata.get("v13_development_artifact_id"),
                    "v13_velum_artifact_id": velum_artifact_id,
                    "v13_velum_passed": True,
                    "v13_validation_passed": False,
                    "v13_holdout_passed": False,
                },
            )

        if stage == V13_VALIDATION_STAGE:
            candidate_spec = metadata.get("v13_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v13_validation_candidate_spec_missing")
            if metadata.get("v13_velum_passed") is not True:
                raise RuntimeError("v13_validation_opened_before_velum_pass")
            velum_artifact_id = str(metadata.get("v13_velum_artifact_id") or "")
            if not velum_artifact_id:
                raise RuntimeError("v13_validation_velum_artifact_missing")
            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V13_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V13_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                velum_artifact_id=velum_artifact_id,
                evidence_phase="VALIDATION",
            )
            activation_artifact_id = await record_stage(
                "CRYPTO_BTC_V13_VALIDATION_ACTIVATION",
                {
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": velum_artifact_id,
                    "activation": activation,
                    "fresh_confirmation": True,
                    "validation_opened": True,
                    "holdout_opened": False,
                    "promotion_authorized": False,
                },
            )
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V13_VALIDATION_ACTIVE",
                    "status": "FORWARD_VALIDATION_ACTIVE",
                    "decision": "COLLECT_FRESH_VALIDATION_EVIDENCE",
                    "campaign_id": V13_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V13_FAMILY,
                    "velum_artifact_id": velum_artifact_id,
                    "activation_artifact_id": activation_artifact_id,
                    "validation_opened": True,
                    "holdout_opened": False,
                    "next_action": "WAIT_FOR_VALIDATION_GATE",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            )

        if stage == V13_HOLDOUT_STAGE:
            candidate_spec = metadata.get("v13_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v13_holdout_candidate_spec_missing")
            if (
                metadata.get("v13_velum_passed") is not True
                or metadata.get("v13_validation_passed") is not True
            ):
                raise RuntimeError("v13_holdout_opened_before_predecessor_gates")
            velum_artifact_id = str(metadata.get("v13_velum_artifact_id") or "")
            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V13_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V13_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                velum_artifact_id=velum_artifact_id,
                evidence_phase="HOLDOUT",
            )
            activation_artifact_id = await record_stage(
                "CRYPTO_BTC_V13_HOLDOUT_ACTIVATION",
                {
                    "candidate_spec": dict(candidate_spec),
                    "validation_artifact_id": metadata.get("v13_validation_artifact_id"),
                    "velum_artifact_id": velum_artifact_id,
                    "activation": activation,
                    "validation_passed": True,
                    "holdout_opened": True,
                    "promotion_authorized": False,
                },
            )
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V13_HOLDOUT_ACTIVE",
                    "status": "FORWARD_HOLDOUT_ACTIVE",
                    "decision": "COLLECT_FRESH_HOLDOUT_EVIDENCE",
                    "campaign_id": V13_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V13_FAMILY,
                    "activation_artifact_id": activation_artifact_id,
                    "validation_opened": True,
                    "holdout_opened": True,
                    "next_action": "WAIT_FOR_HOLDOUT_GATE",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                },
            )

        raise RuntimeError(f"unsupported_v13_research_stage:{stage}")

    async def _activate_forward_shadow(
        self,
        *,
        problem_id: str,
        graen_run_id: str,
        campaign_id: str,
        epoch_index: int,
        generation: int,
        candidate_methodology: str,
        candidate_spec: Mapping[str, Any],
        velum_artifact_id: str | None,
        evidence_phase: str = "FORWARD_SHADOW",
    ) -> dict[str, Any]:
        if not self.shadow_configured:
            raise RuntimeError("forward shadow service is not configured")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.shadow_base_url}/v1/candidate-shadow/activate",
                headers={"x-graen-shadow-token": self.shadow_token},
                json={
                    "problem_id": problem_id,
                    "graen_run_id": graen_run_id,
                    "campaign_id": campaign_id,
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_methodology": candidate_methodology,
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": str(velum_artifact_id or ""),
                    "evidence_phase": evidence_phase,
                },
            )
            response.raise_for_status()
            payload = response.json()
        activation = payload.get("activation")
        if not isinstance(activation, Mapping):
            raise RuntimeError("forward shadow activation returned no activation")
        return {
            "activation": dict(activation),
            "duplicate": bool(payload.get("duplicate")),
            "execution_authority": False,
            "broker_orders_possible": False,
            "promotion_authorized": False,
        }



    async def _activate_paper_canary(
        self,
        *,
        problem_id: str,
        graen_run_id: str,
        campaign_id: str,
        candidate_methodology: str,
        candidate_spec: Mapping[str, Any],
        shadow_activation_id: str,
        shadow_checkpoint: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self.paper_configured:
            raise RuntimeError("paper canary service is not configured")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.paper_base_url}/v1/graen-paper/activate",
                headers={"x-graen-paper-token": self.paper_token},
                json={
                    "problem_id": problem_id,
                    "graen_run_id": graen_run_id,
                    "campaign_id": campaign_id,
                    "candidate_methodology": candidate_methodology,
                    "candidate_spec": dict(candidate_spec),
                    "shadow_activation_id": shadow_activation_id,
                    "shadow_checkpoint": dict(shadow_checkpoint),
                },
            )
            if response.status_code == 422:
                try:
                    detail = str((response.json() or {}).get("detail") or "")
                except Exception:
                    detail = response.text
                normalized = detail.casefold()
                if "already owns another candidate" in normalized:
                    return {
                        "busy": True,
                        "reason": "paper_canary_slot_busy",
                        "detail": detail[:1000],
                        "execution_authority": "PAPER_ONLY",
                        "live_execution_authority": False,
                    }
                if "paper adapter is not authorized" in normalized:
                    return {
                        "not_authorized": True,
                        "reason": "paper_canary_configuration_required",
                        "detail": detail[:1000],
                        "execution_authority": False,
                        "live_execution_authority": False,
                    }
            response.raise_for_status()
            payload = response.json()
        activation = payload.get("activation")
        if not isinstance(activation, Mapping):
            raise RuntimeError("paper canary activation returned no activation")
        return {
            "activation": dict(activation),
            "duplicate": bool(payload.get("duplicate")),
            "checkpoint": (
                dict(payload.get("checkpoint"))
                if isinstance(payload.get("checkpoint"), Mapping)
                else {}
            ),
            "execution_authority": "PAPER_ONLY",
            "live_execution_authority": False,
        }

    async def _paper_canary_status(
        self,
        activation_id: str,
    ) -> dict[str, Any]:
        if not self.paper_configured:
            raise RuntimeError("paper canary service is not configured")
        activation_id = str(activation_id or "").strip()
        if not activation_id:
            raise RuntimeError("paper canary activation id is required")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.paper_base_url}/v1/graen-paper/checkpoint/{activation_id}",
                headers={"x-graen-paper-token": self.paper_token},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, Mapping):
            raise RuntimeError("paper canary checkpoint is malformed")
        return dict(payload)

    async def _forward_shadow_status(
        self,
        activation_id: str,
    ) -> dict[str, Any]:
        if not self.shadow_configured:
            raise RuntimeError("forward shadow service is not configured")
        activation_id = str(activation_id or "").strip()
        if not activation_id:
            raise RuntimeError("forward shadow activation id is required")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.shadow_base_url}/v1/candidate-shadow/checkpoint/{activation_id}",
                headers={"x-graen-shadow-token": self.shadow_token},
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, Mapping):
            raise RuntimeError("forward shadow checkpoint is malformed")
        return dict(payload)

    async def _velum_health_snapshot(self) -> dict[str, Any] | None:
        if not self.velum_configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                response = await client.get(f"{self.velum_base_url}/health")
                response.raise_for_status()
                payload = response.json()
            if (
                isinstance(payload, Mapping)
                and payload.get("system") == "VELUM"
                and payload.get("mode") == "research_replay_only"
            ):
                return dict(payload)
        except Exception:
            pass
        return None

    async def _velum_health_ready(self) -> bool:
        return await self._velum_health_snapshot() is not None


    async def _replay_in_velum(
        self,
        *,
        problem_id: str,
        graen_run_id: str,
        campaign_id: str,
        epoch_index: int,
        generation: int,
        candidate_methodology: str,
        candidate_spec: Mapping[str, Any],
        replay_start: datetime,
        replay_end: datetime,
        seed: int,
    ) -> dict[str, Any]:
        if not self.velum_configured:
            raise RuntimeError("VELUM candidate replay is not configured")
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{self.velum_base_url}/v1/graen/candidate-replay",
                headers={"x-graen-velum-token": self.velum_token},
                json={
                    "problem_id": problem_id,
                    "graen_run_id": graen_run_id,
                    "campaign_id": campaign_id,
                    "epoch_index": epoch_index,
                    "generation": generation,
                    "candidate_methodology": candidate_methodology,
                    "candidate_spec": dict(candidate_spec),
                    "replay_start": replay_start.isoformat(),
                    "replay_end": replay_end.isoformat(),
                    "seed": seed,
                },
            )
            response.raise_for_status()
            payload = response.json()
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("VELUM candidate replay returned no result")
        return dict(result)

    async def _callback_iren(
        self,
        linked_job_id: str | None,
        *,
        status: str,
        result: dict[str, Any],
    ) -> bool:
        if not linked_job_id or not self.callback_configured:
            return False
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.callback_base_url}/v1/iren/jobs/{linked_job_id}/callback",
                    headers={"x-anevum-scheduler-token": self.callback_token},
                    json={"status": status, "result": result, "error": {}},
                )
                response.raise_for_status()
            return True
        except Exception as exc:
            print(
                "GRAEN_RESEARCH_IREN_CALLBACK_ERROR",
                {"error": type(exc).__name__, "job_id": linked_job_id},
                flush=True,
            )
            return False


    async def _execute_compiled_hypothesis(self, problem, run):
        from importlib import import_module
        from graen.engineering import IntegrityError, digest, stage_window, validate_spec
        metadata = problem.get("metadata") or {}
        promotion = metadata.get("code_promotion") or {}
        spec = promotion.get("prespec") or {}
        spec_hash = validate_spec(spec)
        if (
            promotion.get("phase") != "COMPLETE"
            or promotion.get("spec_hash") != spec_hash
            or metadata.get("compiled_specification_hash") != spec_hash
        ):
            raise IntegrityError("verified_engineering_handoff_required")
        module_name = spec["hypothesis_id"].lower().replace("-", "_")
        implementation = import_module("graen.crypto.generated." + module_name)
        if implementation.SPEC_HASH != spec_hash or digest(implementation.SPEC) != spec_hash:
            raise IntegrityError("deployed_candidate_does_not_match_frozen_prespec")
        stage = str(metadata.get("research_stage", "")).removeprefix("CRYPTO_COMPILED_").lower()
        if stage not in {"development", "validation", "holdout"}:
            raise IntegrityError("compiled_stage_not_supported")
        prior = await self.gateway._request("POST", {
            "action": "compiled_stage_evidence",
            "problem_id": str(problem["problem_id"]), "spec_hash": spec_hash,
            "stage": stage, "epoch": spec["epoch"],
        })
        if prior.get("current"):
            # A completed stage is never re-evaluated after a failed continuation.
            result = prior["current"]
        else:
            predecessor = prior.get("predecessor")
            start, end = stage_window(spec, stage, predecessor)
            await self.gateway.record_artifact(
                problem_id=str(problem["problem_id"]), run_id=str(run["run_id"]),
                artifact_type="COMPILED_STAGE_OPENED", methodology_version="graen-crypto-flow-pressure-v1",
                content={"spec_hash": spec_hash, "stage": stage, "epoch": spec["epoch"],
                         "fetch_start": start.isoformat(), "fetch_end_exclusive": end.isoformat()},
            )
            # No future padding. Warmup is restricted to this stage: early
            # signals abstain until the entire lookback has accumulated.
            bars = {symbol: [] for symbol in spec["universe"]}
            current = start
            while current < end:
                limit = min(current + timedelta(days=20), end)
                chunk = await self.market_data.historical_crypto_bars_many(
                    spec["universe"], start=current, end=limit - timedelta(microseconds=1),
                )
                for symbol in spec["universe"]:
                    for row in chunk.get(symbol, []):
                        stamp = datetime.fromisoformat(str(row["t"]).replace("Z", "+00:00"))
                        if current <= stamp < limit:
                            bars[symbol].append(row)
                current = limit
            result = implementation.evaluate(bars, stage=stage, predecessor=predecessor)
            await self.gateway.record_artifact(
                problem_id=str(problem["problem_id"]), run_id=str(run["run_id"]),
                artifact_type="COMPILED_STAGE_RESULT", methodology_version="graen-crypto-flow-pressure-v1",
                content=result,
            )
        # Terminal stages must leave the claimable research stage. Otherwise
        # WAITING would repeatedly claim the same exhausted candidate.
        next_stage = (
            "CANDIDATE_READY_FOR_VELUM" if result.get("passed") is True and stage == "holdout"
            else "RESEARCH_IMPLEMENTATION_REQUIRED"
        )
        if result.get("passed") is True and stage != "holdout":
            next_stage = "CRYPTO_COMPILED_" + {"development": "VALIDATION", "validation": "HOLDOUT"}[stage]
        summary = {
            "state": "CANDIDATE_READY_FOR_VELUM" if result.get("passed") is True and stage == "holdout"
                else "COMPILED_STAGE_PASSED" if result.get("passed") is True else "COMPILED_CANDIDATE_REJECTED",
            "decision": "CONTINUE_RESEARCH" if result.get("passed") is True else "NEEDS_NEW_HYPOTHESIS_ENGINE",
            "next_action": "VELUM_CANDIDATE_REPLAY" if stage == "holdout" and result.get("passed") is True
                else "RUN_NEXT_FROZEN_STAGE" if result.get("passed") is True else "MODEL_HYPOTHESIS_GENERATION_REQUIRED",
            "candidate_id": spec["hypothesis_id"], "epoch": spec["epoch"], "spec_hash": spec_hash,
            "research_only": True, "execution_authority": False, "broker_orders_possible": False,
        }
        return await self._finalize(problem=problem, run=run, status="WAITING", summary=summary,
            next_stage=next_stage, next_metadata={"compiled_specification_hash": spec_hash} if next_stage else None)

    async def _reconcile_orphaned_confirmatory_claim(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Burn stale sealed-stage claims instead of reusing opened evidence."""
        problems = {
            str(row.get("problem_id")): row
            for row in (snapshot.get("problems") or [])
            if isinstance(row, Mapping) and row.get("problem_id")
        }
        runtime_state = snapshot.get("runtime_state")
        runtime_metadata = (
            runtime_state.get("metadata")
            if isinstance(runtime_state, Mapping)
            and isinstance(runtime_state.get("metadata"), Mapping)
            else {}
        )
        executor_state = (
            runtime_metadata.get("research_executor")
            if isinstance(runtime_metadata.get("research_executor"), Mapping)
            else {}
        )
        active_problem_id = str(executor_state.get("active_problem_id") or "")
        minimum_age = timedelta(seconds=max(300, self.interval_seconds * 4))
        now = datetime.now(UTC)

        for run in (snapshot.get("runs") or []):
            if not isinstance(run, Mapping) or run.get("status") != "RUNNING":
                continue
            problem_id = str(run.get("problem_id") or "")
            run_id = str(run.get("run_id") or "")
            problem = problems.get(problem_id)
            if not problem or problem.get("status") != "RUNNING" or active_problem_id == problem_id:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if "VALIDATION" not in stage and "HOLDOUT" not in stage:
                continue
            try:
                started_at = datetime.fromisoformat(str(run.get("started_at")).replace("Z", "+00:00"))
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                started_at = started_at.astimezone(UTC)
            except (TypeError, ValueError):
                continue
            if now - started_at < minimum_age:
                continue

            error = "sealed_confirmatory_stage_orphaned:" + stage
            await self.gateway.block_research_claim(
                problem_id=problem_id,
                run_id=run_id,
                worker_id=self.worker_id,
                error=error,
            )
            await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage="RESEARCH_IMPLEMENTATION_REQUIRED",
                metadata={
                    "invalidated_run_id": run_id,
                    "invalidated_research_stage": stage,
                    "research_integrity_incident": "SEALED_STAGE_EXECUTION_ORPHANED",
                    "sealed_stage_invalidated": True,
                    "next_action": "MODEL_HYPOTHESIS_GENERATION_REQUIRED",
                },
            )
            return {
                "problem_id": problem_id,
                "run_id": run_id,
                "invalidated_stage": stage,
                "next_stage": "RESEARCH_IMPLEMENTATION_REQUIRED",
            }
        return None

    async def _recover_exhausted_v9_into_v10(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Repair the one-time v9 -> v10 handoff without opening execution authority."""
        problems = snapshot.get("problems") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v10_campaign_id") == V10_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V10_STAGE_KEYS:
                return None

        latest_by_problem: dict[str, Mapping[str, Any]] = {}
        for run in snapshot.get("runs") or []:
            if not isinstance(run, Mapping):
                continue
            problem_id = str(run.get("problem_id") or "")
            if problem_id and problem_id not in latest_by_problem:
                latest_by_problem[problem_id] = run

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if problem.get("status") != "WAITING" or problem.get("domain") != PROBLEM_DOMAIN:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            run = latest_by_problem.get(problem_id)
            if not isinstance(run, Mapping):
                continue
            summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
            if not (
                summary.get("campaign_id") == V9_CAMPAIGN_ID
                and summary.get("state") == "V9_CAMPAIGN_EXHAUSTED"
                and summary.get("decision") == "NEEDS_NEW_HYPOTHESIS_ENGINE"
                and summary.get("next_action") == "MODEL_HYPOTHESIS_GENERATION_REQUIRED"
            ):
                continue
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V10_DEVELOPMENT_STAGE,
                metadata={
                    "v10_campaign_id": V10_CAMPAIGN_ID,
                    "v10_epoch_index": 0,
                    "v10_generation": 1,
                    "v10_transition_source": "v9_campaign_exhausted",
                    "v9_terminal_run_id": str(run.get("run_id") or "") or None,
                },
            )
            if queued.get("problem"):
                return {
                    "recovered": True,
                    "problem_id": problem_id,
                    "next_research_stage": V10_DEVELOPMENT_STAGE,
                    "source": "V9_CAMPAIGN_EXHAUSTED",
                }
        return None

    async def _ensure_autonomous_loop_seed(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Seed exactly one canonical self-driving research loop.

        The bootstrap creates research work only. It has no broker, source-code,
        credential, spending, deployment, or live-risk authority.
        """
        if not self.autonomous_loop_bootstrap:
            return None

        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            stage = str(metadata.get("research_stage") or "")
            if metadata.get("autonomous_loop_id") == AUTONOMOUS_LOOP_ID:
                if (
                    problem.get("status") in {"QUEUED", "WAITING"}
                    and not stage
                ):
                    problem_id = str(problem.get("problem_id") or "")
                    if not problem_id:
                        raise RuntimeError(
                            "autonomous_loop_bootstrap_problem_identity_missing"
                        )
                    queued = await self.gateway.queue_research_stage(
                        problem_id=problem_id,
                        stage=HYPOTHESIS_PLANNER_STAGE,
                        metadata={
                            "autonomous_loop_id": AUTONOMOUS_LOOP_ID,
                            "autonomous_loop_bootstrap_version": (
                                AUTONOMOUS_LOOP_BOOTSTRAP_VERSION
                            ),
                            "autonomous_continuation": True,
                            "bootstrap_source": "interrupted_bootstrap_recovery",
                        },
                    )
                    if not queued.get("problem"):
                        raise RuntimeError(
                            "autonomous_loop_bootstrap_recovery_failed"
                        )
                    result = {
                        "seeded": False,
                        "recovered": True,
                        "problem_id": problem_id,
                        "autonomous_loop_id": AUTONOMOUS_LOOP_ID,
                        "next_research_stage": HYPOTHESIS_PLANNER_STAGE,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "runtime_source_mutation_authorized": False,
                        "live_execution_authorized": False,
                    }
                    print(
                        "GRAEN_AUTONOMOUS_LOOP_BOOTSTRAP_RECOVERY",
                        result,
                        flush=True,
                    )
                    return result
                return None
            if (
                problem.get("domain") == PROBLEM_DOMAIN
                and stage
                in (
                    STRATEGY_STAGE_KEYS
                    | {WAITING_CORPUS_STAGE, ENGINEERING_REQUIRED_STAGE}
                )
            ):
                # Adopt an already-running new-loop problem rather than create
                # a competing duplicate after an upgrade.
                return None

        created = await self.gateway.create_problem({
            "title": "GRAEN Autonomous Crypto Research Loop v1",
            "statement": (
                "Continuously discover, falsify, validate, replay, forward-shadow, "
                "and paper-test reproducible cost-aware crypto strategies using the "
                "trusted ANEVUM research grammar and frozen scientific gates. "
                "Escalate only missing software capability or protected live-risk authority."
            ),
            "domain": PROBLEM_DOMAIN,
            "priority": 100,
            "source": "GRAEN_AUTONOMOUS_OPERATING_LOOP",
            "requested_by": "ANEVUM",
            "constraints": {
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "runtime_source_mutation_authorized": False,
                "runtime_git_write_authorized": False,
                "runtime_merge_authorized": False,
                "runtime_deploy_authorized": False,
                "credential_mutation_authority": False,
                "spending_authority": False,
                "production_risk_increase_authority": False,
                "unrestricted_live_promotion_authority": False,
            },
            "success_criteria": {
                "autonomous_stage_order": [
                    "HYPOTHESIZE",
                    "DEVELOPMENT",
                    "VALIDATION",
                    "HOLDOUT",
                    "VELUM",
                    "FORWARD_SHADOW",
                    "PAPER",
                ],
                "failed_hypotheses_return_to_planner": True,
                "software_gaps_emit_engineering_required": True,
                "paper_pass_requires_human_live_risk_decision": True,
                "service_health_alone_is_insufficient": True,
            },
            "metadata": {
                "autonomous_loop_id": AUTONOMOUS_LOOP_ID,
                "autonomous_loop_bootstrap_version": AUTONOMOUS_LOOP_BOOTSTRAP_VERSION,
                "autonomous_continuation": True,
            },
        })
        problem = created.get("problem")
        if not isinstance(problem, Mapping) or not problem.get("problem_id"):
            raise RuntimeError("autonomous_loop_bootstrap_problem_unavailable")

        problem_id = str(problem["problem_id"])
        queued = await self.gateway.queue_research_stage(
            problem_id=problem_id,
            stage=HYPOTHESIS_PLANNER_STAGE,
            metadata={
                "autonomous_loop_id": AUTONOMOUS_LOOP_ID,
                "autonomous_loop_bootstrap_version": AUTONOMOUS_LOOP_BOOTSTRAP_VERSION,
                "autonomous_continuation": True,
                "bootstrap_source": "canonical_autonomous_operating_loop",
            },
        )
        if not queued.get("problem"):
            raise RuntimeError("autonomous_loop_bootstrap_queue_failed")

        result = {
            "seeded": True,
            "problem_id": problem_id,
            "autonomous_loop_id": AUTONOMOUS_LOOP_ID,
            "next_research_stage": HYPOTHESIS_PLANNER_STAGE,
            "execution_authority": False,
            "broker_orders_possible": False,
            "runtime_source_mutation_authorized": False,
            "live_execution_authorized": False,
        }
        print("GRAEN_AUTONOMOUS_LOOP_BOOTSTRAP", result, flush=True)
        return result


    async def _ensure_v10_campaign_seed(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Create exactly one canonical v10 research problem when the campaign is absent."""
        problems = snapshot.get("problems") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v10_campaign_id") == V10_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V10_STAGE_KEYS:
                return None

        created = await self.gateway.create_problem({
            "title": "GRAEN Crypto Trend Pullback v10",
            "statement": (
                "Execute the frozen crypto trend-pullback v10 campaign through "
                "development, validation, untouched holdout, VELUM replay, and native "
                "forward shadow. Research only; no broker or production execution authority."
            ),
            "domain": PROBLEM_DOMAIN,
            "priority": 95,
            "source": "GRAEN_RESEARCH_EXECUTOR",
            "requested_by": "ANEVUM",
            "constraints": {
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_promotion_authority": False,
            },
            "success_criteria": {
                "frozen_stage_order": [
                    "DEVELOPMENT", "VALIDATION", "HOLDOUT", "VELUM_REPLAY", "FORWARD_SHADOW"
                ],
                "promotion_requires_all_gates": True,
            },
            "metadata": {
                "v10_campaign_id": V10_CAMPAIGN_ID,
                "v10_bootstrap_version": 1,
                "v10_generation": 1,
            },
        })
        problem = created.get("problem")
        if not isinstance(problem, Mapping) or not problem.get("problem_id"):
            raise RuntimeError("v10_campaign_bootstrap_problem_unavailable")
        problem_id = str(problem["problem_id"])
        queued = await self.gateway.queue_research_stage(
            problem_id=problem_id,
            stage=V10_DEVELOPMENT_STAGE,
            metadata={
                "v10_campaign_id": V10_CAMPAIGN_ID,
                "v10_epoch_index": 0,
                "v10_generation": 1,
                "v10_transition_source": "canonical_bootstrap",
                "v10_bootstrap_version": 1,
            },
        )
        if not queued.get("problem"):
            raise RuntimeError("v10_campaign_bootstrap_queue_failed")
        result = {
            "seeded": True,
            "problem_id": problem_id,
            "next_research_stage": V10_DEVELOPMENT_STAGE,
            "campaign_id": V10_CAMPAIGN_ID,
        }
        print("GRAEN_V10_BOOTSTRAP", result, flush=True)
        return result

    def _observe_blocked_v10(self, snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
        """Surface the stored failure for a blocked v10 stage without mutating research state."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if problem.get("status") != "BLOCKED" or stage not in V10_STAGE_KEYS:
                continue
            problem_id = str(problem.get("problem_id") or "")
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id or run.get("status") != "BLOCKED":
                    continue
                summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
                if summary.get("state") != "RESEARCH_EXECUTION_BLOCKED":
                    continue
                run_id = str(run.get("run_id") or "")
                if run_id and run_id == self.last_observed_blocked_run_id:
                    return None
                result = {
                    "problem_id": problem_id,
                    "run_id": run_id or None,
                    "research_stage": stage,
                    "epoch_index": metadata.get("v10_epoch_index"),
                    "candidate_id": (
                        metadata.get("v10_candidate_spec", {}).get("candidate_id")
                        if isinstance(metadata.get("v10_candidate_spec"), Mapping)
                        else None
                    ),
                    "error": str(summary.get("error") or "unknown_research_execution_error")[:1000],
                    "next_action": summary.get("next_action"),
                    "execution_authority": False,
                }
                self.last_observed_blocked_run_id = run_id or None
                print("GRAEN_V10_BLOCKED", result, flush=True)
                return result
        return None

    async def _recover_blocked_v10_corpus_into_v11(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Convert an unusable terminal v10 corpus into the BTC-only v11 path."""
        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v11_campaign_id") == V11_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V11_STAGE_KEYS:
                return None

        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if (
                problem.get("status") != "BLOCKED"
                or metadata.get("research_stage") != V10_VALIDATION_STAGE
            ):
                continue
            epoch_index = int(metadata.get("v10_epoch_index") or 0)
            if _v10_next_epoch(epoch_index) is not None:
                continue
            problem_id = str(problem.get("problem_id") or "")
            candidate_spec = (
                dict(metadata.get("v10_candidate_spec"))
                if isinstance(metadata.get("v10_candidate_spec"), Mapping)
                else {}
            )
            for run in snapshot.get("runs") or []:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id or run.get("status") != "BLOCKED":
                    continue
                summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
                error = str(summary.get("error") or "")
                if not error.startswith("ValueError: v9_stage_corpus_incomplete:"):
                    continue
                blocked_run_id = str(run.get("run_id") or "")
                artifact = await self.gateway.record_artifact(
                    problem_id=problem_id,
                    run_id=blocked_run_id or None,
                    artifact_type="CRYPTO_TREND_PULLBACK_V10_CORPUS_EXHAUSTION",
                    methodology_version=V10_METHODOLOGY_VERSION,
                    content={
                        "campaign_id": V10_CAMPAIGN_ID,
                        "state": "V10_CAMPAIGN_EXHAUSTED_CORPUS",
                        "epoch_index": epoch_index,
                        "candidate_id": candidate_spec.get("candidate_id"),
                        "blocked_stage": V10_VALIDATION_STAGE,
                        "corpus_error": error,
                        "validation_strategy_evaluation_performed": False,
                        "validation_corpus_availability_inspected": True,
                        "validation_window_burned": True,
                        "historical_promotion_eligible": False,
                        "next_methodology": V11_METHODOLOGY_VERSION,
                        "next_campaign_id": V11_CAMPAIGN_ID,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "production_promotion_authority": False,
                    },
                )
                queued = await self.gateway.queue_research_stage(
                    problem_id=problem_id,
                    stage=V11_DEVELOPMENT_STAGE,
                    metadata={
                        "v11_campaign_id": V11_CAMPAIGN_ID,
                        "v11_generation": 1,
                        "v11_origin": "v10_terminal_corpus_exhaustion",
                        "v10_terminal_candidate_id": candidate_spec.get("candidate_id"),
                        "v10_terminal_error": error,
                        "v10_terminal_artifact_id": (
                            (artifact.get("artifact") or {}).get("artifact_id")
                            if isinstance(artifact.get("artifact"), Mapping)
                            else None
                        ),
                    },
                )
                if not queued.get("problem"):
                    raise RuntimeError("v11_queue_after_v10_corpus_exhaustion_failed")
                result = {
                    "recovered": True,
                    "problem_id": problem_id,
                    "state": "V10_CAMPAIGN_EXHAUSTED_CORPUS",
                    "next_research_stage": V11_DEVELOPMENT_STAGE,
                    "v11_campaign_id": V11_CAMPAIGN_ID,
                    "execution_authority": False,
                }
                print("GRAEN_V10_TO_V11", result, flush=True)
                return result
        return None

    async def _recover_exhausted_v11_into_v12(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance a completed v11 no-survivor state into the orthogonal BTC v12 campaign."""
        problems = snapshot.get("problems") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v12_campaign_id") == V12_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V12_STAGE_KEYS:
                return None

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
                or metadata.get("v11_campaign_id") != V11_CAMPAIGN_ID
            ):
                continue
            problem_id = str(problem.get("problem_id") or "")
            latest_run: Mapping[str, Any] | None = None
            for run in snapshot.get("runs") or []:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id:
                    continue
                summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
                if (
                    summary.get("campaign_id") == V11_CAMPAIGN_ID
                    and summary.get("state") == "V11_NO_DEVELOPMENT_SURVIVOR"
                    and summary.get("decision") == "NEEDS_NEW_HYPOTHESIS_ENGINE"
                ):
                    latest_run = run
                    break
            if latest_run is None:
                continue
            run_id = str(latest_run.get("run_id") or "")
            artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id or None,
                artifact_type="CRYPTO_BTC_V11_EXHAUSTION",
                methodology_version=V11_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V11_CAMPAIGN_ID,
                    "state": "V11_CAMPAIGN_EXHAUSTED",
                    "reason": "NO_DEVELOPMENT_SURVIVOR",
                    "next_methodology": V12_METHODOLOGY_VERSION,
                    "next_campaign_id": V12_CAMPAIGN_ID,
                    "historical_promotion_eligible": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                },
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V12_DEVELOPMENT_STAGE,
                metadata={
                    "v12_campaign_id": V12_CAMPAIGN_ID,
                    "v12_generation": 1,
                    "v12_origin": "v11_no_development_survivor",
                    "v11_terminal_run_id": run_id or None,
                    "v11_terminal_artifact_id": (
                        (artifact.get("artifact") or {}).get("artifact_id")
                        if isinstance(artifact.get("artifact"), Mapping)
                        else None
                    ),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v12_queue_after_v11_exhaustion_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "state": "V11_CAMPAIGN_EXHAUSTED",
                "next_research_stage": V12_DEVELOPMENT_STAGE,
                "v12_campaign_id": V12_CAMPAIGN_ID,
                "execution_authority": False,
            }
            print("GRAEN_V11_TO_V12", result, flush=True)
            return result
        return None

    async def _recover_orphaned_v13_nonconfirmatory_claim(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Requeue interrupted V13 DEVELOPMENT/VELUM work without opening sealed evidence."""
        problems = {
            str(row.get("problem_id")): row
            for row in (snapshot.get("problems") or [])
            if isinstance(row, Mapping) and row.get("problem_id")
        }
        runtime_state = snapshot.get("runtime_state")
        runtime_metadata = (
            runtime_state.get("metadata")
            if isinstance(runtime_state, Mapping)
            and isinstance(runtime_state.get("metadata"), Mapping)
            else {}
        )
        executor_state = (
            runtime_metadata.get("research_executor")
            if isinstance(runtime_metadata.get("research_executor"), Mapping)
            else {}
        )
        active_problem_id = str(executor_state.get("active_problem_id") or "")
        minimum_age = timedelta(seconds=max(180, self.interval_seconds * 4))
        now = datetime.now(UTC)

        for run in (snapshot.get("runs") or []):
            if not isinstance(run, Mapping) or run.get("status") != "RUNNING":
                continue
            problem_id = str(run.get("problem_id") or "")
            run_id = str(run.get("run_id") or "")
            problem = problems.get(problem_id)
            if not problem or problem.get("status") != "RUNNING":
                continue
            if active_problem_id == problem_id:
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            stage = str(metadata.get("research_stage") or "")
            if stage not in {V13_DEVELOPMENT_STAGE, V13_VELUM_STAGE}:
                continue
            try:
                started_at = datetime.fromisoformat(
                    str(run.get("started_at")).replace("Z", "+00:00")
                )
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=UTC)
                started_at = started_at.astimezone(UTC)
            except (TypeError, ValueError):
                continue
            if now - started_at < minimum_age:
                continue

            await self.gateway.block_research_claim(
                problem_id=problem_id,
                run_id=run_id,
                worker_id=self.worker_id,
                error="v13_nonconfirmatory_stage_interrupted:" + stage,
            )
            preserved = {
                key: value
                for key, value in metadata.items()
                if key != "research_stage"
            }
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=stage,
                metadata={
                    **preserved,
                    "v13_recovered_interrupted_run_id": run_id,
                    "v13_recovered_interrupted_stage": stage,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v13_interrupted_stage_requeue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "invalidated_run_id": run_id,
                "requeued_stage": stage,
                "sealed_evidence_opened": False,
                "execution_authority": False,
            }
            print("GRAEN_V13_INTERRUPTED_STAGE_RECOVERY", result, flush=True)
            return result
        return None

    def _research_director_evidence_packet(
        self,
        snapshot: Mapping[str, Any],
        problem: Mapping[str, Any],
    ) -> dict[str, Any]:
        history: list[dict[str, Any]] = []
        detailed_v13: dict[str, Any] | None = None
        for run in snapshot.get("runs") or []:
            if not isinstance(run, Mapping):
                continue
            summary = (
                run.get("result_summary")
                if isinstance(run.get("result_summary"), Mapping)
                else {}
            )
            campaign_id = str(summary.get("campaign_id") or "")
            state = str(summary.get("state") or "")
            if not campaign_id and not any(
                token in state for token in ("V6", "V7", "V8", "V9", "V10", "V11", "V12", "V13")
            ):
                continue
            compact = {
                "run_id": run.get("run_id"),
                "status": run.get("status"),
                "methodology_version": run.get("methodology_version"),
                "campaign_id": summary.get("campaign_id"),
                "state": summary.get("state"),
                "decision": summary.get("decision"),
                "next_action": summary.get("next_action"),
                "candidate_family": summary.get("candidate_family"),
                "candidate_id": summary.get("candidate_id"),
                "validation_opened": summary.get("validation_opened"),
                "holdout_opened": summary.get("holdout_opened"),
                "holdout_passed": summary.get("holdout_passed"),
                "failure_map": summary.get("failure_map"),
            }
            if summary.get("state") == "V13_NO_DEVELOPMENT_SURVIVOR":
                detailed_v13 = {
                    **compact,
                    "candidate_diagnostics": list(
                        summary.get("candidate_diagnostics") or []
                    )[:20],
                }
            history.append(compact)
            if len(history) >= 80:
                break

        return {
            "schema_version": "graen.research-director-evidence.v1",
            "objective_context": "BTC strategy research after V13 exhaustion",
            "current_problem": {
                "problem_id": problem.get("problem_id"),
                "title": problem.get("title"),
                "statement": problem.get("statement"),
                "constraints": problem.get("constraints"),
                "success_criteria": problem.get("success_criteria"),
            },
            "canonical_research_history": history,
            "v13_terminal_evidence": detailed_v13,
            "known_research_state": {
                "v13_result": "V13_NO_DEVELOPMENT_SURVIVOR",
                "v13_decision": "V13_HYPOTHESES_FALSIFIED",
                "live_crypto_execution_enabled": False,
                "promotion_ready_strategy": None,
            },
            "data_inventory": {
                "currently_integrated": [
                    "Alpaca crypto 5-minute OHLCV historical bars",
                    "Foundation/PostgreSQL canonical research artifacts",
                ],
                "provider_capabilities_to_reverify_before_use": [
                    "historical BTC top-of-book quotes",
                    "historical BTC trades",
                    "latest crypto orderbook",
                ],
                "historical_full_depth_orderbook_integrated": False,
            },
            "protected_methodology": {
                "stage_order": [
                    "DEVELOPMENT",
                    "VELUM",
                    "VALIDATION",
                    "HOLDOUT",
                ],
                "holdout_sealed_until_validation_pass": True,
                "realistic_costs_required": True,
                "delayed_entry_sensitivity_required": True,
                "multiplicity_control_required": True,
                "temporal_splits_required": True,
                "live_execution_requires_separate_protected_authorization": True,
            },
            "trusted_compiler_inventory": {
                "currently_supported_mechanisms": ["bar_flow_pressure_v1"],
                "arbitrary_model_code_generation_allowed": False,
                "compiler_self_modification_allowed": False,
            },
            "authority": {
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "risk_or_sizing_authority": False,
                "production_promotion_authority": False,
                "crypto_execution_enabled": False,
            },
        }


    async def _recover_blocked_r2h_velum_transport(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Recover the frozen R2H VELUM replay only after bounded infra repairs."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            recovery_version = int(
                metadata.get("v14_r2h_velum_transport_recovery_version") or 0
            )
            if (
                problem.get("status") != "BLOCKED"
                or problem.get("domain") != PROBLEM_DOMAIN
                or metadata.get("research_stage") != V14_R2H_VELUM_STAGE
                or recovery_version >= 6
            ):
                continue

            problem_id = str(problem.get("problem_id") or "")
            matching: list[Mapping[str, Any]] = []
            for run in runs:
                if (
                    not isinstance(run, Mapping)
                    or str(run.get("problem_id") or "") != problem_id
                    or run.get("status") != "BLOCKED"
                    or not isinstance(run.get("result_summary"), Mapping)
                ):
                    continue
                error = str(run.get("result_summary", {}).get("error") or "")
                connect_error = error.startswith("ConnectError:")
                known_velum_500 = bool(
                    recovery_version in {3, 4, 5}
                    and error.startswith("HTTPStatusError:")
                    and "500 Internal Server Error" in error
                    and "/v1/graen/candidate-replay" in error
                    and "rhen-velum.railway.internal:8080" in error
                )
                if connect_error or known_velum_500:
                    matching.append(run)
            if not matching:
                continue

            if recovery_version == 1 and ".internal" not in self.velum_base_url:
                continue
            if recovery_version in {2, 3, 4, 5} and not (
                ".internal" in self.velum_base_url
                and self.velum_base_url.endswith(":8080")
            ):
                continue
            if recovery_version == 3 and not await self._velum_health_ready():
                return {
                    "recovered": False,
                    "problem_id": problem_id,
                    "state": "WAITING_FOR_VELUM_PRIVATE_HEALTH",
                    "next_research_stage": V14_R2H_VELUM_STAGE,
                    "retry_count": 3,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                }
            verified_velum_commit: str | None = None
            if recovery_version == 4:
                health = await self._velum_health_snapshot()
                provenance = (
                    health.get("runtime_provenance")
                    if isinstance(health, Mapping)
                    and isinstance(health.get("runtime_provenance"), Mapping)
                    else {}
                )
                verified_velum_commit = str(provenance.get("git_commit") or "")
                if verified_velum_commit != V14_R2H_VELUM_4H_FETCH_FIX_COMMIT:
                    return {
                        "recovered": False,
                        "problem_id": problem_id,
                        "state": "WAITING_FOR_VELUM_4H_FETCH_FIX",
                        "next_research_stage": V14_R2H_VELUM_STAGE,
                        "retry_count": 4,
                        "observed_velum_git_commit": verified_velum_commit or None,
                        "required_velum_git_commit": V14_R2H_VELUM_4H_FETCH_FIX_COMMIT,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    }
            if recovery_version == 5:
                health = await self._velum_health_snapshot()
                provenance = (
                    health.get("runtime_provenance")
                    if isinstance(health, Mapping)
                    and isinstance(health.get("runtime_provenance"), Mapping)
                    else {}
                )
                verified_velum_commit = str(provenance.get("git_commit") or "")
                if verified_velum_commit != V14_R2H_VELUM_CHUNK_FIX_COMMIT:
                    return {
                        "recovered": False,
                        "problem_id": problem_id,
                        "state": "WAITING_FOR_VELUM_CHUNK_FIX",
                        "next_research_stage": V14_R2H_VELUM_STAGE,
                        "retry_count": 5,
                        "observed_velum_git_commit": verified_velum_commit or None,
                        "required_velum_git_commit": V14_R2H_VELUM_CHUNK_FIX_COMMIT,
                        "execution_authority": False,
                        "broker_orders_possible": False,
                    }

            blocked_run = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            blocked_run_id = str(blocked_run.get("run_id") or "")
            repair = (
                "retry_after_railway_private_network_repair"
                if recovery_version == 1
                else "retry_after_velum_port_8080_repair"
                if recovery_version == 2
                else "retry_after_velum_ipv6_bind_repair"
                if recovery_version == 3
                else "retry_after_velum_4h_fetch_repair"
                if recovery_version == 4
                else "retry_after_velum_chunked_4h_fetch_repair"
                if recovery_version == 5
                else "retry_after_velum_transport_recovered"
            )
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=blocked_run_id or None,
                artifact_type="CRYPTO_V14_R2H_VELUM_TRANSPORT_RECOVERY",
                methodology_version=V14_R2H_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_R2H_CAMPAIGN_ID,
                    "candidate_id": v14_r2h_candidate_spec().candidate_id,
                    "blocked_run_id": blocked_run_id or None,
                    "blocked_error": blocked_run.get("result_summary", {}).get(
                        "error"
                    ),
                    "repair": repair,
                    "retry_count": recovery_version + 1,
                    "methodology_changed": False,
                    "private_health_verified": recovery_version in {3, 4, 5},
                    "verified_velum_git_commit": verified_velum_commit,
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "promotion_authorized": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            candidate_spec = metadata.get("v14_r2h_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                candidate_spec = v14_r2h_candidate_spec().to_dict()
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2H_VELUM_STAGE,
                metadata={
                    "v14_r2h_campaign_id": V14_R2H_CAMPAIGN_ID,
                    "v14_r2h_candidate_spec": dict(candidate_spec),
                    "v14_r2h_origin_run_id": metadata.get(
                        "v14_r2h_origin_run_id"
                    ),
                    "v14_r2h_transfer_artifact_id": metadata.get(
                        "v14_r2h_transfer_artifact_id"
                    ),
                    "v14_r2h_velum_transport_recovery_version": recovery_version + 1,
                    "v14_r2h_recovered_blocked_run_id": blocked_run_id or None,
                    "v14_r2h_transport_recovery_artifact_id": artifact.get(
                        "artifact_id"
                    ),
                    "r2f_forward_shadow_preserved": True,
                    "r2g_forward_shadow_preserved": True,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2h_velum_transport_requeue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "blocked_run_id": blocked_run_id or None,
                "next_research_stage": V14_R2H_VELUM_STAGE,
                "retry_count": recovery_version + 1,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2H_VELUM_TRANSPORT_RECOVERY", result, flush=True)
            return result
        return None


    async def _recover_blocked_v14_runtime_dependency(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resume the frozen V14 preflight after restoring its ML dependency."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if (
                problem.get("status") != "BLOCKED"
                or problem.get("domain") != PROBLEM_DOMAIN
                or metadata.get("research_stage") != V14_PREFLIGHT_STAGE
                or metadata.get("v14_campaign_id") != V14_CAMPAIGN_ID
                or int(metadata.get("v14_dependency_recovery_version") or 0) >= 1
            ):
                continue
            problem_id = str(problem.get("problem_id") or "")
            matching_runs = [
                run
                for run in runs
                if isinstance(run, Mapping)
                and str(run.get("problem_id") or "") == problem_id
                and run.get("status") == "BLOCKED"
                and isinstance(run.get("result_summary"), Mapping)
                and str(run.get("result_summary", {}).get("error") or "")
                == "ImportError: sklearn needs to be installed in order to use this module"
            ]
            if not matching_runs:
                continue
            blocked_run = max(
                matching_runs,
                key=lambda row: str(row.get("started_at") or ""),
            )
            blocked_run_id = str(blocked_run.get("run_id") or "")
            artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=blocked_run_id or None,
                artifact_type="CRYPTO_BTC_V14_R1_RUNTIME_DEPENDENCY_REPAIR",
                methodology_version=V14_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_CAMPAIGN_ID,
                    "blocked_run_id": blocked_run_id or None,
                    "blocked_error": blocked_run.get("result_summary", {}).get("error"),
                    "repair": "add_scikit_learn_to_isolated_graen_ml_runtime",
                    "dependency": "scikit-learn==1.9.1",
                    "methodology_changed": False,
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            repair_artifact = (
                artifact.get("artifact")
                if isinstance(artifact.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_PREFLIGHT_STAGE,
                metadata={
                    "v14_campaign_id": V14_CAMPAIGN_ID,
                    "v14_dependency_recovery_version": 1,
                    "v14_recovered_blocked_run_id": blocked_run_id or None,
                    "v14_dependency_repair_artifact_id": repair_artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_runtime_dependency_recovery_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "blocked_run_id": blocked_run_id or None,
                "next_research_stage": V14_PREFLIGHT_STAGE,
                "v14_campaign_id": V14_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_RUNTIME_DEPENDENCY_RECOVERY", result, flush=True)
            return result
        return None


    async def _recover_blocked_v14_feature_pipeline(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resume the frozen V14 preflight after the finite-rolling repair."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if (
                problem.get("status") != "BLOCKED"
                or problem.get("domain") != PROBLEM_DOMAIN
                or metadata.get("research_stage") != V14_PREFLIGHT_STAGE
                or metadata.get("v14_campaign_id") != V14_CAMPAIGN_ID
                or int(metadata.get("v14_feature_recovery_version") or 0) >= 1
            ):
                continue
            problem_id = str(problem.get("problem_id") or "")
            matching_runs = [
                run
                for run in runs
                if isinstance(run, Mapping)
                and str(run.get("problem_id") or "") == problem_id
                and run.get("status") == "BLOCKED"
                and isinstance(run.get("result_summary"), Mapping)
                and str(run.get("result_summary", {}).get("error") or "")
                == "ValueError: v14_fold_insufficient_rows:train=0:validation=0:test=0"
            ]
            if not matching_runs:
                continue
            blocked_run = max(
                matching_runs,
                key=lambda row: str(row.get("started_at") or ""),
            )
            blocked_run_id = str(blocked_run.get("run_id") or "")
            artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=blocked_run_id or None,
                artifact_type="CRYPTO_BTC_V14_R1_FEATURE_PIPELINE_REPAIR",
                methodology_version=V14_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_CAMPAIGN_ID,
                    "blocked_run_id": blocked_run_id or None,
                    "blocked_error": blocked_run.get("result_summary", {}).get("error"),
                    "repair": "finite_rolling_statistics_preserve_warmup_nan_only",
                    "methodology_changed": False,
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            repair_artifact = (
                artifact.get("artifact")
                if isinstance(artifact.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_PREFLIGHT_STAGE,
                metadata={
                    "v14_campaign_id": V14_CAMPAIGN_ID,
                    "v14_feature_recovery_version": 1,
                    "v14_recovered_blocked_run_id": blocked_run_id or None,
                    "v14_feature_repair_artifact_id": repair_artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_feature_pipeline_recovery_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "blocked_run_id": blocked_run_id or None,
                "next_research_stage": V14_PREFLIGHT_STAGE,
                "v14_campaign_id": V14_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_FEATURE_PIPELINE_RECOVERY", result, flush=True)
            return result
        return None


    async def _recover_blocked_v14_pagination(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resume a frozen V14 pagination failure through bounded repair versions."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if (
                problem.get("status") != "BLOCKED"
                or problem.get("domain") != PROBLEM_DOMAIN
                or metadata.get("research_stage") != V14_PREFLIGHT_STAGE
                or metadata.get("v14_campaign_id") != V14_CAMPAIGN_ID
            ):
                continue
            recovery_version = int(metadata.get("v14_pagination_recovery_version") or 0)
            if recovery_version >= 2:
                continue
            problem_id = str(problem.get("problem_id") or "")
            matching_runs = [
                run
                for run in runs
                if isinstance(run, Mapping)
                and str(run.get("problem_id") or "") == problem_id
                and run.get("status") == "BLOCKED"
                and isinstance(run.get("result_summary"), Mapping)
                and str(run.get("result_summary", {}).get("error") or "")
                == "RuntimeError: v14_hourly_btc_pagination_exceeded_safety_limit"
            ]
            if not matching_runs:
                continue
            blocked_run = max(
                matching_runs,
                key=lambda row: str(row.get("started_at") or ""),
            )
            blocked_run_id = str(blocked_run.get("run_id") or "")
            artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=blocked_run_id or None,
                artifact_type="CRYPTO_BTC_V14_R1_INFRA_REPAIR",
                methodology_version=V14_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_CAMPAIGN_ID,
                    "blocked_run_id": blocked_run_id or None,
                    "blocked_error": (
                        blocked_run.get("result_summary", {}).get("error")
                    ),
                    "repair": (
                        "expand_bounded_alpaca_history_pagination"
                        if recovery_version == 0
                        else "alpaca_aggregation_pagination_v2"
                    ),
                    "pagination_max_pages": 768,
                    "pagination_cycle_detection": True,
                    "rate_limit_retry_cap": 6,
                    "recovery_from_version": recovery_version,
                    "retry_count": recovery_version + 1,
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            repair_artifact = (
                artifact.get("artifact")
                if isinstance(artifact.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_PREFLIGHT_STAGE,
                metadata={
                    "v14_campaign_id": V14_CAMPAIGN_ID,
                    "v14_pagination_recovery_version": recovery_version + 1,
                    "v14_recovered_blocked_run_id": blocked_run_id or None,
                    "v14_infra_repair_artifact_id": repair_artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_pagination_recovery_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "blocked_run_id": blocked_run_id or None,
                "next_research_stage": V14_PREFLIGHT_STAGE,
                "v14_campaign_id": V14_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_PAGINATION_RECOVERY", result, flush=True)
            return result
        return None


    async def _recover_blocked_v14_r2e_pagination(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Resume the frozen R2E stage after replacing global deep pagination."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        recoverable_errors = {
            "RuntimeError: v14_r2e_bar_pagination_exceeded_safety_limit",
            "RuntimeError: v14_r2e_chunk_pagination_exceeded_safety_limit",
        }
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if (
                problem.get("status") != "BLOCKED"
                or problem.get("domain") != PROBLEM_DOMAIN
                or metadata.get("research_stage") != V14_R2E_STAGE
                or metadata.get("v14_r2e_campaign_id") != V14_R2E_CAMPAIGN_ID
                or int(metadata.get("v14_r2e_pagination_recovery_version") or 0) >= 1
            ):
                continue

            problem_id = str(problem.get("problem_id") or "")
            matching_runs = [
                run
                for run in runs
                if isinstance(run, Mapping)
                and str(run.get("problem_id") or "") == problem_id
                and run.get("status") == "BLOCKED"
                and run.get("methodology_version") == V14_R2E_METHODOLOGY_VERSION
                and isinstance(run.get("result_summary"), Mapping)
                and str(run.get("result_summary", {}).get("error") or "")
                in recoverable_errors
            ]
            if not matching_runs:
                continue

            blocked_run = max(
                matching_runs,
                key=lambda row: str(row.get("started_at") or ""),
            )
            blocked_run_id = str(blocked_run.get("run_id") or "")
            blocked_error = str(
                blocked_run.get("result_summary", {}).get("error") or ""
            )
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=blocked_run_id or None,
                artifact_type="CRYPTO_V14_R2E_CORPUS_FETCH_REPAIR",
                methodology_version=V14_R2E_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_R2E_CAMPAIGN_ID,
                    "blocked_run_id": blocked_run_id or None,
                    "blocked_error": blocked_error,
                    "repair": "bounded_60_day_4h_chunks_with_local_pagination",
                    "chunk_days": 60,
                    "max_pages_per_chunk": 16,
                    "rate_limit_retry_cap": 6,
                    "methodology_changed": False,
                    "strategy_parameters_changed": False,
                    "oos_boundary_changed": False,
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            repair_artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2E_STAGE,
                metadata={
                    "v14_r2e_campaign_id": V14_R2E_CAMPAIGN_ID,
                    "v14_r2e_pagination_recovery_version": 1,
                    "v14_r2e_recovered_blocked_run_id": blocked_run_id or None,
                    "v14_r2e_fetch_repair_artifact_id": repair_artifact.get(
                        "artifact_id"
                    ),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2e_pagination_recovery_queue_failed")

            result = {
                "recovered": True,
                "problem_id": problem_id,
                "blocked_run_id": blocked_run_id or None,
                "blocked_error": blocked_error,
                "next_research_stage": V14_R2E_STAGE,
                "v14_r2e_campaign_id": V14_R2E_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2E_PAGINATION_RECOVERY", result, flush=True)
            return result
        return None


    async def _recover_v13_into_v14(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance the terminal V13 result into the external V14-R1 preflight."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("v14_campaign_id") == V14_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V14_STAGE_KEYS:
                return None

        for run in runs:
            if not isinstance(run, Mapping):
                continue
            summary = (
                run.get("result_summary")
                if isinstance(run.get("result_summary"), Mapping)
                else {}
            )
            if summary.get("campaign_id") == V14_CAMPAIGN_ID:
                return None

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            terminal_run: Mapping[str, Any] | None = None
            for run in runs:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id:
                    continue
                summary = (
                    run.get("result_summary")
                    if isinstance(run.get("result_summary"), Mapping)
                    else {}
                )
                if (
                    summary.get("campaign_id") == V13_CAMPAIGN_ID
                    and summary.get("state") == "V13_NO_DEVELOPMENT_SURVIVOR"
                    and summary.get("decision") == "V13_HYPOTHESES_FALSIFIED"
                ):
                    terminal_run = run
                    break
            if terminal_run is None:
                continue

            origin_run_id = str(terminal_run.get("run_id") or "")
            selection = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_BTC_V14_EXTERNAL_REPLICATION_SELECTION",
                methodology_version=V14_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_CAMPAIGN_ID,
                    "selected_method": "cost_aware_xgboost_btc",
                    "selection_basis": (
                        "deep evidence review prioritized chronological walk-forward "
                        "BTC XGBoost with transaction-cost-aware switching before any "
                        "new proprietary hypothesis family"
                    ),
                    "manifest": v14_campaign_manifest(),
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "research_only": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                },
            )
            artifact = (
                selection.get("artifact")
                if isinstance(selection.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_PREFLIGHT_STAGE,
                metadata={
                    "v14_campaign_id": V14_CAMPAIGN_ID,
                    "v14_origin": "v13_no_development_survivor_external_replication",
                    "v13_terminal_run_id": origin_run_id or None,
                    "v14_selection_artifact_id": artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_preflight_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_PREFLIGHT_STAGE,
                "v14_campaign_id": V14_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V13_TO_V14_R1", result, flush=True)
            return result
        return None

    async def _execute_btc_xgb_v14(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = (
            problem.get("metadata")
            if isinstance(problem.get("metadata"), Mapping)
            else {}
        )
        stage = str(metadata.get("research_stage") or V14_PREFLIGHT_STAGE)
        if stage != V14_PREFLIGHT_STAGE:
            raise RuntimeError(f"unsupported_v14_research_stage:{stage}")
        self.active_methodology_version = V14_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_BTC_V14_R1_PREFLIGHT_PRESPEC",
            methodology_version=V14_METHODOLOGY_VERSION,
            content={
                **v14_campaign_manifest(),
                "frozen_before_alpaca_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = (
            prespec_response.get("artifact")
            if isinstance(prespec_response.get("artifact"), Mapping)
            else {}
        )

        rows = await self._fetch_v14_hourly_btc()
        result = await asyncio.to_thread(
            evaluate_v14_alpaca_transfer_screen,
            rows,
        )
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_BTC_V14_R1_BROKER_PREFLIGHT_RESULT",
            methodology_version=V14_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        result_artifact = (
            result_response.get("artifact")
            if isinstance(result_response.get("artifact"), Mapping)
            else {}
        )
        gate = (
            result.get("broker_feasibility_gate")
            if isinstance(result.get("broker_feasibility_gate"), Mapping)
            else {}
        )
        survived = bool(gate.get("worth_full_original_replication"))
        aggregate = (
            result.get("aggregate")
            if isinstance(result.get("aggregate"), Mapping)
            else {}
        )
        paper = (
            aggregate.get("paper_10bp")
            if isinstance(aggregate.get("paper_10bp"), Mapping)
            else {}
        )
        alpaca = (
            aggregate.get("alpaca_base_30bp")
            if isinstance(aggregate.get("alpaca_base_30bp"), Mapping)
            else {}
        )
        if not survived:
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": "V14_R1_BROKER_FEASIBILITY_FAIL",
                    "status": "EXTERNAL_METHOD_PREFLIGHT_REJECTED",
                    "decision": "V14_R1_DO_NOT_REPLICATE_FURTHER",
                    "campaign_id": V14_CAMPAIGN_ID,
                    "candidate_family": V14_FAMILY,
                    "result_artifact_id": result_artifact.get("artifact_id"),
                    "paper_10bp_sharpe": paper.get("sharpe"),
                    "alpaca_base_30bp_sharpe": alpaca.get("sharpe"),
                    "alpaca_base_30bp_total_return": alpaca.get("total_return"),
                    "positive_fold_share": gate.get("alpaca_base_positive_fold_share"),
                    "original_replication_complete": False,
                    "development_opened": False,
                    "validation_opened": False,
                    "holdout_opened": False,
                    "next_action": "ADVANCE_TO_V14_R2_QUEUE_IMBALANCE_RESEARCH",
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                },
            )

        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": "V14_R1_BROKER_FEASIBILITY_SURVIVES",
                "status": "EXTERNAL_METHOD_PREFLIGHT_PASS",
                "decision": "CONTINUE_RESEARCH",
                "campaign_id": V14_CAMPAIGN_ID,
                "candidate_family": V14_FAMILY,
                "result_artifact_id": result_artifact.get("artifact_id"),
                "paper_10bp_sharpe": paper.get("sharpe"),
                "alpaca_base_30bp_sharpe": alpaca.get("sharpe"),
                "alpaca_base_30bp_total_return": alpaca.get("total_return"),
                "positive_fold_share": gate.get("alpaca_base_positive_fold_share"),
                "original_replication_complete": False,
                "development_opened": False,
                "validation_opened": False,
                "holdout_opened": False,
                "next_action": "VERIFY_SOURCE_AND_RUN_V14_R1_ORIGINAL_REPLICATION",
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2d_fail_into_r2e(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance terminal R2D rejection or unusable quote corpus into R2E."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if problem.get("domain") != PROBLEM_DOMAIN:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            status = str(problem.get("status") or "")
            stage = str(metadata.get("research_stage") or "")
            ordinary_terminal = status == "WAITING" and not stage
            blocked_r2d = status == "BLOCKED" and stage == V14_R2D_STAGE
            if not ordinary_terminal and not blocked_r2d:
                continue

            problem_id = str(problem.get("problem_id") or "")
            problem_runs = [
                row for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
            ]
            if any(
                row.get("methodology_version") == V14_R2E_METHODOLOGY_VERSION
                or (
                    isinstance(row.get("result_summary"), Mapping)
                    and row.get("result_summary", {}).get("campaign_id") == V14_R2E_CAMPAIGN_ID
                )
                for row in problem_runs
            ):
                continue

            terminal_failures = [
                row for row in problem_runs
                if row.get("methodology_version") == V14_R2D_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state") == "V14_R2D_BROKER_FEASIBILITY_FAIL"
                and row.get("result_summary", {}).get("decision") == "V14_R2D_DO_NOT_PROMOTE"
            ]
            blocked_corpus = []
            for row in problem_runs:
                if (
                    row.get("methodology_version") != V14_R2D_METHODOLOGY_VERSION
                    or row.get("status") != "BLOCKED"
                    or not isinstance(row.get("result_summary"), Mapping)
                ):
                    continue
                summary = row.get("result_summary") or {}
                error = str(summary.get("error") or "")
                if (
                    summary.get("state") == "RESEARCH_EXECUTION_BLOCKED"
                    and error.startswith("ValueError: v14_r2d_matched_quote_corpus_too_small:")
                ):
                    blocked_corpus.append(row)

            matching = terminal_failures or blocked_corpus
            if not matching:
                continue
            origin = max(matching, key=lambda row: str(row.get("started_at") or ""))
            origin_run_id = str(origin.get("run_id") or "")
            origin_summary = (
                origin.get("result_summary")
                if isinstance(origin.get("result_summary"), Mapping)
                else {}
            )
            origin_error = str(origin_summary.get("error") or "")
            origin_state = str(origin_summary.get("state") or "")
            corpus_terminal_artifact_id = None

            if blocked_corpus:
                corpus_response = await self.gateway.record_artifact(
                    problem_id=problem_id,
                    run_id=origin_run_id or None,
                    artifact_type="CRYPTO_V14_R2D_CORPUS_INFEASIBLE",
                    methodology_version=V14_R2D_METHODOLOGY_VERSION,
                    content={
                        "campaign_id": V14_R2D_CAMPAIGN_ID,
                        "state": "V14_R2D_CORPUS_UNAVAILABLE",
                        "blocked_stage": V14_R2D_STAGE,
                        "corpus_error": origin_error,
                        "strategy_evaluation_performed": False,
                        "broker_feasibility_evaluation_performed": False,
                        "historical_promotion_eligible": False,
                        "next_action": "ADVANCE_TO_V14_R2E_BTC_4H_TREND_PREFLIGHT",
                        "source_commit": _source_commit(),
                        "deployment_id": _deployment_id(),
                        "execution_authority": False,
                        "broker_orders_possible": False,
                        "live_execution_authorized": False,
                    },
                )
                corpus_artifact = (
                    corpus_response.get("artifact")
                    if isinstance(corpus_response.get("artifact"), Mapping)
                    else {}
                )
                corpus_terminal_artifact_id = corpus_artifact.get("artifact_id")

            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2E_BTC_4H_TREND_SELECTION",
                methodology_version=V14_R2E_METHODOLOGY_VERSION,
                content={
                    **v14_r2e_campaign_manifest(),
                    "origin_campaign_id": V14_R2D_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "origin_r2d_state": origin_state,
                    "origin_r2d_error": origin_error or None,
                    "origin_r2d_corpus_terminal_artifact_id": corpus_terminal_artifact_id,
                    "selection_basis": (
                        "R2D could not establish a promotable Alpaca triangular-arbitrage edge; "
                        "advance to a public leakage-controlled 4h BTC long/flat trend family "
                        "whose edge is explicitly low turnover and crash avoidance."
                    ),
                    "frozen_before_bar_corpus_access": True,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = artifact_response.get("artifact") if isinstance(artifact_response.get("artifact"), Mapping) else {}
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2E_STAGE,
                metadata={
                    "v14_r2e_campaign_id": V14_R2E_CAMPAIGN_ID,
                    "v14_r2e_origin_run_id": origin_run_id or None,
                    "v14_r2e_selection_artifact_id": artifact.get("artifact_id"),
                    "v14_r2d_corpus_terminal_artifact_id": corpus_terminal_artifact_id,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2e_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "origin_state": origin_state,
                "origin_error": origin_error or None,
                "next_research_stage": V14_R2E_STAGE,
                "v14_r2e_campaign_id": V14_R2E_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2D_TO_R2E", result, flush=True)
            return result
        return None

    async def _execute_btc_4h_trend_v14_r2e(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V14_R2E_STAGE)
        if stage != V14_R2E_STAGE:
            raise RuntimeError(f"unsupported_v14_r2e_research_stage:{stage}")
        self.active_methodology_version = V14_R2E_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2E_BTC_4H_TREND_PRESPEC",
            methodology_version=V14_R2E_METHODOLOGY_VERSION,
            content={
                **v14_r2e_campaign_manifest(),
                "frozen_before_bar_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = prespec_response.get("artifact") if isinstance(prespec_response.get("artifact"), Mapping) else {}
        rows = await self._fetch_v14_r2e_btc_4h()
        result = await asyncio.to_thread(evaluate_v14_r2e_btc_4h_trend, rows)
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2E_BTC_4H_TREND_RESULT",
            methodology_version=V14_R2E_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        artifact = result_response.get("artifact") if isinstance(result_response.get("artifact"), Mapping) else {}
        gate = result.get("broker_feasibility_gate") if isinstance(result.get("broker_feasibility_gate"), Mapping) else {}
        development = result.get("development") if isinstance(result.get("development"), Mapping) else {}
        oos = result.get("oos") if isinstance(result.get("oos"), Mapping) else {}
        scenarios = oos.get("scenarios") if isinstance(oos.get("scenarios"), Mapping) else {}
        decisive = scenarios.get("taker_stress_30bp") if isinstance(scenarios.get("taker_stress_30bp"), Mapping) else {}
        buy_hold = oos.get("buy_hold") if isinstance(oos.get("buy_hold"), Mapping) else {}
        survived = bool(gate.get("survives_to_shadow"))
        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2E_SURVIVES_TO_BTC_4H_TREND_SHADOW"
                    if survived else "V14_R2E_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "BTC_4H_TREND_PREFLIGHT_PASS"
                    if survived else "BTC_4H_TREND_PREFLIGHT_REJECTED"
                ),
                "decision": "CONTINUE_RESEARCH" if survived else "V14_R2E_DO_NOT_PROMOTE",
                "campaign_id": V14_R2E_CAMPAIGN_ID,
                "candidate_family": V14_R2E_FAMILY,
                "result_artifact_id": artifact.get("artifact_id"),
                "selected_sma_window": development.get("selected_window"),
                "taker_stress_30bp_bar_count": decisive.get("bar_count"),
                "taker_stress_30bp_entry_count": decisive.get("entry_count"),
                "taker_stress_30bp_total_return": decisive.get("total_return"),
                "taker_stress_30bp_sharpe": decisive.get("sharpe"),
                "taker_stress_30bp_max_drawdown": decisive.get("max_drawdown"),
                "taker_stress_30bp_positive_time_quarter_share": decisive.get("positive_time_quarter_share"),
                "oos_buy_hold_sharpe": buy_hold.get("sharpe"),
                "oos_buy_hold_total_return": buy_hold.get("total_return"),
                "next_action": (
                    "START_V14_R2E_BTC_4H_TREND_FORWARD_SHADOW"
                    if survived else "ADVANCE_TO_V14_R2F_BTC_DAILY_SLOW_MOMENTUM_RESEARCH"
                ),
                "shadow_only": survived,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2f_shadow_into_r2g(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Continue adaptive research without replacing the active R2F shadow."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        artifacts = snapshot.get("artifacts") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")

            if any(
                isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and (
                    row.get("methodology_version")
                    == V14_R2G_METHODOLOGY_VERSION
                    or (
                        isinstance(row.get("result_summary"), Mapping)
                        and row.get("result_summary", {}).get("campaign_id")
                        == V14_R2G_CAMPAIGN_ID
                    )
                )
                for row in runs
            ):
                continue

            activation_artifacts = [
                row
                for row in artifacts
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("artifact_type")
                == "CRYPTO_V14_R2F_FORWARD_SHADOW_ACTIVATION"
                and (
                    not isinstance(row.get("content"), Mapping)
                    or row.get("content", {}).get("candidate_id")
                    == v14_r2f_candidate_spec().candidate_id
                )
            ]
            if not activation_artifacts:
                continue

            matching = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("methodology_version")
                == V14_R2F_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == "V14_R2F_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW"
            ]
            if not matching:
                continue

            origin = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            origin_run_id = str(origin.get("run_id") or "")
            activation_artifact = max(
                activation_artifacts,
                key=lambda row: str(row.get("created_at") or ""),
            )
            activation_artifact_id = str(
                activation_artifact.get("artifact_id") or ""
            ) or None

            selection_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2G_CONSENSUS_SELECTION",
                methodology_version=V14_R2G_METHODOLOGY_VERSION,
                content={
                    **v14_r2g_campaign_manifest(),
                    "origin_campaign_id": V14_R2F_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "r2f_activation_artifact_id": activation_artifact_id,
                    "r2f_forward_shadow_preserved": True,
                    "selection_basis": (
                        "Adaptive robustness diagnostics found that combining "
                        "180-day momentum with a slow daily SMA using OR logic "
                        "survived the 30bp gate across a broad 165/180/195 by "
                        "200/250/300 neighborhood. R2G is evaluated separately "
                        "while the frozen R2F forward shadow remains active."
                    ),
                    "adaptive_selection_disclosed": True,
                    "fresh_confirmation_required": (
                        "NONDISRUPTIVE_FORWARD_SHADOW_COMPARISON"
                    ),
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                },
            )
            selection_artifact = (
                selection_response.get("artifact")
                if isinstance(selection_response.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2G_STAGE,
                metadata={
                    "v14_r2g_campaign_id": V14_R2G_CAMPAIGN_ID,
                    "v14_r2g_origin_run_id": origin_run_id or None,
                    "v14_r2g_selection_artifact_id": (
                        selection_artifact.get("artifact_id")
                    ),
                    "r2f_forward_shadow_preserved": True,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2g_stage_queue_failed")

            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2G_STAGE,
                "v14_r2g_campaign_id": V14_R2G_CAMPAIGN_ID,
                "r2f_forward_shadow_preserved": True,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2F_TO_R2G", result, flush=True)
            return result
        return None


    async def _execute_btc_consensus_trend_v14_r2g(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = (
            problem.get("metadata")
            if isinstance(problem.get("metadata"), Mapping)
            else {}
        )
        stage = str(metadata.get("research_stage") or V14_R2G_STAGE)
        if stage != V14_R2G_STAGE:
            raise RuntimeError(f"unsupported_v14_r2g_research_stage:{stage}")
        self.active_methodology_version = V14_R2G_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2G_CONSENSUS_PRESPEC",
            methodology_version=V14_R2G_METHODOLOGY_VERSION,
            content={
                **v14_r2g_campaign_manifest(),
                "r2f_forward_shadow_preserved": True,
                "frozen_before_daily_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = (
            prespec_response.get("artifact")
            if isinstance(prespec_response.get("artifact"), Mapping)
            else {}
        )

        rows = await self._fetch_v14_r2f_btc_daily()
        result = await asyncio.to_thread(
            evaluate_v14_r2g_consensus_trend,
            rows,
        )
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2G_CONSENSUS_RESULT",
            methodology_version=V14_R2G_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "r2f_forward_shadow_preserved": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        result_artifact = (
            result_response.get("artifact")
            if isinstance(result_response.get("artifact"), Mapping)
            else {}
        )

        gate = (
            result.get("adaptive_gate")
            if isinstance(result.get("adaptive_gate"), Mapping)
            else {}
        )
        recent = (
            result.get("adaptive_recent_window")
            if isinstance(result.get("adaptive_recent_window"), Mapping)
            else {}
        )
        scenarios = (
            recent.get("scenarios")
            if isinstance(recent.get("scenarios"), Mapping)
            else {}
        )
        decisive = (
            scenarios.get("taker_stress_30bp")
            if isinstance(scenarios.get("taker_stress_30bp"), Mapping)
            else {}
        )
        severe = (
            scenarios.get("severe_stress_50bp")
            if isinstance(scenarios.get("severe_stress_50bp"), Mapping)
            else {}
        )
        delayed = (
            recent.get("one_day_execution_delay")
            if isinstance(recent.get("one_day_execution_delay"), Mapping)
            else {}
        )
        robustness = (
            result.get("robustness_neighborhood")
            if isinstance(result.get("robustness_neighborhood"), Mapping)
            else {}
        )
        survived = bool(
            gate.get("survives_to_forward_shadow_comparison")
        )
        spec = v14_r2g_candidate_spec().to_dict()

        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2G_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW_COMPARISON"
                    if survived
                    else "V14_R2G_ADAPTIVE_DISCOVERY_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "BTC_DAILY_CONSENSUS_DISCOVERY_PASS"
                    if survived
                    else "BTC_DAILY_CONSENSUS_DISCOVERY_REJECTED"
                ),
                "decision": (
                    "HOLD_FOR_NONDISRUPTIVE_FORWARD_SHADOW_COMPARISON"
                    if survived
                    else "V14_R2G_DO_NOT_PROMOTE"
                ),
                "campaign_id": V14_R2G_CAMPAIGN_ID,
                "candidate_id": spec.get("candidate_id"),
                "candidate_family": V14_R2G_FAMILY,
                "candidate_spec": spec,
                "result_artifact_id": result_artifact.get("artifact_id"),
                "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
                "independent_historical_validation": False,
                "independent_historical_holdout": False,
                "r2f_forward_shadow_preserved": True,
                "taker_stress_30bp_bar_count": decisive.get("bar_count"),
                "taker_stress_30bp_entry_count": decisive.get("entry_count"),
                "taker_stress_30bp_turnover_units": decisive.get("turnover_units"),
                "taker_stress_30bp_total_return": decisive.get("total_return"),
                "taker_stress_30bp_sharpe": decisive.get("sharpe"),
                "taker_stress_30bp_max_drawdown": decisive.get("max_drawdown"),
                "severe_stress_50bp_total_return": severe.get("total_return"),
                "one_day_delay_total_return": delayed.get("total_return"),
                "one_day_delay_sharpe": delayed.get("sharpe"),
                "neighborhood_positive_return_share": robustness.get(
                    "positive_return_share"
                ),
                "neighborhood_sharpe_gte_045_share": robustness.get(
                    "sharpe_gte_045_share"
                ),
                "next_action": (
                    "DESIGN_NONDISRUPTIVE_R2F_VS_R2G_FORWARD_COMPARISON"
                    if survived
                    else "KEEP_R2F_FORWARD_SHADOW_AND_CONTINUE_RESEARCH"
                ),
                "shadow_only": survived,
                "promotion_eligible": False,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2g_pass_into_r2h_faststart(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Queue the 4h fast-start transfer while preserving both daily shadows."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            matching = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("methodology_version") == V14_R2G_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == "V14_R2G_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW_COMPARISON"
            ]
            if not matching:
                continue
            if any(
                isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and (
                    row.get("methodology_version") == V14_R2H_METHODOLOGY_VERSION
                    or (
                        isinstance(row.get("result_summary"), Mapping)
                        and row.get("result_summary", {}).get("campaign_id")
                        == V14_R2H_CAMPAIGN_ID
                    )
                )
                for row in runs
            ):
                continue
            origin = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2H_STAGE,
                metadata={
                    "v14_r2h_campaign_id": V14_R2H_CAMPAIGN_ID,
                    "v14_r2h_origin_run_id": str(origin.get("run_id") or "") or None,
                    "r2f_forward_shadow_preserved": True,
                    "r2g_forward_shadow_preserved": True,
                    "faststart_validation_mode": "HISTORICAL_TRANSFER_THEN_VELUM_THEN_4H_SHADOW",
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2h_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2H_STAGE,
                "v14_r2h_campaign_id": V14_R2H_CAMPAIGN_ID,
                "r2f_forward_shadow_preserved": True,
                "r2g_forward_shadow_preserved": True,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2G_TO_R2H", result, flush=True)
            return result
        return None


    async def _execute_btc_4h_consensus_v14_r2h(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = (
            problem.get("metadata")
            if isinstance(problem.get("metadata"), Mapping)
            else {}
        )
        stage = str(metadata.get("research_stage") or V14_R2H_STAGE)
        self.active_methodology_version = V14_R2H_METHODOLOGY_VERSION

        if stage == V14_R2H_VELUM_STAGE:
            candidate_spec = metadata.get("v14_r2h_candidate_spec")
            if not isinstance(candidate_spec, Mapping):
                raise RuntimeError("v14_r2h_velum_candidate_spec_missing")
            replay_end = V14_R2H_BAR_SCREEN_END
            replay_start = replay_end - timedelta(days=180)
            replay = await self._replay_in_velum(
                problem_id=problem_id,
                graen_run_id=run_id,
                campaign_id=V14_R2H_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V14_R2H_METHODOLOGY_VERSION,
                candidate_spec=candidate_spec,
                replay_start=replay_start,
                replay_end=replay_end,
                seed=142000,
            )
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id,
                artifact_type="CRYPTO_V14_R2H_VELUM_RESULT",
                methodology_version=V14_R2H_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_R2H_CAMPAIGN_ID,
                    "candidate_spec": dict(candidate_spec),
                    "replay": replay,
                    "replay_evidence_role": "POST_TRANSFER_ENGINEERING_REPLAY_ONLY",
                    "independent_confirmatory_evidence": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "promotion_authorized": False,
                },
            )
            artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            gate = (
                replay.get("engineering_gate")
                if isinstance(replay.get("engineering_gate"), Mapping)
                else {}
            )
            passed = bool(gate.get("passed"))
            scenarios = (
                replay.get("scenarios")
                if isinstance(replay.get("scenarios"), Mapping)
                else {}
            )
            stressed = (
                scenarios.get("taker_stress_30bp")
                if isinstance(scenarios.get("taker_stress_30bp"), Mapping)
                else {}
            )
            severe = (
                scenarios.get("severe_stress_50bp")
                if isinstance(scenarios.get("severe_stress_50bp"), Mapping)
                else {}
            )
            delayed = (
                replay.get("one_bar_execution_delay")
                if isinstance(replay.get("one_bar_execution_delay"), Mapping)
                else {}
            )
            return await self._finalize(
                problem=problem,
                run=run,
                status="WAITING",
                summary={
                    "state": (
                        "V14_R2H_VELUM_PASS"
                        if passed
                        else "V14_R2H_VELUM_REJECTED"
                    ),
                    "status": (
                        "ENGINEERING_REPLAY_PASS"
                        if passed
                        else "ENGINEERING_REPLAY_FAIL"
                    ),
                    "decision": (
                        "ACTIVATE_4H_FORWARD_SHADOW"
                        if passed
                        else "V14_R2H_DO_NOT_PROMOTE"
                    ),
                    "campaign_id": V14_R2H_CAMPAIGN_ID,
                    "candidate_id": candidate_spec.get("candidate_id"),
                    "candidate_family": V14_R2H_FAMILY,
                    "candidate_spec": dict(candidate_spec),
                    "velum_artifact_id": artifact.get("artifact_id"),
                    "velum_replay_start": replay_start.isoformat(),
                    "velum_replay_end": replay_end.isoformat(),
                    "stressed_30bp_total_return": stressed.get("total_return"),
                    "stressed_30bp_sharpe": stressed.get("sharpe"),
                    "stressed_30bp_max_drawdown": stressed.get("max_drawdown"),
                    "severe_50bp_total_return": severe.get("total_return"),
                    "one_bar_delay_total_return": delayed.get("total_return"),
                    "engineering_gate_reasons": list(gate.get("reasons") or []),
                    "r2f_forward_shadow_preserved": True,
                    "r2g_forward_shadow_preserved": True,
                    "next_action": (
                        "ACTIVATE_R2H_4H_FORWARD_SHADOW"
                        if passed
                        else "KEEP_DAILY_SHADOWS_AND_CONTINUE_RESEARCH"
                    ),
                    "promotion_eligible": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                },
            )

        if stage != V14_R2H_STAGE:
            raise RuntimeError(f"unsupported_v14_r2h_research_stage:{stage}")

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2H_4H_CONSENSUS_PRESPEC",
            methodology_version=V14_R2H_METHODOLOGY_VERSION,
            content={
                **v14_r2h_campaign_manifest(),
                "r2f_forward_shadow_preserved": True,
                "r2g_forward_shadow_preserved": True,
                "frozen_before_4h_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = (
            prespec_response.get("artifact")
            if isinstance(prespec_response.get("artifact"), Mapping)
            else {}
        )

        rows = await self._fetch_v14_r2e_btc_4h()
        result = await asyncio.to_thread(
            evaluate_v14_r2h_consensus_transfer,
            rows,
        )
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2H_4H_CONSENSUS_RESULT",
            methodology_version=V14_R2H_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "r2f_forward_shadow_preserved": True,
                "r2g_forward_shadow_preserved": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        result_artifact = (
            result_response.get("artifact")
            if isinstance(result_response.get("artifact"), Mapping)
            else {}
        )
        gate = (
            result.get("transfer_gate")
            if isinstance(result.get("transfer_gate"), Mapping)
            else {}
        )
        window = (
            result.get("adaptive_transfer_window")
            if isinstance(result.get("adaptive_transfer_window"), Mapping)
            else {}
        )
        scenarios = (
            window.get("scenarios")
            if isinstance(window.get("scenarios"), Mapping)
            else {}
        )
        decisive = (
            scenarios.get("taker_stress_30bp")
            if isinstance(scenarios.get("taker_stress_30bp"), Mapping)
            else {}
        )
        severe = (
            scenarios.get("severe_stress_50bp")
            if isinstance(scenarios.get("severe_stress_50bp"), Mapping)
            else {}
        )
        delayed = (
            window.get("one_bar_execution_delay")
            if isinstance(window.get("one_bar_execution_delay"), Mapping)
            else {}
        )
        robustness = (
            result.get("robustness_neighborhood")
            if isinstance(result.get("robustness_neighborhood"), Mapping)
            else {}
        )
        survived = bool(gate.get("survives_to_velum_and_forward_shadow"))
        spec = v14_r2h_candidate_spec().to_dict()

        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2H_4H_TRANSFER_SURVIVES_TO_VELUM_AND_FORWARD_SHADOW"
                    if survived
                    else "V14_R2H_4H_TRANSFER_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "BTC_4H_CONSENSUS_TRANSFER_PASS"
                    if survived
                    else "BTC_4H_CONSENSUS_TRANSFER_REJECTED"
                ),
                "decision": (
                    "ADVANCE_TO_VELUM_AND_4H_SHADOW"
                    if survived
                    else "V14_R2H_DO_NOT_PROMOTE"
                ),
                "campaign_id": V14_R2H_CAMPAIGN_ID,
                "candidate_id": spec.get("candidate_id"),
                "candidate_family": V14_R2H_FAMILY,
                "candidate_spec": spec,
                "result_artifact_id": result_artifact.get("artifact_id"),
                "evidence_role": "ADAPTIVE_CROSS_RESOLUTION_TRANSFER_ONLY",
                "independent_historical_validation": False,
                "independent_historical_holdout": False,
                "taker_stress_30bp_bar_count": decisive.get("bar_count"),
                "taker_stress_30bp_entry_count": decisive.get("entry_count"),
                "taker_stress_30bp_turnover_units": decisive.get("turnover_units"),
                "taker_stress_30bp_total_return": decisive.get("total_return"),
                "taker_stress_30bp_sharpe": decisive.get("sharpe"),
                "taker_stress_30bp_max_drawdown": decisive.get("max_drawdown"),
                "severe_stress_50bp_total_return": severe.get("total_return"),
                "one_bar_delay_total_return": delayed.get("total_return"),
                "one_bar_delay_sharpe": delayed.get("sharpe"),
                "neighborhood_positive_return_share": robustness.get(
                    "positive_return_share"
                ),
                "neighborhood_sharpe_gte_045_share": robustness.get(
                    "sharpe_gte_045_share"
                ),
                "r2f_forward_shadow_preserved": True,
                "r2g_forward_shadow_preserved": True,
                "next_action": (
                    "RUN_VELUM_REPLAY_AND_ACTIVATE_4H_FORWARD_SHADOW"
                    if survived
                    else "KEEP_DAILY_SHADOWS_AND_CONTINUE_RESEARCH"
                ),
                "shadow_only": survived,
                "promotion_eligible": False,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2h_pass_into_velum(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance a durable R2H transfer pass into bounded VELUM replay."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        artifacts = snapshot.get("artifacts") or []
        candidate_id = v14_r2h_candidate_spec().candidate_id

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            if any(
                isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("artifact_type") == "CRYPTO_V14_R2H_VELUM_RESULT"
                for row in artifacts
            ):
                continue
            matching = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("methodology_version") == V14_R2H_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == "V14_R2H_4H_TRANSFER_SURVIVES_TO_VELUM_AND_FORWARD_SHADOW"
                and row.get("result_summary", {}).get("decision")
                == "ADVANCE_TO_VELUM_AND_4H_SHADOW"
            ]
            if not matching:
                continue
            origin = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            origin_summary = (
                origin.get("result_summary")
                if isinstance(origin.get("result_summary"), Mapping)
                else {}
            )
            spec = origin_summary.get("candidate_spec")
            if not isinstance(spec, Mapping) or spec.get("candidate_id") != candidate_id:
                spec = v14_r2h_candidate_spec().to_dict()
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2H_VELUM_STAGE,
                metadata={
                    "v14_r2h_campaign_id": V14_R2H_CAMPAIGN_ID,
                    "v14_r2h_origin_run_id": str(origin.get("run_id") or "") or None,
                    "v14_r2h_candidate_spec": dict(spec),
                    "v14_r2h_transfer_artifact_id": origin_summary.get(
                        "result_artifact_id"
                    ),
                    "r2f_forward_shadow_preserved": True,
                    "r2g_forward_shadow_preserved": True,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2h_velum_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "candidate_id": candidate_id,
                "next_research_stage": V14_R2H_VELUM_STAGE,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2H_TO_VELUM", result, flush=True)
            return result
        return None


    async def _recover_v14_r2g_pass_into_forward_shadow_comparison(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Activate R2G beside R2F after adaptive comparison discovery passes."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        artifacts = snapshot.get("artifacts") or []
        r2f_candidate_id = v14_r2f_candidate_spec().candidate_id
        r2g_candidate_id = v14_r2g_candidate_spec().candidate_id

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")

            if any(
                isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("artifact_type")
                == "CRYPTO_V14_R2G_FORWARD_SHADOW_ACTIVATION"
                and (
                    not isinstance(row.get("content"), Mapping)
                    or row.get("content", {}).get("candidate_id")
                    == r2g_candidate_id
                )
                for row in artifacts
            ):
                continue

            r2f_activation = next(
                (
                    row
                    for row in artifacts
                    if isinstance(row, Mapping)
                    and str(row.get("problem_id") or "") == problem_id
                    and row.get("artifact_type")
                    == "CRYPTO_V14_R2F_FORWARD_SHADOW_ACTIVATION"
                    and (
                        not isinstance(row.get("content"), Mapping)
                        or row.get("content", {}).get("candidate_id")
                        == r2f_candidate_id
                    )
                ),
                None,
            )
            if r2f_activation is None:
                continue

            matching = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("methodology_version")
                == V14_R2G_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == (
                    "V14_R2G_ADAPTIVE_DISCOVERY_"
                    "SURVIVES_TO_FORWARD_SHADOW_COMPARISON"
                )
                and row.get("result_summary", {}).get("decision")
                == "HOLD_FOR_NONDISRUPTIVE_FORWARD_SHADOW_COMPARISON"
            ]
            if not matching:
                continue

            if not self.shadow_configured:
                diagnostic = {
                    "recovered": False,
                    "problem_id": problem_id,
                    "state": "V14_R2G_FORWARD_COMPARISON_WAITING_FOR_SERVICE",
                    "candidate_id": r2g_candidate_id,
                    "r2f_forward_shadow_preserved": True,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                }
                print("GRAEN_V14_R2G_SHADOW_WAIT", diagnostic, flush=True)
                return diagnostic

            origin = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            origin_run_id = str(origin.get("run_id") or "")
            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=origin_run_id,
                campaign_id=V14_R2G_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V14_R2G_METHODOLOGY_VERSION,
                candidate_spec=v14_r2g_candidate_spec().to_dict(),
                velum_artifact_id="",
                evidence_phase="FORWARD_SHADOW",
            )
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2G_FORWARD_SHADOW_ACTIVATION",
                methodology_version=V14_R2G_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_R2G_CAMPAIGN_ID,
                    "candidate_id": r2g_candidate_id,
                    "candidate_spec": v14_r2g_candidate_spec().to_dict(),
                    "activation": activation,
                    "comparison_against_candidate_id": r2f_candidate_id,
                    "r2f_activation_artifact_id": str(
                        r2f_activation.get("artifact_id") or ""
                    )
                    or None,
                    "r2f_forward_shadow_preserved": True,
                    "evidence_role": "FRESH_FORWARD_SHADOW_COMPARISON_ONLY",
                    "historical_adaptive_evidence_counts_as_fresh": False,
                    "live_execution_authorized": False,
                    "promotion_authorized": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "candidate_id": r2g_candidate_id,
                "comparison_against_candidate_id": r2f_candidate_id,
                "activation_id": (
                    activation.get("activation_id")
                    or (activation.get("activation") or {}).get("activation_id")
                ),
                "activation_artifact_id": artifact.get("artifact_id"),
                "evidence_role": "FRESH_FORWARD_SHADOW_COMPARISON_ONLY",
                "r2f_forward_shadow_preserved": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "live_execution_authorized": False,
            }
            print("GRAEN_V14_R2G_SHADOW_ACTIVATED", result, flush=True)
            return result
        return None


    async def _recover_v14_r2f_pass_into_forward_shadow(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Activate fresh-only daily shadow after adaptive R2F broker feasibility."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        artifacts = snapshot.get("artifacts") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            forward_shadow = (
                metadata.get("forward_shadow")
                if isinstance(metadata.get("forward_shadow"), Mapping)
                else {}
            )
            if (
                forward_shadow.get("candidate_id")
                == v14_r2f_candidate_spec().candidate_id
            ):
                continue

            problem_id = str(problem.get("problem_id") or "")
            if any(
                isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("artifact_type")
                == "CRYPTO_V14_R2F_FORWARD_SHADOW_ACTIVATION"
                and (
                    not isinstance(row.get("content"), Mapping)
                    or row.get("content", {}).get("candidate_id")
                    == v14_r2f_candidate_spec().candidate_id
                )
                for row in artifacts
            ):
                continue

            matching = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
                and row.get("methodology_version")
                == V14_R2F_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == "V14_R2F_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW"
                and row.get("result_summary", {}).get("decision")
                == "START_FRESH_FORWARD_SHADOW_REQUIRED"
            ]
            if not matching:
                continue

            if not self.shadow_configured:
                diagnostic = {
                    "recovered": False,
                    "problem_id": problem_id,
                    "state": "V14_R2F_FORWARD_SHADOW_WAITING_FOR_SERVICE",
                    "candidate_id": v14_r2f_candidate_spec().candidate_id,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                }
                print("GRAEN_V14_R2F_SHADOW_WAIT", diagnostic, flush=True)
                return diagnostic

            origin = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            origin_run_id = str(origin.get("run_id") or "")
            activation = await self._activate_forward_shadow(
                problem_id=problem_id,
                graen_run_id=origin_run_id,
                campaign_id=V14_R2F_CAMPAIGN_ID,
                epoch_index=0,
                generation=1,
                candidate_methodology=V14_R2F_METHODOLOGY_VERSION,
                candidate_spec=v14_r2f_candidate_spec().to_dict(),
                velum_artifact_id="",
                evidence_phase="FORWARD_SHADOW",
            )
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2F_FORWARD_SHADOW_ACTIVATION",
                methodology_version=V14_R2F_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V14_R2F_CAMPAIGN_ID,
                    "candidate_id": v14_r2f_candidate_spec().candidate_id,
                    "candidate_spec": v14_r2f_candidate_spec().to_dict(),
                    "activation": activation,
                    "evidence_role": "FRESH_FORWARD_SHADOW_ONLY",
                    "historical_adaptive_evidence_counts_as_fresh": False,
                    "live_execution_authorized": False,
                    "promotion_authorized": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "candidate_id": v14_r2f_candidate_spec().candidate_id,
                "activation_id": (
                    activation.get("activation_id")
                    or (activation.get("activation") or {}).get("activation_id")
                ),
                "activation_artifact_id": artifact.get("artifact_id"),
                "evidence_role": "FRESH_FORWARD_SHADOW_ONLY",
                "execution_authority": False,
                "broker_orders_possible": False,
                "live_execution_authorized": False,
            }
            print("GRAEN_V14_R2F_SHADOW_ACTIVATED", result, flush=True)
            return result
        return None


    async def _recover_v14_r2e_fail_into_r2f(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance terminal R2E rejection into adaptive daily slow momentum R2F."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue

            problem_id = str(problem.get("problem_id") or "")
            problem_runs = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
            ]
            if any(
                row.get("methodology_version") == V14_R2F_METHODOLOGY_VERSION
                or (
                    isinstance(row.get("result_summary"), Mapping)
                    and row.get("result_summary", {}).get("campaign_id")
                    == V14_R2F_CAMPAIGN_ID
                )
                for row in problem_runs
            ):
                continue

            matching = [
                row
                for row in problem_runs
                if row.get("methodology_version") == V14_R2E_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == "V14_R2E_BROKER_FEASIBILITY_FAIL"
                and row.get("result_summary", {}).get("decision")
                == "V14_R2E_DO_NOT_PROMOTE"
            ]
            if not matching:
                continue

            origin = max(
                matching,
                key=lambda row: str(row.get("started_at") or ""),
            )
            origin_run_id = str(origin.get("run_id") or "")
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2F_SLOW_MOMENTUM_SELECTION",
                methodology_version=V14_R2F_METHODOLOGY_VERSION,
                content={
                    **v14_r2f_campaign_manifest(),
                    "origin_campaign_id": V14_R2E_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "origin_state": "V14_R2E_BROKER_FEASIBILITY_FAIL",
                    "selection_basis": (
                        "R2E showed that 4-hour trend reduced drawdown but "
                        "failed after Alpaca turnover costs. Adaptive diagnostics "
                        "identified a fixed 180-day BTC daily time-series momentum "
                        "rule as the first low-turnover candidate to clear the "
                        "30bp broker-feasibility screen. Because 2025-2026 history "
                        "was inspected during discovery, this evidence is adaptive "
                        "only and fresh forward shadow is mandatory."
                    ),
                    "adaptive_selection_disclosed": True,
                    "fresh_confirmation_required": "FORWARD_SHADOW",
                    "frozen_before_daily_corpus_access": True,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "crypto_execution_enabled": False,
                    "live_execution_authorized": False,
                },
            )
            artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2F_STAGE,
                metadata={
                    "v14_r2f_campaign_id": V14_R2F_CAMPAIGN_ID,
                    "v14_r2f_origin_run_id": origin_run_id or None,
                    "v14_r2f_selection_artifact_id": artifact.get("artifact_id"),
                    "adaptive_selection_disclosed": True,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2f_stage_queue_failed")

            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2F_STAGE,
                "v14_r2f_campaign_id": V14_R2F_CAMPAIGN_ID,
                "adaptive_selection_disclosed": True,
                "fresh_confirmation_required": "FORWARD_SHADOW",
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2E_TO_R2F", result, flush=True)
            return result
        return None


    async def _execute_btc_slow_momentum_v14_r2f(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = (
            problem.get("metadata")
            if isinstance(problem.get("metadata"), Mapping)
            else {}
        )
        stage = str(metadata.get("research_stage") or V14_R2F_STAGE)
        if stage != V14_R2F_STAGE:
            raise RuntimeError(f"unsupported_v14_r2f_research_stage:{stage}")
        self.active_methodology_version = V14_R2F_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2F_SLOW_MOMENTUM_PRESPEC",
            methodology_version=V14_R2F_METHODOLOGY_VERSION,
            content={
                **v14_r2f_campaign_manifest(),
                "adaptive_selection_disclosed": True,
                "frozen_before_daily_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = (
            prespec_response.get("artifact")
            if isinstance(prespec_response.get("artifact"), Mapping)
            else {}
        )

        rows = await self._fetch_v14_r2f_btc_daily()
        result = await asyncio.to_thread(
            evaluate_v14_r2f_slow_momentum,
            rows,
        )
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2F_SLOW_MOMENTUM_RESULT",
            methodology_version=V14_R2F_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        artifact = (
            result_response.get("artifact")
            if isinstance(result_response.get("artifact"), Mapping)
            else {}
        )

        screen = (
            result.get("broker_feasibility_screen")
            if isinstance(result.get("broker_feasibility_screen"), Mapping)
            else {}
        )
        recent = (
            result.get("adaptive_recent_window")
            if isinstance(result.get("adaptive_recent_window"), Mapping)
            else {}
        )
        scenarios = (
            recent.get("scenarios")
            if isinstance(recent.get("scenarios"), Mapping)
            else {}
        )
        decisive = (
            scenarios.get("taker_stress_30bp")
            if isinstance(scenarios.get("taker_stress_30bp"), Mapping)
            else {}
        )
        survived = bool(screen.get("survives_to_forward_shadow"))
        spec = v14_r2f_candidate_spec().to_dict()

        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2F_ADAPTIVE_DISCOVERY_SURVIVES_TO_FORWARD_SHADOW"
                    if survived
                    else "V14_R2F_ADAPTIVE_DISCOVERY_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "BTC_DAILY_SLOW_MOMENTUM_DISCOVERY_PASS"
                    if survived
                    else "BTC_DAILY_SLOW_MOMENTUM_DISCOVERY_REJECTED"
                ),
                "decision": (
                    "START_FRESH_FORWARD_SHADOW_REQUIRED"
                    if survived
                    else "V14_R2F_DO_NOT_PROMOTE"
                ),
                "campaign_id": V14_R2F_CAMPAIGN_ID,
                "candidate_id": spec.get("candidate_id"),
                "candidate_family": V14_R2F_FAMILY,
                "candidate_spec": spec,
                "result_artifact_id": artifact.get("artifact_id"),
                "evidence_role": "ADAPTIVE_DISCOVERY_ONLY",
                "independent_historical_validation": False,
                "independent_historical_holdout": False,
                "fresh_confirmation_required": "FORWARD_SHADOW",
                "taker_stress_30bp_bar_count": decisive.get("bar_count"),
                "taker_stress_30bp_entry_count": decisive.get("entry_count"),
                "taker_stress_30bp_turnover_units": decisive.get("turnover_units"),
                "taker_stress_30bp_total_return": decisive.get("total_return"),
                "taker_stress_30bp_sharpe": decisive.get("sharpe"),
                "taker_stress_30bp_max_drawdown": decisive.get("max_drawdown"),
                "taker_stress_30bp_positive_time_quarter_share": decisive.get(
                    "positive_time_quarter_share"
                ),
                "next_action": (
                    "IMPLEMENT_AND_ACTIVATE_V14_R2F_FRESH_FORWARD_SHADOW"
                    if survived
                    else "REASSESS_VENUE_AND_STRATEGY_FAMILY"
                ),
                "shadow_only": survived,
                "promotion_eligible": False,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2c_fail_into_r2d(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance terminal R2C rejection into Alpaca triangular-arbitrage R2D."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if problem.get("status") != "WAITING" or problem.get("domain") != PROBLEM_DOMAIN:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            problem_runs = [
                row for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
            ]
            if any(
                row.get("methodology_version") == V14_R2D_METHODOLOGY_VERSION
                or (
                    isinstance(row.get("result_summary"), Mapping)
                    and row.get("result_summary", {}).get("campaign_id") == V14_R2D_CAMPAIGN_ID
                )
                for row in problem_runs
            ):
                continue
            matching = [
                row for row in problem_runs
                if row.get("methodology_version") == V14_R2C_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state") == "V14_R2C_BROKER_FEASIBILITY_FAIL"
                and row.get("result_summary", {}).get("decision") == "V14_R2C_DO_NOT_PROMOTE"
            ]
            if not matching:
                continue
            origin = max(matching, key=lambda row: str(row.get("started_at") or ""))
            origin_run_id = str(origin.get("run_id") or "")
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2D_TRIANGULAR_ARBITRAGE_SELECTION",
                methodology_version=V14_R2D_METHODOLOGY_VERSION,
                content={
                    **v14_r2d_campaign_manifest(),
                    "origin_campaign_id": V14_R2C_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "selection_basis": (
                        "R2C cross-sectional momentum failed; advance to an Alpaca-documented "
                        "BTC/USD, ETH/BTC, ETH/USD executable-quote triangular-arbitrage screen"
                    ),
                    "frozen_before_quote_corpus_access": True,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = artifact_response.get("artifact") if isinstance(artifact_response.get("artifact"), Mapping) else {}
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2D_STAGE,
                metadata={
                    "v14_r2d_campaign_id": V14_R2D_CAMPAIGN_ID,
                    "v14_r2d_origin_run_id": origin_run_id or None,
                    "v14_r2d_selection_artifact_id": artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2d_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2D_STAGE,
                "v14_r2d_campaign_id": V14_R2D_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2C_TO_R2D", result, flush=True)
            return result
        return None


    async def _execute_triangular_arbitrage_v14_r2d(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V14_R2D_STAGE)
        if stage != V14_R2D_STAGE:
            raise RuntimeError(f"unsupported_v14_r2d_research_stage:{stage}")
        self.active_methodology_version = V14_R2D_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2D_TRIANGULAR_ARBITRAGE_PRESPEC",
            methodology_version=V14_R2D_METHODOLOGY_VERSION,
            content={
                **v14_r2d_campaign_manifest(),
                "frozen_before_quote_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = prespec_response.get("artifact") if isinstance(prespec_response.get("artifact"), Mapping) else {}
        rows = await self._fetch_v14_r2d_quotes()
        result = await asyncio.to_thread(evaluate_v14_r2d_triangular_arbitrage, rows)
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2D_TRIANGULAR_ARBITRAGE_RESULT",
            methodology_version=V14_R2D_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        artifact = result_response.get("artifact") if isinstance(result_response.get("artifact"), Mapping) else {}
        gate = result.get("broker_feasibility_gate") if isinstance(result.get("broker_feasibility_gate"), Mapping) else {}
        scenarios = result.get("scenarios") if isinstance(result.get("scenarios"), Mapping) else {}
        decisive = scenarios.get("taker_75bp") if isinstance(scenarios.get("taker_75bp"), Mapping) else {}
        survived = bool(gate.get("survives_to_shadow"))
        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2D_SURVIVES_TO_TRIANGULAR_ARBITRAGE_SHADOW"
                    if survived else "V14_R2D_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "TRIANGULAR_ARBITRAGE_PREFLIGHT_PASS"
                    if survived else "TRIANGULAR_ARBITRAGE_PREFLIGHT_REJECTED"
                ),
                "decision": "CONTINUE_RESEARCH" if survived else "V14_R2D_DO_NOT_PROMOTE",
                "campaign_id": V14_R2D_CAMPAIGN_ID,
                "candidate_family": V14_R2D_FAMILY,
                "result_artifact_id": artifact.get("artifact_id"),
                "taker_75bp_snapshot_count": decisive.get("snapshot_count"),
                "taker_75bp_profitable_snapshot_count": decisive.get("profitable_snapshot_count"),
                "taker_75bp_max_net_edge_bps": decisive.get("max_net_edge_bps"),
                "taker_75bp_positive_time_quarter_share": decisive.get("positive_time_quarter_share"),
                "next_action": (
                    "START_V14_R2D_TRIANGULAR_ARBITRAGE_LIVE_QUOTE_SHADOW"
                    if survived else "ADVANCE_TO_NEXT_EXTERNAL_STRATEGY_FAMILY"
                ),
                "shadow_only": survived,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2b_fail_into_r2c(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance terminal R2B rejection into Alpaca cross-sectional momentum R2C."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if problem.get("status") != "WAITING" or problem.get("domain") != PROBLEM_DOMAIN:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            problem_runs = [
                row for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
            ]
            if any(
                row.get("methodology_version") == V14_R2C_METHODOLOGY_VERSION
                or (
                    isinstance(row.get("result_summary"), Mapping)
                    and row.get("result_summary", {}).get("campaign_id") == V14_R2C_CAMPAIGN_ID
                )
                for row in problem_runs
            ):
                continue
            matching = [
                row for row in problem_runs
                if row.get("methodology_version") == V14_R2B_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state") == "V14_R2B_BROKER_FEASIBILITY_FAIL"
                and row.get("result_summary", {}).get("decision") == "V14_R2B_DO_NOT_PROMOTE"
            ]
            if not matching:
                continue
            origin = max(matching, key=lambda row: str(row.get("started_at") or ""))
            origin_run_id = str(origin.get("run_id") or "")
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_V14_R2C_CROSS_SECTIONAL_MOMENTUM_SELECTION",
                methodology_version=V14_R2C_METHODOLOGY_VERSION,
                content={
                    **v14_r2c_campaign_manifest(),
                    "origin_campaign_id": V14_R2B_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "selection_basis": (
                        "R2B passive scalping failed; advance to Alpaca's published "
                        "long-only seven-day cross-sectional crypto momentum family"
                    ),
                    "frozen_before_bar_corpus_access": True,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = artifact_response.get("artifact") if isinstance(artifact_response.get("artifact"), Mapping) else {}
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2C_STAGE,
                metadata={
                    "v14_r2c_campaign_id": V14_R2C_CAMPAIGN_ID,
                    "v14_r2c_origin_run_id": origin_run_id or None,
                    "v14_r2c_selection_artifact_id": artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2c_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2C_STAGE,
                "v14_r2c_campaign_id": V14_R2C_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2B_TO_R2C", result, flush=True)
            return result
        return None


    async def _execute_cross_sectional_momentum_v14_r2c(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V14_R2C_STAGE)
        if stage != V14_R2C_STAGE:
            raise RuntimeError(f"unsupported_v14_r2c_research_stage:{stage}")
        self.active_methodology_version = V14_R2C_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2C_CROSS_SECTIONAL_MOMENTUM_PRESPEC",
            methodology_version=V14_R2C_METHODOLOGY_VERSION,
            content={
                **v14_r2c_campaign_manifest(),
                "frozen_before_bar_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = prespec_response.get("artifact") if isinstance(prespec_response.get("artifact"), Mapping) else {}
        rows = await self._fetch_v14_r2c_daily_crypto()
        result = await asyncio.to_thread(evaluate_v14_r2c_momentum, rows)
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_V14_R2C_CROSS_SECTIONAL_MOMENTUM_RESULT",
            methodology_version=V14_R2C_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        artifact = result_response.get("artifact") if isinstance(result_response.get("artifact"), Mapping) else {}
        gate = result.get("broker_feasibility_gate") if isinstance(result.get("broker_feasibility_gate"), Mapping) else {}
        oos = result.get("oos") if isinstance(result.get("oos"), Mapping) else {}
        scenarios = oos.get("scenarios") if isinstance(oos.get("scenarios"), Mapping) else {}
        decisive = scenarios.get("taker_switch_50bp") if isinstance(scenarios.get("taker_switch_50bp"), Mapping) else {}
        survived = bool(gate.get("survives_to_shadow"))
        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2C_SURVIVES_TO_CROSS_SECTIONAL_MOMENTUM_SHADOW"
                    if survived else "V14_R2C_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "CROSS_SECTIONAL_MOMENTUM_PREFLIGHT_PASS"
                    if survived else "CROSS_SECTIONAL_MOMENTUM_PREFLIGHT_REJECTED"
                ),
                "decision": "CONTINUE_RESEARCH" if survived else "V14_R2C_DO_NOT_PROMOTE",
                "campaign_id": V14_R2C_CAMPAIGN_ID,
                "candidate_family": V14_R2C_FAMILY,
                "result_artifact_id": artifact.get("artifact_id"),
                "taker_switch_50bp_day_count": decisive.get("day_count"),
                "taker_switch_50bp_switch_count": decisive.get("switch_count"),
                "taker_switch_50bp_sharpe": decisive.get("sharpe"),
                "taker_switch_50bp_total_return": decisive.get("total_return"),
                "positive_quarter_share": oos.get("taker_switch_50bp_positive_quarter_share"),
                "next_action": (
                    "START_V14_R2C_CROSS_SECTIONAL_MOMENTUM_SHADOW"
                    if survived else "ADVANCE_TO_V14_R2D_TRIANGULAR_ARBITRAGE"
                ),
                "shadow_only": survived,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r2a_fail_into_r2b(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance terminal R2A rejection into Alpaca passive scalping R2B."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if problem.get("status") != "WAITING" or problem.get("domain") != PROBLEM_DOMAIN:
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            problem_runs = [
                row for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
            ]
            if any(
                row.get("methodology_version") == V14_R2B_METHODOLOGY_VERSION
                or (
                    isinstance(row.get("result_summary"), Mapping)
                    and row.get("result_summary", {}).get("campaign_id") == V14_R2B_CAMPAIGN_ID
                )
                for row in problem_runs
            ):
                continue
            matching = [
                row for row in problem_runs
                if row.get("methodology_version") == V14_R2A_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state") == "V14_R2A_BROKER_FEASIBILITY_FAIL"
                and row.get("result_summary", {}).get("decision") == "V14_R2A_DO_NOT_PROMOTE"
            ]
            if not matching:
                continue
            origin = max(matching, key=lambda row: str(row.get("started_at") or ""))
            origin_run_id = str(origin.get("run_id") or "")
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_BTC_V14_R2B_PASSIVE_SCALPING_SELECTION",
                methodology_version=V14_R2B_METHODOLOGY_VERSION,
                content={
                    **v14_r2b_campaign_manifest(),
                    "origin_campaign_id": V14_R2A_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "selection_basis": (
                        "R2A top-of-book imbalance failed; advance to the official "
                        "Alpaca passive range-scalping family with conservative "
                        "chronological fill simulation"
                    ),
                    "frozen_before_bar_corpus_access": True,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = artifact_response.get("artifact") if isinstance(artifact_response.get("artifact"), Mapping) else {}
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2B_STAGE,
                metadata={
                    "v14_r2b_campaign_id": V14_R2B_CAMPAIGN_ID,
                    "v14_r2b_origin_run_id": origin_run_id or None,
                    "v14_r2b_selection_artifact_id": artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2b_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2B_STAGE,
                "v14_r2b_campaign_id": V14_R2B_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R2A_TO_R2B", result, flush=True)
            return result
        return None


    async def _execute_btc_passive_scalping_v14_r2b(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        stage = str(metadata.get("research_stage") or V14_R2B_STAGE)
        if stage != V14_R2B_STAGE:
            raise RuntimeError(f"unsupported_v14_r2b_research_stage:{stage}")
        self.active_methodology_version = V14_R2B_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_BTC_V14_R2B_PASSIVE_SCALPING_PRESPEC",
            methodology_version=V14_R2B_METHODOLOGY_VERSION,
            content={
                **v14_r2b_campaign_manifest(),
                "frozen_before_bar_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = prespec_response.get("artifact") if isinstance(prespec_response.get("artifact"), Mapping) else {}
        rows = await self._fetch_v14_r2b_minute_btc()
        result = await asyncio.to_thread(evaluate_v14_r2b_passive_scalping, rows)
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_BTC_V14_R2B_PASSIVE_SCALPING_RESULT",
            methodology_version=V14_R2B_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        artifact = result_response.get("artifact") if isinstance(result_response.get("artifact"), Mapping) else {}
        gate = result.get("broker_feasibility_gate") if isinstance(result.get("broker_feasibility_gate"), Mapping) else {}
        oos = result.get("oos") if isinstance(result.get("oos"), Mapping) else {}
        scenarios = oos.get("scenarios") if isinstance(oos.get("scenarios"), Mapping) else {}
        decisive = scenarios.get("taker_stress_50bp") if isinstance(scenarios.get("taker_stress_50bp"), Mapping) else {}
        survived = bool(gate.get("survives_to_shadow"))
        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2B_SURVIVES_TO_PASSIVE_SCALPING_SHADOW"
                    if survived else "V14_R2B_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "PASSIVE_SCALPING_PREFLIGHT_PASS"
                    if survived else "PASSIVE_SCALPING_PREFLIGHT_REJECTED"
                ),
                "decision": "CONTINUE_RESEARCH" if survived else "V14_R2B_DO_NOT_PROMOTE",
                "campaign_id": V14_R2B_CAMPAIGN_ID,
                "candidate_family": V14_R2B_FAMILY,
                "result_artifact_id": artifact.get("artifact_id"),
                "selected_config": oos.get("config") if isinstance(oos.get("config"), Mapping) else None,
                "taker_stress_50bp_trade_count": decisive.get("trade_count"),
                "taker_stress_50bp_sharpe": decisive.get("sharpe"),
                "taker_stress_50bp_total_return": decisive.get("total_return"),
                "positive_quarter_share": oos.get("taker_stress_50bp_positive_quarter_share"),
                "next_action": (
                    "START_V14_R2B_PASSIVE_SCALPING_SHADOW"
                    if survived else "ADVANCE_TO_V14_R2C_CROSS_SECTIONAL_MOMENTUM"
                ),
                "shadow_only": survived,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v14_r1_fail_into_r2a(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance the terminal V14-R1 rejection into the frozen R2A preflight."""
        problems = snapshot.get("problems") or []
        runs = snapshot.get("runs") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            problem_runs = [
                row
                for row in runs
                if isinstance(row, Mapping)
                and str(row.get("problem_id") or "") == problem_id
            ]
            if any(
                row.get("methodology_version") == V14_R2A_METHODOLOGY_VERSION
                or (
                    isinstance(row.get("result_summary"), Mapping)
                    and row.get("result_summary", {}).get("campaign_id") == V14_R2A_CAMPAIGN_ID
                )
                for row in problem_runs
            ):
                continue
            matching = [
                row
                for row in problem_runs
                if row.get("methodology_version") == V14_METHODOLOGY_VERSION
                and isinstance(row.get("result_summary"), Mapping)
                and row.get("result_summary", {}).get("state")
                == "V14_R1_BROKER_FEASIBILITY_FAIL"
                and row.get("result_summary", {}).get("decision")
                == "V14_R1_DO_NOT_REPLICATE_FURTHER"
            ]
            if not matching:
                continue
            origin = max(matching, key=lambda row: str(row.get("started_at") or ""))
            origin_run_id = str(origin.get("run_id") or "")
            artifact_response = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=origin_run_id or None,
                artifact_type="CRYPTO_BTC_V14_R2A_QUEUE_IMBALANCE_SELECTION",
                methodology_version=V14_R2A_METHODOLOGY_VERSION,
                content={
                    **v14_r2a_campaign_manifest(),
                    "origin_campaign_id": V14_CAMPAIGN_ID,
                    "origin_run_id": origin_run_id or None,
                    "selection_basis": (
                        "V14-R1 failed realistic broker-cost feasibility; "
                        "advance to Alpaca-native top-of-book queue imbalance "
                        "using historical quotes before any live L2 shadow test"
                    ),
                    "frozen_before_quote_corpus_access": True,
                    "source_commit": _source_commit(),
                    "deployment_id": _deployment_id(),
                },
            )
            artifact = (
                artifact_response.get("artifact")
                if isinstance(artifact_response.get("artifact"), Mapping)
                else {}
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V14_R2A_STAGE,
                metadata={
                    "v14_r2a_campaign_id": V14_R2A_CAMPAIGN_ID,
                    "v14_r2a_origin_run_id": origin_run_id or None,
                    "v14_r2a_selection_artifact_id": artifact.get("artifact_id"),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v14_r2a_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": V14_R2A_STAGE,
                "v14_r2a_campaign_id": V14_R2A_CAMPAIGN_ID,
                "execution_authority": False,
                "broker_orders_possible": False,
            }
            print("GRAEN_V14_R1_TO_R2A", result, flush=True)
            return result
        return None


    async def _execute_btc_queue_imbalance_v14_r2a(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        metadata = (
            problem.get("metadata")
            if isinstance(problem.get("metadata"), Mapping)
            else {}
        )
        stage = str(metadata.get("research_stage") or V14_R2A_STAGE)
        if stage != V14_R2A_STAGE:
            raise RuntimeError(f"unsupported_v14_r2a_research_stage:{stage}")
        self.active_methodology_version = V14_R2A_METHODOLOGY_VERSION

        prespec_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_BTC_V14_R2A_QUEUE_IMBALANCE_PRESPEC",
            methodology_version=V14_R2A_METHODOLOGY_VERSION,
            content={
                **v14_r2a_campaign_manifest(),
                "frozen_before_quote_corpus_access": True,
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        prespec_artifact = (
            prespec_response.get("artifact")
            if isinstance(prespec_response.get("artifact"), Mapping)
            else {}
        )
        rows = await self._fetch_v14_r2a_quotes()
        result = await asyncio.to_thread(evaluate_v14_r2a_queue_imbalance, rows)
        result_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_BTC_V14_R2A_QUEUE_IMBALANCE_RESULT",
            methodology_version=V14_R2A_METHODOLOGY_VERSION,
            content={
                **result,
                "prespec_artifact_id": prespec_artifact.get("artifact_id"),
                "source_commit": _source_commit(),
                "deployment_id": _deployment_id(),
            },
        )
        artifact = (
            result_response.get("artifact")
            if isinstance(result_response.get("artifact"), Mapping)
            else {}
        )
        gate = (
            result.get("broker_feasibility_gate")
            if isinstance(result.get("broker_feasibility_gate"), Mapping)
            else {}
        )
        oos = result.get("oos") if isinstance(result.get("oos"), Mapping) else {}
        scenarios = (
            oos.get("scenarios")
            if isinstance(oos.get("scenarios"), Mapping)
            else {}
        )
        decisive = (
            scenarios.get("taker_base_50bp")
            if isinstance(scenarios.get("taker_base_50bp"), Mapping)
            else {}
        )
        survived = bool(gate.get("survives_to_live_l2_shadow"))
        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": (
                    "V14_R2A_SURVIVES_TO_LIVE_L2_SHADOW"
                    if survived
                    else "V14_R2A_BROKER_FEASIBILITY_FAIL"
                ),
                "status": (
                    "QUEUE_IMBALANCE_PREFLIGHT_PASS"
                    if survived
                    else "QUEUE_IMBALANCE_PREFLIGHT_REJECTED"
                ),
                "decision": (
                    "CONTINUE_RESEARCH"
                    if survived
                    else "V14_R2A_DO_NOT_PROMOTE"
                ),
                "campaign_id": V14_R2A_CAMPAIGN_ID,
                "candidate_family": V14_R2A_FAMILY,
                "result_artifact_id": artifact.get("artifact_id"),
                "selected_config": (
                    oos.get("config")
                    if isinstance(oos.get("config"), Mapping)
                    else None
                ),
                "taker_base_50bp_trade_count": decisive.get("trade_count"),
                "taker_base_50bp_sharpe": decisive.get("sharpe"),
                "taker_base_50bp_total_return": decisive.get("total_return"),
                "positive_quarter_share": oos.get(
                    "taker_base_50bp_positive_quarter_share"
                ),
                "development_opened": False,
                "validation_opened": False,
                "holdout_opened": False,
                "next_action": (
                    "START_V14_R2A_LIVE_L2_SHADOW_CONFIRMATION"
                    if survived
                    else "ADVANCE_TO_NEXT_EXTERNAL_ALPACA_CRYPTO_METHOD"
                ),
                "shadow_only": survived,
                "execution_authority": False,
                "broker_orders_possible": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )


    async def _recover_v13_into_research_director(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Queue the model-driven research decision only when explicitly enabled."""
        if not self.research_director_autorun:
            return None
        if not self.research_director.configured:
            return None

        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage") == RESEARCH_DIRECTOR_STAGE:
                return None

        latest_by_problem: dict[str, Mapping[str, Any]] = {}
        for run in snapshot.get("runs") or []:
            if not isinstance(run, Mapping):
                continue
            problem_id = str(run.get("problem_id") or "")
            if problem_id and problem_id not in latest_by_problem:
                latest_by_problem[problem_id] = run

        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            metadata = (
                problem.get("metadata")
                if isinstance(problem.get("metadata"), Mapping)
                else {}
            )
            if metadata.get("research_stage"):
                continue
            problem_id = str(problem.get("problem_id") or "")
            latest = latest_by_problem.get(problem_id)
            if not isinstance(latest, Mapping):
                continue
            summary = (
                latest.get("result_summary")
                if isinstance(latest.get("result_summary"), Mapping)
                else {}
            )
            if not (
                summary.get("campaign_id") == V13_CAMPAIGN_ID
                and summary.get("state") == "V13_NO_DEVELOPMENT_SURVIVOR"
                and summary.get("decision") == "V13_HYPOTHESES_FALSIFIED"
            ):
                continue
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=RESEARCH_DIRECTOR_STAGE,
                metadata={
                    "research_director_version": RESEARCH_DIRECTOR_METHODOLOGY,
                    "research_director_origin_run_id": latest.get("run_id"),
                    "research_director_origin_campaign": V13_CAMPAIGN_ID,
                    "research_director_cycle": 1,
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("research_director_stage_queue_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "next_research_stage": RESEARCH_DIRECTOR_STAGE,
                "execution_authority": False,
            }
            print("GRAEN_V13_TO_RESEARCH_DIRECTOR", result, flush=True)
            return result
        return None

    async def _execute_research_director(
        self,
        problem: Mapping[str, Any],
        run: Mapping[str, Any],
    ) -> dict[str, Any]:
        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        self.active_methodology_version = RESEARCH_DIRECTOR_METHODOLOGY

        snapshot = await self.gateway.snapshot()
        evidence = self._research_director_evidence_packet(snapshot, problem)
        objective = (
            "Identify the highest-value next BTC research program after V13. "
            "Prefer faithful replication of a credible existing quantitative, AI, "
            "machine-learning, statistical-arbitrage, market-microstructure, or "
            "execution methodology over another arbitrary internally invented indicator. "
            "Evaluate positive and negative evidence, reproducibility, transaction costs, "
            "data availability, BTC transferability, and whether the method requires a new "
            "trusted compiler. Do not authorize live trading."
        )
        response = await self.research_director.research(
            objective=objective,
            canonical_evidence=evidence,
        )
        decision = response.get("decision")
        if not isinstance(decision, Mapping):
            raise RuntimeError("research_director_decision_missing")
        usage = (
            dict(response.get("usage"))
            if isinstance(response.get("usage"), Mapping)
            else {"invoked": True}
        )
        artifact_response = await self.gateway.record_artifact(
            problem_id=problem_id,
            run_id=run_id,
            artifact_type="CRYPTO_RESEARCH_DIRECTOR_DECISION",
            methodology_version=RESEARCH_DIRECTOR_METHODOLOGY,
            content={
                "schema_version": "graen.research-director-artifact.v1",
                "decision": dict(decision),
                "retrieved_sources": list(response.get("retrieved_sources") or []),
                "web_search_calls": int(response.get("web_search_calls") or 0),
                "model_response_id": response.get("response_id"),
                "origin_campaign": V13_CAMPAIGN_ID,
                "origin_state": "V13_NO_DEVELOPMENT_SURVIVOR",
                "research_only": True,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_promotion_authority": False,
                "crypto_execution_enabled": False,
                "live_execution_authorized": False,
            },
        )
        artifact = (
            artifact_response.get("artifact")
            if isinstance(artifact_response.get("artifact"), Mapping)
            else {}
        )
        artifact_id = artifact.get("artifact_id")

        implementation_class = str(decision.get("implementation_class") or "")
        action = str(decision.get("action") or "")
        next_action = str(decision.get("next_action") or "")
        method = (
            decision.get("selected_method")
            if isinstance(decision.get("selected_method"), Mapping)
            else {}
        )

        state = "RESEARCH_DIRECTOR_NO_ACTION"
        if implementation_class == "TRUSTED_COMPILER_EXTENSION_REQUIRED":
            state = "RESEARCH_DIRECTOR_COMPILER_EXTENSION_REQUIRED"
        elif implementation_class == "DATA_COLLECTION_REQUIRED":
            state = "RESEARCH_DIRECTOR_DATA_COLLECTION_REQUIRED"
        elif implementation_class == "EXISTING_TRUSTED_COMPILER":
            state = "RESEARCH_DIRECTOR_TRUSTED_COMPILER_TARGET_SELECTED"
        elif action == "REPLICATE_EXTERNAL_METHOD":
            state = "RESEARCH_DIRECTOR_REPLICATION_TARGET_SELECTED"

        return await self._finalize(
            problem=problem,
            run=run,
            status="WAITING",
            summary={
                "state": state,
                "status": "RESEARCH_DECISION_COMPLETE",
                "decision": "RESEARCH_BLUEPRINT_READY",
                "campaign_id": "graen-research-director-v1",
                "selected_method": method.get("name"),
                "selected_method_category": method.get("category"),
                "implementation_class": implementation_class,
                "research_action": action,
                "research_decision_artifact_id": artifact_id,
                "web_search_calls": int(response.get("web_search_calls") or 0),
                "next_action": next_action,
                "live_execution_authorized": False,
                "execution_authority": False,
                "broker_orders_possible": False,
                "production_state_changed": False,
            },
            model_usage=usage,
        )

    async def _recover_exhausted_v12_into_v13(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Advance the canonical V12 no-survivor result into the frozen V13 tournament."""
        problems = snapshot.get("problems") or []
        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            if metadata.get("v13_campaign_id") == V13_CAMPAIGN_ID:
                return None
            if metadata.get("research_stage") in V13_STAGE_KEYS:
                return None

        for problem in problems:
            if not isinstance(problem, Mapping):
                continue
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
            ):
                continue
            problem_id = str(problem.get("problem_id") or "")
            terminal_run: Mapping[str, Any] | None = None
            for run in snapshot.get("runs") or []:
                if not isinstance(run, Mapping):
                    continue
                if str(run.get("problem_id") or "") != problem_id:
                    continue
                summary = run.get("result_summary") if isinstance(run.get("result_summary"), Mapping) else {}
                if (
                    summary.get("campaign_id") == V12_CAMPAIGN_ID
                    and summary.get("state") == "V12_NO_DEVELOPMENT_SURVIVOR"
                    and summary.get("decision") == "NEEDS_NEW_HYPOTHESIS_ENGINE"
                ):
                    terminal_run = run
                    break
            if terminal_run is None:
                continue
            run_id = str(terminal_run.get("run_id") or "")
            artifact = await self.gateway.record_artifact(
                problem_id=problem_id,
                run_id=run_id or None,
                artifact_type="CRYPTO_BTC_V12_EXHAUSTION",
                methodology_version=V12_METHODOLOGY_VERSION,
                content={
                    "campaign_id": V12_CAMPAIGN_ID,
                    "state": "V12_CAMPAIGN_EXHAUSTED",
                    "reason": "NO_DEVELOPMENT_SURVIVOR",
                    "next_methodology": V13_METHODOLOGY_VERSION,
                    "next_campaign_id": V13_CAMPAIGN_ID,
                    "historical_promotion_eligible": False,
                    "execution_authority": False,
                    "broker_orders_possible": False,
                    "production_promotion_authority": False,
                },
            )
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=V13_DEVELOPMENT_STAGE,
                metadata={
                    "v13_campaign_id": V13_CAMPAIGN_ID,
                    "v13_origin": "v12_no_development_survivor",
                    "v12_terminal_run_id": run_id or None,
                    "v12_terminal_artifact_id": (
                        (artifact.get("artifact") or {}).get("artifact_id")
                        if isinstance(artifact.get("artifact"), Mapping)
                        else None
                    ),
                },
            )
            if not queued.get("problem"):
                raise RuntimeError("v13_queue_after_v12_exhaustion_failed")
            result = {
                "recovered": True,
                "problem_id": problem_id,
                "state": "V12_CAMPAIGN_EXHAUSTED",
                "next_research_stage": V13_DEVELOPMENT_STAGE,
                "v13_campaign_id": V13_CAMPAIGN_ID,
                "execution_authority": False,
            }
            print("GRAEN_V12_TO_V13", result, flush=True)
            return result
        return None

    async def _reconcile_v12_native_stage_precedence(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Clear stale generic code-promotion state from an already-staged v12 problem."""
        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            promotion = metadata.get("code_promotion")
            if (
                problem.get("status") != "WAITING"
                or problem.get("domain") != PROBLEM_DOMAIN
                or stage not in V12_STAGE_KEYS
                or not isinstance(promotion, Mapping)
                or promotion.get("phase") == "COMPLETE"
            ):
                continue
            problem_id = str(problem.get("problem_id") or "")
            queued = await self.gateway.queue_research_stage(
                problem_id=problem_id,
                stage=stage,
                metadata={
                    "v12_campaign_id": metadata.get("v12_campaign_id") or V12_CAMPAIGN_ID,
                    "v12_native_stage_precedence_reconciled": True,
                },
            )
            queued_problem = queued.get("problem")
            queued_metadata = (
                queued_problem.get("metadata")
                if isinstance(queued_problem, Mapping)
                and isinstance(queued_problem.get("metadata"), Mapping)
                else {}
            )
            cleared = not isinstance(queued_metadata.get("code_promotion"), Mapping)
            result = {
                "problem_id": problem_id,
                "research_stage": stage,
                "stale_code_promotion_phase": promotion.get("phase"),
                "cleared": cleared,
                "execution_authority": False,
            }
            print("GRAEN_V12_NATIVE_STAGE_PRECEDENCE", result, flush=True)
            return result
        return None

    def _observe_v13_claimability(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Log the fail-closed Foundation claim predicate for the canonical V13 problem."""
        runs = snapshot.get("runs") or []
        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if metadata.get("v13_campaign_id") != V13_CAMPAIGN_ID and stage not in V13_STAGE_KEYS:
                continue

            problem_id = str(problem.get("problem_id") or "")
            latest_run: Mapping[str, Any] | None = None
            for run in runs:
                if isinstance(run, Mapping) and str(run.get("problem_id") or "") == problem_id:
                    latest_run = run
                    break
            latest_summary = (
                latest_run.get("result_summary")
                if isinstance(latest_run, Mapping)
                and isinstance(latest_run.get("result_summary"), Mapping)
                else {}
            )
            code_promotion = (
                metadata.get("code_promotion")
                if isinstance(metadata.get("code_promotion"), Mapping)
                else None
            )
            code_promotion_phase = (
                str(code_promotion.get("phase") or "")
                if isinstance(code_promotion, Mapping)
                else None
            )
            status_waiting = problem.get("status") == "WAITING"
            domain_match = problem.get("domain") == PROBLEM_DOMAIN
            stage_recognized = stage in V13_STAGE_KEYS or stage in V14_STAGE_KEYS
            code_promotion_allows = (
                code_promotion is None or code_promotion_phase == "COMPLETE"
            )
            diagnostic = {
                "problem_id": problem_id,
                "status": problem.get("status"),
                "priority": problem.get("priority"),
                "domain": problem.get("domain"),
                "research_stage": stage or None,
                "v13_campaign_id": metadata.get("v13_campaign_id"),
                "v14_campaign_id": metadata.get("v14_campaign_id"),
                "code_promotion_phase": code_promotion_phase,
                "latest_run_status": latest_run.get("status") if isinstance(latest_run, Mapping) else None,
                "latest_run_methodology_version": (
                    latest_run.get("methodology_version")
                    if isinstance(latest_run, Mapping)
                    else None
                ),
                "latest_run_state": latest_summary.get("state"),
                "claim_predicate": {
                    "status_waiting": status_waiting,
                    "domain_match": domain_match,
                    "stage_recognized": stage_recognized,
                    "code_promotion_allows": code_promotion_allows,
                    "eligible": bool(
                        status_waiting
                        and domain_match
                        and stage_recognized
                        and code_promotion_allows
                    ),
                },
                "execution_authority": False,
            }
            signature = (
                problem_id,
                problem.get("status"),
                stage,
                code_promotion_phase,
                diagnostic["latest_run_status"],
                diagnostic["latest_run_methodology_version"],
                diagnostic["latest_run_state"],
            )
            if signature != self.last_v13_claim_diagnostic_signature:
                self.last_v13_claim_diagnostic_signature = signature
                print("GRAEN_V13_CLAIM_DIAGNOSTIC", diagnostic, flush=True)
            return diagnostic
        return None

    def _observe_v12_claimability(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Log the exact fail-closed claim predicate for the canonical v12 problem."""
        runs = snapshot.get("runs") or []
        for problem in snapshot.get("problems") or []:
            if not isinstance(problem, Mapping):
                continue
            metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if metadata.get("v13_campaign_id") == V13_CAMPAIGN_ID or stage in V13_STAGE_KEYS:
                continue
            if metadata.get("v12_campaign_id") != V12_CAMPAIGN_ID and stage not in V12_STAGE_KEYS:
                continue

            problem_id = str(problem.get("problem_id") or "")
            latest_run: Mapping[str, Any] | None = None
            for run in runs:
                if isinstance(run, Mapping) and str(run.get("problem_id") or "") == problem_id:
                    latest_run = run
                    break
            latest_summary = (
                latest_run.get("result_summary")
                if isinstance(latest_run, Mapping)
                and isinstance(latest_run.get("result_summary"), Mapping)
                else {}
            )
            code_promotion = (
                metadata.get("code_promotion")
                if isinstance(metadata.get("code_promotion"), Mapping)
                else None
            )
            code_promotion_phase = (
                str(code_promotion.get("phase") or "")
                if isinstance(code_promotion, Mapping)
                else None
            )
            status_waiting = problem.get("status") == "WAITING"
            domain_match = problem.get("domain") == PROBLEM_DOMAIN
            stage_recognized = stage in V12_STAGE_KEYS
            code_promotion_allows = (
                code_promotion is None or code_promotion_phase == "COMPLETE"
            )
            diagnostic = {
                "problem_id": problem_id,
                "status": problem.get("status"),
                "priority": problem.get("priority"),
                "domain": problem.get("domain"),
                "research_stage": stage or None,
                "v12_campaign_id": metadata.get("v12_campaign_id"),
                "code_promotion_phase": code_promotion_phase,
                "latest_run_status": latest_run.get("status") if isinstance(latest_run, Mapping) else None,
                "latest_run_methodology_version": (
                    latest_run.get("methodology_version")
                    if isinstance(latest_run, Mapping)
                    else None
                ),
                "latest_run_state": latest_summary.get("state"),
                "claim_predicate": {
                    "status_waiting": status_waiting,
                    "domain_match": domain_match,
                    "stage_recognized": stage_recognized,
                    "code_promotion_allows": code_promotion_allows,
                    "eligible": bool(
                        status_waiting
                        and domain_match
                        and stage_recognized
                        and code_promotion_allows
                    ),
                },
                "execution_authority": False,
            }
            signature = (
                problem_id,
                problem.get("status"),
                stage,
                code_promotion_phase,
                diagnostic["latest_run_status"],
                diagnostic["latest_run_methodology_version"],
                diagnostic["latest_run_state"],
            )
            if signature != self.last_v12_claim_diagnostic_signature:
                self.last_v12_claim_diagnostic_signature = signature
                print("GRAEN_V12_CLAIM_DIAGNOSTIC", diagnostic, flush=True)
            return diagnostic
        return None

    async def process_once(self) -> dict[str, Any]:
        snapshot = await self.gateway.snapshot()

        # Dependency waits are durable problem states. Re-arm the frozen stage
        # only when its declared eligibility time arrives; otherwise report the
        # wait as productive state instead of spinning the executor.
        now = datetime.now(UTC)
        waiting_until: list[datetime] = []
        expired_waits: list[tuple[str, str]] = []
        engineering_retries: list[tuple[str, str]] = []
        engineering_required = 0
        current_source_commit = str(_source_commit() or "")
        for row in snapshot.get("problems") or []:
            if not isinstance(row, Mapping) or row.get("status") != "WAITING":
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            stage = str(metadata.get("research_stage") or "")
            if stage == ENGINEERING_REQUIRED_STAGE:
                engineering_required += 1
                required_on_commit = str(
                    metadata.get("engineering_required_source_commit") or ""
                )
                resume_stage = str(
                    metadata.get("resume_stage") or HYPOTHESIS_PLANNER_STAGE
                )
                if (
                    current_source_commit
                    and required_on_commit
                    and current_source_commit != required_on_commit
                ):
                    engineering_retries.append(
                        (str(row.get("problem_id")), resume_stage)
                    )
                continue
            if stage != WAITING_CORPUS_STAGE:
                continue
            resume_stage = str(metadata.get("resume_stage") or HYPOTHESIS_PLANNER_STAGE)
            raw = str(metadata.get("next_eligible_at") or "")
            try:
                eligible = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if eligible.tzinfo is None:
                    eligible = eligible.replace(tzinfo=UTC)
                eligible = eligible.astimezone(UTC)
            except ValueError:
                eligible = now
            if eligible <= now:
                expired_waits.append((str(row.get("problem_id")), resume_stage))
            else:
                waiting_until.append(eligible)

        self.engineering_required_count = engineering_required
        self.waiting_dependency_until = min(waiting_until) if waiting_until else None
        for engineering_problem_id, resume_stage in engineering_retries[:4]:
            await self.gateway.queue_research_stage(
                problem_id=engineering_problem_id,
                stage=resume_stage,
                metadata={
                    "autonomous_continuation": True,
                    "engineering_dependency_released_at": now.isoformat(),
                    "engineering_resumed_on_source_commit": current_source_commit,
                },
            )
        if engineering_retries:
            snapshot = await self.gateway.snapshot()
            self.engineering_required_count = max(
                0,
                engineering_required - len(engineering_retries[:4]),
            )
        for waiting_problem_id, resume_stage in expired_waits[:4]:
            await self.gateway.queue_research_stage(
                problem_id=waiting_problem_id,
                stage=resume_stage,
                metadata={
                    "autonomous_continuation": True,
                    "dependency_wait_released_at": now.isoformat(),
                },
            )
        if expired_waits:
            snapshot = await self.gateway.snapshot()
            self.waiting_dependency_until = None
        r2h_velum_transport_recovery = (
            await self._recover_blocked_r2h_velum_transport(snapshot)
        )
        if (
            r2h_velum_transport_recovery is not None
            and r2h_velum_transport_recovery.get("recovered")
        ):
            snapshot = await self.gateway.snapshot()
        blocked_v10 = self._observe_blocked_v10(snapshot)
        v10_corpus_recovery = await self._recover_blocked_v10_corpus_into_v11(snapshot)
        if v10_corpus_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v11_to_v12_recovery = await self._recover_exhausted_v11_into_v12(snapshot)
        if v11_to_v12_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v13_interrupted_stage_recovery = await self._recover_orphaned_v13_nonconfirmatory_claim(snapshot)
        if v13_interrupted_stage_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v12_to_v13_recovery = await self._recover_exhausted_v12_into_v13(snapshot)
        if v12_to_v13_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_dependency_recovery = await self._recover_blocked_v14_runtime_dependency(snapshot)
        if v14_dependency_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_feature_recovery = await self._recover_blocked_v14_feature_pipeline(snapshot)
        if v14_feature_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_pagination_recovery = await self._recover_blocked_v14_pagination(snapshot)
        if v14_pagination_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v13_to_v14_recovery = await self._recover_v13_into_v14(snapshot)
        if v13_to_v14_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2e_pagination_recovery = await self._recover_blocked_v14_r2e_pagination(snapshot)
        if v14_r2e_pagination_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2e_to_r2f_recovery = await self._recover_v14_r2e_fail_into_r2f(snapshot)
        if v14_r2e_to_r2f_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2f_shadow_recovery = await self._recover_v14_r2f_pass_into_forward_shadow(snapshot)
        if v14_r2f_shadow_recovery is not None and v14_r2f_shadow_recovery.get("recovered"):
            snapshot = await self.gateway.snapshot()
        v14_r2f_to_r2g_recovery = await self._recover_v14_r2f_shadow_into_r2g(snapshot)
        if v14_r2f_to_r2g_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2g_shadow_recovery = (
            await self._recover_v14_r2g_pass_into_forward_shadow_comparison(
                snapshot
            )
        )
        if (
            v14_r2g_shadow_recovery is not None
            and v14_r2g_shadow_recovery.get("recovered")
        ):
            snapshot = await self.gateway.snapshot()
        v14_r2h_faststart_recovery = (
            await self._recover_v14_r2g_pass_into_r2h_faststart(snapshot)
        )
        if v14_r2h_faststart_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2h_velum_recovery = (
            await self._recover_v14_r2h_pass_into_velum(snapshot)
        )
        if v14_r2h_velum_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2d_to_r2e_recovery = await self._recover_v14_r2d_fail_into_r2e(snapshot)
        if v14_r2d_to_r2e_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2c_to_r2d_recovery = await self._recover_v14_r2c_fail_into_r2d(snapshot)
        if v14_r2c_to_r2d_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2b_to_r2c_recovery = await self._recover_v14_r2b_fail_into_r2c(snapshot)
        if v14_r2b_to_r2c_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r2a_to_r2b_recovery = await self._recover_v14_r2a_fail_into_r2b(snapshot)
        if v14_r2a_to_r2b_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v14_r1_to_r2a_recovery = await self._recover_v14_r1_fail_into_r2a(snapshot)
        if v14_r1_to_r2a_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v13_to_director_recovery = await self._recover_v13_into_research_director(snapshot)
        if v13_to_director_recovery is not None:
            snapshot = await self.gateway.snapshot()
        v12_native_stage_precedence = await self._reconcile_v12_native_stage_precedence(snapshot)
        if v12_native_stage_precedence is not None:
            snapshot = await self.gateway.snapshot()
        v12_claim_diagnostic = self._observe_v12_claimability(snapshot)
        v13_claim_diagnostic = self._observe_v13_claimability(snapshot)
        reconciliation = await self._reconcile_orphaned_confirmatory_claim(snapshot)
        if reconciliation is not None:
            snapshot = await self.gateway.snapshot()
        v10_transition_recovery = await self._recover_exhausted_v9_into_v10(snapshot)
        if v10_transition_recovery is not None:
            snapshot = await self.gateway.snapshot()

        autonomous_loop_seed = await self._ensure_autonomous_loop_seed(snapshot)
        if autonomous_loop_seed is not None:
            snapshot = await self.gateway.snapshot()

        v10_campaign_seed = None
        if self.legacy_campaign_bootstrap:
            v10_campaign_seed = await self._ensure_v10_campaign_seed(snapshot)
            if v10_campaign_seed is not None:
                snapshot = await self.gateway.snapshot()
        # Research-code promotion is an explicit bounded step in the same
        # deterministic executor loop. Process eligible handoffs without
        # starving unrelated staged research.
        promotion_results: list[dict[str, Any]] = []
        for promotion_problem_id in list(engineering_problem_ids(snapshot))[:4]:
            promotion_problem = next(
                (
                    row
                    for row in (snapshot.get("problems") or [])
                    if str(row.get("problem_id")) == str(promotion_problem_id)
                ),
                {},
            )
            promotion_state = await self.research_promotion.tick(promotion_problem_id)
            promotion_results.append({
                "problem_id": promotion_problem_id,
                "phase": promotion_state.get("phase"),
                "blocked_reason": promotion_state.get("blocked_reason"),
            })
            if promotion_state.get("transitioned") is True:
                linked_job_id = (
                    str(promotion_problem.get("linked_iren_job_id"))
                    if promotion_problem.get("linked_iren_job_id")
                    else None
                )
                requirement = promotion_state.get("engineering_requirement")
                if (
                    promotion_state.get("phase") == "ENGINEERING_REQUIRED"
                    and isinstance(requirement, Mapping)
                ):
                    await self._callback_iren(
                        linked_job_id,
                        status="WAITING",
                        result={
                            "condition": "ENGINEERING_REQUIRED",
                            "graen_problem_id": str(promotion_problem_id),
                            "engineering_requirement": dict(requirement),
                            "manual_chatgpt_workspace_required": True,
                            "independent_research_continues": True,
                            "execution_authority": False,
                            "runtime_code_mutation_authorized": False,
                        },
                    )
                elif promotion_state.get("phase") == "COMPLETE":
                    await self._callback_iren(
                        linked_job_id,
                        status="SUCCEEDED",
                        result={
                            "condition": "RESEARCHING",
                            "graen_problem_id": str(promotion_problem_id),
                            "engineering_requirement_resolved": True,
                            "resume_stage": promotion_state.get("resume_stage"),
                            "manual_resolution": promotion_state.get(
                                "manual_resolution"
                            ),
                            "execution_authority": False,
                        },
                    )
        staged_strategy = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in STRATEGY_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2h = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage")
            in {V14_R2H_STAGE, V14_R2H_VELUM_STAGE}
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2g = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2G_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2f = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2F_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2e = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2E_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2d = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2D_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2c = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2C_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2b = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2B_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14_r2a = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == V14_R2A_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v14 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V14_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_director = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == RESEARCH_DIRECTOR_STAGE
            for row in (snapshot.get("problems") or [])
        )
        staged_v13 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V13_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v12 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V12_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v11 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V11_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v10 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V10_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_v9 = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in V9_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_autonomous = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") in AUTONOMOUS_STAGE_KEYS
            for row in (snapshot.get("problems") or [])
        )
        staged_leadlag = any(
            isinstance(row, Mapping)
            and row.get("status") == "WAITING"
            and row.get("domain") == PROBLEM_DOMAIN
            and isinstance(row.get("metadata"), Mapping)
            and row.get("metadata", {}).get("research_stage") == LEADLAG_STAGE_KEY
            for row in (snapshot.get("problems") or [])
        )
        self.active_methodology_version = (
            STRATEGY_RUNNER_VERSION
            if staged_strategy
            else V14_R2H_METHODOLOGY_VERSION
            if staged_v14_r2h
            else V14_R2G_METHODOLOGY_VERSION
            if staged_v14_r2g
            else V14_R2F_METHODOLOGY_VERSION
            if staged_v14_r2f
            else V14_R2E_METHODOLOGY_VERSION
            if staged_v14_r2e
            else V14_R2D_METHODOLOGY_VERSION
            if staged_v14_r2d
            else V14_R2C_METHODOLOGY_VERSION
            if staged_v14_r2c
            else V14_R2B_METHODOLOGY_VERSION
            if staged_v14_r2b
            else V14_R2A_METHODOLOGY_VERSION
            if staged_v14_r2a
            else V14_METHODOLOGY_VERSION
            if staged_v14
            else RESEARCH_DIRECTOR_METHODOLOGY
            if staged_director
            else V13_METHODOLOGY_VERSION
            if staged_v13
            else V12_METHODOLOGY_VERSION
            if staged_v12
            else V11_METHODOLOGY_VERSION
            if staged_v11
            else V10_METHODOLOGY_VERSION
            if staged_v10
            else V9_METHODOLOGY_VERSION
            if staged_v9
            else AUTONOMOUS_METHODOLOGY_PREFIX
            if staged_autonomous
            else LEADLAG_METHODOLOGY_VERSION
            if staged_leadlag
            else V7_METHODOLOGY_VERSION
        )
        claimed = await self.gateway.claim_research_problem(
            worker_id=self.worker_id,
            runtime_version=RUNTIME_VERSION,
            methodology_version=self.active_methodology_version,
            domain=PROBLEM_DOMAIN,
        )
        self.last_heartbeat_at = datetime.now(UTC)
        problem = claimed.get("problem")
        run = claimed.get("run")
        if not isinstance(problem, Mapping) or not isinstance(run, Mapping):
            self.active_problem_id = None
            await self._heartbeat()
            return {
                "status": "IDLE",
                "claimed": False,
                "research_promotion": promotion_results,
                "reconciliation": reconciliation,
                "v10_transition_recovery": v10_transition_recovery,
                "autonomous_loop_seed": autonomous_loop_seed,
                "v10_campaign_seed": v10_campaign_seed,
                "v10_corpus_recovery": v10_corpus_recovery,
                "v11_to_v12_recovery": v11_to_v12_recovery,
                "v13_interrupted_stage_recovery": v13_interrupted_stage_recovery,
                "v12_to_v13_recovery": v12_to_v13_recovery,
                "v14_dependency_recovery": v14_dependency_recovery,
                "v14_feature_recovery": v14_feature_recovery,
                "v14_pagination_recovery": v14_pagination_recovery,
                "v13_to_v14_recovery": v13_to_v14_recovery,
                "v14_r2e_pagination_recovery": v14_r2e_pagination_recovery,
                "v14_r2e_to_r2f_recovery": v14_r2e_to_r2f_recovery,
                "v14_r2f_shadow_recovery": v14_r2f_shadow_recovery,
                "v14_r2f_to_r2g_recovery": v14_r2f_to_r2g_recovery,
                "v14_r2h_faststart_recovery": v14_r2h_faststart_recovery,
                "v14_r2h_velum_recovery": v14_r2h_velum_recovery,
                "r2h_velum_transport_recovery": r2h_velum_transport_recovery,
                "v14_r2d_to_r2e_recovery": v14_r2d_to_r2e_recovery,
                "v14_r2c_to_r2d_recovery": v14_r2c_to_r2d_recovery,
                "v14_r2b_to_r2c_recovery": v14_r2b_to_r2c_recovery,
                "v14_r2a_to_r2b_recovery": v14_r2a_to_r2b_recovery,
                "v14_r1_to_r2a_recovery": v14_r1_to_r2a_recovery,
                "v13_to_director_recovery": v13_to_director_recovery,
                "v12_native_stage_precedence": v12_native_stage_precedence,
                "v12_claim_diagnostic": v12_claim_diagnostic,
                "v13_claim_diagnostic": v13_claim_diagnostic,
                "blocked_v10": blocked_v10,
            }

        problem_id = str(problem.get("problem_id"))
        run_id = str(run.get("run_id"))
        linked_job_id = (
            str(problem.get("linked_iren_job_id"))
            if problem.get("linked_iren_job_id")
            else None
        )
        self.active_problem_id = problem_id
        self.last_claim_at = datetime.now(UTC)
        await self._heartbeat()

        metadata = problem.get("metadata") if isinstance(problem.get("metadata"), Mapping) else {}
        try:
            research_stage = str(metadata.get("research_stage") or "")
            if (
                research_stage == HYPOTHESIS_PLANNER_STAGE
                or (
                    metadata.get("autonomous_continuation") is True
                    and not research_stage
                )
            ):
                return await self._execute_hypothesis_planner(problem, run)
            if research_stage in {
                STRATEGY_DEVELOPMENT_STAGE,
                STRATEGY_VALIDATION_STAGE,
                STRATEGY_HOLDOUT_STAGE,
                STRATEGY_VELUM_STAGE,
                STRATEGY_SHADOW_STAGE,
                STRATEGY_PAPER_STAGE,
            }:
                return await self._execute_strategy_manifest(problem, run)
            if research_stage.startswith("CRYPTO_COMPILED_"):
                return await self._execute_compiled_hypothesis(problem, run)
            if metadata.get("research_stage") in {
                V14_R2H_STAGE,
                V14_R2H_VELUM_STAGE,
            }:
                return await self._execute_btc_4h_consensus_v14_r2h(problem, run)
            if metadata.get("research_stage") == V14_R2G_STAGE:
                return await self._execute_btc_consensus_trend_v14_r2g(problem, run)
            if metadata.get("research_stage") == V14_R2F_STAGE:
                return await self._execute_btc_slow_momentum_v14_r2f(problem, run)
            if metadata.get("research_stage") == V14_R2E_STAGE:
                return await self._execute_btc_4h_trend_v14_r2e(problem, run)
            if metadata.get("research_stage") == V14_R2D_STAGE:
                return await self._execute_triangular_arbitrage_v14_r2d(problem, run)
            if metadata.get("research_stage") == V14_R2C_STAGE:
                return await self._execute_cross_sectional_momentum_v14_r2c(problem, run)
            if metadata.get("research_stage") == V14_R2B_STAGE:
                return await self._execute_btc_passive_scalping_v14_r2b(problem, run)
            if metadata.get("research_stage") == V14_R2A_STAGE:
                return await self._execute_btc_queue_imbalance_v14_r2a(problem, run)
            if metadata.get("research_stage") == V14_PREFLIGHT_STAGE:
                return await self._execute_btc_xgb_v14(problem, run)
            if metadata.get("research_stage") == RESEARCH_DIRECTOR_STAGE:
                return await self._execute_research_director(problem, run)
            if metadata.get("research_stage") in V13_STAGE_KEYS:
                return await self._execute_btc_hypotheses_v13(problem, run)
            if metadata.get("research_stage") in V12_STAGE_KEYS:
                return await self._execute_btc_mechanisms_v12(problem, run)
            if metadata.get("research_stage") in V11_STAGE_KEYS:
                return await self._execute_btc_trend_pullback_v11(problem, run)
            if metadata.get("research_stage") in V10_STAGE_KEYS:
                return await self._execute_trend_pullback_v10(problem, run)
            if metadata.get("research_stage") in V9_STAGE_KEYS:
                return await self._execute_activity_shock_v9(problem, run)
            if metadata.get("research_stage") in AUTONOMOUS_STAGE_KEYS:
                return await self._execute_autonomous_campaign(problem, run)
            if metadata.get("research_stage") == LEADLAG_STAGE_KEY:
                return await self._execute_leadlag_r2(problem, run)
            return await self._execute_v7_staged(problem, run)
        except Exception as exc:
            # A claimed run must never be left RUNNING after the worker has
            # abandoned it. Preserve the frozen stage and block it for an
            # explicit repair/reconciliation decision.
            failure = f"{type(exc).__name__}: {exc}"[:1000]
            print(
                "GRAEN_RESEARCH_FAILURE",
                {
                    "problem_id": problem_id,
                    "run_id": run_id,
                    "research_stage": metadata.get("research_stage"),
                    "epoch_index": metadata.get("v10_epoch_index"),
                    "candidate_id": (
                        metadata.get("v10_candidate_spec", {}).get("candidate_id")
                        if isinstance(metadata.get("v10_candidate_spec"), Mapping)
                        else None
                    ),
                    "error": failure,
                    "execution_authority": False,
                },
                flush=True,
            )
            try:
                await self.gateway.block_research_claim(
                    problem_id=problem_id,
                    run_id=run_id,
                    worker_id=self.worker_id,
                    error=failure,
                )
            finally:
                self.active_problem_id = None
                self.last_error = failure
            raise


runtime = GraenResearchExecutor()


@asynccontextmanager
async def lifespan(_: FastAPI):
    state = runtime.health()
    if not runtime.gateway.configured:
        raise RuntimeError("GRAEN gateway is not configured")
    if not runtime.settings.credentials_configured:
        raise RuntimeError("market-data credentials are not configured")
    if state["isolation_violations"]:
        raise RuntimeError("GRAEN research executor isolation check failed")
    await runtime.start()
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(
    title="ANEVUM GRAEN Research Executor",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/live")
async def live():
    running = runtime.task is not None and not runtime.task.done()
    if not running:
        raise HTTPException(status_code=503, detail={"ok": False, "running": False})
    return {
        "ok": True,
        "service": "graen-research-executor",
        "running": True,
        "runtime_version": RUNTIME_VERSION,
    }


@app.get("/health")
async def health():
    state = runtime.health()
    if not state["ok"]:
        raise HTTPException(status_code=503, detail=state)
    return state


@app.get("/v1/status")
async def status():
    return runtime.health()
