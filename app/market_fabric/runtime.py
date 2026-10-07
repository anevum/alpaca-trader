"""Opt-in shadow observer inside RHEN, independent of the champion loops.

Release deliberately cannot promote itself. Engine/risk/order integrations are
deferred until signed equivalence and runtime gates are established.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.command_visuals.visual_projector import VisualProjector
from app.equity_sessions import EquitySessionResolver

from .broker_updates import BrokerInbox, BrokerUpdateStream, execution_marker
from .feed_router import route_feed, validate_symbols
from .rejection_engine import Evaluation, RejectionEngine
from .stream_manager import MarketStreamManager
from .stream_recovery import StreamCheckpoint, merge_bars
from .stream_state import MarketStateStore


class ShadowFabric:
    def __init__(self, settings, market_data, *, evaluator=None):
        self.settings = settings
        validate_symbols(settings.extended_equity_symbols)
        self.store = MarketStateStore(settings.extended_equity_symbols, warm_bars=max(15, settings.slow_window+1))
        self.visual = VisualProjector(self.store, flush_ms=settings.command_live_flush_ms)
        self.rejections = RejectionEngine()
        self.evaluator = evaluator
        self.signal_cache = {}
        self.resolver = EquitySessionResolver(market_data)
        self.market_data = market_data
        self.context = None
        self.route = None
        self.checkpoint = StreamCheckpoint(settings.rhen_market_stream_checkpoint_path)
        self.manager = MarketStreamManager(self.store, api_key=settings.alpaca_api_key, api_secret=settings.alpaca_api_secret,
                                           on_event=self.on_event, bootstrap=self.bootstrap)
        self.tasks = []
        self.stream_task = None
        self.last_error = None
        self.broker = None
        self.inbox = None

    async def bootstrap(self, feed, session_id):
        now = datetime.now(timezone.utc)
        try:
            self.checkpoint.restore(self.store, now)
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
                merge_bars(self.store, body.get("bars", {}), now, starts_at=self.context.starts_at)
            for symbol, row in self.store.rows.items():
                for bar in row["bars"]:
                    self.visual.point("candles:"+symbol, bar)
            self.visual.system_patch({"reconstruction_source": "checkpoint+ALPACA_HISTORY", "recovery_at": now.isoformat()})
        except Exception as exc:
            self.last_error = type(exc).__name__
            self.visual.system_patch({"reconstruction_source": "PARTIAL_OR_UNAVAILABLE", "bootstrap_error": self.last_error})

    async def on_event(self, event):
        now = event.received_at
        row = self.store.snapshot(event.symbol, now)
        reasons = tuple(row["rejection_codes"])
        classification = "NOT_EVALUABLE"
        signal = None
        if row["evaluable"]:
            if self.evaluator and event.session == "REGULAR":
                # The champion signal uses completed bars. Quote bursts update cheap
                # state without recalculating identical signal inputs. Confirmation
                # bar revisions invalidate the key too; no formula is reimplemented.
                revisions = tuple((s, self.store.rows.get(s, {}).get("feature_revision", 0))
                                  for s in (event.symbol, *self.settings.confirmation_symbols))
                key = (event.generation, revisions, now.replace(second=0, microsecond=0).isoformat())
                cached = self.signal_cache.get(event.symbol)
                if cached is None or cached[0] != key:
                    cached = (key, self.evaluator(event.symbol, now, self.store))
                    self.signal_cache[event.symbol] = cached
                signal = cached[1]
                classification = "CANDIDATE" if signal.action == "buy" else "EVALUABLE_REJECTED"
                reasons = () if signal.action == "buy" else ("SIGNAL_BELOW_THRESHOLD",)
            else:
                classification = "EVALUABLE_REJECTED"
                reasons = ("POLICY_NOT_PROMOTED",)
        evaluation = Evaluation(f"{event.generation}:{event.sequence}", now, event.session, event.feed,
                                event.symbol, classification, reasons, strategy_version=self.settings.strategy_version_id)
        self.rejections.record(evaluation)
        self.visual.market(event, now)
        patch = {**self.visual.scanner[event.symbol], "candidate_state": "CANDIDATE" if classification == "CANDIDATE" else row["candidate_state"],
                 "rejection_code": reasons[0] if reasons else None, "classification": classification,
                 "signal_reason": signal.reason if signal else "Strategy integration not validated for this session"}
        self.visual.scanner[event.symbol] = patch
        self.visual.publisher.stage("scanner:"+event.symbol, "scanner_patch", patch)

    async def broker_event(self, event_id, data):
        # Durable inbox acknowledges source evidence before ephemeral visualization.
        # Canonical 4.3 order/position reconciliation is intentionally not mutated.
        self.visual.critical(execution_marker(event_id, data))
        return True

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
                    self.visual.system_patch({"session": route.session, "feed": route.expected_feed,
                                              "capability": route.capability, "execution_policy": route.execution_policy,
                                              "connection_state": self.store.connection, "stream_generation": self.store.generation,
                                              "subscribed_symbols": len(self.store.subscribed), "intended_symbols": len(self.store.symbols),
                                              "evaluable_fraction": sum(r["evaluable"] for r in rows)/len(rows),
                                              "reconnects": self.manager.reconnects, "out_of_order_events": self.store.out_of_order,
                                              "scanner_summary": self.rejections.summary(now), "entry_authority": False,
                                              "broker_stream_state": self.broker.state if self.broker else "DISABLED",
                                              "quality_state": "LIVE" if all(r["evaluable"] for r in rows) else "DEGRADED",
                                              "source_at": now.isoformat()})
                    self.checkpoint.save(self.store, now)
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
                    last_key = None
                    self.visual.system_patch({"quality_state": "DEGRADED", "last_error": self.last_error, "entry_authority": False})
                # Calendar/capability/checkpoint audit, not primary price observation.
                await asyncio.sleep(30)
        finally:
            if self.stream_task:
                self.stream_task.cancel()
                await asyncio.gather(self.stream_task, return_exceptions=True)

    def start(self):
        self.tasks = [asyncio.create_task(self.supervise()), asyncio.create_task(self.visual.publisher.run())]
        if self.settings.rhen_broker_stream_shadow_enabled:
            self.inbox = BrokerInbox(self.settings.rhen_market_stream_checkpoint_path+".broker")
            self.broker = BrokerUpdateStream(api_key=self.settings.alpaca_api_key, api_secret=self.settings.alpaca_api_secret,
                                             paper=self.settings.trading_mode != "live", inbox=self.inbox, callback=self.broker_event)
            self.tasks.append(asyncio.create_task(self.broker.run()))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.checkpoint.close()
        if self.inbox:
            self.inbox.close()
