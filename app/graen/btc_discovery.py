"""Canonical bounded GRAEN BTC job, hosted by the existing research executor."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from typing import Any

import httpx

from app.btc_discovery_contract import (COSTS, GATES, LIVE_ID, STAGES, VERSION, catalog, candidate_order_matches,
                                       candidate_strategy, chrono_contract, fingerprint, gate, valid_assignment)
from app.crypto_layer import CryptoMarketDataClient
from app.rhen_core.store import RhenCoreStore
from app.velum_core import ContinuousReplayEngine
from app.velum_btc import metrics, corpus_coverage

NAMESPACE = "btc_discovery"
UTC = timezone.utc


def read_state(store: RhenCoreStore) -> dict[str, Any]:
    state = store.get_kv(NAMESPACE, "pipeline", {})[0]
    state["paper_runtime"] = store.get_kv(NAMESPACE, "paper_runtime", None)[0]
    active_id = (state["paper_runtime"] or {}).get("candidate_id", "")
    observation = store.get_kv(NAMESPACE, "paper_observation:" + active_id, {})[0]
    state["paper_last_observed_at"] = observation.get("observed_at")
    state["paper_error"] = observation.get("error")
    state["paper_errors_observed"] = observation.get("errors_observed", 0)
    return state


def approved_assignment(state: dict[str, Any]) -> dict[str, Any] | None:
    identity = state.get("paper_candidate_id")
    if not identity:
        return None
    row = (state.get("candidates") or {}).get(identity)
    if not row:
        raise ValueError("unknown_paper_candidate")
    if row.get("history") != list(STAGES[:4]):
        raise ValueError("btc_candidate_lifecycle_incomplete")
    results = row.get("results") or {}
    previous = None
    for stage in STAGES[:3]:
        result = results.get(stage) or {}
        if gate(result, previous):
            raise ValueError("btc_candidate_evidence_invalid")
        previous = result
    verification = results.get("VELUM_REPLAY") or {}
    if not verification.get("verified") or verification.get("candidate_fingerprint") != row.get("fingerprint"):
        raise ValueError("btc_candidate_velum_unverified")
    for stage in STAGES[:3]:
        receipt = (verification.get("receipts") or {}).get(stage) or {}
        if (receipt.get("dataset_fingerprint") != results[stage].get("dataset_fingerprint")
                or receipt.get("result_fingerprint") != fingerprint(results[stage])):
            raise ValueError("btc_candidate_velum_receipt_mismatch")
    approval = fingerprint({"candidate": row["fingerprint"], "results": results,
                            "contract": state["contract"], "gates": GATES, "version": VERSION})
    if row.get("approval_fingerprint") != approval:
        raise ValueError("btc_candidate_approval_mismatch")
    assignment = {key: row[key] for key in ("candidate_id", "parameters", "fingerprint", "methodology_version", "stage", "approval_fingerprint")}
    assignment.update({"live_authority": False, "approved_at": row["approved_at"],
                       "lifecycle_history": row["history"], "approval_gates": GATES,
                       "approval_contract": state["contract"], "approval_results": results,
                       "entries_allowed": row["stage"] == "FORWARD_PAPER" and not row.get("paper_rejected"),
                       "paper_runtime": state.get("paper_runtime")})
    return valid_assignment(assignment)


def projection(store: RhenCoreStore) -> dict[str, Any]:
    state = read_state(store)
    candidates = []
    for original in (state.get("candidates") or {}).values():
        row = dict(original)
        row["results"] = {stage: {**result, "result_fingerprint": fingerprint(result),
                         "scenarios": {key: {field: value for field, value in scenario.items() if field != "trades"}
                                       for key, scenario in (result.get("scenarios") or {}).items()}}
                          for stage, result in (row.get("results") or {}).items()}
        candidates.append(row)
    identity = state.get("paper_candidate_id") or state.get("selected_candidate_id") or state.get("current_candidate_id")
    current = next((x for x in candidates if x["candidate_id"] == identity), None)
    data_quality = {}
    for stage in STAGES[:3]:
        corpus = store.get_kv(NAMESPACE, "corpus:" + stage.lower(), None)[0]
        if corpus is not None:
            start, end = (datetime.fromisoformat(x) for x in state["contract"][stage.lower()])
            data_quality[stage] = corpus_coverage(corpus, start, end)
    return {"schema_version": "btc_discovery.v1", "methodology_version": VERSION,
            "authority": "CANONICAL_BTC_RESEARCH", "live_strategy_version_id": LIVE_ID,
            "live_broker_writes_allowed": False, "automatic_live_promotion": False,
            "state": state.get("status", "NOT_STARTED"), "current_stage": state.get("stage", "DEVELOPMENT"),
            "updated_at": state.get("updated_at"), "running": bool(state.get("running")),
            "candidate": current, "candidates": candidates, "contract": state.get("contract"),
            "cost_scenarios": COSTS, "gates": GATES, "paper_candidate_id": state.get("paper_candidate_id"),
            "paper_runtime": state.get("paper_runtime"), "paper_progress": state.get("paper_progress"),
            "paper_reader": store.get_kv(NAMESPACE, "paper_reader", None)[0], "data_quality": data_quality,
            "last_error": state.get("last_error"), "rejection_reasons": (current or {}).get("rejection_reasons", []),
            "search_completed": sum(x.get("status") != "QUEUED" for x in candidates), "search_bound": len(catalog())}


def record_paper(store: RhenCoreStore, body: dict[str, Any]) -> dict[str, Any]:
    # Authenticated paper adapter sends observations only. It cannot create assignments,
    # pass statistical stages, modify parameters, or request live promotion.
    with store._lock:
        state = read_state(store)
        assignment = approved_assignment(state)
        if assignment is None and body.get("heartbeat") is True and body.get("candidate_id") == LIVE_ID and body.get("trading_mode") == "paper":
            reader = {"status": "BASELINE", "strategy_version_id": LIVE_ID, "observed_at": datetime.now(UTC).isoformat(),
                      "run_id": body.get("run_id"), "authenticated": True, "live_authority": False}
            store.set_kv(NAMESPACE, "paper_reader", reader)
            return {"ok": True, "candidate_id": LIVE_ID, "live_authority": False}
        if not assignment or body.get("candidate_id") != assignment["candidate_id"] or body.get("trading_mode") != "paper":
            raise ValueError("paper_observation_identity_mismatch")
        now = datetime.now(UTC).isoformat()
        runtime = state.get("paper_runtime") or {}
        if body.get("activate"):
            if runtime.get("candidate_id") != assignment["candidate_id"]:
                if not body.get("flat") or body.get("open_orders"):
                    raise ValueError("paper_candidate_activation_requires_flat_account")
                runtime = {"candidate_id": assignment["candidate_id"], "activated_at": now,
                           "run_id": body.get("run_id"), "parameters": assignment["parameters"]}
                store.set_kv(NAMESPACE, "paper_runtime", runtime)
        if runtime.get("candidate_id") != assignment["candidate_id"] or runtime.get("run_id") != body.get("run_id"):
            raise ValueError("paper_candidate_not_activated")
        fills_key = "paper_fills:" + assignment["candidate_id"]
        fills = store.get_kv(NAMESPACE, fills_key, {})[0]
        for order in body.get("orders") or []:
            if not isinstance(order, dict) or order.get("status") != "filled":
                continue
            identity = str(order.get("id") or "")
            filled_at = str(order.get("filled_at") or "")
            try:
                observed = datetime.fromisoformat(filled_at.replace("Z", "+00:00"))
                valid_time = observed.tzinfo is not None and observed >= datetime.fromisoformat(runtime["activated_at"])
            except ValueError:
                valid_time = False
            if not identity or not candidate_order_matches(order, assignment["candidate_id"]) or not valid_time:
                continue
            if str(order.get("symbol") or "").replace("/", "").upper() != "BTCUSD":
                continue
            fills[identity] = {key: order.get(key) for key in ("id", "filled_at", "filled_qty", "filled_avg_price", "side", "client_order_id")}
        if len(fills) > 2000:
            raise ValueError("paper_evidence_bound_exhausted")
        store.set_kv(NAMESPACE, fills_key, fills)
        store.set_kv(NAMESPACE, "paper_observation:" + assignment["candidate_id"], {"observed_at": now, "error": body.get("error"),
                     "errors_observed": int(state.get("paper_errors_observed") or 0) + int(bool(body.get("error")))})
        return {"ok": True, "candidate_id": assignment["candidate_id"], "paper_runtime": runtime,
                "live_authority": False}


def paper_progress(store: RhenCoreStore, state: dict[str, Any]) -> None:
    from decimal import Decimal
    runtime = state.get("paper_runtime") or {}
    if not runtime or runtime.get("candidate_id") != state.get("paper_candidate_id"):
        state["paper_progress"] = {"status": "WAITING_FOR_FLAT_PAPER_ACTIVATION", "trade_count": 0}
        return
    fills = store.get_kv(NAMESPACE, "paper_fills:" + state["paper_candidate_id"], {})[0]
    inventory = cost = Decimal("0")
    entry_at = None
    trades = []
    for row in sorted(fills.values(), key=lambda x: (x["filled_at"], x["id"])):
        qty, price = Decimal(str(row["filled_qty"])), Decimal(str(row["filled_avg_price"]))
        if not qty.is_finite() or not price.is_finite() or min(qty, price) <= 0:
            raise ValueError("invalid_paper_fill")
        if row["side"] == "buy":
            entry_at = entry_at or row["filled_at"]
            inventory += qty
            cost += qty * price
        elif row["side"] == "sell" and inventory > 0:
            matched = min(qty, inventory)
            average = cost / inventory
            gross = float(price / average - 1)
            inventory -= matched
            cost -= average * matched
            # Count a trading episode only once the position is flat, not partial fills.
            pending = trades[-1] if trades and trades[-1].get("partial") else None
            if pending:
                pending["value"] += gross * float(matched)
                pending["qty"] += float(matched)
            else:
                pending = {"entry_at": entry_at, "exit_at": row["filled_at"], "value": gross * float(matched), "qty": float(matched), "partial": True}
                trades.append(pending)
            if inventory <= Decimal("0.0000000001"):
                pending["partial"] = False
                pending["exit_at"] = row["filled_at"]
                pending["gross_return"] = pending["value"] / pending["qty"]
                inventory = cost = Decimal("0")
                entry_at = None
    completed = [x for x in trades if not x["partial"]]
    high = COSTS["HIGH"]
    # Paper fills already reflect observed prices. Charge conservative HIGH friction
    # again to avoid mistaking a fee-free paper broker scorecard for net expectancy.
    friction = (2 * high["fee_bps"] + high["spread_bps"] + 2 * high["slippage_bps"]) / 10000
    net = [{"entry_at": x["entry_at"], "net_return": (1 + x["gross_return"]) * (1 - friction) - 1} for x in completed]
    m = metrics(net)
    elapsed = (datetime.now(UTC) - datetime.fromisoformat(runtime["activated_at"])).total_seconds() / 86400
    fresh = bool(state.get("paper_last_observed_at") and (datetime.now(UTC) - datetime.fromisoformat(state["paper_last_observed_at"])).total_seconds() < 600)
    passed = (m["trade_count"] >= GATES["paper_min_trades"] and m["independent_days"] >= GATES["paper_min_days"]
              and elapsed >= GATES["paper_elapsed_days"] and m["net_expectancy"] > 0
              and m["profit_factor"] >= GATES["min_profit_factor"] and m["max_drawdown"] <= GATES["max_drawdown"]
              and not state.get("paper_errors_observed") and fresh and inventory == 0)
    state["paper_progress"] = {"status": "ELIGIBLE_FOR_REVIEW" if passed else "ACCUMULATING_EVIDENCE",
                               "metrics": m, "elapsed_days": elapsed, "costs": high, "fresh": fresh,
                               "filled_orders": len(fills), "open_quantity": str(inventory),
                               "live_authority": False, "fee_source": "conservative_HIGH_paper_fee_adjustment"}
    if m["max_drawdown"] > GATES["max_drawdown"] or state.get("paper_errors_observed"):
        row = state["candidates"][state["paper_candidate_id"]]
        row["paper_rejected"] = True
        row["rejection_reasons"] = ["forward_paper_drawdown_or_execution_failure"]
        state["paper_progress"]["status"] = "PAPER_REJECTED"
        passed = False
    if passed:
        row = state["candidates"][state["paper_candidate_id"]]
        row["stage"] = "ELIGIBLE_FOR_REVIEW"
        state["stage"] = "ELIGIBLE_FOR_REVIEW"
        state["status"] = "ELIGIBLE_FOR_REVIEW"


class BtcDiscoveryJob:
    def __init__(self, settings):
        self.store = RhenCoreStore()
        self.settings = settings
        self.data = CryptoMarketDataClient(settings)
        self.lock = asyncio.Lock()

    def save(self, state):
        state["updated_at"] = datetime.now(UTC).isoformat()
        self.store.set_kv(NAMESPACE, "pipeline", state)

    async def tick(self):
        if self.lock.locked():
            return {"ok": True, "status": "RUNNING"}
        async with self.lock:
            state = read_state(self.store)
            if not state.get("version"):
                state = {"version": VERSION, "contract": chrono_contract(datetime.now(UTC)), "stage": "DEVELOPMENT",
                         "status": "SEARCHING", "candidates": {k: {**v, "stage": "DEVELOPMENT", "status": "QUEUED", "history": [], "results": {}, "rejection_reasons": []} for k, v in catalog().items()}}
                self.save(state)
            if state.get("running"):
                row = state["candidates"][state["current_candidate_id"]]
                if state["stage"] != "DEVELOPMENT":
                    row.update(status="REJECTED", rejection_reasons=["interrupted_confirmatory_stage_evidence_burned"])
                    state.update(status="REJECTED", running=False)
                    self.save(state)
                    return projection(self.store)
                state["running"] = False
                row["status"] = "QUEUED"
            if state.get("paper_candidate_id"):
                paper_progress(self.store, state)
                self.save(state)
                return projection(self.store)
            if state.get("status") in {"REJECTED", "EXHAUSTED"}:
                return projection(self.store)
            stage = state["stage"]
            row = None
            if stage == "DEVELOPMENT":
                row = next((x for x in state["candidates"].values() if x["status"] == "QUEUED"), None)
                if row is None:
                    eligible = [x for x in state["candidates"].values() if x["status"] == "DEVELOPMENT_PASSED"]
                    if not eligible:
                        state["status"] = "EXHAUSTED"
                        self.save(state)
                        return projection(self.store)
                    row = max(eligible, key=lambda x: (x["results"]["DEVELOPMENT"]["scenarios"]["HIGH:delay_2"]["metrics"]["net_expectancy"], x["candidate_id"]))
                    state["selected_candidate_id"] = row["candidate_id"]
                    state["stage"] = stage = "VALIDATION"
                    row["stage"] = stage
                    self.save(state)
            if row is None:
                row = state["candidates"][state["selected_candidate_id"]]
            index = STAGES.index(stage)
            if row["history"] != list(STAGES[:index]):
                raise ValueError("btc_lifecycle_stage_skip_blocked")
            state.update(running=True, current_candidate_id=row["candidate_id"], last_error=None)
            row["status"] = "RUNNING"
            self.save(state)  # Evidence exposure committed before any stage reads.
            try:
                if stage == "VELUM_REPLAY":
                    token = os.getenv("TRADING_INGEST_TOKEN", "")
                    if not token:
                        raise ValueError("velum_verifier_token_missing")
                    async with httpx.AsyncClient(timeout=300) as client:
                        response = await client.post(os.getenv("VELUM_SERVICE_URL", "http://127.0.0.1:8113") + "/v1/graen/btc-direct-replay",
                                                     headers={"x-anevum-scheduler-token": token}, json={"candidate_id": row["candidate_id"]})
                        response.raise_for_status()
                        result = response.json()
                    reasons = [] if result.get("verified") else ["velum_independent_replay_rejected"]
                else:
                    start, end = (datetime.fromisoformat(x) for x in state["contract"][stage.lower()])
                    cache_key = stage.lower()
                    corpus = self.store.get_kv(NAMESPACE, "corpus:" + cache_key, None)[0]
                    if corpus is None:
                        bars = await self.data.historical_bars_many(["BTC/USD"], start=start - timedelta(days=35), end=end - timedelta(microseconds=1), timeframe="1Hour")
                        corpus = bars.get("BTC/USD") or []
                        self.store.set_kv(NAMESPACE, "corpus:" + cache_key, corpus)
                    strategy = candidate_strategy(row)
                    engine = ContinuousReplayEngine(self.settings, strategy)
                    result = await asyncio.to_thread(engine.run_btc_direct, corpus, start=start, end=end, candidate=row)
                    previous = row["results"].get(STAGES[index - 1]) if index else None
                    reasons = gate(result, previous)
                row["results"][stage] = result
                row["history"].append(stage)
                row["rejection_reasons"] = reasons
                row["status"] = "REJECTED" if reasons else stage + "_PASSED"
                if reasons and stage != "DEVELOPMENT":
                    state["status"] = "REJECTED"
                elif not reasons and stage != "DEVELOPMENT":
                    state["stage"] = row["stage"] = STAGES[index + 1]
                    if stage == "VELUM_REPLAY":
                        row["approved_at"] = datetime.now(UTC).isoformat()
                        row["approval_fingerprint"] = fingerprint({"candidate": row["fingerprint"], "results": row["results"],
                                                                  "contract": state["contract"], "gates": GATES, "version": VERSION})
                        state["paper_candidate_id"] = row["candidate_id"]
                        state["status"] = "FORWARD_PAPER"
                state["running"] = False
                self.save(state)
                self.store.ingest_events([{"event_key": f"{VERSION}:{row['candidate_id']}:{stage}", "event_type": "btc_discovery_stage",
                    "occurred_at": state["updated_at"], "source": "GRAEN", "strategy_version_id": row["candidate_id"],
                    "payload": {"candidate_id": row["candidate_id"], "stage": stage, "status": row["status"], "rejection_reasons": reasons,
                                "fingerprint": row["fingerprint"], "live_authority": False}}])
            except Exception as exc:
                state["running"] = False
                state["last_error"] = type(exc).__name__ + ":" + str(exc)[:200]
                row["status"] = "REJECTED"
                row["rejection_reasons"] = ["stage_failed:" + state["last_error"]]
                if stage != "DEVELOPMENT":
                    state["status"] = "REJECTED"
                self.save(state)
            return projection(self.store)
