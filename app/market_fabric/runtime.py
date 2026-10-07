"""Opt-in shadow observer inside RHEN, independent of the champion loops.

Release deliberately cannot promote itself. Engine/risk/order integrations are
deferred until signed equivalence and runtime gates are established.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.command_visuals.visual_projector import VisualProjector
from app.command_visuals.account_projection import account_projection
from app.command_visuals.visual_archive import VisualArchive
from app.command_visuals.performance_projection import AccountPerformance
from app.equity_sessions import EquitySessionResolver
from app.adaptive_policy import AdaptivePolicyController, PolicyLibrary, PolicyContext, fingerprint
from app.capital_governor import govern_capital

from .broker_updates import BrokerInbox, BrokerUpdateStream, execution_marker
from .feed_router import route_feed, validate_symbols
from .rejection_engine import Evaluation, RejectionEngine
from .stream_manager import MarketStreamManager
from .stream_recovery import StreamCheckpoint, merge_bars
from .stream_state import MarketStateStore
from .decision_evidence import DecisionEvidence
from .shadow_features import regime_observation
from .signal_reasons import rejection_reasons
from .policy_state import PolicyStateStore
from .contracts import utc
from .coverage_evidence import CoverageEvidence
from .asset_eligibility import AssetEligibility
from zoneinfo import ZoneInfo


class ShadowFabric:
    def __init__(self, settings, market_data, *, evaluator=None, account_reader=None, champion_reader=None, asset_reader=None, ledger_reader=None):
        self.settings = settings
        validate_symbols(settings.extended_equity_symbols, cap=settings.rhen_market_stream_capacity)
        self.store = MarketStateStore(settings.extended_equity_symbols, warm_bars=max(15, settings.slow_window+1))
        self.visual = VisualProjector(self.store, flush_ms=settings.command_live_flush_ms)
        self.rejections = RejectionEngine()
        self.evaluator = evaluator
        self.account_reader = account_reader
        self.asset_reader = asset_reader
        self.asset_refresh = asyncio.Event()
        self.champion_reader = champion_reader
        self.ledger_reader = ledger_reader
        self.champion_observation = None
        self.account_refresh = asyncio.Event()
        self.signal_cache = {}
        self.resolver = EquitySessionResolver(market_data)
        self.market_data = market_data
        self.context = None
        self.route = None
        self.checkpoint = StreamCheckpoint(settings.rhen_market_stream_checkpoint_path)
        # 32 MiB ceiling for isolated bars, summaries and bounded shadow evidence.
        self.checkpoint.db.execute("PRAGMA max_page_count=8192")
        self.evidence = DecisionEvidence(self.checkpoint.db)
        self.assets = AssetEligibility(self.checkpoint.db, self.store.symbols)
        self.archive = VisualArchive(self.checkpoint.db)
        recovered_executions = self.archive.executions(datetime.now(timezone.utc), self.store.symbols)
        self.visual.executions.points.extend(recovered_executions["points"])
        self.visual.system.update(execution_recovery={"restored_events":len(recovered_executions["points"]),
            "rejected_records":recovered_executions["rejected_records"],"coverage_state":"BOUNDED_OBSERVATIONS_ONLY",
            "source":"RHEN/observed_execution_archive","provenance":"OPERATIONAL","entry_authority":False})
        self.performance = AccountPerformance(self.checkpoint.db)
        self.library = PolicyLibrary.load()
        baseline = {k: str(getattr(settings, k)) for k in ("stop_pct", "target_pct", "max_hold_minutes", "reentry_cooldown_minutes", "max_spread_pct", "min_quality_score")}
        # This identifies shadow inputs, not the protected production configuration.
        self.shadow_configuration = fingerprint({"baseline": baseline, "strategy": settings.strategy_version_id,
            "symbols": settings.extended_equity_symbols, "fast_window": settings.fast_window, "slow_window": settings.slow_window,
            "asset_eligibility_methodology":AssetEligibility.METHODOLOGY_VERSION if asset_reader is not None else "UNATTESTED"})
        self.coverage = CoverageEvidence(self.checkpoint.db, strategy_version=settings.strategy_version_id,
                                         configuration=self.shadow_configuration)
        self.freshness_wakeup = asyncio.Event()
        self.policy = AdaptivePolicyController(self.library, baseline=baseline, hard_limits={"stop_pct": settings.max_dynamic_stop_pct,
            "max_spread_pct": settings.max_spread_pct}, configuration_fingerprint=self.shadow_configuration)
        self.policy_state = PolicyStateStore(self.checkpoint.db)
        self.policy_session = None
        self.policy_recovery = "UNAVAILABLE"
        self.regime_key = None
        self.regime = None
        self.features = {}
        self.policy_snapshot = None
        self.manager = MarketStreamManager(self.store, api_key=settings.alpaca_api_key, api_secret=settings.alpaca_api_secret,
                                           on_event=self.on_event, bootstrap=self.bootstrap, on_status=self.on_stream_status)
        self.tasks = []
        self.stream_task = None
        self.last_error = None
        self.broker = None
        self.inbox = None

    async def on_stream_status(self, status):
        self.refresh_observation(utc(status["source_at"]))
        self.visual.system_patch(status)
        # Connection loss/recovery is critical operational evidence. Do not wait
        # for the 30-second calendar/reconciliation audit to propagate it.
        self.visual.publisher.flush()

    def refresh_observation(self, now, *, symbols=None):
        targets = tuple(symbols) if symbols is not None else self.store.symbols
        self.coverage.advance(self.store, now, symbols=targets)
        eligibility_changed = False
        for symbol in targets:
            row = self.store.snapshot(symbol, now)
            if self.asset_reader is not None:
                row["asset_eligibility"] = self.assets.snapshot(symbol, (self.store.context or (None,"CLOSED"))[1].split("/")[-1], now)
            previous = self.visual.scanner.get(symbol, {})
            fields = ("feed", "session", "evaluable", "rejection_codes", "quality_state", "observed_bar_count")
            asset_changed = any(previous.get("asset_eligibility",{}).get(k) != row.get("asset_eligibility",{}).get(k)
                                for k in ("eligible", "fetched_at", "facts", "rejection_codes"))
            if asset_changed or any(previous.get(k) != row.get(k) for k in fields):
                eligibility_changed = eligibility_changed or previous.get("evaluable") != row.get("evaluable")
                patch = {**previous, **row}
                if not row["evaluable"]:
                    patch.update(classification="NOT_EVALUABLE", candidate_state="BLOCKED",
                        signal_reason="Market prerequisites unavailable; previous signal is historical")
                elif self.asset_reader is not None and not row["asset_eligibility"]["eligible"]:
                    patch.update(classification="EVALUABLE_REJECTED", candidate_state="BLOCKED",
                        rejection_code=row["asset_eligibility"]["rejection_codes"][0],
                        signal_reason="Fresh asset eligibility evidence is required")
                self.visual.scanner[symbol] = patch
                self.visual.publisher.stage("scanner:"+symbol, "scanner_patch", patch)
        if eligibility_changed:
            self.regime_key = None
        self.freshness_wakeup.set()

    async def freshness_observer(self):
        # One deadline timer invalidates quiet symbols. No market polling or
        # fabricated quote/bar event; the publisher supplies ordered state deltas.
        while True:
            now = datetime.now(timezone.utc)
            # Scheduling can resume after a source deadline has already passed.
            # Invalidate only unpublished expiries before choosing future timers;
            # otherwise a fresh bar can mask an expired quote until its own timer.
            overdue = []
            for symbol in self.store.symbols:
                reasons = self.visual.scanner.get(symbol, {}).get("rejection_codes", ())
                timestamps = self.store.rows.get(symbol, {}).get("timestamps", {})
                if any(source <= now and source + timedelta(milliseconds=limit) < now
                       and reason not in reasons
                       for kind, limit, reason in (
                           ("quote", self.store.QUOTE_FRESHNESS_MS, "STALE_QUOTE"),
                           ("bar", self.store.BAR_FRESHNESS_MS, "STALE_BAR"))
                       if (source := timestamps.get(kind)) is not None):
                    overdue.append(symbol)
            asset_overdue = (self.asset_reader is not None and self.assets.fetched_at is not None
                and self.assets.fetched_at + timedelta(seconds=self.assets.MAX_AGE_SECONDS) < now
                and any(row.get("asset_eligibility", {}).get("quality_state") == "LIVE"
                        for row in self.visual.scanner.values()))
            if overdue or asset_overdue:
                self.refresh_observation(now, symbols=None if asset_overdue else overdue)
                self.visual.system_patch({"scanner_coverage": self.coverage.summary(self.store, now)})
                if asset_overdue:
                    self.visual.system_patch({"asset_eligibility": self.asset_summary(now)})
            # No await occurs between expiry processing and clearing the wakeup.
            self.freshness_wakeup.clear()
            deadlines = [at for symbol in self.store.symbols
                         if (at := self.store.freshness_deadline(symbol, now)) is not None]
            if self.asset_reader is not None and (asset_deadline := self.assets.deadline(now)) is not None:
                deadlines.append(asset_deadline)
            if not deadlines:
                await self.freshness_wakeup.wait()
                continue
            timeout = max(0, (min(deadlines)-now).total_seconds()) + .001
            try:
                await asyncio.wait_for(self.freshness_wakeup.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                now = datetime.now(timezone.utc)
                self.refresh_observation(now)
                self.visual.system_patch({"scanner_coverage": self.coverage.summary(self.store, now)})
                if self.asset_reader is not None:
                    self.visual.system_patch({"asset_eligibility":self.asset_summary(now)})

    async def bootstrap(self, feed, session_id):
        now = datetime.now(timezone.utc)
        self.visual.system_patch({"bootstrap_error": None, "restored_bar_count": 0})
        if feed == "overnight":
            # This feed has no historical endpoint. Delayed BOATS history is a
            # different source and cannot silently become live overnight bars.
            restored = self.checkpoint.restore(self.store, now)
            for symbol, row in self.store.rows.items():
                for bar in row["bars"]:
                    self.visual.point("candles:"+symbol, bar)
            self.visual.system_patch({"reconstruction_source": "CHECKPOINT+LIVE_STREAM", "restored_bar_count": restored,
                                      "bootstrap_error": "OVERNIGHT_HISTORY_UNAVAILABLE", "recovery_at": now.isoformat()})
            return
        try:
            restored = self.checkpoint.restore(self.store, now)
            start = max(now-timedelta(minutes=120), self.context.starts_at)
            # Explicit feed parameter; inherited 4.3 DATA_FEED cannot substitute a different source.
            async with httpx.AsyncClient(timeout=20) as http:
                r = await http.get(self.settings.data_base_url+"/v2/stocks/bars", headers=self.market_data.headers,
                                   params={"symbols": ",".join(self.store.symbols), "timeframe": "1Min",
                                           "start": start.isoformat(), "end": now.isoformat(), "feed": feed,
                                           "limit": 10000, "sort": "asc"})
                r.raise_for_status()
                body = r.json()
                if body.get("next_page_token"):
                    raise ValueError("bootstrap incomplete; remain warming")
                historical = merge_bars(self.store, body.get("bars", {}), now, starts_at=self.context.starts_at)
            for symbol, row in self.store.rows.items():
                for bar in row["bars"]:
                    self.visual.point("candles:"+symbol, bar)
            self.visual.system_patch({"reconstruction_source": "checkpoint+ALPACA_HISTORY", "recovery_at": now.isoformat(),
                                      "restored_bar_count": restored, "historical_bar_count": historical})
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.visual.system_patch({"reconstruction_source": "PARTIAL_OR_UNAVAILABLE", "bootstrap_error": self.last_error})

    async def on_event(self, event):
        now = event.received_at
        # A market event can only make its own symbol fresher. Keep the hot quote
        # path O(1); full-universe expiry/coverage remains on deadline and audit paths.
        self.refresh_observation(now, symbols=(event.symbol,))
        if self.policy_session != event.session_id:
            self.policy.profile, self.policy.since = "BASELINE_LOCKED", None
            self.policy.pending, self.policy.confirmed, self.policy.last_observation = None, 0, None
            self.policy_recovery = self.policy_state.restore(self.policy, event.session_id, now)
            self.policy_session = event.session_id
            self.regime_key = None
        row = self.store.snapshot(event.symbol, now)
        reasons = tuple(row["rejection_codes"])
        classification = "NOT_EVALUABLE"
        signal = None
        decision_id = None
        if row["evaluable"]:
            asset = self.assets.snapshot(event.symbol, event.session, now) if self.asset_reader is not None else None
            if asset is not None and not asset["eligible"]:
                classification = "EVALUABLE_REJECTED"
                reasons = tuple(asset["rejection_codes"])
            elif row["spread_bps"] is not None and row["spread_bps"] > float(self.settings.max_spread_pct)*10000:
                classification = "EVALUABLE_REJECTED"
                reasons = ("SPREAD_TOO_WIDE",)
            elif self.evaluator and event.session == "REGULAR":
                # The champion signal uses completed bars. Quote bursts update cheap
                # state without recalculating identical signal inputs. Confirmation
                # bar revisions invalidate the key too; no formula is reimplemented.
                revisions = tuple((s, self.store.rows.get(s, {}).get("feature_revision", 0))
                                  for s in (event.symbol, *self.settings.confirmation_symbols))
                key = (event.generation, revisions, now.replace(second=0, microsecond=0).isoformat())
                cached = self.signal_cache.get(event.symbol)
                if cached is None or cached[0] != key:
                    identity = fingerprint({"symbol": event.symbol, "session_id": event.session_id,
                        "shadow_configuration": self.shadow_configuration,
                        "inputs": {s: [b for b in self.store.rows.get(s, {}).get("bars", ()) if utc(b["timestamp"]) < now.replace(second=0, microsecond=0)]
                                   for s in (event.symbol, *self.settings.confirmation_symbols)}})
                    cached = (key, self.evaluator(event.symbol, now, self.store), identity)
                    self.signal_cache[event.symbol] = cached
                signal = cached[1]
                # Identity uses actual completed-bar input contents, not socket
                # generation or quote sequence. Reconnects cannot duplicate candidates.
                decision_id = cached[2]
                classification = "CANDIDATE" if signal.action == "buy" else "EVALUABLE_REJECTED"
                reasons = () if signal.action == "buy" else rejection_reasons(signal)
            else:
                classification = "EVALUABLE_REJECTED"
                reasons = ("POLICY_NOT_PROMOTED",)
        if decision_id is None:
            decision_id = fingerprint({"symbol": event.symbol, "session_id": event.session_id, "minute": now.replace(second=0, microsecond=0).isoformat(),
                "classification": classification, "reasons": reasons, "shadow_configuration": self.shadow_configuration})
        # NOSTRA inputs are completed bars. Quote bursts must not rebuild every
        # symbol snapshot merely to discover that no bar feature changed.
        feature_key = (self.store.context,
                       tuple((s, self.store.rows.get(s, {}).get("feature_revision", 0)) for s in self.store.symbols),
                       now.replace(second=0, microsecond=0))
        if feature_key != self.regime_key:
            self.regime, self.features = regime_observation(self.store, now, fast_window=self.settings.fast_window, slow_window=self.settings.slow_window)
            context = PolicyContext(now, utc(self.regime["feature_as_of"]), event.session, self.regime["primary_regime"],
                self.regime["confidence"], self.regime["unknown_probability"], self.regime["market_familiarity"],
                # Missing canonical health/approval lineage is a veto, not an assumed pass.
                False, self.champion_ready(now), self.champion_ready(now), False, self.shadow_configuration, self.library.fingerprint)
            self.policy_snapshot = self.policy.observe(context, enabled=True, mode="shadow")
            self.policy_state.save(self.policy, event.session_id, now)
            self.regime_key = feature_key
            for symbol, feature in self.features.items():
                self.visual.point("rolling_vwap:"+symbol, {"timestamp": feature["bar_timestamp"], "value": feature["vwap"],
                    "symbol": symbol, "provenance": "DERIVED", "source": feature["source"], "quality_state": "LIVE",
                    "methodology_version": feature["methodology_version"]})
            self.visual.system_patch({"nostra": self.regime, "adaptive_control": {
                "mode": self.policy_snapshot.mode, "current_profile": "BASELINE_LOCKED", "proposed_profile": self.policy_snapshot.proposed_profile,
                "snapshot_fingerprint": self.policy_snapshot.snapshot_fingerprint, "policy_library_fingerprint": self.library.fingerprint,
                "shadow_configuration_fingerprint": self.shadow_configuration,
                "protected_configuration_fingerprint": (self.champion_observation or {}).get("protected_configuration_fingerprint"),
                "effective": dict(self.policy_snapshot.execution_values), "counterfactual": dict(self.policy_snapshot.counterfactual_values),
                "reason_codes": list(self.policy_snapshot.reasons), "entry_authority": False,
                "canonical_health_lineage": "VERIFIED_READ" if self.champion_ready(now) else "UNAVAILABLE",
                "recovery_state": self.policy_recovery, "profile_since": self.policy.since.isoformat() if self.policy.since else None,
                "pending_profile": self.policy.pending, "confirmation_count": self.policy.confirmed}})
        evaluation = Evaluation(decision_id, now, event.session, event.feed,
                                event.symbol, classification, reasons, strategy_version=self.settings.strategy_version_id)
        day = event.session_id.split("/")[0]
        evidence = {"observed_at": now.isoformat(), "session_day": day, "session": event.session, "session_id": event.session_id,
            "symbol": event.symbol, "feed": event.feed, "stream_generation": event.generation, "classification": classification,
            "reasons": list(reasons), "signal_reason": signal.reason if signal else None, "signal_metadata": signal.metadata if signal else {},
            "strategy_version": self.settings.strategy_version_id, "policy_snapshot": self.policy_snapshot.snapshot_fingerprint,
            "policy_library_fingerprint": self.library.fingerprint, "shadow_configuration_fingerprint": self.shadow_configuration,
            "regime": self.regime, "entry_authority": False, "risk_validation_state": "UNAVAILABLE",
            "candidate_kind": "SIGNAL_ONLY_COUNTERFACTUAL", "provenance": "DERIVED", "source": "RHEN/market_fabric"}
        if self.asset_reader is not None:
            evidence["asset_eligibility"] = self.assets.snapshot(event.symbol, event.session, now)
        if decision_id not in self.rejections.ids and self.evidence.record(decision_id, evidence, coverage=self.coverage):
            self.rejections.record(evaluation)
        self.visual.market(event, now)
        if event.kind in {"bar", "bar_revision"}:
            self.archive.append("candles:"+event.symbol, self.store._bar(event), now)
        patch = {**self.visual.scanner[event.symbol], "candidate_state": "CANDIDATE" if classification == "CANDIDATE" else row["candidate_state"],
                 "rejection_code": reasons[0] if reasons else None, "classification": classification,
                 "signal_reason": signal.reason if signal else "Strategy integration not validated for this session"}
        patch.update(decision_id=decision_id, signal_features=signal.metadata if signal else {}, entry_authority=False)
        if self.asset_reader is not None:
            patch["asset_eligibility"] = evidence["asset_eligibility"]
            if not patch["asset_eligibility"]["eligible"]:
                patch["candidate_state"] = "BLOCKED"
        self.visual.scanner[event.symbol] = patch
        self.visual.publisher.stage("scanner:"+event.symbol, "scanner_patch", patch)

    async def broker_event(self, event_id, data):
        # Durable inbox acknowledges source evidence before ephemeral visualization.
        # Canonical 4.3 order/position reconciliation is intentionally not mutated.
        marker = execution_marker(event_id, data)
        if marker:
            self.archive.append("executions:"+marker["symbol"], marker, datetime.now(timezone.utc))
        self.visual.critical(marker)
        self.account_refresh.set()
        return True

    @staticmethod
    def canonical_ledger_parity(canonical, observed):
        """Compare only the source-time overlap retained by the shadow archive."""
        base = {"entry_authority":False,"broker_write_authority":False,
            "source":"RHEN/canonical_ledger_read","provenance":"DERIVED",
            "methodology_version":"canonical-observation-parity-v1","parity_complete":False}
        if (not isinstance(canonical,dict) or canonical.get("ok") is not True
            or canonical.get("truncated") is True or not isinstance(canonical.get("events"),list)):
            return {**base,"quality_state":"UNAVAILABLE","reason":"CANONICAL_LEDGER_UNAVAILABLE_OR_TRUNCATED"}
        points = [p for p in observed if isinstance(p,dict) and p.get("order_ref") and p.get("timestamp")]
        if not points:
            return {**base,"quality_state":"NO_OVERLAP","reason":"NO_RETAINED_BROKER_OBSERVATIONS",
                "canonical_events":len(canonical["events"]),"observed_events":0}
        try:
            start = min(utc(p["timestamp"]) for p in points)
        except (ValueError,TypeError):
            return {**base,"quality_state":"UNAVAILABLE","reason":"INVALID_OBSERVED_EXECUTION_TIME"}
        events = []
        for event in canonical["events"]:
            try:
                if utc(event.get("occurred_at")) >= start:
                    events.append(event)
            except (ValueError,TypeError):
                continue
        canonical_orders = {str((e.get("order") or {}).get("id") or "")
            for e in events if e.get("event_type") == "broker_order"}
        canonical_orders.discard("")
        canonical_fills = {str((e.get("fill") or {}).get("order_id") or "")
            for e in events if e.get("event_type") == "broker_fill"}
        canonical_fills.discard("")
        observed_orders = {str(p["order_ref"]) for p in points}
        observed_fills = {str(p["order_ref"]) for p in points
            if p.get("event_type") in {"FILL","PARTIAL_FILL"}}
        missing_orders = canonical_orders-observed_orders
        unknown_orders = observed_orders-canonical_orders
        missing_fills = canonical_fills-observed_fills
        unknown_fills = observed_fills-canonical_fills
        complete = not (missing_orders or unknown_orders or missing_fills or unknown_fills)
        return {**base,"quality_state":"LIVE" if complete else "DEGRADED",
            "reason":"PARITY" if complete else "DIVERGENCE","parity_complete":complete,
            "overlap_started_at":start.isoformat(),"canonical_events":len(events),"observed_events":len(points),
            "canonical_order_count":len(canonical_orders),"observed_order_count":len(observed_orders),
            "canonical_fill_order_count":len(canonical_fills),"observed_fill_order_count":len(observed_fills),
            "missing_observed_orders":len(missing_orders),"unknown_observed_orders":len(unknown_orders),
            "missing_observed_fills":len(missing_fills),"unknown_observed_fills":len(unknown_fills)}

    async def reconcile_account(self):
        if self.account_reader is None:
            return
        snapshot = await self.account_reader()
        now = datetime.now(timezone.utc)
        if self.champion_reader:
            try:
                self.champion_observation = await self.champion_reader()
            except Exception:
                self.champion_observation = None
            self.visual.system_patch({"champion_observation":self.champion_observation or {"quality_state":"UNAVAILABLE"}})
        if self.ledger_reader:
            run_id = (self.champion_observation or {}).get("run_id")
            try:
                canonical = await self.ledger_reader(run_id)
                observed = self.archive.executions(now,None)["points"]
                parity = self.canonical_ledger_parity(canonical,observed)
            except Exception as exc:
                parity = {"quality_state":"UNAVAILABLE","reason":type(exc).__name__,
                    "entry_authority":False,"broker_write_authority":False,
                    "parity_complete":False,"source":"RHEN/canonical_ledger_read",
                    "provenance":"DERIVED","methodology_version":"canonical-observation-parity-v1"}
            self.visual.system_patch({"canonical_ledger_parity":parity})
            print("RHEN44_LEDGER_PARITY "+json.dumps({
                "observed_at":now.isoformat(),
                "quality_state":parity.get("quality_state"),
                "reason":parity.get("reason"),
                "parity_complete":parity.get("parity_complete") is True,
                "canonical_events":parity.get("canonical_events"),
                "observed_events":parity.get("observed_events"),
                "canonical_order_count":parity.get("canonical_order_count"),
                "observed_order_count":parity.get("observed_order_count"),
                "canonical_fill_order_count":parity.get("canonical_fill_order_count"),
                "observed_fill_order_count":parity.get("observed_fill_order_count"),
                "missing_observed_orders":parity.get("missing_observed_orders"),
                "unknown_observed_orders":parity.get("unknown_observed_orders"),
                "missing_observed_fills":parity.get("missing_observed_fills"),
                "unknown_observed_fills":parity.get("unknown_observed_fills"),
                "entry_authority":False,
                "broker_write_authority":False,
                "methodology_version":parity.get("methodology_version"),
            },allow_nan=False),flush=True)
        projection = account_projection(snapshot, now)
        for key, point in projection["points"].items():
            self.visual.point("account:"+key, point)
            self.archive.append("account:"+key, point, now)
        self.visual.system_patch({"account_observation": {k:v for k,v in projection.items() if k != "points"}})
        performance = self.performance.observe(snapshot, now)
        for key, point in performance["points"].items():
            self.visual.point("performance:"+key, point)
            self.archive.append("performance:"+key, point, now)
        self.visual.system_patch({"account_performance":{k:v for k,v in performance.items() if k != "points"}})
        sizing = snapshot.get("sizing")
        if sizing is not None:
            decision = govern_capital(**sizing, evidence_factor=0)
            self.visual.system_patch({"capital_governor": {"notional": str(decision.notional),
                "risk_throttle": str(decision.risk_throttle), "binding_caps": list(decision.binding_caps),
                "factors": {k:str(v) for k,v in decision.factors.items()}, "allow_margin": False,
                "entry_authority": False, "mode": "SHADOW", "quality_state": "UNVALIDATED",
                "evidence_reason": "CANONICAL_POLICY_VALIDATION_UNAVAILABLE", "observed_at": now.isoformat(),
                "provenance": "DERIVED", "source": "RHEN/canonical_sizing_read", "methodology_version": "capital-governor-v1"}})

    def champion_ready(self, now):
        row = self.champion_observation or {}
        try:
            identity = str(row.get("protected_configuration_fingerprint") or "")
            valid_hash = len(identity) == 71 and identity.startswith("sha256:") and all(c in "0123456789abcdef" for c in identity[7:])
            return bool(row.get("observed_at") and 0 <= (utc(now)-utc(row["observed_at"])).total_seconds() <= 150
                and row.get("runtime_ok") is True and row.get("reconciliation_safe") is True
                and row.get("strategy_version") == self.settings.strategy_version_id and valid_hash)
        except (ValueError, TypeError):
            return False

    async def account_observer(self):
        # Event-driven refresh after trade_updates. Timeout is reconciliation/audit,
        # not a primary market path. No new socket or broker-writing client is created.
        while True:
            self.account_refresh.clear()
            try:
                await self.reconcile_account()
            except Exception as exc:
                self.visual.system_patch({"account_observation": {"quality_state": "UNAVAILABLE", "error": type(exc).__name__},
                                          "capital_governor": {"quality_state": "UNAVAILABLE", "entry_authority": False}})
            try:
                await asyncio.wait_for(self.account_refresh.wait(), timeout=120)
                await asyncio.sleep(.25)  # coalesce broker bursts; market/critical events continue
            except asyncio.TimeoutError:
                pass

    def asset_summary(self, now):
        session = (self.store.context or (None, "CLOSED"))[1].split("/")[-1]
        rows = [self.assets.snapshot(s, session, now) for s in self.store.symbols]
        return {"intended_symbols":len(rows),"eligible_symbols":sum(r["eligible"] for r in rows),
            "attested_symbols":sum(r["quality_state"] == "LIVE" for r in rows),
            "fetched_at":self.assets.fetched_at.isoformat() if self.assets.fetched_at else None,
            "session":session,"entry_authority":False,"provenance":"OPERATIONAL","source":"RHEN/asset_eligibility"}

    async def asset_observer(self):
        # REST is an eligibility audit, never the primary price/scanner path.
        while True:
            self.asset_refresh.clear()
            try:
                rows = await self.asset_reader()
                self.assets.replace(rows, datetime.now(timezone.utc))
                self.freshness_wakeup.set()
                self.refresh_observation(datetime.now(timezone.utc))
                self.visual.system_patch({"asset_eligibility":self.asset_summary(datetime.now(timezone.utc)),"asset_error":None})
            except Exception as exc:
                self.visual.system_patch({"asset_error":type(exc).__name__,"asset_eligibility":self.asset_summary(datetime.now(timezone.utc))})
            # Refresh at 19:55 ET for the pre-session sync, as well as every
            # ten minutes and immediately when the session changes.
            now = datetime.now(timezone.utc)
            local = now.astimezone(ZoneInfo("America/New_York"))
            preflight = local.replace(hour=19,minute=55,second=0,microsecond=0)
            timeout = min(600,max(.01,(preflight-local).total_seconds())) if local < preflight else 600
            try:
                await asyncio.wait_for(self.asset_refresh.wait(),timeout=timeout)
            except asyncio.TimeoutError:
                pass

    async def supervise(self):
        last_key = None
        try:
            while True:
                now = datetime.now(timezone.utc)
                try:
                    context = await self.resolver.classify(now)
                    route = route_feed(context, now)  # Basic only until live entitlement is attested.
                    key = (route.expected_feed, route.session_id)
                    if key != last_key:
                        self.asset_refresh.set()
                        if self.stream_task:
                            self.stream_task.cancel()
                            await asyncio.gather(self.stream_task, return_exceptions=True)
                        self.context, self.route = context, route
                        self.store.connection = "DISCONNECTED"
                        if route.expected_feed:
                            self.stream_task = asyncio.create_task(self.manager.run(*key))
                        else:
                            self.stream_task = None
                        last_key = key
                    rows = [self.store.snapshot(s, now) for s in self.store.symbols]
                    self.refresh_observation(now)
                    self.visual.system_patch({"session": route.session, "feed": route.expected_feed,
                                              "capability": route.capability, "execution_policy": route.execution_policy,
                                              "connection_state": self.store.connection, "stream_generation": self.store.generation,
                                              "subscribed_symbols": len(self.store.subscribed), "intended_symbols": len(self.store.symbols),
                                              "evaluable_fraction": sum(r["evaluable"] for r in rows)/len(rows),
                                              "reconnects": self.manager.reconnects, "out_of_order_events": self.store.out_of_order,
                                              "subscribed_channels": sorted(self.manager.subscribed_channels),
                                              "unavailable_channels": sorted(self.manager.unavailable_channels),
                                              "scanner_summary": self.rejections.summary(now), "entry_authority": False,
                                              "scanner_session_summary": self.evidence.summary(route.session_id.split("/")[0], route.session),
                                              "scanner_coverage": self.coverage.summary(self.store, now),
                                              "broker_stream_state": self.broker.state if self.broker else "DISABLED",
                                              "quality_state": "LIVE" if all(r["evaluable"] for r in rows) else "DEGRADED",
                                              "source_at": now.isoformat()})
                    self.checkpoint.save(self.store, now)
                    self.coverage.save()
                    self.checkpoint.save_summary(now.strftime("%Y-%m-%dT%H:%M"), {
                        "observed_at": now.isoformat(), "session": route.session, "feed": route.expected_feed,
                        "strategy_version": self.settings.strategy_version_id, "entry_authority": False,
                        "scanner": self.rejections.summary(now), "stream_generation": self.store.generation})
                except Exception as exc:
                    self.last_error = type(exc).__name__
                    if self.stream_task:
                        self.stream_task.cancel()
                        await asyncio.gather(self.stream_task, return_exceptions=True)
                        self.stream_task = None
                    self.store.connection = "DISCONNECTED"
                    self.store.subscribed.clear()
                    self.refresh_observation(now)
                    last_key = None
                    self.visual.system_patch({"quality_state": "DEGRADED", "last_error": self.last_error, "entry_authority": False})
                # Calendar/capability/checkpoint audit, not primary price observation.
                await asyncio.sleep(30)
        finally:
            if self.stream_task:
                self.stream_task.cancel()
                await asyncio.gather(self.stream_task, return_exceptions=True)

    def start(self):
        self.tasks = [asyncio.create_task(self.supervise()), asyncio.create_task(self.visual.publisher.run()),
                      asyncio.create_task(self.freshness_observer())]
        if self.account_reader is not None:
            self.tasks.append(asyncio.create_task(self.account_observer()))
        if self.asset_reader is not None:
            self.tasks.append(asyncio.create_task(self.asset_observer()))
        if self.settings.rhen_broker_stream_shadow_enabled:
            self.inbox = BrokerInbox(self.settings.rhen_market_stream_checkpoint_path+".broker")
            self.broker = BrokerUpdateStream(api_key=self.settings.alpaca_api_key, api_secret=self.settings.alpaca_api_secret,
                                             paper=self.settings.trading_mode != "live", inbox=self.inbox, callback=self.broker_event)
            self.tasks.append(asyncio.create_task(self.broker.run()))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.coverage.advance(self.store, datetime.now(timezone.utc))
        self.coverage.save()
        self.checkpoint.close()
        if self.inbox:
            self.inbox.close()
